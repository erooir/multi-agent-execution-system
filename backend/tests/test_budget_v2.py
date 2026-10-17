"""Safety contracts use isolated ledgers and HTTP fakes; never paid models."""

import asyncio
import concurrent.futures
from types import SimpleNamespace

import httpx
import pytest

from backend.app.model_gateway import (
    BudgetExceeded,
    BudgetLedger,
    InvalidModelUsage,
    MeteredTransport,
    ModelGateway,
    ModelRequestCancelled,
    request_scope,
)


def test_task_cap_is_atomic_and_cannot_be_expanded(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")

    def reserve(_):
        try:
            return ledger.reserve(0.1, "execute", "task-a", task_limit=0.5)
        except BudgetExceeded:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert len([r for r in pool.map(reserve, range(20)) if r]) == 5
    with pytest.raises(BudgetExceeded):
        ledger.reserve(0.1, "review", "task-a", task_limit=10)
    ledger.reserve(0.1, "execute", "task-b", task_limit=0.5)
    assert ledger.usage("task-a")["total_cny"] == 0.5
    assert ledger.usage("task-b")["request_count"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_nonfinite_and_negative_reservations_rejected(tmp_path, value):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(value, "invalid")
    assert ledger.budget()["request_count"] == 0


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": -1, "completion_tokens": 5},
        {"prompt_tokens": "10", "completion_tokens": 5},
        {"prompt_tokens": 10, "completion_tokens": True},
        {"prompt_tokens": 1.5, "completion_tokens": 5},
    ],
)
def test_invalid_usage_never_releases_reservation(tmp_path, usage):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    request_id = ledger.reserve(0.5, "execute", "task")
    with pytest.raises(InvalidModelUsage):
        ledger.settle(request_id, usage)
    assert ledger.usage("task")["reserved_cny"] == 0.5
    assert ledger.usage("task")["uncertain_count"] == 1


def test_unpriced_response_model_blocks_future_requests(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    request_id = ledger.reserve(0.5, "execute", "task")
    with pytest.raises(InvalidModelUsage):
        ledger.settle(request_id, {"prompt_tokens": 1, "completion_tokens": 1}, "unknown-model")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(0.01, "execute")
    assert ledger.budget()["reserved_cny"] == 0.5


def test_aggregates_include_all_requests_and_uncertain_charges(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    first = ledger.reserve(0.5, "execute", "task")
    ledger.settle(first, {"prompt_tokens": 1000, "completion_tokens": 500})
    second = ledger.reserve(0.3, "review", "task")
    ledger.uncertain(second)
    ledger.reserve(0.1, "execute", "another")
    assert ledger.usage("task")["total_cny"] == 0.312
    assert ledger.usage("task", "execute")["cost_cny"] == 0.012
    assert ledger.usage("task")["accounting_complete"] is False
    assert ledger.usage(request_ids=[])["request_count"] == 0


def test_default_gateway_cannot_be_reset_by_runtime_data_directory(tmp_path, monkeypatch):
    from backend.app import model_gateway as module

    original = tmp_path / "historical.sqlite3"
    ledger = BudgetLedger(original)
    ledger.reserve(2, "historical")
    monkeypatch.setattr(module, "BUDGET_LEDGER_PATH", original)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "different-data"))
    assert ModelGateway().ledger.budget()["reserved_cny"] == 2
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(module, "BUDGET_LEDGER_PATH", missing)
    with pytest.raises(RuntimeError, match="ledger_missing"):
        _ = ModelGateway().ledger
    assert not missing.exists()


@pytest.mark.asyncio
async def test_egress_denial_covers_both_gateway_interfaces(tmp_path):
    gateway = ModelGateway(BudgetLedger(tmp_path / "isolated.sqlite3"))
    with request_scope("task", 1, False):
        with pytest.raises(PermissionError):
            await gateway.complete("private")
        with pytest.raises(PermissionError):
            await gateway.run_agent({}, "private", {})
    with pytest.raises(PermissionError):
        await gateway.run_agent({}, "private", {}, SimpleNamespace(data_visibility="local"))
    assert gateway.ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_nested_scope_cannot_escalate_egress_or_switch_tasks(tmp_path):
    gateway = ModelGateway(BudgetLedger(tmp_path / "isolated.sqlite3"))
    with request_scope("task", 1, False):
        with request_scope("task", 10, True) as child:
            assert child.max_cost_cny == 1
            with pytest.raises(PermissionError):
                await gateway.complete("text")
        with pytest.raises(PermissionError), request_scope("another", 1, True):
            pass


@pytest.mark.asyncio
async def test_cancellation_before_dispatch_creates_no_charge(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    transport = MeteredTransport(ledger, "execute", inner=httpx.MockTransport(lambda _: pytest.fail()))
    with request_scope("task", 1, True, cancelled=lambda: True):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ModelRequestCancelled):
                await client.post("https://api.deepseek.com/chat/completions", json={})
    assert ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_inflight_cancellation_retains_uncertain_charge(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    started, cancel = asyncio.Event(), asyncio.Event()

    async def handler(_):
        started.set()
        await asyncio.Event().wait()

    async def cancel_after_dispatch():
        await started.wait()
        cancel.set()

    transport = MeteredTransport(ledger, "execute", inner=httpx.MockTransport(handler))
    with request_scope("task", 1, True, cancelled=cancel.is_set):
        trigger = asyncio.create_task(cancel_after_dispatch())
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ModelRequestCancelled):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-flash", "max_tokens": 100},
                )
        await trigger
    assert ledger.usage("task")["uncertain_count"] == 1
    assert ledger.usage("task")["reserved_cny"] > 0


@pytest.mark.asyncio
async def test_task_budget_checked_before_network(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    transport = MeteredTransport(ledger, "execute", inner=httpx.MockTransport(lambda _: pytest.fail()))
    with request_scope("task", 0.001, True):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BudgetExceeded):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-flash", "max_tokens": 100},
                )
    assert ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_multi_round_transport_returns_total_cost(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    transport = MeteredTransport(
        ledger,
        "execute",
        inner=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"usage": {"prompt_tokens": 10, "completion_tokens": 20}, "model": "deepseek-flash"},
            )
        ),
    )
    with request_scope("task", 1, True):
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(3):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={
                        "model": "deepseek-flash",
                        "max_tokens": 100,
                    },
                )
    assert transport.summary()["request_count"] == 3
    assert transport.summary()["usage"]["completion_tokens"] == 60
    assert transport.summary()["cost_cny"] == 0.00108


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"max_completion_tokens": 9000},
        {"n": 2},
        {"tools": [{"type": "web_search"}]},
    ],
)
async def test_unbounded_or_extra_billing_rejected(tmp_path, extra):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    transport = MeteredTransport(ledger, "execute", inner=httpx.MockTransport(lambda _: pytest.fail()))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError):
            await client.post(
                "https://api.deepseek.com/chat/completions",
                json={
                    "model": "deepseek-flash",
                    "max_tokens": 100,
                    **extra,
                },
            )
    assert ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["complete", "run_agent"])
async def test_sdk_wrapping_does_not_hide_worker_policy_failure(tmp_path, monkeypatch, method):
    import agno.agent

    from backend.app import model_gateway as module

    class TaskDeadline(RuntimeError):
        pass

    failure = TaskDeadline("task deadline reached")

    class CapturingTransport(MeteredTransport):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.policy_failure = failure

    class WrappedAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, *args, **kwargs):
            raise RuntimeError("SDK wrapper with private request details")

    monkeypatch.setattr(module, "MeteredTransport", CapturingTransport)
    monkeypatch.setattr(module, "get_api_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(module, "get_settings", lambda: {"max_output_tokens": 8000, "budget_limit_cny": 300})
    monkeypatch.setattr(agno.agent, "Agent", WrappedAgent)
    gateway = ModelGateway(BudgetLedger(tmp_path / "isolated.sqlite3"))
    with request_scope("task", 1, True), pytest.raises(TaskDeadline) as error:
        if method == "complete":
            await gateway.complete("synthetic text")
        else:
            await gateway.run_agent({}, "synthetic text", {})
    assert error.value is failure
    assert gateway.ledger.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_worker_exception_during_network_is_retained_for_sdk(tmp_path):
    ledger = BudgetLedger(tmp_path / "isolated.sqlite3")
    started = asyncio.Event()

    class LeaseLost(RuntimeError):
        pass

    def check_lease():
        if started.is_set():
            raise LeaseLost("fenced worker")
        return False

    async def handler(_):
        started.set()
        await asyncio.Event().wait()

    transport = MeteredTransport(ledger, "execute", inner=httpx.MockTransport(handler))
    with request_scope("task", 1, True, cancelled=check_lease):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(LeaseLost):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={
                        "model": "deepseek-flash",
                        "max_tokens": 100,
                    },
                )
    assert isinstance(transport.policy_failure, LeaseLost)
    assert ledger.usage("task")["uncertain_count"] == 1
