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
        assert client.get(f"/api/documents/{document['id']}/file").content == "仅本地测试".encode()
