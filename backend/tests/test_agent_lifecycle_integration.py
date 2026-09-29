"""Public API acceptance: two workflows reuse a definition, never a task context."""

import time
from itertools import pairwise

from fastapi.testclient import TestClient

from backend.tests.test_engine import isolated_engine as _isolated_engine_fixture

isolated_engine = _isolated_engine_fixture


def _workflow(name, agent_id):
    kinds = ["start", "analyze", "report", "end"]
    return {
        "name": name,
        "nodes": [
            {
                "id": kind,
                "type": "task",
                "position": {"x": index * 200, "y": 0},
                "data": {
                    "kind": kind,
                    "label": kind,
                    "config": {},
                    **({"agent_id": agent_id} if kind in {"analyze", "report"} else {}),
                },
            }
            for index, kind in enumerate(kinds)
        ],
        "edges": [
            {"id": f"e{index}", "source": source, "target": target}
            for index, (source, target) in enumerate(pairwise(kinds))
        ],
    }


def _finished(client, run_id):
    for _ in range(300):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == "completed", run
            return run
        time.sleep(0.01)
    raise AssertionError("rehearsal did not finish")


def test_two_workflows_reuse_versioned_definition_and_preserve_destroyed_history(isolated_engine):
    from backend.app.main import app

    store, _ = isolated_engine
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        assert login.status_code == 200, login.text
        response = client.post(
            "/api/agents",
            json={"name": "生命周期验收角色", "role": "writer", "instructions": "保留证据限制"},
        )
        assert response.status_code == 201, response.text
        agent_id = response.json()["id"]
        flows = []
        for label in ["合成专题甲", "合成专题乙"]:
            response = client.post("/api/workflows", json=_workflow(label, agent_id))
            assert response.status_code == 201, response.text
            flow = response.json()
            assert client.post(f"/api/workflows/{flow['id']}/publish").status_code == 200
            flows.append(flow)
        assert client.delete(f"/api/agents/{agent_id}").status_code == 409
        detail = client.get(f"/api/agents/{agent_id}/lifecycle").json()
        assert detail["agent"]["usage"]["workflow_count"] == 2
        assert {w["id"] for w in detail["workflows"]} == {w["id"] for w in flows}

        runs = []
        for index, flow in enumerate(flows):
            if index:
                response = client.put(f"/api/agents/{agent_id}", json={"instructions": "新版证据要求"})
                assert response.status_code == 200, response.text
            response = client.post("/api/runs", json={"workflow_id": flow["id"], "mode": "rehearsal"})
            assert response.status_code == 201, response.text
            runs.append(_finished(client, response.json()["id"]))
        detail = client.get(f"/api/agents/{agent_id}/lifecycle").json()
        instances = detail["instances"]
        assert len(instances) == 2
        assert {i["run_id"] for i in instances} == {r["id"] for r in runs}
        assert len({i["id"] for i in instances}) == 2
        assert {i["agent_version"] for i in instances} == {1, 2}
        assert all(i["status"] == "released" and i["use_count"] == 2 for i in instances)
        assert all(set(i["node_ids"]) == {"analyze", "report"} for i in instances)
        assert detail["agent"]["usage"]["active_instance_count"] == 0
        assert detail["agent"]["usage"]["run_count"] == 2
        for run in runs:
            bound_steps = [s for s in run["steps"] if s["kind"] in {"analyze", "report"}]
            assert len({s["agent_instance_id"] for s in bound_steps}) == 1
        assert "agent_snapshots" not in client.get("/api/bootstrap").json()["runs"][0]

        for flow in flows:
            assert client.delete(f"/api/workflows/{flow['id']}").status_code == 200
        response = client.delete(f"/api/agents/{agent_id}")
        assert response.status_code == 200, response.text
        detail = client.get(f"/api/agents/{agent_id}/lifecycle").json()
        assert detail["agent"]["lifecycle_status"] == "destroyed"
        assert len(detail["instances"]) == 2
        assert all(client.get(f"/api/runs/{r['id']}").status_code == 200 for r in runs)
        assert store.get("runs", runs[0]["id"])["agent_snapshots"][agent_id]["version"] == 1
        assert store.get("runs", runs[1]["id"])["agent_snapshots"][agent_id]["version"] == 2
