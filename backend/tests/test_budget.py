import concurrent.futures

import httpx
import pytest

from backend.app.model_gateway import BudgetExceeded, BudgetLedger, MeteredTransport


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
    assert ledger.budget()["spent_cny"] == 0.012
    assert ledger.budget()["reserved_cny"] == 0
    with pytest.raises(BudgetExceeded):
        ledger.reserve(301, "test", limit=1000)


@pytest.mark.asyncio
async def test_network_failure_keeps_reserved_charge(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")

    def handler(request):
        raise httpx.ReadTimeout("unknown outcome")

    transport = MeteredTransport(ledger, "test", inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post(
                "https://api.deepseek.com/chat/completions",
                json={"model": "deepseek-flash", "max_tokens": 100},
            )
    assert ledger.budget()["uncertain_count"] == 1
    assert ledger.budget()["reserved_cny"] > 0


@pytest.mark.asyncio
async def test_budget_block_never_sends_request(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")
    ledger.reserve(300, "previous")
    transport = MeteredTransport(
        ledger,
        "test",
        inner=httpx.MockTransport(lambda _: pytest.fail("budgeted request must not reach network")),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BudgetExceeded):
            await client.post(
                "https://api.deepseek.com/chat/completions",
                json={"model": "deepseek-flash", "max_tokens": 100},
            )


@pytest.mark.asyncio
async def test_local_evidence_blocked_before_credentials_or_network(tmp_path):
    from backend.app.model_gateway import ModelGateway

    gateway = ModelGateway(BudgetLedger(tmp_path / "budget.db"))
    with pytest.raises(PermissionError):
        await gateway.complete("private", evidence=[{"visibility": "local"}])
    assert gateway.budget()["request_count"] == 0


@pytest.mark.asyncio
async def test_transport_reserves_before_network_and_settles(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.db")

    def handler(request):
        assert ledger.budget()["reserved_cny"] > 0
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 20}, "model": "deepseek-flash"}
        )

    transport = MeteredTransport(ledger, "test", inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(
            "https://api.deepseek.com/chat/completions",
            json={"model": "deepseek-flash", "max_tokens": 100, "messages": []},
        )
    assert ledger.budget()["reserved_cny"] == 0
    assert ledger.budget()["output_tokens"] == 20


@pytest.mark.asyncio
async def test_transport_forbids_redirect_target(tmp_path):
    transport = MeteredTransport(
        BudgetLedger(tmp_path / "budget.db"),
        "test",
        inner=httpx.MockTransport(lambda _: pytest.fail("network must not execute")),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError):
            await client.post("https://example.com/chat/completions", json={})


@pytest.mark.asyncio
async def test_compressed_response_decodes_once(tmp_path):
    import gzip
    import json

    ledger = BudgetLedger(tmp_path / "budget.db")
    data = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "choices": [{"message": {"content": "ok"}}],
    }
    compressed = gzip.compress(json.dumps(data).encode())
    transport = MeteredTransport(
        ledger,
        "test",
        inner=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=compressed,
                headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
            )
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await client.post(
            "https://api.deepseek.com/chat/completions", json={"model": "deepseek-flash", "max_tokens": 100}
        )
        assert result.json()["choices"][0]["message"]["content"] == "ok"
    assert ledger.budget()["uncertain_count"] == 0


def test_cancelled_run_cannot_be_resurrected_by_stale_save(tmp_path):
    from backend.app.storage import Store

    store = Store(tmp_path / "records.db")
    stale = store.save("runs", {"id": "run", "status": "running"})
    store.save("runs", {**stale, "status": "cancelled"})
    store.save("runs", stale)
    assert store.get("runs", "run")["status"] == "cancelled"
    store.save("runs", {**stale, "status": "queued"}, allow_cancelled_resume=True)
    assert store.get("runs", "run")["status"] == "queued"
