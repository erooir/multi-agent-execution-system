import json
from types import SimpleNamespace

import pytest

from backend.app.model_gateway import BudgetLedger, ModelCallError, ModelOutputTruncated
from backend.app.platform import runtime
from backend.app.platform.db import Repository
from backend.tests.test_v2_runtime import execute, seed


@pytest.fixture
def recovery_repository(tmp_path, monkeypatch):
    repository = Repository(f"sqlite:///{(tmp_path / 'recovery_test.sqlite3').as_posix()}")
    monkeypatch.setattr(
        runtime, "model_gateway", SimpleNamespace(ledger=BudgetLedger(tmp_path / "budget_test.sqlite3"))
    )
    return repository


def valid_execution():
    return {
        "text": json.dumps(
            {"text": "负责人张明。", "citations": [{"source_id": "input-1", "quote": "负责人张明。"}]}
        ),
        "request_id": "fixture-execution",
    }


def passed_review():
    return {
        "text": json.dumps(
            {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
        ),
        "request_id": "fixture-review",
    }


@pytest.mark.asyncio
async def test_model_output_error_recovers_with_bounded_feedback(recovery_repository, monkeypatch):
    seed(
        recovery_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=1
    )
    executions = []

    async def provider_fixture(prompt, **kwargs):
        if kwargs["purpose"] == "v2_execution":
            executions.append(prompt)
            if len(executions) == 1:
                return {"text": "not-json", "request_id": "fixture-invalid"}
            assert "model_output_invalid" in prompt
            assert "仅修正JSON结构" in prompt
            return valid_execution()
        return passed_review()

    monkeypatch.setattr(runtime.model_gateway, "complete", provider_fixture, raising=False)
    result = await execute(recovery_repository)
    assert result["status"] == "succeeded"
    assert result["revision"] == 1
    assert len(executions) == 2
    nodes = recovery_repository.list("nodes", task_id=result["id"])
    assert [node["status"] for node in nodes] == ["failed", "succeeded"]
    events = recovery_repository.list("events", task_id=result["id"])
    recovery = next(item for item in events if item["payload"].get("strategy") == "bounded_error_recovery")
    assert recovery["payload"]["reason_code"] == "model_output_invalid"
    assert "not-json" not in json.dumps(events)


@pytest.mark.asyncio
async def test_recoverable_node_errors_stop_at_existing_revision_limit(recovery_repository, monkeypatch):
    seed(
        recovery_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=1
    )
    calls = []

    async def invalid_fixture(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        return {"text": "not-json", "request_id": "fixture-invalid"}

    monkeypatch.setattr(runtime.model_gateway, "complete", invalid_fixture, raising=False)
    result = await execute(recovery_repository)
    assert result["status"] == "failed"
    assert result["reason_code"] == "model_output_invalid"
    assert result["revision"] == 1
    assert calls == ["v2_execution", "v2_execution"]
    assert not recovery_repository.list("artifacts")


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["truncated", "timeout", "connection"])
async def test_uncertain_paid_error_recovers_without_erasing_reservation(
    recovery_repository, monkeypatch, error_kind
):
    seed(
        recovery_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=2
    )
    calls = []

    async def uncertain_fixture(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        if kwargs["purpose"] == "v2_execution" and calls.count("v2_execution") == 1:
            request_id = runtime.model_gateway.ledger.reserve(0.01, "fixture", "task-1", 300, task_limit=1)
            runtime.model_gateway.ledger.uncertain(request_id)
            if error_kind == "truncated":
                raise ModelOutputTruncated({"request_id": request_id})
            code = "model_timeout" if error_kind == "timeout" else "model_connection_failed"
            raise ModelCallError(code, "synthetic recoverable transport error")
        if kwargs["purpose"] == "v2_execution":
            return valid_execution()
        return passed_review()

    monkeypatch.setattr(runtime.model_gateway, "complete", uncertain_fixture, raising=False)
    result = await execute(recovery_repository)
    assert result["status"] == "succeeded"
    assert result["revision"] == 1
    assert calls == ["v2_execution", "v2_execution", "v2_independent_review"]
    assert result["result"]["usage"]["uncertain_count"] == 1
    assert result["result"]["usage"]["reserved_cny"] > 0
    event = next(
        item
        for item in recovery_repository.list("events", task_id=result["id"])
        if item["type"] == "model.recoverable"
    )
    assert event["payload"]["accounting_complete"] is False


@pytest.mark.asyncio
async def test_error_recovery_reuses_successful_upstream_node(recovery_repository, monkeypatch):
    task = seed(recovery_repository, max_revisions=1)
    task["snapshot"]["workflow"]["nodes"] = [
        {**task["snapshot"]["workflow"]["nodes"][0], "id": "n1"},
        {**task["snapshot"]["workflow"]["nodes"][0], "id": "n2", "depends_on": ["n1"]},
    ]
    recovery_repository.put("tasks", task)
    claimed = recovery_repository.claim("fixture-worker")
    execution = runtime.Execution(recovery_repository, claimed, "fixture-worker", claimed["lease_token"])
    real_atomic = runtime._run_atomic
    calls = []

    async def flaky_atomic(execution, plan, agent, previous, revision, *args, **kwargs):
        calls.append((plan["id"], revision))
        if plan["id"] == "n2" and revision == 0:
            raise ModelCallError("tool_call_limit", "synthetic")
        return await real_atomic(execution, plan, agent, previous, revision, *args, **kwargs)

    monkeypatch.setattr(runtime, "_run_atomic", flaky_atomic)
    await runtime._execute_with_recovery(execution)
    assert calls == [("n1", 0), ("n2", 0), ("n1", 1), ("n2", 1)]
    nodes = recovery_repository.list("nodes", task_id=task["id"])
    reused = next(node for node in nodes if node["id"].endswith(":1:n1"))
    assert reused["status"] == "succeeded"
    assert reused["reused_from"].endswith(":0:n1")


@pytest.mark.asyncio
@pytest.mark.parametrize("review_recovers,expected_status", [(True, "succeeded"), (False, "failed")])
async def test_review_protocol_retry_never_reexecutes_artifact(
    recovery_repository, monkeypatch, review_recovers, expected_status
):
    seed(
        recovery_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=2
    )
    purposes = []

    async def provider_fixture(prompt, **kwargs):
        purposes.append(kwargs["purpose"])
        if kwargs["purpose"] == "v2_execution":
            return valid_execution()
        if review_recovers and purposes.count("v2_independent_review") == 2:
            return passed_review()
        return {"text": "invalid-review", "request_id": "fixture-review"}

    monkeypatch.setattr(runtime.model_gateway, "complete", provider_fixture, raising=False)
    result = await execute(recovery_repository)
    assert result["status"] == expected_status
    assert result["revision"] == 0
    assert purposes == ["v2_execution", "v2_independent_review", "v2_independent_review"]
    events = recovery_repository.list("events", task_id=result["id"])
    assert sum(event["type"] == "review.retrying" for event in events) == 1
    assert not any(event["type"] == "task.revising" for event in events)
    if not review_recovers:
        issues = result["result"]["quality_verdict"]["issues"]
        assert [issue["code"] for issue in issues] == ["review_protocol_failed"]
        assert issues[0]["category"] == "review_protocol"
