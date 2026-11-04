"""Project names, authorization and database-serialized first-use initialization."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import service
from backend.app.platform.db import Repository
from backend.tests import test_dameng as dm_fixtures
from backend.tests import test_v2_platform as platform_fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = platform_fixtures.db
real_dm = dm_fixtures.real_dm


def assert_concurrent_bootstrap(repository, monkeypatch):
    actor = {"id": "synthetic-bootstrap-user", "role": "operator"}
    barrier = Barrier(6)
    original = Repository.list

    def read_before_any_creation(self, kind, *args, **kwargs):
        rows = original(self, kind, *args, **kwargs)
        if self.connection is None and kind == "projects":
            # Every caller must observe the initial empty state before entering
            # its own transaction/connection, as concurrent API processes can.
            barrier.wait(timeout=20)
        return rows

    with monkeypatch.context() as context:
        context.setattr(Repository, "list", read_before_any_creation)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(
                    lambda _: service.bootstrap_projects(actor, Repository(engine=repository.engine)),
                    range(6),
                )
            )
    ids = {rows[0]["id"] for rows in results}
    assert len(ids) == 1 and all(len(rows) == 1 for rows in results)
    current = service.bootstrap_projects(actor, repository)
    assert {p["id"] for p in current} == ids
    assert len(repository.list("projects", owner_id=actor["id"])) == 1
    assert len(repository.list("audits", current[0]["id"], action="project.created")) == 1
    agent_count = len(repository.list("agents", current[0]["id"]))
    assert agent_count > 0
    assert len(repository.list("agent_versions", current[0]["id"])) == agent_count
    assert repository.list("tasks", current[0]["id"]) == []


def test_first_use_is_serialized_and_repeatable(db, monkeypatch):
    assert_concurrent_bootstrap(db, monkeypatch)


def test_dm8_first_use_is_serialized(real_dm, monkeypatch):
    assert_concurrent_bootstrap(real_dm, monkeypatch)


def test_bootstrap_keeps_existing_duplicate_projects_and_respects_membership(db):
    first, second = project(db), project(db)
    assert {p["id"] for p in service.bootstrap_projects(ADMIN, db)} == {first["id"], second["id"]}
    for role in ("reviewer", "requester"):
        actor = {"id": "unassigned", "role": role}
        assert service.bootstrap_projects(actor, db) == []
    assert len(db.list("projects")) == 2


def test_rename_preserves_project_identity_data_and_versions(db):
    p = project(db)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)
    before = {
        kind: db.list(kind, p["id"])
        for kind in ("agents", "agent_versions", "workflows", "workflow_versions", "data_assets", "tasks")
    }
    with TestClient(application()) as client:
        path = f"/api/v2/projects/{p['id']}/name"
        response = client.put(path, json={"name": "  新项目名称  "})
        assert response.status_code == 200
        renamed = response.json()
        assert renamed["name"] == "新项目名称"
        assert {k: v for k, v in renamed.items() if k not in {"name", "updated_at"}} == {
            k: v for k, v in p.items() if k not in {"name", "updated_at"}
        }
        result = client.get("/api/v2/bootstrap", params={"project_id": p["id"]}).json()
        assert result["projects"][0]["name"] == "新项目名称"
        assert result["current_project_id"] == p["id"]
        assert client.put(path, json={"name": "新项目名称"}).status_code == 200
    assert db.get("tasks", task["id"]) == task
    assert before == {kind: db.list(kind, p["id"]) for kind in before}
    audits = db.list("audits", p["id"], action="project.renamed")
    assert len(audits) == 1
    assert audits[0]["actor"] == ADMIN["id"]
    assert audits[0]["detail"] == {"previous_name": p["name"], "name": "新项目名称"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": " \t"},
        {"name": "x" * 101},
        {"name": "合法", "members": ["intruder"]},
        {"name": "合法", "description": "unexpected change"},
    ],
)
def test_rename_validates_only_the_name(db, body):
    p = project(db)
    with TestClient(application()) as client:
        assert client.put(f"/api/v2/projects/{p['id']}/name", json=body).status_code == 422
    assert db.get("projects", p["id"]) == p


@pytest.mark.parametrize(
    "role,member,expected",
    [
        ("operator", True, 200),
        ("operator", False, 403),
        ("reviewer", True, 403),
        ("requester", True, 403),
        ("admin", False, 200),
    ],
)
def test_rename_enforces_project_write_permission(db, role, member, expected):
    p = project(db)
    actor = {"id": "rename-actor", "role": role}
    if member:
        p = db.put("projects", {**p, "members": [*p["members"], actor["id"]]})
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: actor
    with TestClient(app) as client:
        assert client.put(f"/api/v2/projects/{p['id']}/name", json={"name": "修改后"}).status_code == expected
    assert db.get("projects", p["id"])["name"] == ("修改后" if expected == 200 else p["name"])


def test_rename_missing_project_returns_not_found(db):
    with TestClient(application()) as client:
        assert client.put("/api/v2/projects/missing/name", json={"name": "名字"}).status_code == 404
