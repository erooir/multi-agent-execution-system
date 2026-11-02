"""Regression tests for streaming boundaries, withdrawal and cold-start concurrency."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import auth
from backend.app.main import RequestBodyLimitMiddleware, concurrent_write, domain_error
from backend.app.platform import api, contracts, service
from backend.app.platform.db import ConcurrentWrite, Repository, schema
from backend.tests import test_v2_platform as platform_fixtures
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = platform_fixtures.db


def application():
    app = FastAPI()
    app.add_exception_handler(service.DomainError, domain_error)
    app.add_exception_handler(ConcurrentWrite, concurrent_write)
    app.include_router(api.router)
    app.dependency_overrides[auth.get_current_user] = lambda: ADMIN
    return app


def test_bootstrap_scopes_business_queries_and_active_tasks_but_keeps_the_project_picker(db, monkeypatch):
    first, second = project(db), project(db)
    a, b = asset(db, first), asset(db, second)
    one = service.create_task(task_body(first, a), ADMIN, db)
    two = service.create_task(task_body(second, b), ADMIN, db)
    db.put("workers", {"id": "worker", "status": "running", "active_task_ids": [one["id"], two["id"]]})
    with TestClient(application()) as client:
        legacy = client.get("/api/v2/bootstrap").json()
        assert "current_project_id" not in legacy
        assert {row["id"] for row in legacy["tasks"]} == {one["id"], two["id"]}
        calls = []
        original = db.list

        def tracked(kind, project_id=None, **filters):
            calls.append((kind, project_id))
            return original(kind, project_id, **filters)

        monkeypatch.setattr(db, "list", tracked)
        r = client.get("/api/v2/bootstrap", params={"scope": "current", "project_id": second["id"]}).json()
        assert {p["id"] for p in r["projects"]} == {first["id"], second["id"]}
        assert r["current_project_id"] == second["id"] and [t["id"] for t in r["tasks"]] == [two["id"]]
        assert r["stats"]["task_count"] == 1 and r["workers"][0]["active_task_ids"] == [two["id"]]
        assert all(pid == second["id"] for kind, pid in calls if kind not in {"projects", "workers"})
        assert (
            client.get("/api/v2/bootstrap", params={"scope": "current"}).json()["current_project_id"]
            == first["id"]
        )


def test_bootstrap_requires_membership_and_returns_no_business_data_for_unassigned_reviewer(db):
    first, second = project(db), project(db)
    viewer = {"id": "scoped-reader", "role": "reviewer"}
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: viewer
    with TestClient(app) as client:
        r = client.get("/api/v2/bootstrap", params={"scope": "current"}).json()
        assert r["projects"] == [] and r["current_project_id"] is None and not r["agents"] and not r["tasks"]
        db.put("projects", {**first, "members": [viewer["id"]]})
        r = client.get("/api/v2/bootstrap", params={"scope": "current"}).json()
        assert [p["id"] for p in r["projects"]] == [first["id"]] and r["current_project_id"] == first["id"]
        assert (
            client.get(
                "/api/v2/bootstrap", params={"scope": "current", "project_id": second["id"]}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v2/bootstrap", params={"scope": "current", "project_id": "missing-project"}
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "headers,chunks,status",
    [
        ([(b"content-length", b"99")], [b"a"], 413),
        ([(b"content-length", b"bad")], [b"a"], 400),
        ([], [b"abc", b"def"], 413),
        ([], [b"a", b"b"], 200),
    ],
)
async def test_request_limit_before_parser(headers, chunks, status):
    received, sent = [], []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            received.append(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages = iter(
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await RequestBodyLimitMiddleware(downstream, max_bytes=4)(
        {"type": "http", "method": "POST", "headers": headers}, receive, send
    )
    assert sent[0]["status"] == status
    assert received == (chunks if status == 200 else [])


def test_concurrent_database_cold_start(tmp_path):
    url = f"sqlite:///{(tmp_path / 'fresh.sqlite3').as_posix()}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        repositories = list(pool.map(lambda _: Repository(url), range(8)))
    for repository in repositories:
        with repository.engine.connect() as connection:
            assert connection.execute(select(schema.c.version)).scalars().all() == [1]
        repository.engine.dispose()


def test_concurrent_write_is_http_conflict():
    app = application()

    @app.get("/collision")
    def collide():
        raise ConcurrentWrite("Changed since read")

    with TestClient(app) as client:
        response = client.get("/collision")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "concurrent_write"


async def test_withdrawn_sources_hidden_on_all_collection_paths(db):
    p = project(db)
    a = asset(db, p, [{"private_marker": "WITHDRAWN_SENTINEL"}])
    task = await run(db, task_body(p, a))
    db.put(
        "memories",
        {
            "project_id": p["id"],
            "content": "WITHDRAWN_SENTINEL",
            "source_refs": [a["id"]],
            "expires_at": a["expires_at"],
            "status": "active",
        },
    )
    candidate = db.put(
        "improvements",
        {"project_id": p["id"], "samples": [{"snapshot": {"inputs": [a]}}], "status": "evaluated"},
    )
    db.put(
        "evaluations",
        {
            "project_id": p["id"],
            "candidate_id": candidate["id"],
            "samples": [{"output": "WITHDRAWN_SENTINEL"}],
        },
    )
    service.revoke_asset(a["id"], ADMIN, db)
    app = application()
    app.dependency_overrides[api.edit.dependency] = lambda: ADMIN
    with TestClient(app) as client:
        original = db.get("tasks", task["id"])
        original["quality_verdict"] = {"issues": [{"message": "WITHDRAWN_SENTINEL"}]}
        original["repair_history"] = [{"targets": ["WITHDRAWN_SENTINEL"]}]
        original["result"]["quality_verdict"] = original["quality_verdict"]
        db.put("tasks", original)
        for response in (
            client.post("/api/v2/tasks", json=task_body(p, a)),
            client.post(f"/api/v2/tasks/{task['id']}/cancel"),
        ):
            assert response.status_code in {200, 202}
            assert response.json()["content_withheld"] and "WITHDRAWN_SENTINEL" not in response.text
        for path in [
            "/bootstrap",
            f"/bootstrap?scope=current&project_id={p['id']}",
            "/tasks",
            f"/tasks/{task['id']}",
            f"/tasks/{task['id']}/result",
            "/memories",
            "/improvements",
            "/evaluations",
        ]:
            response = client.get("/api/v2" + path)
            assert response.status_code == 200, response.text
            assert "WITHDRAWN_SENTINEL" not in response.text, path
            if path.endswith("/result"):
                contracts.ResultBundle.model_validate(response.json())
        artifact = db.list("artifacts", p["id"])[0]
        assert client.get(f"/api/v2/artifacts/{artifact['id']}/download").status_code == 409


async def test_stream_revocation_ends_with_event_and_invalid_cursor_rejected(db, monkeypatch):
    p = project(db)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)

    async def denied(request):
        raise HTTPException(401, "Session revoked")

    async def connected():
        return False

    monkeypatch.setattr(auth, "get_current_user", denied)
    response = await api.stream(task["id"], SimpleNamespace(headers={}, is_disconnected=connected), 0, ADMIN)
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == 1 and "event: access_revoked" in frames[0]
    with pytest.raises(service.DomainError) as error:
        await api.stream(task["id"], SimpleNamespace(headers={"last-event-id": "-2"}), 0, ADMIN)
    assert error.value.status == 422


def test_download_missing_file_is_domain_error(db, tmp_path, monkeypatch):
    p = project(db)
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")
    a = service.create_asset(
        {
            "project_id": p["id"],
            "name": "synthetic.txt",
            "modality": "text",
            "content": "Synthetic fixture",
            "source_id": "fixture",
            "provenance": "Synthetic fixture",
        },
        ADMIN,
        db,
        file_data=b"Synthetic fixture",
    )
    from pathlib import Path

    Path(a["content_ref"]).unlink()
    with TestClient(application()) as client:
        response = client.get(f"/api/v2/data-assets/{a['id']}/content")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "input_unavailable"
