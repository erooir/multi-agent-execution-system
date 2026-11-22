"""Independent persistent task worker and deduplicated interval scheduler."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from ..storage import store
from .dameng import supports_row_lock
from .db import Repository, now, repo, tables
from .runtime import process_claim, timestamp

log = logging.getLogger("hangzhi.worker")


def _admission_busy(exc: OperationalError) -> bool:
    # This statement failed before the transaction obtained a writer lock.
    # A later poll can start a fresh transaction without replaying any writes.
    return (
        exc.statement == "BEGIN IMMEDIATE"
        and isinstance(exc.orig, sqlite3.OperationalError)
        and (getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    )


def schedule_due(repository: Repository, *, current_time: float | None = None) -> int:
    """Create task and advance its schedule in one DB transaction.

    Missed intervals coalesce to the latest due slot, never an unlimited catch-up
    storm. Its stable UTC timestamp is part of the task idempotency key.
    Project/identity rights are rechecked on every trigger, not only on creation.
    """
    from . import service

    current_time = time.time() if current_time is None else current_time
    count = 0
    for schedule in repository.list("schedules", status="enabled"):
        if timestamp(schedule["next_run_at"]) > current_time:
            continue
        with repository.transaction() as tx:
            query = select(tables["schedules"]).where(tables["schedules"].c.id == schedule["id"])
            if supports_row_lock(repository.engine):
                query = query.with_for_update(skip_locked=True)
            locked = tx.connection.execute(query).first()
            if locked is None:
                continue
            schedule = tx.get("schedules", schedule["id"])
            if schedule["status"] != "enabled" or timestamp(schedule["next_run_at"]) > current_time:
                continue
            creator = schedule.get("created_by")
            user = store.get("users", creator) if creator else None
            if not user or not user.get("enabled", True) or user.get("role") not in {"operator", "admin"}:
                schedule.update(status="disabled", last_error="schedule_principal_unavailable")
                tx.put("schedules", schedule)
                continue
            due = timestamp(schedule["next_run_at"])
            interval = schedule["interval_seconds"]
            missed = max(0, int((current_time - due) // interval))
            slot = due + missed * interval
            slot_iso = datetime.fromtimestamp(slot, UTC).isoformat()
            body = dict(schedule["task_template"])
            body["project_id"] = schedule["project_id"]
            body["idempotency_key"] = f"schedule:{schedule['id']}:{slot_iso}"
            try:
                task = service.create_task(body, user, repository=tx)
            except (service.DomainError, ValidationError) as exc:
                # Persist a safe error and disable invalid definitions rather than
                # hammering a failing identity/data/policy every polling tick.
                detail = getattr(exc, "detail", None)
                code = (
                    detail.get("code", "schedule_submission_failed")
                    if isinstance(detail, dict)
                    else getattr(exc, "code", "schedule_submission_failed")
                )
                schedule.update(status="disabled", last_error=code, last_attempt_at=now())
                tx.put("schedules", schedule)
                log.warning("Schedule %s disabled: %s", schedule["id"], code)
                continue
            schedule.update(
                next_run_at=datetime.fromtimestamp(slot + interval, UTC).isoformat(),
                last_run_at=slot_iso,
                last_task_id=task["id"],
                last_error=None,
                missed_intervals=missed,
            )
            tx.put("schedules", schedule)
            tx.event(
                task["id"],
                "schedule.triggered",
                {"schedule_id": schedule["id"], "slot": slot_iso, "missed_intervals": missed},
            )
            count += 1
    return count


def register_worker(repository: Repository, worker_id: str, status: str, active: list[str]):
    previous = repository.get("workers", worker_id) or {}
    repository.put(
        "workers",
        {
            **previous,
            "id": worker_id,
            "status": status,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "heartbeat_at": now(),
            "active_task_ids": active,
            "runtime": "agno-3.0.10",
            "telemetry_enabled": False,
            "auto_improve": os.environ.get("HANGZHI_AUTO_IMPROVE", "").lower() == "true",
        },
    )


async def run_worker(
    repository: Repository = repo,
    *,
    worker_id: str | None = None,
    concurrency: int = 2,
    poll_seconds: float = 0.5,
    once=False,
    stop: asyncio.Event | None = None,
):
    if repository.connection is not None:
        raise ValueError("Worker requires a repository without a bound transaction")
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
    stop = stop or asyncio.Event()
    concurrency = max(1, min(4, concurrency))
    active: dict[asyncio.Task, str] = {}
    last_heartbeat = 0.0
    maintenance: asyncio.Task | None = None
    intake: asyncio.Task | None = None
    last_maintenance = 0.0
    # Keep claim/registration/schedule locks out of the Agent event loop. This
    # dedicated thread cannot be starved by local decoders in the default pool.
    control_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hangzhi-scheduler")
    loop = asyncio.get_running_loop()

    async def control(function, *args, defer_busy=False, **kwargs):
        try:
            return await loop.run_in_executor(control_pool, partial(function, *args, **kwargs))
        except OperationalError as exc:
            if once or not defer_busy or not _admission_busy(exc):
                raise
            log.warning("SQLite writer busy; deferred worker %s", function.__name__)
            return None

    async def report(status, task_ids):
        await control(
            register_worker,
            repository,
            worker_id,
            status,
            task_ids,
            defer_busy=True,
        )

    try:
        await report("running", [])
        while not stop.is_set():
            for future in list(active):
                if future.done():
                    del active[future]
                    try:
                        future.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001 - a failed task must not kill other claims.
                        log.error("Task worker failed: %s", type(exc).__name__)
            if maintenance and maintenance.done():
                try:
                    maintenance.result()
                except Exception as exc:  # noqa: BLE001 - preserve worker availability, log safe diagnostic.
                    log.error("Improvement maintenance failed: %s", type(exc).__name__)
                maintenance = None
            if time.monotonic() - last_heartbeat >= 5:
                await report("running", list(active.values()))
                await control(schedule_due, repository, defer_busy=True)
                if intake is None or intake.done():
                    if intake:
                        try:
                            intake.result()
                        except Exception as exc:  # noqa: BLE001 - lease permits recovery after interruption.
                            log.error("Upstream retrieval failed: %s", type(exc).__name__)
                    from .task_intake import retrieve_one

                    if once:
                        await retrieve_one(repository)
                    else:
                        intake = asyncio.create_task(retrieve_one(repository))
                last_heartbeat = time.monotonic()
            if not active and maintenance is None and not once and time.monotonic() - last_maintenance >= 30:
                from .improvement import maintain

                # maintain performs synchronous local replays. Its own event loop
                # runs in a thread so lease/worker heartbeats remain responsive.
                maintenance = asyncio.create_task(
                    asyncio.to_thread(lambda: asyncio.run(maintain(repository)))
                )
                last_maintenance = time.monotonic()
            while not stop.is_set() and len(active) < concurrency:
                task = await control(repository.claim, worker_id, defer_busy=True)
                if task is None:
                    break
                if stop.is_set():
                    await control(repository.finish_claim, task["id"], worker_id, task["lease_token"])
                    break
                if once:
                    await process_claim(repository, task, worker_id)
                    return
                active[asyncio.create_task(process_claim(repository, task, worker_id))] = task["id"]
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.05, poll_seconds))
            except TimeoutError:
                pass
    finally:
        try:
            # Cancelling coroutine execution stops gateway transports; unfinished
            # external calls retain in_flight and reservations for honest recovery.
            for future in active:
                future.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            if intake:
                intake.cancel()
                await asyncio.gather(intake, return_exceptions=True)
            if maintenance:
                await asyncio.gather(maintenance, return_exceptions=True)
            try:
                await report("stopped", [])
            except Exception as exc:  # noqa: BLE001 - preserve the original failure.
                log.error("Worker stopped status unavailable: %s", type(exc).__name__)
        finally:
            control_pool.shutdown(wait=True)


async def _main(args):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await run_worker(concurrency=args.concurrency, once=args.once, stop=stop)


def main():
    parser = argparse.ArgumentParser(description="航智独立任务工作进程")
    parser.add_argument("--concurrency", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--once", action="store_true", help="调度到期计划并执行至多一个任务后退出")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
