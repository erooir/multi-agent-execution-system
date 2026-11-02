"""Authorization, frozen versions and real tool calls over small synthetic sources."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.model_gateway import ModelGateway
from backend.app.platform import api, runtime, service
from backend.app.platform.db import LeaseLost, Repository
from backend.app.platform.tool_registry import BASE_TOOL_IDS, catalog, freeze_bindings, validate_frozen
from backend.app.platform.tool_trace import TaskToolTrace
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_runtime import execute, seed

db = fixtures.db


def install(db, project, ids, *, cloud=False):
    body = {
        "project_id": project["id"],
        "name": "合成证据操作",
        "capabilities": ["text_analysis"],
        "tool_ids": ["text_analysis"],
        "tool_refs": [{"tool_id": tool_id, "version": 1} for tool_id in ids],
        "model_policy": "cloud" if cloud else "local",
        "execution_strategy": "tool_agent" if cloud else "fixed",
    }
    a = service.save_agent(body, fixtures.ADMIN, repository=db)
    return service.agent_action(a["id"], "publish", fixtures.ADMIN, db)


def test_private_preview_checks_source_scope_agent_version_and_grant(db):
    project = fixtures.project(db)
    source = fixtures.asset(
        db,
        project,
        "Aerosol optical depth (AOD) changed.",
        modality="text",
        media_type="text/plain",
        classification="local",
    )
    other = fixtures.asset(
        db, fixtures.project(db), "Different source", modality="text", media_type="text/plain"
    )
    tool_id = "evidence.list_definitions"
    agent = install(db, project, [tool_id])
    app = application()
    app.dependency_overrides[api.edit.dependency] = lambda: fixtures.ADMIN
    path = f"/api/v2/agents/{agent['id']}/tools/{tool_id}/preview"
    args = {"source_id": source["id"], "acronym": "AOD"}
    body = {"version": agent["current_version"], "input_refs": [source["id"]], "arguments": args}
    with TestClient(app) as client:
        ok = client.post(path, json=body)
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["result"]["items"][0]["expansion"].lower() == "aerosol optical depth"
        assert data["source_refs"] == [source["id"]] and data["checksum"] == service.digest(data["result"])
        assert client.post(path, json={**body, "version": 99}).status_code == 409
        assert client.post(path, json={**body, "input_refs": [other["id"]]}).status_code == 403
        structured = fixtures.asset(db, project, [{"example": 1}])
        assert client.post(path, json={**body, "input_refs": [structured["id"]]}).status_code == 422
        assert client.post(path, json={**body, "arguments": {"source_id": other["id"]}}).status_code == 403
        assert client.post(path.replace(tool_id, "evidence.timeline_dates"), json=body).status_code == 403
        assert (
            client.post(path, json={**body, "arguments": {**args, "path": "unexpected"}}).status_code == 422
        )
        assert (
            client.post(
                path, json={**body, "arguments": {"source_id": "https://invalid.test/source"}}
            ).status_code
            == 403
        )
        expired = fixtures.asset(db, project, "Expired text", modality="text", media_type="text/plain")
        db.put("data_assets", {**expired, "expires_at": "2000-01-01T00:00:00+00:00"})
        assert client.post(path, json={**body, "input_refs": [expired["id"]]}).status_code == 409
        service.revoke_asset(source["id"], fixtures.ADMIN, db)
        assert client.post(path, json=body).status_code == 409
    event = next(row for row in db.list("audits", project["id"]) if row["action"] == "tool.preview")
    assert event["detail"]["result_checksum"] == data["checksum"]
    assert "optical" not in json.dumps(event)
    assert not db.list("tasks", project["id"])


def test_preview_withdrawal_during_execution_is_checked_again_before_return(db, monkeypatch):
    from backend.app.platform import tool_preview

    project = fixtures.project(db)
    source = fixtures.asset(
        db, project, "Aerosol optical depth (AOD).", modality="text", media_type="text/plain"
    )
    agent = install(db, project, ["evidence.list_definitions"])
    original = tool_preview.dispatch

    def withdraw(*args):
        value = original(*args)
        service.revoke_asset(source["id"], fixtures.ADMIN, db)
        return value

    monkeypatch.setattr(tool_preview, "dispatch", withdraw)
    app = application()
    app.dependency_overrides[api.edit.dependency] = lambda: fixtures.ADMIN
    with TestClient(app) as client:
        r = client.post(
            f"/api/v2/agents/{agent['id']}/tools/evidence.list_definitions/preview",
            json={"version": 1, "input_refs": [source["id"]], "arguments": {"source_id": source["id"]}},
        )
        assert r.status_code == 409 and "optical" not in r.text


def test_preview_role_contract_can_further_restrict_an_agent_grant(db):
    project = fixtures.project(db)
    source = fixtures.asset(
        db, project, "Aerosol optical depth (AOD).", modality="text", media_type="text/plain"
    )
    agent = install(db, project, ["evidence.list_definitions"])
    # A constrained case role still owns a narrower fixed grant than its tool references.
    db.put("agents", {**agent, "status": "draft", "role_contract": {"grants": ["evidence.timeline_dates"]}})
    published = service.agent_action(agent["id"], "publish", fixtures.ADMIN, db)
    app = application()
    app.dependency_overrides[api.edit.dependency] = lambda: fixtures.ADMIN
    with TestClient(app) as client:
        r = client.post(
            f"/api/v2/agents/{agent['id']}/tools/evidence.list_definitions/preview",
            json={
                "version": published["current_version"],
                "input_refs": [source["id"]],
                "arguments": {"source_id": source["id"]},
            },
        )
        assert r.status_code == 403 and r.json()["detail"]["code"] == "tool_not_authorized"


def test_default_templates_and_public_evidence_keep_the_original_six_pins(db):
    from backend.app.platform.public_evidence import _workflow

    project = fixtures.project(db)
    legacy = next(a for a in db.list("agents", project["id"]) if a.get("execution_strategy") == "tool_agent")
    assert {t["id"] for t in legacy["runtime_bindings"]["tools"]} == BASE_TOOL_IDS
    assert all(t["version"] == 4 for t in legacy["runtime_bindings"]["tools"])
    with db.transaction() as tx:
        _workflow(tx, project["id"])
        _workflow(tx, project["id"])
    evidence = db.get("agents", project["id"] + "-public-evidence-agent")
    assert {r["tool_id"] for r in evidence["tool_refs"]} == BASE_TOOL_IDS
    assert len(catalog()["tools"]) > len(BASE_TOOL_IDS)
    validate_frozen(legacy)
    validate_frozen(evidence)


def test_grants_detect_a_changed_domain_implementation_without_invalidating_unrelated_tools(monkeypatch):
    from pathlib import Path

    evidence = {"tool_refs": [{"tool_id": "evidence.list_definitions", "version": 1}]}
    tables = {"tool_refs": [{"tool_id": "table.audit_keys", "version": 1}]}
    for agent in (evidence, tables):
        agent["runtime_bindings"] = freeze_bindings(agent)
    original = Path.read_text

    def changed(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text + "\n# synthetic implementation change" if path.name == "evidence_tools.py" else text

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(ValueError, match="capability_version_changed"):
        validate_frozen(evidence)
    validate_frozen(tables)


@pytest.mark.asyncio
async def test_governed_agent_dispatches_typed_operation_and_audits_derived_result(db, monkeypatch):
    task = seed(
        db, task_type="text_analysis", mode="cloud", content="Aerosol optical depth (AOD) was observed."
    )
    a = {
        **task["snapshot"]["agents"][0],
        "execution_strategy": "tool_agent",
        "tool_refs": [{"tool_id": "evidence.list_definitions", "version": 1}],
        "skill_refs": [],
    }
    a["runtime_bindings"] = freeze_bindings(a)
    task["snapshot"]["agents"] = [a]
    db.put("tasks", task)
    original_list = Repository.list

    def forbid_unscoped_node_scan(repository, kind, project_id=None, **filters):
        if kind == "nodes" and project_id is None:
            pytest.fail("Tool tracing must use the active instance, not search prior node payloads")
        return original_list(repository, kind, project_id, **filters)

    monkeypatch.setattr(Repository, "list", forbid_unscoped_node_scan)

    async def controlled_agent(spec, messages, bindings, **kwargs):
        assert set(bindings) == {"list_definitions"}
        function = ModelGateway._bind_tool("list_definitions", bindings["list_definitions"], [])
        assert function.parameters == a["runtime_bindings"]["tools"][0]["input_schema"]
        supplied = await function.entrypoint(source_id="input-1", acronym="AOD")
        data = supplied["data"]
        assert data["items"][0]["expansion"].lower() == "aerosol optical depth"
        assert supplied["evidence"] == [{"source_id": "input-1", "classification": "public"}]
        return {
            "text": json.dumps(
                {
                    "text": "AOD 的释义可在所给原文中核验。",
                    "citations": [data["derived_evidence"]],
                    "missing_information": [],
                }
            ),
            "request_id": "synthetic-model",
        }

    async def review(*args, **kwargs):
        return {
            "text": json.dumps(
                {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
            ),
            "request_id": "synthetic-review",
        }

    monkeypatch.setattr(runtime.model_gateway, "run_agent", controlled_agent, raising=False)
    monkeypatch.setattr(runtime.model_gateway, "complete", review, raising=False)
    result = await execute(db)
    assert result["status"] == "succeeded", result["result"]
    (call,) = result["tool_calls"]
    assert call["capability_id"] == "evidence.list_definitions" and call["source_refs"] == ["input-1"]
    node = db.get("nodes", f"{task['id']}:0:{call['node_id']}")
    assert node["instance_id"] == call["instance_id"]
    checkpoint = result["tool_checkpoints"][call["id"]]
    assert checkpoint["result"]["items"][0]["quote"] == "Aerosol optical depth (AOD)"
    assert call["result_checksum"] == service.digest(checkpoint["result"])


def test_trace_rejects_writes_to_wrong_instance_and_writes_from_an_old_lease(db):
    task = seed(db)
    claim = db.claim("test-worker")
    execution = runtime.Execution(db, claim, "test-worker", claim["lease_token"])
    execution.active_node_id = "n1"
    trace = TaskToolTrace(execution, 0, "frozen-instance")
    record = {"id": "call", "task_id": task["id"], "node_id": "n1", "revision": 0, "instance_id": "different"}
    with pytest.raises(ValueError, match="tool_node_instance_mismatch"):
        trace.save(record, "tool.started")
    record["instance_id"] = "frozen-instance"
    db.put("tasks", {**claim, "lease_token": claim["lease_token"] + 1, "lease_owner": "replacement"})
    with pytest.raises(LeaseLost):
        trace.save(record, "tool.started")
    assert not db.get("tasks", task["id"]).get("tool_calls")
