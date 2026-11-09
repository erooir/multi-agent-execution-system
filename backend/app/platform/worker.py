"""Independent persistent task worker and deduplicated interval scheduler."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial

from pydantic import ValidationError
from sqlalchemy import select

from ..storage import store
from .dameng import supports_row_lock
from .db import Repository, now, repo, tables
from .runtime import process_claim, timestamp

log = logging.getLogger("hangzhi.worker")
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 32


def worker_concurrency(value: int | str) -> int:
    """Reject invalid deployment configuration instead of silently reducing it."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("Worker concurrency must be an integer from 1 to 32") from None
    if str(parsed) != str(value).strip() or not 1 <= parsed <= MAX_CONCURRENCY:
        raise ValueError("Worker concurrency must be an integer from 1 to 32")
    return parsed


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


def register_worker(
    repository: Repository,
    worker_id: str,
    status: str,
    active: list[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    maintenance_active: bool = False,
):
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
            "concurrency": concurrency,
            "active_count": len(active),
            "available_slots": max(0, concurrency - len(active)) if status == "running" else 0,
            "execution_model": "asyncio_with_threaded_local_tools",
            "maintenance_active": maintenance_active,
            "runtime": "agno-3.0.10",
            "telemetry_enabled": False,
            "auto_improve": os.environ.get("HANGZHI_AUTO_IMPROVE", "").lower() == "true",
        },
    )


async def run_worker(
    repository: Repository = repo,
    *,
    worker_id: str | None = None,
    concurrency: int | None = None,
    poll_seconds: float = 0.5,
    once=False,
    stop: asyncio.Event | None = None,
):
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
    stop = stop or asyncio.Event()
    concurrency = worker_concurrency(
        os.environ.get("HANGZHI_WORKER_CONCURRENCY", str(DEFAULT_CONCURRENCY))
        if concurrency is None
        else concurrency
    )
    if repository.connection is not None:
        raise ValueError("Worker requires a repository without a bound transaction")
    if once:
        concurrency = 1
    active: dict[asyncio.Task, str] = {}
    last_heartbeat = 0.0
    maintenance: asyncio.Task | None = None
    last_maintenance = 0.0
    last_report: tuple | None = None
    # Keep claim/registration/schedule locks out of the Agent event loop. This
    # dedicated thread cannot be starved by local decoders in the default pool.
    control_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hangzhi-scheduler")
    loop = asyncio.get_running_loop()

    async def control(function, *args, **kwargs):
        return await loop.run_in_executor(control_pool, partial(function, *args, **kwargs))

    async def report(status, task_ids):
        await control(
            register_worker,
            repository,
            worker_id,
            status,
            task_ids,
            concurrency=concurrency,
            maintenance_active=bool(maintenance and not maintenance.done()),
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
            heartbeat_due = time.monotonic() - last_heartbeat >= 5
            if heartbeat_due:
                await control(schedule_due, repository)
                last_heartbeat = time.monotonic()
            while not stop.is_set() and len(active) < concurrency:
                task = await control(repository.claim, worker_id)
                if task is None:
                    break
                if stop.is_set():
                    await control(repository.finish_claim, task["id"], worker_id, task["lease_token"])
                    break
                if once:
                    await report("running", [task["id"]])
                    await process_claim(repository, task, worker_id)
                    return
                active[asyncio.create_task(process_claim(repository, task, worker_id))] = task["id"]
            if once:
                return
            # Give already queued user work first access to local execution slots.
            if (
                not active
                and not stop.is_set()
                and maintenance is None
                and time.monotonic() - last_maintenance >= 30
            ):
                from .improvement import maintain

                maintenance = asyncio.create_task(
                    asyncio.to_thread(lambda: asyncio.run(maintain(repository)))
                )
                last_maintenance = time.monotonic()
            report_key = (tuple(active.values()), bool(maintenance and not maintenance.done()))
            if heartbeat_due or report_key != last_report:
                await report("running", list(active.values()))
                last_report = report_key
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.05, poll_seconds))
            except TimeoutError:
                pass
    finally:
        try:
            # Cancelling coroutine execution stops gateway transports. Local
            # read-only calls must finish before their slot is reported released.
            # In-flight markers/reservations remain available for honest recovery.
            pending = {future for future in active if not future.done()}
            for future in pending:
                future.cancel()
            if maintenance and not maintenance.done():
                pending.add(maintenance)
            while pending:
                try:
                    await report(
                        "stopping", [task_id for future, task_id in active.items() if not future.done()]
                    )
                except Exception as exc:  # noqa: BLE001 - database failure must not skip transport/tool cleanup.
                    log.error("Worker shutdown status unavailable: %s", type(exc).__name__)
                _, pending = await asyncio.wait(pending, timeout=5)
            await asyncio.gather(*active, *([maintenance] if maintenance else []), return_exceptions=True)
            try:
                await report("stopped", [])
            except Exception as exc:  # noqa: BLE001 - keep the original failure; stale health remains visible.
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
    parser.add_argument(
        "--concurrency",
        type=worker_concurrency,
        default=os.environ.get("HANGZHI_WORKER_CONCURRENCY", str(DEFAULT_CONCURRENCY)),
        help="每个 Worker 的任务并发数，1–32；默认读取 HANGZHI_WORKER_CONCURRENCY 或使用 2",
    )
    parser.add_argument("--once", action="store_true", help="调度到期计划并执行至多一个任务后退出")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
