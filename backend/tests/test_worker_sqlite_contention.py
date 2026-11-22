"""Exercise worker admission with a real competing SQLite writer and a local-only task."""

import asyncio
import sqlite3
import threading
from contextlib import closing
from functools import wraps

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from backend.app.platform import worker
from backend.tests import test_v2_runtime as fixtures

repository = fixtures.repository


@pytest.mark.parametrize("operation", ["initial_registration", "heartbeat", "claim"])
async def test_worker_resumes_after_sqlite_writer_contention(repository, monkeypatch, operation):
    attempted = threading.Event()
    errors = []
    owner, name = (repository, "claim") if operation == "claim" else (worker, "register_worker")
    original = getattr(owner, name)
    remaining = 2 if operation == "heartbeat" else 1

    @event.listens_for(repository.engine, "checkout")
    def short_busy_timeout(connection, *_):
        connection.execute("PRAGMA busy_timeout=75")

    @wraps(original)
    def contended(*args, **kwargs):
        nonlocal remaining
        remaining -= 1
        if remaining != 0:
            return original(*args, **kwargs)
        with closing(sqlite3.connect(repository.engine.url.database)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                return original(*args, **kwargs)
            except OperationalError as exc:
                errors.append((exc.statement, exc.orig.sqlite_errorcode))
                raise
            finally:
                writer.rollback()
                attempted.set()

    monkeypatch.setattr(owner, name, contended)
    stop = asyncio.Event()
    runner = asyncio.create_task(
        worker.run_worker(repository, worker_id="busy-test", concurrency=1, poll_seconds=0.05, stop=stop)
    )
    try:
        assert await asyncio.to_thread(attempted.wait, 10)
        assert errors == [("BEGIN IMMEDIATE", sqlite3.SQLITE_BUSY)]
        fixtures.seed(repository)
        async with asyncio.timeout(12):
            while repository.get("tasks", "task-1")["status"] != "succeeded":
                if runner.done():
                    await runner
                    pytest.fail("worker exited before servicing the queued task")
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await runner
    task = repository.get("tasks", "task-1")
    assert task["lease_token"] == 1 and task["lease_owner"] is None
    assert task["result"]["usage"]["request_count"] == 0
    assert repository.get("workers", "busy-test")["status"] == "stopped"


async def test_worker_propagates_sqlite_non_lock_admission_errors(repository, monkeypatch):
    def readonly_claim(worker_id):
        with repository.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            finally:
                connection.exec_driver_sql("PRAGMA query_only=OFF")

    monkeypatch.setattr(repository, "claim", readonly_claim)
    with pytest.raises(OperationalError) as failure:
        await worker.run_worker(repository, worker_id="readonly-test")
    assert failure.value.statement == "BEGIN IMMEDIATE"
    assert failure.value.orig.sqlite_errorcode == sqlite3.SQLITE_READONLY
