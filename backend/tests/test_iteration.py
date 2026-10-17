"""Synthetic fixtures only. Real MCP protocol; model behavior is an explicit test double."""

import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from backend.app.model_gateway import BudgetLedger, ModelCallError, _validated_run
from backend.app.platform import runtime
from backend.app.platform.db import Repository
from backend.app.platform.quality import deterministic_quality
from backend.app.platform.tool_registry import catalog, freeze_bindings, permissions
from backend.app.platform.tool_runtime import LocalMCP, ToolSession
from backend.tests.test_v2_runtime import execute, seed

pytest_plugins = ["backend.tests.test_v2_postgres"]


def test_sdk_logs_never_include_validation_inputs():
    from backend.app.model_gateway import SDKMetadataOnly

    record = logging.LogRecord("agno", logging.WARNING, "fixture", 1, "raw %s", ("synthetic-secret",), None)
    record.exc_text = "synthetic-private-source"
    SDKMetadataOnly().filter(record)
    rendered = logging.Formatter().format(record)
    assert "synthetic-secret" not in rendered
    assert "synthetic-private-source" not in rendered
    assert "agno_sdk_warning" in rendered


@pytest.mark.asyncio
async def test_composite_rejects_bad_internal_evidence_before_next_member(monkeypatch):
    execution = SimpleNamespace(
        request={"task_type": "text_analysis"},
        inputs=[{"id": "fixture-source", "content": "真实合成资料", "source_version": "1"}],
    )
    agent = {
        "structure": "composite",
        "members": [{"agent_id": "first", "version": 1}, {"agent_id": "second", "version": 1}],
        "model_policy": "cloud",
        "capabilities": ["text_analysis"],
    }
    called = []

    async def bad_output(*args):
        called.append(args[2]["id"])
        return {
            "method": "tool_agent",
            "source_refs": ["fixture-source"],
            "content": {
                "text": "错误结果",
                "citations": [{"source_id": "fixture-source", "quote": "不存在的引用"}],
            },
        }

    monkeypatch.setattr(runtime, "_agent", lambda execution, agent_id, version: {"id": agent_id})
    monkeypatch.setattr(runtime, "_run_atomic", bad_output)
    with pytest.raises(runtime.TaskFailure) as error:
        await runtime._run_node(execution, {"id": "root", "label": "fixture"}, agent, [], 0)
    assert error.value.code == "internal_output_invalid"
    assert called == ["first"]


@pytest.fixture
def tool_repository(tmp_path, monkeypatch):
    repository = Repository(f"sqlite:///{(tmp_path / 'iteration_test.sqlite3').as_posix()}")
    monkeypatch.setattr(
        runtime, "model_gateway", SimpleNamespace(ledger=BudgetLedger(tmp_path / "budget_test.sqlite3"))
    )
    return repository


def tool_agent(agent):
    available = catalog()
    agent = {
        **agent,
        "execution_strategy": "tool_agent",
        "tool_refs": [{"tool_id": item["id"], "version": item["version"]} for item in available["tools"]],
        "skill_refs": [{"skill_id": item["id"], "version": item["version"]} for item in available["skills"]],
    }
    agent["runtime_bindings"] = freeze_bindings(agent)
    return agent


@pytest.mark.asyncio
async def test_real_mcp_handshake_aggregate_transform_and_cleanup():
    client = LocalMCP()
    try:
        records = [{"team": "A", "amount": 2}, {"team": "B", "amount": 3}, {"team": "A", "amount": 2}]
        value = await client.call(
            "table_aggregate",
            {"records": records, "metric": "sum", "value_field": "amount", "group_by": "team"},
        )
        assert value["matched_rows"] == 3
        assert value["groups"][0]["value"] == 4
        assert value["groups"][0]["row_numbers"] == [1, 3]
        value = await client.call("table_transform", {"records": records, "operation": "deduplicate"})
        assert value["total_rows"] == 2
        value = await client.call(
            "table_transform",
            {
                "records": [
                    {"team": "A", "amount": 2},
                    {"team": "B", "amount": 4},
                    {"team": "A", "amount": 3},
                ],
                "operation": "filter",
                "where": {"team": "A"},
                "sort_by": "amount",
                "descending": True,
            },
        )
        assert value["records"] == [{"team": "A", "amount": 3}, {"team": "A", "amount": 2}]
        assert value["row_numbers"] == [3, 1]
        value = await client.call(
            "table_transform",
            {
                "records": [{"amount": "2"}, {"amount": "10"}, {"amount": None}],
                "operation": "sort",
                "sort_by": "amount",
                "descending": True,
            },
        )
        assert value["records"] == [{"amount": "10"}, {"amount": "2"}, {"amount": None}]
    finally:
        await client.close()
    assert client.task.done()


def test_sdk_error_string_never_becomes_successful_model_output():
    transport = SimpleNamespace(
        policy_failure=ModelCallError("model_connection_failed", "无法连接"), result={}
    )
    with pytest.raises(ModelCallError) as error:
        _validated_run(SimpleNamespace(status="ERROR", content="Connection error."), transport)
    assert error.value.code == "model_connection_failed"


@pytest.mark.asyncio
async def test_tool_agent_real_skill_and_mcp_are_used_in_formal_runtime(tool_repository, monkeypatch):
    task = seed(
        tool_repository, task_type="text_analysis", mode="cloud", content=[{"amount": 2}, {"amount": 3}]
    )
    task["snapshot"]["inputs"][0]["modality"] = "structured"
    # Persist the same frozen metadata, as intake does for a structured source.
    tool_repository.put("data_assets", task["snapshot"]["inputs"][0])
    agent = tool_agent(task["snapshot"]["agents"][0])
    task["snapshot"]["agents"] = [agent]
    tool_repository.put("tasks", task)

    async def agent_double(spec, messages, bindings, **kwargs):
        assert "get_skill_script" not in bindings
        skill = await bindings["get_skill_instructions"]("table-analysis")
        assert "table_aggregate" in skill["data"]["instructions"]
        result = await bindings["table_aggregate"]("input-1", "sum", "amount")
        assert result["data"]["groups"][0]["value"] == 5
        return {
            "text": json.dumps({"text": "金额合计5。", "citations": [result["data"]["derived_evidence"]]}),
            "request_id": "fixture-agent",
        }

    async def review_double(*args, **kwargs):
        assert "verified_calculations" in args[0]
        assert json.loads(args[0])["allowed_evidence_refs"] == ["input-1"]
        assert kwargs["output_schema"].__name__ == "ModelQualityReview"
        return {
            "text": json.dumps(
                {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
            ),
            "request_id": "fixture-review",
        }

    monkeypatch.setattr(runtime.model_gateway, "run_agent", agent_double, raising=False)
    monkeypatch.setattr(runtime.model_gateway, "complete", review_double, raising=False)
    result = await execute(tool_repository)
    assert result["status"] == "succeeded", result.get("result")
    calls = result["tool_calls"]
    assert [call["kind"] for call in calls] == ["skill", "mcp"]
    assert all(call["status"] == "completed" for call in calls)
    assert result["result"]["artifacts"][0]["quality_status"] == "passed"


@pytest.mark.asyncio
async def test_tool_boundaries_changed_versions_and_call_limit(tool_repository):
    seed(tool_repository, task_type="text_analysis", mode="cloud", content="授权事实。")
    claimed = tool_repository.claim("fixture-worker")
    execution = runtime.Execution(tool_repository, claimed, "fixture-worker", claimed["lease_token"])
    execution.active_node_id = "n1"
    agent = tool_agent(claimed["snapshot"]["agents"][0])
    session = ToolSession(execution, agent, 0, permissions(agent))
    bindings = session.bindings()
    try:
        denied = await bindings["read_source"]("another-project")
        assert denied["data"]["error_code"] == "tool_source_denied"
        assert not session.sources_read
        for _ in range(11):
            await bindings["list_sources"]()
        with pytest.raises(runtime.TaskFailure, match="12"):
            await bindings["list_sources"]()
        assert len(execution.current()["tool_calls"]) == 12
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_all_deliverables_checked_and_review_suggestions_reach_revision(tool_repository, monkeypatch):
    seed(
        tool_repository,
        content=[{"name": "A"}],
        quality={"min_records": 2},
        max_revisions=0,
        allow_partial=False,
    )
    claimed = tool_repository.claim("fixture-worker")
    execution = runtime.Execution(tool_repository, claimed, "fixture-worker", claimed["lease_token"])
    first = runtime.structured_transform(execution.inputs)
    second = {**first, "content": {"records": [{"name": "A"}, {"name": "B"}]}}
    execution.outputs = [first, second]
    verdict = await runtime._review(execution)
    assert not verdict["passed"]
    assert verdict["outputs"][1]["passed"]
    assert any(issue["output_index"] == 0 and issue["code"] == "min_records" for issue in verdict["issues"])


def test_forged_derived_evidence_cannot_pass():
    asset = {"id": "a", "content": "真实事实", "source_version": "1"}
    output = {
        "method": "tool_agent",
        "source_refs": ["a"],
        "content": {
            "text": "结果",
            "citations": [
                {
                    "kind": "derived_result",
                    "source_id": "a",
                    "tool_call_id": "invented",
                    "result_checksum": "fake",
                }
            ],
        },
    }
    assert deterministic_quality({"task_type": "text_analysis"}, output, [asset])["hard_failures"]


@pytest.mark.asyncio
async def test_real_agno_tool_loop_meters_every_provider_round(tmp_path, monkeypatch):
    from backend.app import model_gateway as gateway_module
    from backend.app.config import MODEL_ID
    from backend.app.platform.contracts import AgentTextResult

    ledger = BudgetLedger(tmp_path / "sdk_loop_test.sqlite3")
    original = gateway_module.MeteredTransport
    rounds, calls = [], []

    async def provider_fixture(request):
        payload = json.loads(request.content)
        rounds.append(payload)
        if len(rounds) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "fixture-call",
                        "type": "function",
                        "function": {"name": "read_fixture", "arguments": "{}"},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "text": "fixture result",
                        "citations": [
                            {
                                "kind": "source_span",
                                "source_id": "fixture",
                                "source_version": "1",
                                "start": 0,
                                "end": 7,
                                "quote": "fixture",
                            }
                        ],
                    }
                ),
            }
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": f"fixture-{len(rounds)}",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(
        gateway_module,
        "MeteredTransport",
        lambda *a, **kw: original(*a, **kw, inner=httpx.MockTransport(provider_fixture)),
    )

    async def read_fixture() -> dict:
        """Read an explicitly synthetic public fixture."""
        calls.append(True)
        return {"data": {"text": "fixture"}, "evidence": [{"classification": "public"}]}

    gateway = gateway_module.ModelGateway(ledger)
    with gateway_module.request_scope("fixture-sdk", 1, True):
        result = await gateway.run_agent(
            {"instructions": "Use the authorized fixture tool and return JSON."},
            "Read fixture",
            {"read_fixture": read_fixture},
            output_schema=AgentTextResult,
        )
    assert calls == [True]
    assert len(rounds) == 2
    assert ledger.usage("fixture-sdk")["request_count"] == 2
    assert result["request_count"] == 2
    assert result["accounting_complete"]
    assert rounds[0]["response_format"]["type"] == "json_object"
    assert json.loads(result["text"])["citations"][0]["quote"] == "fixture"
    with gateway_module.request_scope("fixture-review-sdk", 1, True):
        parsed = await gateway.complete(
            "Return the synthetic fixture in JSON.", json_mode=True, output_schema=AgentTextResult
        )
    assert json.loads(parsed["text"])["text"] == "fixture result"
    assert ledger.usage("fixture-review-sdk")["request_count"] == 1


@pytest.mark.asyncio
async def test_tool_agent_overall_timeout_is_coded_and_reservation_retained(tmp_path, monkeypatch):
    import asyncio

    from backend.app import model_gateway as gateway_module

    ledger = BudgetLedger(tmp_path / "sdk_timeout_test.sqlite3")
    original = gateway_module.MeteredTransport

    async def slow_provider_fixture(request):
        await asyncio.sleep(5)
        raise AssertionError("Cancelled fixture must not complete")

    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(
        gateway_module,
        "MeteredTransport",
        lambda *args, **kwargs: original(*args, **kwargs, inner=httpx.MockTransport(slow_provider_fixture)),
    )
    with (
        gateway_module.request_scope("fixture-timeout", 1, True),
        pytest.raises(gateway_module.ModelCallError) as error,
    ):
        await gateway_module.ModelGateway(ledger).run_agent({}, "fixture", {}, timeout_seconds=1)
    assert error.value.code == "model_timeout"
    assert ledger.usage("fixture-timeout")["reserved_cny"] > 0


@pytest.mark.parametrize(
    "fault,code",
    [
        (401, "model_auth_failed"),
        (403, "model_auth_failed"),
        (402, "model_balance_insufficient"),
        (429, "model_rate_limited"),
        (500, "model_service_failed"),
        ("connect", "model_connection_failed"),
        ("timeout", "model_timeout"),
    ],
)
@pytest.mark.asyncio
async def test_sdk_provider_failures_have_safe_codes_without_replay(tmp_path, monkeypatch, fault, code):
    from backend.app import model_gateway as gateway_module

    ledger = BudgetLedger(tmp_path / "provider_fault_test.sqlite3")
    original = gateway_module.MeteredTransport
    sent = []

    async def provider_fixture(request):
        sent.append(True)
        if fault == "connect":
            raise httpx.ConnectError("synthetic-private-provider-detail")
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic-private-provider-detail")
        return httpx.Response(fault, json={"error": {"message": "synthetic-private-provider-detail"}})

    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(
        gateway_module,
        "MeteredTransport",
        lambda *args, **kwargs: original(*args, **kwargs, inner=httpx.MockTransport(provider_fixture)),
    )
    with (
        gateway_module.request_scope("fixture-provider-fault", 1, True),
        pytest.raises(gateway_module.ModelCallError) as error,
    ):
        await gateway_module.ModelGateway(ledger).complete("fixture", json_mode=True)
    assert error.value.code == code
    assert "synthetic-private-provider-detail" not in str(error.value)
    assert len(sent) == 1
    assert ledger.usage("fixture-provider-fault")["reserved_cny"] > 0


@pytest.mark.asyncio
async def test_closed_real_mcp_session_is_reported_as_failure(tool_repository):
    seed(tool_repository, task_type="text_analysis", mode="cloud", content=[{"amount": 2}])
    claimed = tool_repository.claim("fixture-worker")
    execution = runtime.Execution(tool_repository, claimed, "fixture-worker", claimed["lease_token"])
    execution.active_node_id = "n1"
    agent = tool_agent(claimed["snapshot"]["agents"][0])
    session = ToolSession(execution, agent, 0, permissions(agent))
    assert (await session.bindings()["table_aggregate"]("input-1", "sum", "amount"))["data"]["groups"][0][
        "value"
    ] == 2
    await session.close()
    result = await session.bindings()["table_aggregate"]("input-1", "sum", "amount")
    assert result["data"]["error_code"] == "mcp_unavailable"
    assert execution.current()["tool_calls"][-1]["status"] == "failed"


def test_model_request_limit_survives_execution_context_recreation(tool_repository):
    seed(tool_repository, task_type="text_analysis", mode="cloud", content="公开合成资料")
    claimed = tool_repository.claim("fixture-worker")
    execution = runtime.Execution(tool_repository, claimed, "fixture-worker", claimed["lease_token"])
    for _ in range(20):
        execution.before_model_request()
    resumed = runtime.Execution(
        tool_repository, execution.current(), "fixture-worker", claimed["lease_token"]
    )
    with pytest.raises(ModelCallError) as error:
        resumed.before_model_request()
    assert error.value.code == "model_call_limit"
    assert execution.current()["model_request_count"] == 20


@pytest.mark.asyncio
async def test_tool_agent_postgres_runtime(postgres, tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime, "model_gateway", SimpleNamespace(ledger=BudgetLedger(tmp_path / "pg_budget_test.sqlite3"))
    )
    await test_tool_agent_real_skill_and_mcp_are_used_in_formal_runtime(postgres, monkeypatch)


@pytest.mark.asyncio
async def test_exact_review_feedback_drives_bounded_revision(tool_repository, monkeypatch):
    seed(tool_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=1)
    execution_rounds, reviews = [], []

    async def provider_fixture(prompt, **kwargs):
        if kwargs["purpose"] == "v2_execution":
            execution_rounds.append(prompt)
            if len(execution_rounds) == 2:
                assert "补充负责人" in prompt
                assert "缺负责人" in prompt
            return {
                "text": json.dumps(
                    {"text": "负责人张明。", "citations": [{"source_id": "input-1", "quote": "负责人张明。"}]}
                ),
                "request_id": "fixture",
            }
        reviews.append(True)
        return {
            "text": json.dumps(
                {
                    "verdict": "failed" if len(reviews) == 1 else "passed",
                    "issues": ["缺负责人"] if len(reviews) == 1 else [],
                    "evidence_refs": ["input-1"],
                    "suggested_changes": ["补充负责人"] if len(reviews) == 1 else [],
                }
            ),
            "request_id": "fixture",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", provider_fixture, raising=False)
    result = await execute(tool_repository)
    assert result["status"] == "succeeded"
    assert result["revision"] == 1


@pytest.mark.asyncio
async def test_review_uses_actual_read_excerpts_and_reports_incomplete_coverage(tool_repository, monkeypatch):
    seed(tool_repository, task_type="text_analysis", mode="cloud", content="A" * 300_000)
    task = tool_repository.get("tasks", "task-1")
    task["snapshot"]["agents"] = [tool_agent(task["snapshot"]["agents"][0])]
    tool_repository.put("tasks", task)

    async def agent_fixture(spec, messages, bindings, **kwargs):
        await bindings["read_source"]("input-1", 0, 2000)
        return {
            "text": json.dumps(
                {
                    "text": "局部摘要",
                    "citations": [
                        {"source_id": "input-1", "source_version": "1", "start": 0, "end": 1, "quote": "A"}
                    ],
                }
            ),
            "request_id": "fixture",
        }

    async def review_fixture(prompt, **kwargs):
        assert len(prompt.encode()) < 200_000
        source = json.loads(prompt)["sources"][0]
        assert source["available_chars"] == 300_000
        assert source["read_chars"] == 2000
        assert not source["all_text_read"]
        return {
            "text": json.dumps(
                {
                    "verdict": "failed",
                    "issues": ["全文覆盖不足"],
                    "suggested_changes": ["继续阅读后总结"],
                    "evidence_refs": ["input-1"],
                }
            ),
            "request_id": "fixture",
        }

    monkeypatch.setattr(runtime.model_gateway, "run_agent", agent_fixture, raising=False)
    monkeypatch.setattr(runtime.model_gateway, "complete", review_fixture, raising=False)
    result = await execute(tool_repository)
    assert result["status"] == "failed"
    assert result["reason_code"] == "quality_not_met"


@pytest.mark.asyncio
async def test_invalid_tool_output_is_revised_with_field_feedback_and_kept_private(
    tool_repository, monkeypatch
):
    task = seed(
        tool_repository, task_type="text_analysis", mode="cloud", content="负责人张明。", max_revisions=1
    )
    task["snapshot"]["agents"] = [tool_agent(task["snapshot"]["agents"][0])]
    tool_repository.put("tasks", task)
    rounds = []

    async def agent_fixture(spec, messages, bindings, **kwargs):
        await bindings["read_source"]("input-1", 0, 100)
        rounds.append(messages)
        if len(rounds) == 1:
            return {"text": '{"text":"synthetic-invalid-private","citations":[{}]}', "request_id": "fixture"}
        assert "source_id" in messages and "model_output_invalid" in messages
        return {
            "text": json.dumps(
                {
                    "text": "负责人张明。",
                    "citations": [
                        {
                            "source_id": "input-1",
                            "source_version": "1",
                            "start": 0,
                            "end": 6,
                            "quote": "负责人张明。",
                        }
                    ],
                }
            ),
            "request_id": "fixture",
        }

    async def review_fixture(*args, **kwargs):
        return {
            "text": json.dumps(
                {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
            ),
            "request_id": "fixture-review",
        }

    monkeypatch.setattr(runtime.model_gateway, "run_agent", agent_fixture, raising=False)
    monkeypatch.setattr(runtime.model_gateway, "complete", review_fixture, raising=False)
    result = await execute(tool_repository)
    assert result["status"] == "succeeded", result.get("result")
    assert len(rounds) == 2
    assert "synthetic-invalid-private" in json.dumps(result["tool_checkpoints"])
    from backend.app.platform.service import safe_task

    assert "synthetic-invalid-private" not in json.dumps(safe_task(result))
    events = tool_repository.list("events", task_id=result["id"])
    assert any(item["type"] == "model.output_invalid" for item in events)
    assert "synthetic-invalid-private" not in json.dumps(events)
