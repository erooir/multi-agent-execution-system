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
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select

from ..storage import store
from .db import Repository, now, repo, tables
from .runtime import process_claim, timestamp

log = logging.getLogger("hangzhi.worker")


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
            if repository.engine.dialect.name == "postgresql":
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
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
    stop = stop or asyncio.Event()
    concurrency = max(1, min(4, concurrency))
    active: dict[asyncio.Task, str] = {}
    last_heartbeat = 0.0
    maintenance: asyncio.Task | None = None
    last_maintenance = 0.0
    try:
        register_worker(repository, worker_id, "running", [])
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
                register_worker(repository, worker_id, "running", list(active.values()))
                schedule_due(repository)
                last_heartbeat = time.monotonic()
            if not active and maintenance is None and not once and time.monotonic() - last_maintenance >= 30:
                from .improvement import maintain

                # maintain performs synchronous local replays. Its own event loop
                # runs in a thread so lease/worker heartbeats remain responsive.
                maintenance = asyncio.create_task(
                    asyncio.to_thread(lambda: asyncio.run(maintain(repository)))
                )
                last_maintenance = time.monotonic()
            while len(active) < concurrency:
                task = repository.claim(worker_id)
                if task is None:
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
        # Cancelling coroutine execution stops gateway transports; unfinished
        # external calls retain in_flight and reservations for honest recovery.
        for future in active:
            future.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        if maintenance:
            await asyncio.gather(maintenance, return_exceptions=True)
        register_worker(repository, worker_id, "stopped", [])


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
