"""Task ownership continues while synchronous work occupies the workflow event loop."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.platform import runtime
from backend.app.platform.db import Repository
from backend.tests import test_v2_runtime as fixtures

repository = fixtures.repository


async def test_blocked_workflow_loop_retains_lease(repository, monkeypatch):
    fixtures.seed(repository)
    claim = repository.claim("owner", lease_seconds=1)
    competing = Repository(engine=repository.engine)
    entered, attempted = threading.Event(), threading.Event()
    renewal_threads = []
    original_heartbeat = Repository.heartbeat
    original_workflow = runtime.runtime_workflow
    monkeypatch.setattr(runtime, "_HEARTBEAT_SECONDS", 0.05)

    def short_renewal(self, task_id, owner, token):
        assert self is not repository and self.connection is None
        renewal_threads.append(threading.current_thread())
        return original_heartbeat(self, task_id, owner, token, lease_seconds=1)

    def cold_workflow():
        entered.set()
        assert attempted.wait(8), "the independent worker did not claim during cold startup"
        return original_workflow()

    def compete_after_initial_expiry():
        try:
            assert entered.wait(5)
            time.sleep(max(0, claim["lease_until"] + 0.25 - time.time()))
            return competing.claim("competitor")
        finally:
            attempted.set()

    monkeypatch.setattr(Repository, "heartbeat", short_renewal)
    monkeypatch.setattr(runtime, "runtime_workflow", cold_workflow)
    with ThreadPoolExecutor(max_workers=1) as contender:
        other = contender.submit(compete_after_initial_expiry)
        result = await runtime.process_claim(repository, claim, "owner")
        assert other.result() is None

    assert result["status"] == "succeeded" and result["lease_token"] == 1
    assert len(renewal_threads) >= 2
    assert len(set(renewal_threads)) == 1 and not renewal_threads[0].is_alive()


async def test_renewal_error_stops_execution_and_releases_thread(repository, monkeypatch):
    fixtures.seed(repository)
    claim = repository.claim("owner")
    renewal_threads = []
    original_workflow = runtime.runtime_workflow
    monkeypatch.setattr(runtime, "_HEARTBEAT_SECONDS", 0.05)

    def unavailable(*args):
        renewal_threads.append(threading.current_thread())
        raise RuntimeError("synthetic connection failure")

    def wait_for_failed_renewal():
        assert runtime.context().lease_lost.wait(5)
        return original_workflow()

    monkeypatch.setattr(Repository, "heartbeat", unavailable)
    monkeypatch.setattr(runtime, "runtime_workflow", wait_for_failed_renewal)
    result = await runtime.process_claim(repository, claim, "owner")
    assert result["status"] == "accepted" and result["lease_token"] == 1
    assert not repository.list("artifacts")
    assert len(renewal_threads) == 1 and not renewal_threads[0].is_alive()


async def test_process_claim_rejects_transaction_bound_repository(repository):
    fixtures.seed(repository)
    claim = repository.claim("owner")
    with repository.transaction() as transaction, pytest.raises(ValueError, match="bound transaction"):
        await runtime.process_claim(transaction, claim, "owner")
