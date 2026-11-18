"""Resource admission shared by API and workers. Limits are deployment settings."""

from __future__ import annotations

import asyncio
import ctypes
import math
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path


def limit(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get("HANGZHI_" + name)
    value = int(raw) if raw is not None else default
    if not 1 <= value <= maximum:
        raise ValueError(f"HANGZHI_{name} 必须在 1 至 {maximum} 之间")
    return value


def task_slots():
    return limit("TASK_SLOTS", 8, 256)


def model_slots():
    return limit("MODEL_SLOTS", 2, 64)


def context_window():
    return limit("CONTEXT_TOKENS", 1_000_000, 1_008_000)


class CapacityTimeout(RuntimeError):
    pass


@asynccontextmanager
async def model_slot(ledger_path: Path, check_cancelled):
    """A short-lived SQLite exclusive lock represents a live OS process slot.

    Locks live beside the shared ledger, not inside its accounting transaction.
    Closing the handle (including process death) frees the slot. Never unlink a
    lock file while participants are active. All workers use the same policy.
    """
    slots = model_slots()
    deadline = time.monotonic() + limit("MODEL_QUEUE_SECONDS", 30, 300)
    directory = ledger_path.resolve().with_suffix(ledger_path.suffix + ".slots")
    directory.mkdir(exist_ok=True)
    while True:
        check_cancelled()
        for index in range(slots):
            db = sqlite3.connect(directory / f"{index}.sqlite3", timeout=0)
            try:
                try:
                    db.execute("BEGIN EXCLUSIVE")
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", None) not in (
                        sqlite3.SQLITE_BUSY,
                        sqlite3.SQLITE_LOCKED,
                    ):
                        raise
                    continue
                yield
                return
            finally:
                db.close()
        if time.monotonic() >= deadline:
            raise CapacityTimeout("模型资源并发已满，等待超过配置时限，请稍后重试")
        await asyncio.sleep(0.1)


def latency_summary(samples):
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        **{
            name: round(ordered[math.ceil(len(ordered) * q) - 1], 1) if ordered else None
            for name, q in (("p50_ms", 0.5), ("p95_ms", 0.95))
        },
    }


def host_capacity():
    total = None
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (name, ctypes.c_uint64)
                for name in ("physical", "free", "page", "free_page", "virtual", "free_virtual", "extended")
            ]

        record = MemoryStatus()
        record.length = ctypes.sizeof(record)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(record)):
            total = record.physical
    elif hasattr(os, "sysconf"):
        try:
            pages, size = (os.sysconf(name) for name in ("SC_PHYS_PAGES", "SC_PAGE_SIZE"))
            if pages > 0 and size > 0:
                total = pages * size
        except (ValueError, OSError):
            pass
    return {"logical_cpu": os.cpu_count(), "host_ram_mib": total // 2**20 if total else None}
