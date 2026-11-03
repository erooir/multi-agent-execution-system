import subprocess
import time

import pytest

from backend.app.casework import role_tools, team
from backend.app.platform import runtime, service
from backend.app.platform.db import LeaseLost
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_case_repair_runtime import case_request, clauses
from backend.tests.test_v2_platform import ADMIN, asset, project, run

db = fixtures.db


def install_clauses(db, matched=False):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    rows = clauses()
    if matched:
        rows[1]["clause_id"] = rows[0]["clause_id"]
    source = asset(db, p, rows, classification="local")
    return p, source


def inject_error(monkeypatch, stage, revision, error):
    real = role_tools.run_stage
    calls = []

    def flaky(
        execute,
        current_stage,
        profile,
        inputs,
        previous,
        read_bytes,
        feedback,
        current_revision,
        checkpoint=None,
        **kwargs,
    ):
        if current_stage == stage and current_revision == revision:
            calls.append(current_stage)
            raise error
        return real(
            execute,
            current_stage,
            profile,
            inputs,
            previous,
            read_bytes,
            feedback,
            current_revision,
            checkpoint,
            **kwargs,
        )

    monkeypatch.setattr(role_tools, "run_stage", flaky)
    return calls


@pytest.mark.parametrize(
    "failed_stage,reused_stages",
    [("analyze", ["prepare"]), ("review", ["prepare", "analyze"])],
)
async def test_case_transient_tool_error_recovers_same_operation_and_reuses_checkpoints(
    db, monkeypatch, failed_stage, reused_stages
):
    p, source = install_clauses(db, matched=True)
    inject_error(monkeypatch, failed_stage, 0, subprocess.TimeoutExpired("synthetic-tool", 1))
    result = await run(db, case_request(p, source, "T06"))
    assert result["status"] == "succeeded"
    assert result["revision"] == 1
    (entry,) = result["repair_history"]
    assert entry["entry_kind"] == "technical_recovery"
    assert entry["reason_code"] == "case_tool_timeout"
    assert entry["action"] is None
    assert entry["reused_stages"] == reused_stages
    assert entry["rerun_stages"] == [failed_stage] if failed_stage == "review" else ["analyze", "review"]
    assert entry["recovery_outcome"]["passed"]
    assert "outcome" not in entry
    assert result["result"]["usage"]["request_count"] == 0


async def test_business_repair_then_transient_error_preserves_business_history_and_outcome(db, monkeypatch):
    p, source = install_clauses(db)
    inject_error(monkeypatch, "analyze", 1, subprocess.TimeoutExpired("synthetic-tool", 1))
    result = await run(db, case_request(p, source, "T06"))
    assert result["status"] == "succeeded"
    assert result["revision"] == 2
    business, technical = result["repair_history"]
    assert business["action"] == "match_unique_clauses"
    assert business["outcome"]["passed"]
    assert technical["entry_kind"] == "technical_recovery"
    assert technical["base_business_revision"] == business["revision"] == 1
    assert technical["action"] == business["action"]
    assert technical["targets"] == business["targets"]
    assert technical["reused_nodes"] == business["reused_nodes"]
    assert technical["recovery_outcome"]["passed"]
    assert "outcome" not in technical


async def test_case_transient_errors_share_revision_limit(db, monkeypatch):
    p, source = install_clauses(db)
    real = role_tools.run_stage
    calls = []

    def always_timeout(
        execute, stage, profile, inputs, previous, read_bytes, feedback, revision, checkpoint=None, **kwargs
    ):
        if stage == "analyze":
            calls.append(revision)
            raise subprocess.TimeoutExpired("synthetic-tool", 1)
        return real(
            execute, stage, profile, inputs, previous, read_bytes, feedback, revision, checkpoint, **kwargs
        )

    monkeypatch.setattr(role_tools, "run_stage", always_timeout)
    request = case_request(p, source, "T06")
    request["max_revisions"] = 1
    result = await run(db, request)
    assert result["status"] == "failed"
    assert result["reason_code"] == "execution_failed"
    assert result["revision"] == 1
    assert calls == [0, 1]
    assert len(result["repair_history"]) == 1
    assert not db.list("artifacts", p["id"], task_id=result["id"])


@pytest.mark.parametrize("error", [runtime.TaskFailure("invalid_input", "synthetic"), ValueError("unknown")])
async def test_case_nonrecoverable_errors_never_retry(db, monkeypatch, error):
    p, source = install_clauses(db)
    calls = inject_error(monkeypatch, "prepare", 0, error)
    result = await run(db, case_request(p, source, "T06"))
    assert result["status"] == "failed"
    assert result["revision"] == 0
    assert calls == ["prepare"]
    assert not result.get("repair_history")
    assert not db.list("artifacts", p["id"], task_id=result["id"])


async def test_technical_recovery_plan_resumes_without_duplicate_history(db, monkeypatch):
    p, source = install_clauses(db, matched=True)
    task = service.create_task(case_request(p, source, "T06"), ADMIN, db)
    inject_error(monkeypatch, "analyze", 0, subprocess.TimeoutExpired("synthetic-tool", 1))
    original_graph = runtime._execute_graph

    def interrupt():
        row = db.get("tasks", task["id"])
        row["lease_until"] = time.time() - 1
        db.put("tasks", row)
        raise LeaseLost("Injected technical recovery interruption")

    with monkeypatch.context() as patch:

        async def interrupted_graph(execution, revision, **kwargs):
            if revision == 1:
                interrupt()
            await original_graph(execution, revision, **kwargs)

        patch.setattr(runtime, "_execute_graph", interrupted_graph)
        abandoned = await runtime.process_claim(db, db.claim("first"), "first")
    assert abandoned["revision"] == 1
    (plan,) = abandoned["repair_history"]
    assert plan["entry_kind"] == "technical_recovery"
    assert plan["reused_nodes"] == {"prepare": task["id"] + ":0:prepare"}
    recovered = db.claim("replacement")
    finished = await runtime.process_claim(db, recovered, "replacement")
    assert finished["status"] == "succeeded"
    assert finished["revision"] == 1
    assert finished["repair_history"] == [plan | {"recovery_outcome": {"passed": True}}]
    events = db.list("events", task_id=task["id"])
    assert sum(event["type"] == "task.recovery_planned" for event in events) == 1
