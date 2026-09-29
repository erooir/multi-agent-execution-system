"""Lifecycle/reference invariants without paid model calls."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend.app import agent_registry, workflows
from backend.tests.test_engine import isolated_engine as _isolated_engine_fixture

isolated_engine = _isolated_engine_fixture


def minimal_workflow(agent_id: str) -> dict:
    return {
        "name": "生命周期绑定核验",
        "nodes": [
            {"id": "s", "data": {"kind": "start", "label": "开始"}},
            {"id": "a", "data": {"kind": "analyze", "label": "分析", "agent_id": agent_id}},
            {"id": "e", "data": {"kind": "end", "label": "结束"}},
        ],
        "edges": [
            {"id": "sa", "source": "s", "target": "a"},
            {"id": "ae", "source": "a", "target": "e"},
        ],
    }


def test_api_clone_is_independent_and_destroy_is_irreversible(isolated_engine):
    database, _ = isolated_engine
    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        original = client.post(
            "/api/agents",
            json={"name": "自定义分析员", "role": "writer", "instructions": "仅使用证据", "enabled": False},
        ).json()
        assert original["lifecycle_status"] == "disabled"
        cloned = client.post(f"/api/agents/{original['id']}/clone", json={"name": "独立副本"})
        assert cloned.status_code == 201, cloned.text
        clone = cloned.json()
        assert clone["id"] != original["id"]
        assert clone["cloned_from"] == original["id"]
        assert clone["version"] == 1 and clone["lifecycle_status"] == "active"
        assert clone["instructions"] == original["instructions"]
        assert clone["usage"] == {"workflow_count": 0, "active_instance_count": 0, "run_count": 0}
        assert client.put(f"/api/agents/{clone['id']}", json={"instructions": "独立调整"}).status_code == 200
        assert database.get("agents", original["id"])["instructions"] == "仅使用证据"

        snapshot = deepcopy(database.get("agents", clone["id"]))
        database.save(
            "runs", {"id": "history", "status": "completed", "agent_snapshots": {clone["id"]: snapshot}}
        )
        deleted = client.delete(f"/api/agents/{clone['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["agent"]["lifecycle_status"] == "destroyed"
        assert database.get("runs", "history")["agent_snapshots"][clone["id"]] == snapshot
        for method, path, body in (
            ("put", "", {"enabled": True}),
            ("post", "/clone", {}),
            ("post", "/test", {"message": "测试", "mode": "rehearsal"}),
        ):
            assert getattr(client, method)(f"/api/agents/{clone['id']}{path}", json=body).status_code == 409
        detail = client.get(f"/api/agents/{clone['id']}/lifecycle").json()
        assert {event["action"] for event in detail["events"]} == {"clone", "edit", "destroy"}
        assert detail["agent"]["usage"]["run_count"] == 1
        assert client.get("/api/runs/history").json().get("agent_snapshots") is None
        assert next(a for a in client.get("/api/agents").json() if a["id"] == clone["id"])["enabled"] is False


def test_lifecycle_api_roles_builtins_and_invalid_state(isolated_engine):
    from backend.app.main import app

    with TestClient(app) as client:
        assert client.get("/api/agents/agent-writer/lifecycle").status_code == 401
        client.post("/api/auth/login", json={"username": "reviewer", "password": "demo12345"})
        assert client.get("/api/agents/agent-writer/lifecycle").status_code == 200
        assert client.post("/api/agents", json={"name": "不可创建"}).status_code == 403
        assert client.put("/api/agents/agent-writer", json={"enabled": False}).status_code == 403
        assert client.delete("/api/agents/agent-writer").status_code == 403
        assert client.post("/api/agents/agent-writer/clone").status_code == 403
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        response = client.delete("/api/agents/agent-writer")
        assert response.status_code == 409 and "内置" in response.json()["detail"]
        assert client.put("/api/agents/agent-writer", json={"enabled": "false"}).status_code == 400
        assert client.post("/api/agents", json={"name": None}).status_code == 400
        assert client.post("/api/agents", json={"name": "错误技能", "skill_ids": [{}]}).status_code == 400
        assert client.get("/api/agents/missing/lifecycle").status_code == 404
        disabled = client.put("/api/agents/agent-writer", json={"enabled": False})
        assert disabled.status_code == 200 and disabled.json()["lifecycle_status"] == "disabled"
        assert (
            client.post(
                "/api/agents/agent-writer/test", json={"message": "测试", "mode": "rehearsal"}
            ).status_code
            == 400
        )
        assert (
            client.put("/api/agents/agent-writer", json={"enabled": True}).json()["lifecycle_status"]
            == "active"
        )
        events = client.get("/api/agents/agent-writer/lifecycle").json()["events"]
        assert {event["action"] for event in events} == {"disable", "enable"}


@pytest.mark.parametrize("status", ["queued", "running", "waiting_review", "interrupted"])
def test_pending_runs_block_destroy_even_after_workflow_deleted(isolated_engine, status):
    database, _ = isolated_engine
    agent = database.save("agents", {"id": "custom", "name": "自定义", "enabled": True})
    database.save(
        "runs",
        {"id": "pending", "status": status, "workflow_snapshot": minimal_workflow(agent["id"])},
    )
    with pytest.raises(ValueError, match="未结束"):
        agent_registry.destroy_agent(database, agent["id"], "operator")
    database.save("runs", {"id": "pending", "status": "completed", "agent_snapshots": {agent["id"]: agent}})
    assert agent_registry.destroy_agent(database, agent["id"], "operator")["lifecycle_status"] == "destroyed"


def test_reference_counts_and_running_context_guard_preserve_history(isolated_engine):
    database, _ = isolated_engine
    agent = database.save("agents", {"id": "custom", "name": "复用分析员", "enabled": True})
    first = workflows.save_workflow(minimal_workflow(agent["id"]))
    second = workflows.save_workflow(minimal_workflow(agent["id"]))
    first["nodes"].append({"id": "second-analysis", "data": {"kind": "analyze", "agent_id": agent["id"]}})
    database.save("workflows", first)
    database.save("runs", {"id": "cancelled", "status": "cancelled", "agent_snapshots": {agent["id"]: agent}})
    instance = database.save(
        "agent_instances", {"id": "ctx", "agent_id": agent["id"], "run_id": "cancelled", "status": "running"}
    )
    detail = agent_registry.lifecycle_detail(database, agent["id"])
    assert detail["agent"]["usage"] == {"workflow_count": 2, "active_instance_count": 1, "run_count": 1}
    assert sum(len(item["nodes"]) for item in detail["workflows"]) == 3
    with pytest.raises(ValueError, match="解除或替换"):
        agent_registry.destroy_agent(database, agent["id"], "operator")
    database.delete("workflows", first["id"])
    database.delete("workflows", second["id"])
    with pytest.raises(ValueError, match="未释放"):
        agent_registry.destroy_agent(database, agent["id"], "operator")
    instance["status"] = "released"
    database.save("agent_instances", instance)
    agent_registry.destroy_agent(database, agent["id"], "operator")
    assert database.get("agent_instances", "ctx")["status"] == "released"
    assert database.get("runs", "cancelled")["status"] == "cancelled"


def test_disabled_binding_can_be_removed_but_not_newly_bound_or_published(isolated_engine):
    database, _ = isolated_engine
    agent = database.save("agents", {"id": "custom", "name": "分析员", "enabled": True})
    workflow = workflows.save_workflow(minimal_workflow(agent["id"]))
    snapshots = {agent["id"]: deepcopy(agent)}
    agent["enabled"] = False
    database.save("agents", agent)
    assert not workflows.validate_workflow(workflow)["valid"]
    assert workflows.validate_workflow(workflow, agent_snapshots=snapshots)["valid"]
    with pytest.raises(ValueError, match="停用"):
        workflows.publish(workflow["id"])
    with pytest.raises(ValueError, match="停用"):
        workflows.save_workflow(minimal_workflow(agent["id"]))
    assert workflows.save_workflow({"name": "可继续编辑旧草稿"}, workflow["id"])["name"] == "可继续编辑旧草稿"
    unbound = deepcopy(workflow)
    del unbound["nodes"][1]["data"]["agent_id"]
    workflows.save_workflow(unbound, workflow["id"])
    agent_registry.destroy_agent(database, agent["id"], "operator")
    with pytest.raises(ValueError, match="销毁"):
        workflows.save_workflow(minimal_workflow(agent["id"]))
    with pytest.raises(ValueError, match="销毁"):
        workflows.restore(workflow["id"], 1)


def test_implicit_defaults_and_detail_limits_do_not_lose_usage_counts(isolated_engine):
    database, _ = isolated_engine
    implicit = {
        "nodes": [
            {"id": "parse", "data": {"kind": "parse"}},
            {"id": "report", "data": {"kind": "report"}},
            {"id": "skill", "data": {"kind": "parse", "skill_id": "ocr"}},
        ]
    }
    assert agent_registry.workflow_agent_ids(implicit) == {"agent-parser", "agent-writer"}
    agent = database.save("agents", {"id": "legacy", "name": "旧版定义", "enabled": False})
    for index in range(105):
        database.save(
            "agent_instances", {"agent_id": agent["id"], "run_id": str(index), "status": "released"}
        )
        agent_registry.record_event(database, agent["id"], "context_released")
    detail = agent_registry.lifecycle_detail(database, agent["id"])
    assert len(detail["instances"]) == len(detail["events"]) == 100
    assert detail["agent"]["usage"]["run_count"] == 105
    assert detail["agent"]["lifecycle_status"] == "disabled"
    assert detail["agent"]["origin"] == "custom"


def test_malformed_legacy_agent_binding_does_not_break_agent_listing(isolated_engine):
    database, _ = isolated_engine
    database.save(
        "workflows",
        {
            "name": "旧版草稿",
            "nodes": [
                {"id": "bad-list", "data": {"kind": "analyze", "agent_id": ["agent-writer"]}},
                {"id": "bad-map", "data": {"kind": "analyze", "agent_id": {"id": "agent-writer"}}},
            ],
        },
    )
    assert agent_registry.enrich_agents(database)
    assert agent_registry.lifecycle_detail(database, "agent-writer")["agent"]["id"] == "agent-writer"


def test_binding_and_destruction_cannot_race_into_dangling_workflow(isolated_engine):
    database, _ = isolated_engine
    database.save("agents", {"id": "racing", "name": "并发绑定核验", "enabled": True})
    barrier = Barrier(2)

    def bind():
        barrier.wait()
        try:
            return workflows.save_workflow(minimal_workflow("racing"))
        except ValueError:
            return None

    def destroy():
        barrier.wait()
        try:
            return agent_registry.destroy_agent(database, "racing", "operator")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        bound, destroyed = pool.submit(bind), pool.submit(destroy)
        outcomes = [bound.result(), destroyed.result()]
    assert sum(result is not None for result in outcomes) == 1
    agent = database.get("agents", "racing")
    if agent_registry.lifecycle_status(agent) == "destroyed":
        assert not agent_registry.workflow_references(database, "racing")
    else:
        assert agent_registry.workflow_references(database, "racing")
