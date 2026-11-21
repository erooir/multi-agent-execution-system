import sqlite3

import pytest

from backend.app import model_gateway as module


def test_internal_meter_records_tokens_without_paid_schema(tmp_path):
    path = tmp_path / "usage.sqlite3"
    ledger = module.InternalUsageLedger(path)
    request = ledger.begin_internal("local-model", "test", "task", (100, 20, 32768))
    ledger.internal_settle(request, "local-model", {"prompt_tokens": 12, "completion_tokens": 3})
    result = ledger.usage("task")
    assert result["request_count"] == 1 and result["input_tokens"] == 12 and result["output_tokens"] == 3
    assert result["cost_cny"] == 0 and result["accounting_complete"]
    with sqlite3.connect(path) as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('charges','task_limits','counters')"
        ).fetchall()
    assert ledger.budget()["remaining_cny"] == 0
    assert not ledger.budget()["financial_ledger_available"]
    with pytest.raises(RuntimeError, match="internal_meter_only"):
        ledger.reserve(1, "must not pay")


def test_gateway_selects_internal_store_without_touching_history(tmp_path, monkeypatch):
    history = tmp_path / "missing-history.sqlite3"
    monkeypatch.setattr(module, "BUDGET_LEDGER_PATH", history)
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setenv("HANGZHI_UPSTREAM_MODEL_ID", "local-model")
    monkeypatch.setenv("HANGZHI_UPSTREAM_BILLING", "internal_zero")
    monkeypatch.setenv("HANGZHI_UPSTREAM_USAGE_PATH", str(tmp_path / "internal.sqlite3"))
    gateway = module.ModelGateway()
    assert isinstance(gateway.ledger, module.InternalUsageLedger)
    assert gateway.budget()["provider"] == "intranet"
    assert not history.exists()
    for name in ("MODEL_URL", "MODEL_ID", "BILLING", "API_KEY"):
        monkeypatch.delenv("HANGZHI_UPSTREAM_" + name, raising=False)
    with pytest.raises(RuntimeError, match="ledger_missing"):
        _ = module.ModelGateway().ledger
    assert not history.exists()
