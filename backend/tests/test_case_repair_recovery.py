"""Real Agno lease takeover at both boundaries of a targeted, local T06 repair."""

import time
from collections import Counter

import pytest

from backend.app.casework.team import install
from backend.app.platform import runtime, service
from backend.app.platform.db import LeaseLost
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = fixtures.db


@pytest.mark.parametrize(
    "boundary,revoke",
    [("planned", False), ("evaluated", False), ("audited", False), ("planned", True)],
)
async def test_resume_exact_repair_plan_and_revalidate_frozen_inputs(db, monkeypatch, boundary, revoke):
    p = project(db)
    install(p["id"], ADMIN, db)
    a = asset(
        db,
        p,
        [
            {"side": "before", "clause_id": "1", "topic": "storage", "clause_text": "Keep a copy."},
            {"side": "after", "clause_id": "3", "topic": "storage", "clause_text": "Keep two copies."},
        ],
    )
    task = service.create_task(
        task_body(p, a, task_type="case_analysis", case_id="T06", case_scope="sample", max_revisions=2),
        ADMIN,
        db,
    )

    def interrupt():
        row = db.get("tasks", task["id"])
        row["lease_until"] = time.time() - 1
        db.put("tasks", row)
        raise LeaseLost("Injected local worker interruption")

    with monkeypatch.context() as patch:
        if boundary == "planned":
            graph = runtime._execute_graph

            async def interrupted_graph(execution, revision):
                if revision == 1:
                    interrupt()
                await graph(execution, revision)

            patch.setattr(runtime, "_execute_graph", interrupted_graph)
        else:
            record = runtime._record_repair_outcome

            def interrupted_review(execution):
                if boundary == "evaluated" and execution.task.get("revision") == 1:
                    interrupt()
                record(execution)
                if boundary == "audited" and execution.task.get("revision") == 1:
                    interrupt()

            patch.setattr(runtime, "_record_repair_outcome", interrupted_review)
        abandoned = await runtime.process_claim(db, db.claim("first"), "first")

    assert abandoned["revision"] == 1, (abandoned["status"], abandoned.get("reason_code"))
    plan = abandoned["repair_history"][0]
    assert plan["reused_nodes"] == {"prepare": task["id"] + ":0:prepare"}
    assert plan["action"] == "match_unique_clauses"
    if revoke:
        service.revoke_asset(a["id"], ADMIN, db)
    recovered = db.claim("replacement")
    assert recovered["lease_token"] == abandoned["lease_token"] + 1
    finished = await runtime.process_claim(db, recovered, "replacement")
    events = db.list("events", task_id=task["id"])
    counts = Counter(row["type"] for row in events)
    assert counts["task.repair_planned"] == 1
    assert len(finished["repair_history"]) == 1
    if revoke:
        assert finished["status"] == "failed" and finished["reason_code"] == "input_revoked"
        assert not db.list("artifacts", task_id=task["id"])
        assert len(db.list("instances", task_id=task["id"])) == 3
    else:
        assert finished["status"] == "succeeded" and finished["revision"] == 1
        entry = finished["repair_history"][0]
        assert entry["outcome"]["passed"]
        assert (
            entry["before_counts"]["failed_checks"] > 0
            and entry["outcome"]["after_counts"]["failed_checks"] == 0
        )
        assert entry["outcome"]["after_counts"]["evidence"] > 0
        assert counts["task.repair_reviewed"] == 1
        assert sorted(e["payload"]["revision"] for e in events if e["type"] == "quality.checked") == [0, 1]
        assert len(db.list("artifacts", task_id=task["id"])) == 1
        assert len(db.list("instances", task_id=task["id"])) == 5
