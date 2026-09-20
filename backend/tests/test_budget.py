import asyncio
import concurrent.futures
import httpx
import pytest
from backend.app.model_gateway import BudgetLedger, BudgetExceeded, MeteredTransport


def test_atomic_reservations_cannot_overspend(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")
    def reserve():
        try:
            return ledger.reserve(1, "test", limit=5)
        except BudgetExceeded:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: reserve(), range(30)))
    assert len([x for x in results if x]) == 5
    assert ledger.budget()["reserved_cny"] == 5


def test_unknown_charge_survives_restart(tmp_path):
    path = tmp_path / "budget.db"
    ledger = BudgetLedger(path)
    request_id = ledger.reserve(12, "test")
    ledger.uncertain(request_id)
    budget = BudgetLedger(path).budget()
    assert budget["reserved_cny"] == 12
    assert budget["uncertain_count"] == 1


def test_settlement_idempotent_and_cap(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")
    rid = ledger.reserve(2, "test")
    ledger.settle(rid, {"prompt_tokens": 1000, "completion_tokens": 500})
    ledger.settle(rid, {"prompt_tokens": 1000, "completion_tokens": 500})
    assert ledger.budget()["spent_cny"] == .012
    assert ledger.budget()["reserved_cny"] == 0
    with pytest.raises(BudgetExceeded):
        ledger.reserve(301, "test", limit=1000)


@pytest.mark.asyncio
async def test_transport_reserves_before_network_and_settles(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")
    def handler(request):
        assert ledger.budget()["reserved_cny"] > 0
        return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 20}, "model": "deepseek-flash"})
    transport = MeteredTransport(ledger, "test", inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://api.deepseek.com/chat/completions", json={"model": "deepseek-flash", "max_tokens": 100, "messages": []})
    assert ledger.budget()["reserved_cny"] == 0
    assert ledger.budget()["output_tokens"] == 20


@pytest.mark.asyncio
async def test_transport_forbids_redirect_target(tmp_path):
    transport = MeteredTransport(BudgetLedger(tmp_path / "budget.db"), "test", inner=httpx.MockTransport(lambda _: pytest.fail("network must not execute")))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError):
            await client.post("https://example.com/chat/completions", json={})
