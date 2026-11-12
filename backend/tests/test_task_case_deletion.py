"""Task portal deletion preserves immutable history and never restarts paid work."""

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import agent_candidates, runtime, service, task_cases
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_agent_candidates import prepare
from backend.tests.test_task_cases import REQUESTER, create, setup
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, task_body

db = fixtures.db


def portal(user=REQUESTER):
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: user
    return app


@pytest.mark.parametrize("state", ["awaiting_data", "ready", "succeeded", "partial", "failed", "cancelled"])
def test_delete_hides_case_and_prevents_replay_without_destroying_record(db, monkeypatch, state):
    p = setup(db, monkeypatch)
    case = create(db, p)
    db.put("task_cases", {**db.get("task_cases", case["id"]), "status": state})
    other = create(db, p, idempotency_key="other")
    token = task_cases.create_ingest_token(case["id"], 1, REQUESTER, db)
    url = f"/api/v2/task-cases/{case['id']}"
    with TestClient(portal()) as client:
        assert client.delete(url).status_code == 204
        assert client.delete(url).status_code == 204
        assert [
            item["id"] for item in client.get("/api/v2/task-cases", params={"project_id": p["id"]}).json()
        ] == [other["id"]]
        for suffix in (
            "",
            "/runs/retained",
            "/result/download",
            "/sources/retained/download",
            "/artifacts/retained/download",
            "/sandbox-runs/retained/outputs/0",
        ):
            assert client.get(url + suffix).status_code == 404
        for suffix, body in (
            ("/start", {}),
            ("/cancel", {}),
            ("/ingest-tokens", {}),
            ("/candidates", {}),
            ("/submissions", {"input_refs": [], "idempotency_key": "new"}),
        ):
            assert client.post(url + suffix, json=body).status_code == 404
        assert client.delete(url + f"/ingest-tokens/{token['id']}").status_code == 404
        assert (
            client.post(
                url + "/upload",
                files={"file": ("data.txt", b"public", "text/plain")},
                data={"idempotency_key": "upload"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                url + "/ingest/upload",
                files={"file": ("data.txt", b"public", "text/plain")},
                data={"idempotency_key": "upload"},
                headers={"Authorization": "Bearer " + token["token"]},
            ).status_code
            == 403
        )
    with pytest.raises(service.DomainError) as error:
        create(db, p)
    assert error.value.code == "task_case_deleted"
    with pytest.raises(service.DomainError) as error:
        task_cases.ingest_identity(case["id"], token["token"], db)
    assert error.value.code == "permission_denied"
    retained = db.get("task_cases", case["id"])
    assert retained["status"] == state and retained["deleted_by"] == REQUESTER["id"]
    assert retained["deleted_at"] and all(t["revoked"] for t in retained["ingest_tokens"])
    assert len(db.list("audits", action="task_case.deleted")) == 1
    assert [c["id"] for c in task_cases.history(p["id"], ADMIN, db)] == [other["id"]]


def test_owner_and_project_permissions_apply_to_repeated_deletion(db, monkeypatch):
    p = setup(db, monkeypatch)
    case = create(db, p)
    outsider = {"id": "another", "role": "operator"}
    db.put(
        "projects", {**db.get("projects", p["id"]), "members": [ADMIN["id"], REQUESTER["id"], outsider["id"]]}
    )
    for deleted in (False, True):
        if deleted:
            task_cases.delete(case["id"], REQUESTER, db)
        with TestClient(portal(outsider)) as client:
            assert client.delete(f"/api/v2/task-cases/{case['id']}").status_code == 404
        with TestClient(portal({**REQUESTER, "role": "reviewer"})) as client:
            assert client.delete(f"/api/v2/task-cases/{case['id']}").status_code == 403
    db.put("projects", {**db.get("projects", p["id"]), "members": [ADMIN["id"]]})
    with TestClient(portal()) as client:
        assert client.delete(f"/api/v2/task-cases/{case['id']}").status_code == 403
    with TestClient(portal(ADMIN)) as client:
        assert client.delete(f"/api/v2/task-cases/{case['id']}").status_code == 204


@pytest.mark.parametrize("phase", ["requirements", "execution", "evaluation"])
def test_active_runs_including_evaluation_replays_block_deletion(db, monkeypatch, phase):
    p = setup(db, monkeypatch)
    case = create(db, p)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db, task_case_id=case["id"])
    task = db.put("tasks", {**task, "task_case_phase": "requirements" if phase == "requirements" else None})
    if phase != "evaluation":
        db.put(
            "task_cases",
            {**db.get("task_cases", case["id"]), "task_id": task["id"], "task_ids": [task["id"]]},
        )
    with TestClient(portal()) as client:
        response = client.delete(f"/api/v2/task-cases/{case['id']}")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "task_case_not_terminal"
    assert not db.get("task_cases", case["id"]).get("deleted_at")
    service.cancel_task(task["id"], ADMIN, db)
    task_cases.delete(case["id"], REQUESTER, db)


@pytest.mark.parametrize("held", ["lease", "instance"])
def test_finished_task_must_release_executor_before_deletion(db, monkeypatch, held):
    p = setup(db, monkeypatch)
    case = create(db, p)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db, task_case_id=case["id"])
    db.put("tasks", {**task, "status": "succeeded", "lease_owner": "worker" if held == "lease" else None})
    if held == "instance":
        db.put("instances", {"task_id": task["id"], "project_id": p["id"], "status": "running"})
    with pytest.raises(service.DomainError) as error:
        task_cases.delete(case["id"], REQUESTER, db)
    assert error.value.code == "task_case_cleanup_pending"


async def test_deletion_keeps_actual_outputs_candidate_references_and_every_budget_row(db, monkeypatch):
    p = setup(db, monkeypatch)
    case = create(db, p)
    source = asset(db, p)
    task = service.create_task(task_body(p, source), ADMIN, db, task_case_id=case["id"])
    task = await runtime.process_claim(db, db.claim("delete-test"), "delete-test")
    assert task["status"] == "succeeded"
    db.put(
        "task_cases",
        {
            **db.get("task_cases", case["id"]),
            "input_refs": [source["id"]],
            "task_id": task["id"],
            "task_ids": [task["id"]],
        },
    )
    candidate = db.put(
        "agent_candidates",
        {
            "project_id": p["id"],
            "status": "candidate",
            "source_task_case_id": case["id"],
            "source_task_id": task["id"],
        },
    )
    before = {
        kind: db.list(kind)
        for kind in (
            "tasks",
            "nodes",
            "instances",
            "artifacts",
            "data_assets",
            "plan_versions",
            "review_reports",
            "agent_candidates",
        )
    }
    ledger = runtime.model_gateway.ledger
    settled = ledger.reserve(0.1, "synthetic", task["id"], task_limit=2)
    ledger.settle(settled, {"prompt_tokens": 1000, "completion_tokens": 10})
    uncertain = ledger.reserve(0.2, "synthetic-uncertain", task["id"])
    ledger.uncertain(uncertain)
    ledger.reserve(0.3, "synthetic-reserved", task["id"])
    with ledger.connect() as connection:
        charges_before = [dict(row) for row in connection.execute("SELECT * FROM charges")]
        limits_before = [dict(row) for row in connection.execute("SELECT * FROM task_limits")]
    task_cases.delete(case["id"], REQUESTER, db)
    for kind, records in before.items():
        assert db.list(kind) == records
    assert db.get("agent_candidates", candidate["id"])["source_task_case_id"] == case["id"]
    with ledger.connect() as connection:
        assert [dict(row) for row in connection.execute("SELECT * FROM charges")] == charges_before
        assert [dict(row) for row in connection.execute("SELECT * FROM task_limits")] == limits_before
    assert before["artifacts"] and db.get("tasks", task["id"])["result"] == task["result"]


async def test_deleted_sample_cannot_admit_new_paid_candidate_replays(db, monkeypatch):
    _, cases = await prepare(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    task_cases.delete(cases[1]["id"], REQUESTER, db)
    tasks_before = db.list("tasks")
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(candidate["id"], [c["id"] for c in cases[1:]], 4, ADMIN, db)
    assert error.value.code == "not_found"
    assert db.list("tasks") == tasks_before
    assert db.list("evaluations") == []
    assert db.get("agent_candidates", candidate["id"])["status"] == "candidate"
