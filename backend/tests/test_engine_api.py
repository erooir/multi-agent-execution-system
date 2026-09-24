from fastapi.testclient import TestClient

from backend.tests.test_engine import isolated_engine as _isolated_engine_fixture

isolated_engine = _isolated_engine_fixture


def test_api_roles_bootstrap_exports_and_native_routes_are_private(isolated_engine):
    from backend.app.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/bootstrap").status_code == 401
        assert (
            client.post("/api/auth/login", json={"username": "reviewer", "password": "demo12345"}).status_code
            == 200
        )
        response = client.get("/api/bootstrap")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["samples"]) == 30
        assert len(payload["workflows"]) == 6
        assert len(payload["skills"]) == 9
        assert all("evidence_required" in s for s in payload["skills"])
        assert payload["capability_stats"] == {
            "skills": len(payload["skills"]),
            "tools": len(payload["tools"]),
            "mcp_servers": len(payload["mcp_servers"]),
            "healthy_tools": len(payload["tools"]),
        }
        servers_by_id = {s["id"]: s for s in payload["mcp_servers"]}
        assert servers_by_id["docling-local"]["enabled"] is False
        assert servers_by_id["aviation-local"]["enabled"] is True
        assert all("command" not in s for s in payload["mcp_servers"])
        assert "path" not in payload["documents"][0]
        assert payload["system"]["native_routes_public"] is False
        assert client.post("/api/runs", json={"workflow_id": "workflow-tech-trends"}).status_code == 403
        assert client.put("/api/settings", json={"budget_limit_cny": 10}).status_code == 403
        assert client.post("/api/auth/logout").status_code == 200
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        assert client.post("/api/runs/missing/review", json={"decision": "approve"}).status_code == 403
        assert client.post("/api/workflows/workflow-tech-trends/validate", json={}).json()["valid"]
        assert (
            client.post(
                "/api/agents/agent-writer/test", json={"message": "测试", "mode": "rehearsal"}
            ).json()["mode"]
            == "rehearsal"
        )
        assert "/agents" not in client.get("/openapi.json").json()["paths"]
        assert client.post("/workflows/research-node-executor/runs", data={"message": "no"}).status_code in {
            404,
            405,
        }
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_api_upload_rejects_path_and_budget_escalation(isolated_engine):
    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "demo12345"})
        assert client.put("/api/settings", json={"budget_limit_cny": 301}).status_code == 400
        response = client.post(
            "/api/documents/upload",
            data={"project_id": "project-technology", "visibility": "local"},
            files={"file": ("../escape.txt", b"test", "text/plain")},
        )
        assert response.status_code == 400
        response = client.post(
            "/api/documents/upload",
            data={"project_id": "project-technology", "visibility": "local"},
            files={"file": ("local.txt", "仅本地测试".encode(), "text/plain")},
        )
        assert response.status_code == 201, response.text
        document = response.json()
        assert document["visibility"] == "local"
        assert (
            client.post(
                "/api/runs",
                json={
                    "workflow_id": "workflow-tech-trends",
                    "mode": "live",
                    "document_ids": [document["id"]],
                },
            ).status_code
            == 400
        )
        assert client.get("/api/documents/" + document["id"] + "/file").content == "仅本地测试".encode()


def test_api_plan_is_async_and_parser_role_available(isolated_engine):
    import time

    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        agents = client.get("/api/agents").json()
        assert any(a["role"] == "parser" for a in agents)
        created = client.post(
            "/api/agents",
            json={"name": "解析员乙", "role": "parser", "skill_ids": ["document_parse"]},
        )
        assert created.status_code == 201, created.text

        response = client.post(
            "/api/plan",
            json={
                "prompt": "规划一个本地演练流程",
                "project_id": "project-technology",
                "mode": "rehearsal",
            },
        )
        assert response.status_code == 202, response.text
        job = response.json()
        for _ in range(200):
            job = client.get(f"/api/planning/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "completed", job.get("error")
        saved = next(w for w in client.get("/api/workflows").json() if w["id"] == job["workflow_id"])
        assert saved["source_prompt"] == "规划一个本地演练流程"
        assert saved["preferred_mode"] == "rehearsal"


def test_api_workflow_delete_keeps_run_snapshots(isolated_engine):
    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        created = client.post(
            "/api/workflows",
            json={
                "name": "待删除流程",
                "category": "technology",
                "nodes": [
                    {
                        "id": "s",
                        "type": "task",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "开始", "kind": "start", "config": {}},
                    },
                    {
                        "id": "e",
                        "type": "task",
                        "position": {"x": 200, "y": 0},
                        "data": {"label": "结束", "kind": "end", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "s", "target": "e"}],
            },
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        assert client.post(f"/api/workflows/{workflow_id}/publish").status_code == 200
        run = client.post(
            "/api/runs",
            json={
                "workflow_id": workflow_id,
                "project_id": "project-technology",
                "prompt": "删除后仍可追溯",
                "mode": "rehearsal",
            },
        ).json()
        assert client.delete(f"/api/workflows/{workflow_id}").json() == {"deleted": True}
        assert workflow_id not in {w["id"] for w in client.get("/api/workflows").json()}
        assert client.get(f"/api/workflows/{workflow_id}/versions").status_code == 404
        assert client.get(f"/api/runs/{run['id']}").status_code == 200
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "reviewer", "password": "demo12345"})
        assert client.delete("/api/workflows/workflow-tech-trends").status_code == 403


def test_api_temporary_upload_hidden_from_library(isolated_engine):
    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        response = client.post(
            "/api/documents/upload",
            data={"project_id": "project-technology", "visibility": "external", "temporary": "true"},
            files={"file": ("临时资料.txt", "一次性上传内容".encode(), "text/plain")},
        )
        assert response.status_code == 201, response.text
        document = response.json()
        listed = client.get("/api/documents", params={"project_id": "project-technology"}).json()
        assert document["id"] not in {d["id"] for d in listed}
        listed_all = client.get(
            "/api/documents",
            params={"project_id": "project-technology", "include_temporary": True},
        ).json()
        assert document["id"] in {d["id"] for d in listed_all}
        bootstrap = client.get("/api/bootstrap").json()
        assert document["id"] not in {d["id"] for d in bootstrap["documents"]}
        assert client.get(f"/api/documents/{document['id']}/file").status_code == 200
