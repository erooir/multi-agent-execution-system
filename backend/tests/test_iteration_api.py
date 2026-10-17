"""New endpoint boundaries with synthetic records and no provider calls."""

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app import model_gateway as gateway_module
from backend.app.platform import api, service
from backend.app.platform.contracts import AgentTextResult, ToolCallRecord
from backend.app.platform.db import now
from backend.app.platform.quality import deterministic_quality
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = fixtures.db


def test_capability_route_contract_and_legacy_compatibility(db, monkeypatch):
    p = project(db)
    monkeypatch.setattr(api, "repo", db)
    monkeypatch.setattr(service, "repo", db)
    app = application()
    assert len([route for route in api.router.routes if route.path == "/api/v2/capabilities"]) == 1
    with TestClient(app) as client:
        assert isinstance(client.get("/api/v2/capabilities").json(), list)
        response = client.get("/api/v2/capabilities", params={"project_id": p["id"]})
        assert response.status_code == 200
        assert response.json()["tools"]
        schema = app.openapi()["paths"]["/api/v2/capabilities"]["get"]
        assert schema["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]
        app.dependency_overrides[auth.get_current_user] = lambda: {"id": "stranger", "role": "operator"}
        assert client.get("/api/v2/capabilities", params={"project_id": p["id"]}).status_code == 403


def test_preflight_no_side_effects_matching_and_idempotent_replay(db, monkeypatch):
    p = project(db)
    a = asset(db, p, [{"value": 2}], classification="public")
    wrong = service.save_agent(
        {
            "project_id": p["id"],
            "name": "Wrong local specialist",
            "purpose": "specialized",
            "capabilities": ["text_analysis"],
            "modalities": ["structured"],
            "tool_ids": ["text_analysis"],
            "model_policy": "local",
        },
        ADMIN,
        repository=db,
    )
    service.agent_action(wrong["id"], "publish", ADMIN, db)
    monkeypatch.setattr("backend.app.config.get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(
        gateway_module.model_gateway, "budget", lambda: {"remaining_cny": 300, "accounting_violation": False}
    )
    body = task_body(p, a, task_type="text_analysis", mode="cloud", max_cost_cny=1)
    check = service.create_task(body, ADMIN, db, preflight=True)
    assert check["ready"]
    assert not db.list("tasks")
    assert all(item["agent_id"] != wrong["id"] for item in check["selected_agents"])
    task = service.create_task(body, ADMIN, db)
    monkeypatch.setattr("backend.app.config.get_api_key", lambda: None)
    assert service.create_task(body, ADMIN, db)["id"] == task["id"]
    with pytest.raises(service.DomainError) as error:
        service.create_task({**body, "goal": "Changed"}, ADMIN, db)
    assert error.value.code == "idempotency_conflict"


def test_tool_detail_project_permission_and_withdrawal(db, monkeypatch):
    p = project(db)
    a = asset(db, p, [{"value": 2}], classification="public")
    task = service.create_task(task_body(p, a), ADMIN, db)
    call = ToolCallRecord(
        id="synthetic-tool-call",
        task_id=task["id"],
        kind="mcp",
        capability_id="fixture",
        version="2",
        status="completed",
        started_at=now(),
        arguments_checksum="fixture-hash",
        source_refs=[a["id"]],
        summary={"matched_rows": 1},
    ).model_dump()
    task.update(
        tool_calls=[call],
        tool_checkpoints={
            call["id"]: {"arguments": {"source_id": a["id"]}, "result": {"records": [{"value": 2}]}}
        },
    )
    db.put("tasks", task)
    db.event(task["id"], "tool.completed", call)
    monkeypatch.setattr(api, "repo", db)
    app = application()
    with TestClient(app) as client:
        path = f"/api/v2/tasks/{task['id']}/tool-calls/{call['id']}"
        assert client.get(path).json()["result"]["records"] == [{"value": 2}]
        assert "tool_checkpoints" not in client.get(f"/api/v2/tasks/{task['id']}").json()
        app.dependency_overrides[auth.get_current_user] = lambda: {
            "id": "other-project-user",
            "role": "operator",
        }
        assert client.get(path).status_code == 403
        app.dependency_overrides[auth.get_current_user] = lambda: ADMIN
        service.revoke_asset(a["id"], ADMIN, db)
        assert client.get(path).status_code == 403
        assert client.get(f"/api/v2/tasks/{task['id']}/tool-calls").json() == []
        events = client.get(f"/api/v2/tasks/{task['id']}/events").json()
        assert next(item for item in events if item["type"] == "tool.completed")["payload"]["summary"] == {}
        detail_events = client.get(f"/api/v2/tasks/{task['id']}").json()["events"]
        assert (
            next(item for item in detail_events if item["type"] == "tool.completed")["payload"]["summary"]
            == {}
        )


def test_output_contract_normalizes_extra_explanations_without_weakening_proof():
    original = {
        "type": "source",
        "source_id": "original",
        "source_version": "1",
        "start": 0,
        "end": 4,
        "quote": "fact",
    }
    normalized = AgentTextResult.model_validate({"text": "fact", "citations": [original]}).model_dump()
    assert normalized["citations"][0]["kind"] == "source_span"
    source_output = {"content": normalized, "source_refs": ["original"], "method": "tool_agent"}
    source_inputs = [{"id": "original", "source_version": "1", "content": "fact"}]
    assert deterministic_quality({"task_type": "text_analysis"}, source_output, source_inputs)["passed"]
    normalized["citations"][0]["quote"] = "fake"
    assert deterministic_quality({"task_type": "text_analysis"}, source_output, source_inputs)[
        "hard_failures"
    ]
    evidence = {
        "kind": "derived_result",
        "tool_call_id": "real-call",
        "source_id": "real-source",
        "result_checksum": "verified-hash",
    }
    parsed = AgentTextResult.model_validate(
        {"text": "合计5", "citations": [{**evidence, "explanation": "ignored"}]}
    ).model_dump()
    assert parsed["citations"] == [evidence]
    alias = {"type": evidence["kind"], **{key: value for key, value in evidence.items() if key != "kind"}}
    assert AgentTextResult.model_validate({"text": "合计5", "citations": [alias]}).model_dump()[
        "citations"
    ] == [evidence]
    output = {
        "content": parsed,
        "source_refs": ["real-source"],
        "method": "tool_agent",
        "derived_evidence": [evidence],
    }
    inputs = [{"id": "real-source", "content": [{"value": 2}, {"value": 3}], "source_version": "1"}]
    assert deterministic_quality({"task_type": "text_analysis"}, output, inputs)["passed"]
    output["content"]["citations"][0]["result_checksum"] = "forged"
    assert not deterministic_quality({"task_type": "text_analysis"}, output, inputs)["passed"]


def test_knowledge_only_scope_is_opt_in_project_private_and_version_frozen(db, monkeypatch):
    p = project(db)
    public = asset(db, p, "公开知识事实。", modality="text", media_type="text/plain", classification="public")
    asset(db, p, "本地私有内容。", modality="text", media_type="text/plain", classification="local")
    other = project(db)
    asset(db, other, "其他项目事实。", modality="text", media_type="text/plain", classification="public")
    monkeypatch.setattr("backend.app.config.get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(
        gateway_module.model_gateway, "budget", lambda: {"remaining_cny": 300, "accounting_violation": False}
    )
    body = task_body(
        p,
        public,
        input_refs=[],
        task_type="text_analysis",
        mode="cloud",
        max_cost_cny=1,
        use_project_knowledge=True,
    )
    task = service.create_task(body, ADMIN, db)
    assert [item["id"] for item in task["snapshot"]["inputs"]] == [public["id"]]
    asset(db, p, "之后新增的公开知识。", modality="text", media_type="text/plain", classification="public")
    assert len(db.get("tasks", task["id"])["snapshot"]["inputs"]) == 1


def test_legacy_idempotency_survives_added_default_contract_fields(db):
    p = project(db)
    a = asset(db, p)
    body = task_body(p, a)
    task = service.create_task(body, ADMIN, db)
    task["request"].pop("use_project_knowledge")
    task["request_hash"] = service.digest(task["request"])
    db.put("tasks", task)
    assert service.create_task(body, ADMIN, db)["id"] == task["id"]
