"""No real providers or credentials. Exercise the shared admission boundaries."""

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.app import resource_limits as limits
from backend.app.model_gateway import BudgetLedger, MeteredTransport, ModelCallError
from backend.app.platform import resource_usage, service
from backend.tests import test_v2_platform as fixture
from backend.tests.test_scheduling_monitoring import queued
from backend.tests.test_v2_platform import ADMIN, OTHER, project

db = fixture.db


@pytest.mark.asyncio
async def test_actual_vs_estimated_and_unavailable_context(tmp_path, monkeypatch):
    ledger = BudgetLedger(tmp_path / "historical.sqlite3")
    historical = ledger.reserve(0.1, "old", "x")
    ledger.settle(historical, {"prompt_tokens": 11, "completion_tokens": 2})
    ledger.reserve(0.5, "other-project", "not-x")
    response = httpx.Response(
        200, json={"model": "deepseek-flash", "usage": {"prompt_tokens": 101, "completion_tokens": 28}}
    )
    transport = MeteredTransport(ledger, "review", "x", inner=httpx.MockTransport(lambda _: response))
    async with httpx.AsyncClient(transport=transport) as client:
        monkeypatch.setenv("HANGZHI_CONTEXT_TOKENS", "4096")
        payload = {"model": "deepseek-flash", "max_tokens": 22}
        with pytest.raises(ModelCallError, match="上下文窗口"):
            await client.post("https://api.deepseek.com/chat/completions", json=payload)
        assert ledger.usage("x")["request_count"] == 1
        monkeypatch.setenv("HANGZHI_CONTEXT_TOKENS", "16000")
        await client.post("https://api.deepseek.com/chat/completions", json={**payload, "max_tokens": 128})
    ledger.uncertain(ledger.reserve(0.25, "historical-uncertain", "x"))
    data = resource_usage.gateway_measurements(ledger.path, ["x"])
    assert data["requests"] == 3 and data["reported"] == 2 and data["unconfirmed"] == 1
    assert data["input_tokens"] == 112 and data["output_tokens"] == 30
    assert data["peak_context_tokens"] == 129
    assert data["peak_context_ratio"] == pytest.approx(129 / 16000)
    assert data["estimated_peak_upper"] > 4096 and data["estimated_samples"] == 1
    assert data["latency"]["samples"] == 1 and data["reserved_cny"] == 0.25
    assert ledger.budget()["request_count"] == 4


@pytest.mark.asyncio
async def test_request_slot_is_process_scoped_and_released_on_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("HANGZHI_MODEL_SLOTS", "1")
    monkeypatch.setenv("HANGZHI_MODEL_QUEUE_SECONDS", "1")
    ledger = BudgetLedger(tmp_path / "synthetic.sqlite3")
    program = """
import asyncio, pathlib, sys
from backend.app.resource_limits import model_slot
async def claim():
    async with model_slot(pathlib.Path(sys.argv[1]),lambda: None):
        print('locked', flush=True)
        await asyncio.sleep(20)
asyncio.run(claim())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", program, str(ledger.path), stdout=asyncio.subprocess.PIPE
    )
    try:
        assert (await asyncio.wait_for(child.stdout.readline(), timeout=10)).strip() == b"locked"
        async with httpx.AsyncClient(
            transport=MeteredTransport(
                ledger,
                "blocked",
                inner=httpx.MockTransport(lambda _: pytest.fail("dispatched despite occupied slot")),
            )
        ) as client:
            with pytest.raises(ModelCallError, match="并发已满"):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-flash", "max_tokens": 64},
                )
        assert ledger.budget()["request_count"] == 0
    finally:
        child.kill()
        await asyncio.wait_for(child.wait(), timeout=10)
    async with limits.model_slot(ledger.path, lambda: None):
        assert ledger.budget()["spent_cny"] == 0


def test_task_limit_orders_live_claims_and_recovers_expired(db, monkeypatch):
    monkeypatch.setenv("HANGZHI_TASK_SLOTS", "1")
    for i in range(3):
        queued(db, f"q{i}")
    with ThreadPoolExecutor(max_workers=3) as pool:
        claims = [c for c in pool.map(lambda n: db.claim(f"worker-{n}"), range(3)) if c]
    assert len(claims) == 1 and db.claim("full") is None
    first = claims[0]
    db.put("tasks", {**first, "lease_until": time.time() - 1})
    recovered = db.claim("recovery")
    assert recovered["id"] == first["id"] and recovered["lease_token"] > first["lease_token"]
    db.put("tasks", {**recovered, "status": "succeeded", "lease_owner": None})
    assert db.claim("next")["id"] != first["id"]


def test_project_resource_permissions_and_legacy_local_samples(db, monkeypatch, tmp_path):
    p = project(db)
    ledger = BudgetLedger(tmp_path / "synthetic.sqlite3")
    monkeypatch.setattr(resource_usage.model_gateway, "_ledger", ledger)
    ended = datetime.now(UTC)
    queued(
        db,
        "local",
        project_id=p["id"],
        status="succeeded",
        first_claimed_at=(ended - timedelta(seconds=3)).isoformat(),
        finished_at=ended.isoformat(),
    )
    with pytest.raises(service.DomainError):
        resource_usage.project_resource_usage(p["id"], OTHER, db)
    d = resource_usage.project_resource_usage(p["id"], ADMIN, db)
    assert d["local"]["elapsed"] == {"samples": 1, "p50_ms": 3000, "p95_ms": 3000}
    assert d["gateway"]["latency"]["p50_ms"] is None and d["gateway"]["requests"] == 0


def test_read_only_legacy_charges_are_visible_without_schema_mutation(tmp_path):
    import hashlib
    import sqlite3
    from contextlib import closing

    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE charges(id TEXT,run_id TEXT,state TEXT,input_tokens INTEGER,output_tokens INTEGER,cost REAL,reserved REAL)"
        )
        conn.execute("INSERT INTO charges VALUES('r','x','settled',30,10,.001,.1)")
        conn.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = resource_usage.gateway_measurements(path, ["x"])
    assert not data["resource_schema_available"] and data["input_tokens"] == 30 and data["reported"] == 1
    assert data["estimated_peak_upper"] is None and data["latency"]["samples"] == 0
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    missing = tmp_path / "not-created.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        resource_usage.gateway_measurements(missing, [])
    assert not missing.exists()
