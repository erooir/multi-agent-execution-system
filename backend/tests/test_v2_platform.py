"""New product contracts and trust boundaries over real transactional storage."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.main import domain_error
from backend.app.model_gateway import BudgetLedger, ModelGateway
from backend.app.platform import api, improvement, knowledge, runtime, service
from backend.app.platform.db import LeaseLost, Repository
from backend.app.storage import Store

ADMIN = {"id": "test_admin", "role": "admin"}
OTHER = {"id": "other_user", "role": "operator"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = Repository(f"sqlite:///{(tmp_path / 'platform.sqlite3').as_posix()}")
    for module in (service, api, knowledge, improvement):
        monkeypatch.setattr(module, "repo", db)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    gateway = ModelGateway(ledger)
    monkeypatch.setattr(api, "model_gateway", gateway)
    monkeypatch.setattr(runtime, "model_gateway", gateway)
    identity = Store(tmp_path / "identities.sqlite3")
    monkeypatch.setattr(auth, "store", identity)
    monkeypatch.setenv("HANGZHI_ADMIN_PASSWORD", "test-admin-password")
    return db


def project(db, user=ADMIN):
    return service.create_project(
        {"name": "Synthetic acceptance", "description": "Public-safe fixture"}, user, db
    )


def asset(db, p, content=None, **changes):
    data = {
        "project_id": p["id"],
        "name": "Public fixture",
        "modality": "structured",
        "media_type": "application/json",
        "content": [{"name": " Alpha ", "value": 2}, {"name": "Beta", "value": 1}]
        if content is None
        else content,
        "source_id": "synthetic:acceptance",
        "provenance": "Small synthetic input",
        "classification": "public",
    }
    return service.create_asset({**data, **changes}, ADMIN, db)


def task_body(p, a, **changes):
    return {
        "project_id": p["id"],
        "name": "Acceptance run",
        "goal": "Normalize supplied records",
        "task_type": "structured_transform",
        "input_refs": [a["id"]],
        "idempotency_key": "one",
        **changes,
    }


async def run(db, body):
    task = service.create_task(body, ADMIN, db)
    claim = db.claim("test-worker")
    assert claim["id"] == task["id"]
    return await runtime.process_claim(db, claim, "test-worker")


async def test_real_intake_to_agno_result_and_project_isolation(db):
    p = project(db)
    a = asset(db, p)
    finished = await run(db, task_body(p, a))
    assert finished["status"] == "succeeded"
    detail = service.task_detail(finished["id"], ADMIN, db)
    assert detail["artifacts"][0]["content"]["records"][0]["name"] == "Alpha"
    assert detail["snapshot"]["inputs"][0]["checksum"] == a["checksum"]
    assert "content" not in detail["snapshot"]["inputs"][0]
    assert detail["result"]["usage"]["request_count"] == 0
    with pytest.raises(service.DomainError, match="权限"):
        service.task_detail(finished["id"], OTHER, db)
    assert service.visible("tasks", OTHER, db) == []


def test_idempotency_conflict_and_concurrent_submit(db):
    p = project(db)
    a = asset(db, p)
    body = task_body(p, a)
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = list(executor.map(lambda _: service.create_task(body, ADMIN, db), range(4)))
    assert len({t["id"] for t in tasks}) == 1
    assert len(db.list("tasks")) == 1
    with pytest.raises(service.DomainError) as error:
        service.create_task({**body, "goal": "Different goal"}, ADMIN, db)
    assert error.value.code == "idempotency_conflict"


def test_leases_are_exclusive_and_stale_worker_cannot_write(db):
    p = project(db)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda n: db.claim(f"worker-{n}"), range(4)))
    assert sum(c is not None for c in claims) == 1
    old = next(c for c in claims if c)
    with db.transaction() as tx:
        tx.put("tasks", {**old, "lease_until": time.time() - 1})
    new = db.claim("new-owner")
    assert new["lease_token"] == old["lease_token"] + 1
    with pytest.raises(LeaseLost), db.transaction() as tx:
        tx.fenced(task["id"], old["lease_owner"], old["lease_token"])
        tx.put("tasks", {**old, "status": "succeeded"})
    assert db.get("tasks", task["id"])["lease_owner"] == "new-owner"


def test_published_versions_frozen_and_reference_protected(db):
    p = project(db)
    original = db.list("agents", p["id"])[0]
    before = db.get("agent_versions", f"{original['id']}:1")
    body = {
        k: original[k]
        for k in (
            "project_id",
            "name",
            "description",
            "purpose",
            "structure",
            "capabilities",
            "modalities",
            "tool_ids",
            "instructions",
            "model_policy",
            "members",
        )
    }
    service.save_agent({**body, "name": "Changed draft"}, ADMIN, original["id"], db)
    assert db.get("agent_versions", before["id"]) == before
    published = service.agent_action(original["id"], "publish", ADMIN, db)
    assert published["current_version"] == 2
    with pytest.raises(ValueError, match="immutable"):
        db.put("agent_versions", {**before, "instructions": "mutate history"})
    with pytest.raises(service.DomainError) as error:
        service.agent_action(original["id"], "destroy", ADMIN, db)
    assert error.value.code == "reference_protected"


def test_disabled_assets_reject_new_tasks_but_snapshot_remains(db):
    p = project(db)
    a = asset(db, p)
    wf = next(w for w in db.list("workflows", p["id"]) if w["task_type"] == "structured_transform")
    task = service.create_task(task_body(p, a, workflow_id=wf["id"]), ADMIN, db)
    agent_id = wf["nodes"][0]["agent_id"]
    service.agent_action(agent_id, "disable", ADMIN, db)
    with pytest.raises(service.DomainError):
        service.create_task(task_body(p, a, workflow_id=wf["id"], idempotency_key="new"), ADMIN, db)
    assert db.get("tasks", task["id"])["snapshot"]["agents"][0]["version"] == 1


def test_data_egress_expiry_revoke_and_memory(db):
    p = project(db)
    a = asset(db, p, "Exact source fact.", modality="text", media_type="text/plain", classification="local")
    with pytest.raises(service.DomainError) as error:
        service.create_task(
            task_body(p, a, task_type="source_summary", mode="cloud", max_cost_cny=1), ADMIN, db
        )
    assert error.value.code == "data_egress_blocked"
    memory = knowledge.create_memory(
        {"project_id": p["id"], "content": "Exact source", "source_refs": [a["id"]], "retention_days": 7},
        ADMIN,
    )
    with pytest.raises(service.DomainError, match="未经验证"):
        knowledge.create_memory(
            {
                "project_id": p["id"],
                "content": "Invented fact",
                "source_refs": [a["id"]],
                "retention_days": 7,
            },
            ADMIN,
        )
    service.revoke_asset(a["id"], ADMIN, db)
    assert db.get("memories", memory["id"])["status"] == "revoked"
    with pytest.raises(service.DomainError) as error:
        service.create_task(task_body(p, a), ADMIN, db)
    assert error.value.code == "revoked_input"
    with pytest.raises(service.DomainError):
        asset(db, p, expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())


def test_composite_cycles_permissions_and_graph_validation(db):
    p = project(db)
    a = next(a for a in db.list("agents", p["id"]) if "structured_transform" in a["capabilities"])
    spec = {
        "project_id": p["id"],
        "name": "Composite",
        "capabilities": ["structured_transform"],
        "structure": "composite",
        "members": [{"agent_id": a["id"], "version": 1}],
        "tool_ids": [],
    }
    with pytest.raises(service.DomainError, match="权限|授权"):
        service.save_agent(spec, ADMIN, repository=db)
    composite = service.save_agent({**spec, "tool_ids": ["structured_transform"]}, ADMIN, repository=db)
    service.agent_action(composite["id"], "publish", ADMIN, db)
    with pytest.raises(service.DomainError, match="循环"):
        service.save_agent(
            {
                **spec,
                "tool_ids": ["structured_transform"],
                "members": [{"agent_id": composite["id"], "version": 1}],
            },
            ADMIN,
            composite["id"],
            db,
        )
    with pytest.raises(service.DomainError, match="循环|依赖"):
        service.save_workflow(
            {
                "project_id": p["id"],
                "name": "Bad DAG",
                "task_type": "structured_transform",
                "nodes": [{"id": "a", "label": "A", "agent_id": a["id"], "depends_on": ["a"]}],
            },
            ADMIN,
            repository=db,
        )


async def test_measured_improvement_reject_promote_and_rollback(db):
    p = project(db)
    a = asset(db, p, "Public fixture sentence. " * 200, modality="text", media_type="text/plain")
    samples = []
    for index in range(2):
        finished = await run(
            db,
            task_body(
                p,
                a,
                task_type="source_summary",
                idempotency_key=f"sample-{index}",
                quality={"min_length": 3000},
                max_revisions=0,
            ),
        )
        assert finished["status"] == "failed"
        samples.append(finished["id"])
    agent = next(a for a in db.list("agents", p["id"]) if "source_summary" in a["capabilities"])
    base = {
        "project_id": p["id"],
        "name": "Coverage experiment",
        "agent_id": agent["id"],
        "instructions": agent["instructions"],
        "tool_ids": agent["tool_ids"],
        "sample_task_ids": samples,
    }
    bad = improvement.create({**base, "parameters": {"max_chars": 100}}, ADMIN, db)
    evaluated = await improvement.evaluate(bad["id"], ADMIN, db)
    assert evaluated["status"] == "rejected"
    with pytest.raises(service.DomainError):
        improvement.promote(bad["id"], ADMIN, db)
    good = improvement.create({**base, "parameters": {"max_chars": 10000}}, ADMIN, db)
    evaluated = await improvement.evaluate(good["id"], ADMIN, db)
    assert evaluated["verdict"]["candidate_passed"] == 2
    assert evaluated["status"] == "evaluated"
    promoted = improvement.promote(good["id"], ADMIN, db)
    assert db.get("agents", agent["id"])["current_version"] == 2
    improvement.rollback(promoted["id"], ADMIN, db)
    assert db.get("agents", agent["id"])["current_version"] == 1
    assert db.get("agent_versions", f"{agent['id']}:2") is not None
    assert db.get("tasks", samples[0])["snapshot"]["agents"][0]["version"] == 1


def test_api_enforces_auth_schema_and_revocation(db):
    app = FastAPI()
    app.add_exception_handler(service.DomainError, domain_error)
    app.include_router(api.identity)
    app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get("/api/v2/bootstrap").status_code == 401
        assert (
            client.post(
                "/api/auth/register", json={"username": "client_user", "password": "test-password-123"}
            ).status_code
            == 201
        )
        boot = client.get("/api/v2/bootstrap")
        assert boot.status_code == 200, boot.text
        p = boot.json()["projects"][0]
        assert boot.json()["stats"]["success_rate"] is None
        assert client.post("/api/v2/projects", json={"name": "valid", "role": "admin"}).status_code == 422
        assert (
            client.put(f"/api/v2/projects/{p['id']}/members", json={"members": ["other"]}).status_code == 403
        )
        response = client.post(
            "/api/v2/data-assets",
            json={
                "project_id": p["id"],
                "name": "text",
                "modality": "text",
                "content": "Known fact",
                "source_id": "fixture",
                "provenance": "Synthetic",
            },
        )
        assert response.status_code == 201, response.text
        a = response.json()
        assert "content_ref" not in a and "content" not in a
        assert client.get(f"/api/v2/data-assets/{a['id']}/content").status_code == 200
        client.post(f"/api/v2/data-assets/{a['id']}/revoke")
        assert client.get(f"/api/v2/data-assets/{a['id']}/content").status_code == 409
        assert (
            client.post(
                "/api/v2/projects", json={"name": "blocked"}, headers={"Origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )
