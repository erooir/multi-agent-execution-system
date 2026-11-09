"""Actual Agno/local task overlap, durable admission and worker shutdown.

Blocking gates wrap the real deterministic tool; no model or paid provider is
called. Independent worker schedulers share a real isolated SQL repository.
"""

import asyncio
import threading
import time
from collections import Counter

import pytest

from backend.app.platform import runtime, worker
from backend.tests import test_dameng as dm_fixtures
from backend.tests import test_v2_runtime as fixtures

repository = fixtures.repository
real_dm = dm_fixtures.real_dm
seed = fixtures.seed


async def wait_until(condition, timeout=8):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


def gated_tool(monkeypatch, expected):
    original = runtime.execute_local
    state = {"active": 0, "peak": 0, "calls": Counter(), "contexts": {}}
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()

    def execute(*args, **kwargs):
        execution = runtime.context()
        task_id = execution.task["id"]
        with lock:
            state["calls"][task_id] += 1
            state["contexts"][task_id] = execution
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            if state["active"] == expected:
                entered.set()
        try:
            if not release.wait(10):
                raise TimeoutError("Concurrent local tools never reached the release gate")
            return original(*args, **kwargs)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(runtime, "execute_local", execute)
    return state, entered, release


@pytest.mark.parametrize("worker_count,capacity", [(1, 2), (2, 2), (1, 5)])
async def test_real_local_tasks_overlap_without_duplicate_claims(
    repository, monkeypatch, worker_count, capacity
):
    total_slots = worker_count * capacity
    task_ids = [f"parallel-{i}" for i in range(total_slots + 2)]
    for task_id in task_ids:
        seed(repository, task_id=task_id)
    state, entered, release = gated_tool(monkeypatch, total_slots)
    stop = asyncio.Event()
    runners = [
        asyncio.create_task(
            worker.run_worker(
                repository,
                worker_id=f"parallel-worker-{i}",
                concurrency=capacity,
                poll_seconds=0.01,
                stop=stop,
            )
        )
        for i in range(worker_count)
    ]
    try:
        assert await asyncio.to_thread(entered.wait, 8), "Tools ran serially instead of overlapping"
        await wait_until(
            lambda: sum(row.get("active_count", 0) for row in repository.list("workers")) == total_slots
        )
        assert state["active"] == total_slots
        assert len([row for row in repository.list("tasks") if row["lease_token"]]) == total_slots
        for row in repository.list("workers"):
            assert row["concurrency"] == capacity
            assert row["active_count"] == capacity
            assert row["available_slots"] == 0
        release.set()
        await wait_until(lambda: all(row["status"] == "succeeded" for row in repository.list("tasks")))
    finally:
        release.set()
        stop.set()
        await asyncio.wait_for(asyncio.gather(*runners), 8)
    assert state["peak"] == total_slots
    assert state["calls"] == Counter({task_id: 1 for task_id in task_ids})
    assert len({id(context) for context in state["contexts"].values()}) == len(task_ids)
    instances = repository.list("instances")
    assert len(instances) == len(task_ids)
    assert len({row["context_id"] for row in instances}) == len(task_ids)
    assert all(row["status"] == "released" for row in instances)
    assert all(row["status"] == "stopped" and row["active_count"] == 0 for row in repository.list("workers"))
    assert runtime.model_gateway.ledger.usage()["request_count"] == 0


@pytest.mark.parametrize("status_write_fails", [False, True])
async def test_shutdown_keeps_blocked_tool_slot_until_call_really_ends(
    repository, monkeypatch, status_write_fails
):
    seed(repository)
    state, entered, release = gated_tool(monkeypatch, 1)
    stopping = threading.Event()
    register = worker.register_worker

    def report(*args, **kwargs):
        if args[2] == "stopping":
            stopping.set()
            if status_write_fails:
                raise OSError("Synthetic status storage outage")
        return register(*args, **kwargs)

    monkeypatch.setattr(worker, "register_worker", report)
    stop = asyncio.Event()
    running = asyncio.create_task(
        worker.run_worker(repository, worker_id="draining", concurrency=1, stop=stop, poll_seconds=0.01)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 8)
        stop.set()
        assert await asyncio.to_thread(stopping.wait, 3)
        assert not running.done()
        assert state["active"] == 1
        record = repository.get("workers", "draining")
        if not status_write_fails:
            await wait_until(lambda: repository.get("workers", "draining")["status"] == "stopping")
        assert record["active_task_ids"] == ["task-1"]
        assert record["available_slots"] == 0
        assert repository.list("instances")[0]["status"] == "running"
    finally:
        release.set()
        stop.set()
        await asyncio.wait_for(running, 8)
    assert state["active"] == 0
    record = repository.get("workers", "draining")
    assert record["status"] == "stopped"
    assert record["active_task_ids"] == []
    # Shutdown is not a successful result or a user cancellation. Its safe local
    # checkpoint can be recovered under a new lease after the old one expires.
    task = repository.get("tasks", "task-1")
    assert task["status"] == "running"
    repository.put("tasks", {**task, "lease_until": 0})
    await worker.run_worker(repository, worker_id="replacement", once=True)
    assert repository.get("tasks", "task-1")["status"] == "succeeded"
    assert all(row["status"] != "running" for row in repository.list("instances"))


async def test_slow_scheduler_does_not_block_agent_event_loop(repository, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    thread_ids = []
    main_thread = threading.get_ident()

    def blocking_schedule(*_):
        thread_ids.append(threading.get_ident())
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Scheduler blocked the event loop")
        return 0

    monkeypatch.setattr(worker, "schedule_due", blocking_schedule)
    stop = asyncio.Event()
    running = asyncio.create_task(worker.run_worker(repository, worker_id="slow-control", stop=stop))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # A slow SQL claim/schedule must not stop cancellation/lease coroutines.
        started = time.monotonic()
        await asyncio.sleep(0.02)
        assert time.monotonic() - started < 1
        assert thread_ids == [thread_ids[0]] and thread_ids[0] != main_thread
    finally:
        stop.set()
        release.set()
        await asyncio.wait_for(running, 5)


@pytest.mark.parametrize("value", [0, -1, 33, True, 2.5, "invalid"])
async def test_invalid_concurrency_fails_before_registering(repository, value):
    with pytest.raises(ValueError, match="concurrency"):
        await worker.run_worker(repository, worker_id="invalid", concurrency=value, once=True)
    assert not repository.list("workers")


async def test_environment_capacity_is_used_and_explicit_value_overrides_it(repository, monkeypatch):
    monkeypatch.setenv("HANGZHI_WORKER_CONCURRENCY", "7")
    stop = asyncio.Event()
    stop.set()
    await worker.run_worker(repository, worker_id="environment", stop=stop)
    assert repository.get("workers", "environment")["concurrency"] == 7
    await worker.run_worker(repository, worker_id="explicit", concurrency=3, stop=stop)
    assert repository.get("workers", "explicit")["concurrency"] == 3


async def test_worker_rejects_connection_shared_across_threads(repository):
    with repository.transaction() as transaction, pytest.raises(ValueError, match="bound transaction"):
        await worker.run_worker(transaction, once=True)


async def test_dm8_real_workers_execute_four_overlapping_local_tasks(real_dm, repository, monkeypatch):
    # repository supplies only the isolated, non-paid ledger fixture. DM's
    # explicitly configured *_TEST schema is the actual task/lease storage.
    await test_real_local_tasks_overlap_without_duplicate_claims(real_dm, monkeypatch, 2, 2)
