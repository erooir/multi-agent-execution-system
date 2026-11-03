"""Deleting terminal runs hides workbench entries without erasing billing or references."""

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import api, service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, OTHER, asset, project, run, task_body

db = fixtures.db


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed", "cancelled"])
def test_delete_terminal_task_is_idempotent_and_hides_all_workbench_views(db, status):
    p = project(db)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)
    db.put("tasks", {**task, "status": status})
    other = service.create_task(task_body(p, a, idempotency_key="other"), ADMIN, db)
    with TestClient(application()) as client:
        assert client.delete(f"/api/v2/tasks/{task['id']}").status_code == 204
        assert client.delete(f"/api/v2/tasks/{task['id']}").status_code == 204
        assert [t["id"] for t in client.get("/api/v2/tasks").json()] == [other["id"]]
        for scope in ("all", "current"):
            data = client.get("/api/v2/bootstrap", params={"scope": scope}).json()
            assert [t["id"] for t in data["tasks"]] == [other["id"]]
            assert data["stats"]["task_count"] == 1
        for suffix in ("", "/result", "/events", "/tool-calls"):
            assert client.get(f"/api/v2/tasks/{task['id']}{suffix}").status_code == 404
        assert client.post("/api/v2/tasks", json=task_body(p, a)).status_code == 409
    retained = db.get("tasks", task["id"])
    assert retained["deleted_by"] == ADMIN["id"] and retained["deleted_at"]
    assert retained["status"] == status and retained["snapshot"] == task["snapshot"]
    assert len(db.list("events", task_id=task["id"], type="task.deleted")) == 1
    assert len(db.list("audits", action="task.deleted")) == 1


@pytest.mark.parametrize("status", ["accepted", "planning", "queued", "running", "evaluating", "revising"])
def test_reject_deletion_of_active_tasks(db, status):
    p = project(db)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    db.put("tasks", {**task, "status": status})
    with TestClient(application()) as client:
        response = client.delete(f"/api/v2/tasks/{task['id']}")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "task_not_terminal"
    assert not db.get("tasks", task["id"]).get("deleted_at")


def test_delete_requires_project_write_permission_even_after_deleted(db):
    p = project(db)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    service.cancel_task(task["id"], ADMIN, db)
    app = application()
    with TestClient(app) as client:
        for user in (OTHER, {"id": "reader", "role": "reviewer"}, {"id": "asker", "role": "requester"}):
            app.dependency_overrides[auth.get_current_user] = lambda user=user: user
            assert client.delete(f"/api/v2/tasks/{task['id']}").status_code == 403
        app.dependency_overrides[auth.get_current_user] = lambda: ADMIN
        assert client.delete(f"/api/v2/tasks/{task['id']}").status_code == 204
        app.dependency_overrides[auth.get_current_user] = lambda: OTHER
        assert client.delete(f"/api/v2/tasks/{task['id']}").status_code == 403


@pytest.mark.parametrize("held", ["lease", "instance"])
def test_terminal_task_must_finish_worker_cleanup(db, held):
    p = project(db)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    task = db.put(
        "tasks", {**task, "status": "succeeded", "lease_owner": "worker" if held == "lease" else None}
    )
    if held == "instance":
        db.put("instances", {"task_id": task["id"], "project_id": p["id"], "status": "running"})
    with pytest.raises(service.DomainError) as error:
        service.delete_task(task["id"], ADMIN, db)
    assert error.value.code == "task_cleanup_pending"


async def test_delete_preserves_real_output_references_and_budget(db):
    p = project(db)
    task = await run(db, task_body(p, asset(db, p)))
    detail = service.task_detail(task["id"], ADMIN, db)
    before = {kind: db.list(kind, task_id=task["id"]) for kind in ("nodes", "instances", "artifacts")}
    question = db.put("questions", {"project_id": p["id"], "task_id": task["id"], "status": "succeeded"})
    ledger = api.model_gateway.ledger
    request = ledger.reserve(0.1, "synthetic", task["id"])
    ledger.settle(request, {"prompt_tokens": 1000, "completion_tokens": 10})
    budget = ledger.budget()
    service.delete_task(task["id"], ADMIN, db)
    assert ledger.budget() == budget
    assert db.get("questions", question["id"]) == question
    assert db.get("tasks", task["id"])["result"] == task["result"]
    for kind, records in before.items():
        assert db.list(kind, task_id=task["id"]) == records
    assert detail["artifacts"]
