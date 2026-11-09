"""Actual local DAG replay and workflow-only promotion over synthetic sources."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.main import domain_error
from backend.app.platform import api, improvement, service
from backend.app.platform import workflow_improvement as wi
from backend.tests import test_v2_platform as platform

db = platform.db
USER = platform.ADMIN


def setup_flow(db, task_type="source_summary"):
    project = platform.project(db)
    agent = service.save_agent(
        {
            "project_id": project["id"],
            "name": "Synthetic fixed local agent",
            "capabilities": [task_type],
            "modalities": ["text", "structured"],
            "tool_ids": [task_type],
            "model_policy": "local",
            "execution_strategy": "fixed",
        },
        USER,
        repository=db,
    )
    service.agent_action(agent["id"], "publish", USER, db)
    nodes = [
        {
            "id": nid,
            "label": nid,
            "agent_id": agent["id"],
            "depends_on": [] if nid == "extract" else ["extract"],
            "config": {"max_chars": 100} if task_type == "source_summary" else {},
        }
        for nid in ("extract", "deliver")
    ]
    flow = service.save_workflow(
        {"project_id": project["id"], "name": "Synthetic flow", "task_type": task_type, "nodes": nodes},
        USER,
        repository=db,
    )
    return project, service.publish_workflow(flow["id"], USER, db), agent


async def terminal(db, project, flow, marker, *, content=None, quality=None, revisions=0, deadline=300):
    text = flow["task_type"] == "source_summary"
    asset = platform.asset(
        db,
        project,
        content=content if content is not None else marker * 400,
        modality="text" if text else "structured",
        media_type="text/plain" if text else "application/json",
    )
    return await platform.run(
        db,
        platform.task_body(
            project,
            asset,
            workflow_id=flow["id"],
            task_type=flow["task_type"],
            mode="local",
            idempotency_key=asset["id"],
            max_revisions=revisions,
            deadline_seconds=deadline,
            quality=quality if quality is not None else {"min_length": 300},
        ),
    )


def proposal(db, project, flow, tasks, *, nodes=None):
    candidate = deepcopy(nodes if nodes is not None else flow["nodes"])
    for node in candidate:
        node.pop("agent_version", None)
        if flow["task_type"] == "source_summary":
            node["config"]["max_chars"] = 300
    return {
        "project_id": project["id"],
        "name": "Measured workflow candidate",
        "workflow_id": flow["id"],
        "nodes": candidate,
        "sample_task_ids": [t["id"] for t in tasks],
    }


async def test_workflow_replay_promotes_only_workflow_and_preserves_snapshots(db):
    project, flow, agent = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    assert all(t["status"] == "failed" for t in tasks)
    old_snapshot = deepcopy(tasks[0]["snapshot"])
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    assert record["target_kind"] == "workflow"
    result = await improvement.evaluate(record["id"], USER, db)
    assert result["verdict"]["passed"]
    assert result["verdict"]["baseline_passed"] == 0
    assert result["verdict"]["candidate_passed"] == 2
    evidence = db.get("evaluations", result["evaluation_id"])
    assert evidence["method"] == "frozen_local_workflow_replay"
    assert [p["split"] for p in evidence["samples"]] == ["development", "holdout"]
    assert len(evidence["samples"][0]["candidate"]["history"][0]["nodes"]) == 2
    promoted = improvement.promote(record["id"], USER, db)
    assert promoted["candidate_version"] == 2
    assert db.get("agents", agent["id"])["current_version"] == 1
    assert db.get("tasks", tasks[0]["id"])["snapshot"] == old_snapshot
    fresh = await terminal(db, project, flow, "C")
    assert fresh["status"] == "succeeded"
    assert fresh["snapshot"]["workflow"]["version"] == 2
    assert improvement.rollback(record["id"], USER, db)["status"] == "rolled_back"
    assert db.get("workflows", flow["id"])["current_version"] == 1
    assert db.get("workflow_versions", flow["id"] + ":2")
    assert service.publish_workflow(flow["id"], USER, db)["current_version"] == 3


async def test_real_revision_savings_are_measured_not_assumed(db):
    project, flow, _ = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark, revisions=1) for mark in ("A", "B")]
    assert all(t["status"] == "succeeded" and t["revision"] == 1 for t in tasks)
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    assert record["verdict"]["passed"]
    pair = db.get("evaluations", record["evaluation_id"])["samples"][0]
    assert pair["baseline"]["attempts"] == 2 and pair["candidate"]["attempts"] == 1


async def test_workflow_holdout_failure_and_no_gain_reject(db):
    project, flow, _ = setup_flow(db)
    one = await terminal(db, project, flow, "A")
    two = await terminal(db, project, flow, "B", content="too short")
    record = wi.create(proposal(db, project, flow, [one, two]), USER, db)
    result = await improvement.evaluate(record["id"], USER, db)
    assert result["status"] == "rejected" and not result["verdict"]["holdout_passed"]
    with pytest.raises(service.DomainError, match="完整冻结评测"):
        improvement.promote(record["id"], USER, db)
    body = proposal(db, project, flow, [one, two])
    for node in body["nodes"]:
        node["config"]["max_chars"] = 100
    no_change = wi.create(body, USER, db)
    assert (await improvement.evaluate(no_change["id"], USER, db))["status"] == "rejected"


async def test_workflow_rejects_overlapping_content_cycles_and_new_agents(db):
    project, flow, _ = setup_flow(db)
    one = await terminal(db, project, flow, "A")
    clone = await terminal(db, project, flow, "A")
    with pytest.raises(service.DomainError, match="互不重叠"):
        wi.create(proposal(db, project, flow, [one, clone]), USER, db)
    two = await terminal(db, project, flow, "B")
    body = proposal(db, project, flow, [one, two])
    body["nodes"][0]["depends_on"] = ["deliver"]
    with pytest.raises(service.DomainError, match="循环"):
        wi.create(body, USER, db)
    body["nodes"][0]["depends_on"] = []
    body["nodes"][0]["agent_id"] = "foreign-agent"
    with pytest.raises(service.DomainError, match="替换"):
        wi.create(body, USER, db)
    with pytest.raises(service.DomainError, match="权限"):
        wi.create(proposal(db, project, flow, [one, two]), platform.OTHER, db)


async def test_sources_evidence_and_later_publication_protect_promotion(db):
    project, flow, _ = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    evidence = db.get("evaluations", record["evaluation_id"])
    evidence["samples"][0]["candidate"]["history"] = []
    db.put("evaluations", evidence)
    with pytest.raises(service.DomainError, match="证据未被修改"):
        improvement.promote(record["id"], USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    service.publish_workflow(flow["id"], USER, db)
    with pytest.raises(service.DomainError, match="拒绝覆盖"):
        improvement.promote(record["id"], USER, db)
    asset = db.get("data_assets", tasks[0]["snapshot"]["inputs"][0]["id"])
    asset["status"] = "revoked"
    db.put("data_assets", asset)
    with pytest.raises(service.DomainError):
        await improvement.evaluate(record["id"], USER, db)


async def test_auto_workflow_coverage_uses_real_failed_node_and_is_idempotent(db, monkeypatch):
    project, flow, agent = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    assert await improvement.maintain(db) == 0
    monkeypatch.setenv("HANGZHI_AUTO_IMPROVE", "true")
    assert await improvement.maintain(db) == 1
    record = db.list("improvements")[0]
    assert record["target_kind"] == "workflow" and record["status"] == "promoted"
    assert record["coverage_repair"]["node_ids"] == ["extract", "deliver"]
    assert record["source_task_id"] in {t["id"] for t in tasks}
    assert db.get("agents", agent["id"])["current_version"] == 1
    assert await improvement.maintain(db) == 0
    assert len(db.list("improvements")) == 1


async def test_structured_edges_replay_and_measured_automatic_rollback(db, monkeypatch):
    from backend.tests.test_resource_lock_order import trace_locks

    project, flow, _ = setup_flow(db, "structured_transform")
    tasks = [
        await terminal(
            db,
            project,
            flow,
            mark,
            content=[
                {"kind": "keep", "value": mark},
                {"kind": "remove", "value": None},
            ],
            quality={"required_fields": ["value"]},
        )
        for mark in ("A", "B")
    ]
    body = proposal(db, project, flow, tasks)
    body["nodes"][0]["config"] = {"filter": {"kind": "keep"}}
    record = wi.create(body, USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    assert record["verdict"]["passed"]
    output = db.get("evaluations", record["evaluation_id"])["samples"][0]["candidate"]["history"][0]["nodes"][
        1
    ]["output"]
    assert output["content"]["records"] == [{"kind": "keep", "value": "A"}]
    improvement.promote(record["id"], USER, db)
    fresh = await terminal(
        db,
        project,
        flow,
        "C",
        content=[{"kind": "other", "value": "valid"}],
        quality={"required_fields": ["value"]},
    )
    assert fresh["status"] == "failed"
    monkeypatch.setenv("HANGZHI_AUTO_IMPROVE", "true")
    locks = trace_locks(monkeypatch)
    await improvement.maintain(db)
    assert locks.index(("projects", project["id"])) < locks.index(("improvements", record["id"]))
    assert locks.index(("workflows", flow["id"])) < locks.index(
        ("data_assets", fresh["snapshot"]["inputs"][0]["id"])
    )
    record = db.get("improvements", record["id"])
    assert record["status"] == "rolled_back"
    assert record["regression_evidence"]["task_id"] == fresh["id"]
    assert db.get("workflows", flow["id"])["current_version"] == 1


async def test_workflow_api_uses_existing_governed_actions_and_read_permissions(db):
    project, flow, _ = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    app = FastAPI()
    app.include_router(api.router)
    app.add_exception_handler(service.DomainError, domain_error)
    app.dependency_overrides[api.edit.dependency] = lambda: USER
    app.dependency_overrides[api.read.dependency] = lambda: USER
    with TestClient(app) as client:
        response = client.post("/api/v2/workflow-improvements", json=proposal(db, project, flow, tasks))
        assert response.status_code == 201, response.text
        record = response.json()
        assert all("content" not in a for sample in record["samples"] for a in sample["snapshot"]["inputs"])
        assert client.post(f"/api/v2/improvements/{record['id']}/evaluate").json()["verdict"]["passed"]
        assert client.post(f"/api/v2/improvements/{record['id']}/promote").json()["status"] == "promoted"
        assert client.get("/api/v2/improvements").json()[0]["target_kind"] == "workflow"


async def test_workflow_replay_honors_plugin_contract_and_revocation(db):
    from backend.app.platform import capability_service
    from backend.tests.test_task_plugins import plugin

    project, flow, _ = setup_flow(db)
    definition = plugin(db, project, "source_digest")
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    assert record["verdict"]["passed"]
    output = db.get("evaluations", record["evaluation_id"])["samples"][0]["candidate"]["history"][0]["nodes"][
        0
    ]["output"]
    assert output["task_plugin"]["id"] == definition["id"]
    capability_service.action(definition["id"], "revoke", USER, db)
    with pytest.raises(service.DomainError) as exc:
        improvement.promote(record["id"], USER, db)
    assert exc.value.code == "plugin_revoked"


async def test_workflow_replay_cannot_bypass_plugin_output_limit(db):
    from backend.tests.test_task_plugins import plugin

    project, flow, _ = setup_flow(db)
    plugin(db, project, "source_digest", max_output_bytes=100)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    record = await improvement.evaluate(record["id"], USER, db)
    assert record["status"] == "rejected" and record["verdict"]["hard_failure"]


async def test_rollback_cannot_overwrite_later_workflow_version(db):
    project, flow, _ = setup_flow(db)
    tasks = [await terminal(db, project, flow, mark) for mark in ("A", "B")]
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    await improvement.evaluate(record["id"], USER, db)
    improvement.promote(record["id"], USER, db)
    assert service.publish_workflow(flow["id"], USER, db)["current_version"] == 3
    with pytest.raises(service.DomainError) as exc:
        improvement.rollback(record["id"], USER, db)
    assert exc.value.code == "version_changed"


async def test_final_tool_finishing_after_deadline_cannot_promote(db, monkeypatch):
    import time

    from backend.app.platform import runtime

    project, flow, _ = setup_flow(db)
    node = {key: value for key, value in flow["nodes"][0].items() if key != "agent_version"}
    service.save_workflow(
        {"project_id": project["id"], "name": flow["name"], "task_type": flow["task_type"], "nodes": [node]},
        USER,
        flow["id"],
        db,
    )
    flow = service.publish_workflow(flow["id"], USER, db)
    tasks = [await terminal(db, project, flow, mark, deadline=1) for mark in ("A", "B")]
    record = wi.create(proposal(db, project, flow, tasks), USER, db)
    execute = runtime.execute_local

    def delayed(*args, **kwargs):
        output = execute(*args, **kwargs)
        time.sleep(1.02)
        return output

    monkeypatch.setattr(runtime, "execute_local", delayed)
    record = await improvement.evaluate(record["id"], USER, db)
    assert record["status"] == "rejected" and record["verdict"]["hard_failure"]
    evidence = db.get("evaluations", record["evaluation_id"])
    assert all(p["candidate"]["duration_ms"] >= 1000 for p in evidence["samples"])
    with pytest.raises(service.DomainError):
        improvement.promote(record["id"], USER, db)
