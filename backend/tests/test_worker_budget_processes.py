"""Separate worker processes must consume the same historical budget ledger."""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from backend.app.model_gateway import BudgetExceeded, BudgetLedger


def _reserve_from_worker(path):
    ledger = BudgetLedger(path, require_existing=True)
    try:
        return ledger.reserve(1, "synthetic-process-concurrency")
    except BudgetExceeded:
        return None


def test_processes_preserve_uncertain_history_and_enforce_shared_cap(tmp_path):
    path = tmp_path / "isolated-original-ledger.sqlite3"
    ledger = BudgetLedger(path)
    old_request = ledger.reserve(295, "synthetic-historical-uncertain")
    ledger.uncertain(old_request)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_reserve_from_worker, [path] * 8))
    assert len([request_id for request_id in results if request_id]) == 5
    budget = BudgetLedger(path, require_existing=True).budget()
    assert budget["project_cap_cny"] == 300
    assert budget["reserved_cny"] == 300
    assert budget["remaining_cny"] == 0
    assert budget["uncertain_count"] == 1
    assert budget["request_count"] == 6
    assert ledger.usage(request_ids=[old_request])["reserved_cny"] == 295
