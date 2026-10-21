"""Regression cases from the independent domain/concurrency/privacy review."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from backend.app import config, storage
from backend.app import model_gateway as gateway_module
from backend.app.model_gateway import BudgetLedger, ModelGateway
from backend.app.platform import improvement, knowledge, runtime, service
from backend.app.platform.db import ConcurrentWrite, Repository
from backend.app.storage import Store

ADMIN = {"id": "review-admin", "role": "admin"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    result = Repository(f"sqlite:///{(tmp_path / 'review.sqlite3').as_posix()}")
    for module in (service, improvement, knowledge):
        monkeypatch.setattr(module, "repo", result)
    # Intake imports the module gateway independently from runtime. Both must use
    # this test ledger/settings rather than the developer's key and budget file.
    gateway = ModelGateway(BudgetLedger(tmp_path / "billing.sqlite3"))
    monkeypatch.setattr(runtime, "model_gateway", gateway)
    monkeypatch.setattr(gateway_module, "model_gateway", gateway)
    monkeypatch.setattr(storage, "store", Store(tmp_path / "settings.sqlite3"))
    monkeypatch.setattr(config, "get_api_key", lambda: None)
    monkeypatch.setattr(gateway_module, "get_api_key", lambda: None)

    async def unexpected_provider_call(*args, **kwargs):
        pytest.fail("Domain review fixtures must never call a model provider")

    monkeypatch.setattr(gateway, "complete", unexpected_provider_call)
    monkeypatch.setattr(gateway, "run_agent", unexpected_provider_call)
    return result


def fixtures(db, content="source-sentinel " * 500):
    project = service.create_project({"name": "Review fixtures"}, ADMIN, db)
    asset = service.create_asset(
        {
            "project_id": project["id"],
            "name": "Public fixture",
            "modality": "text",
            "media_type": "text/plain",
            "content": content,
            "source_id": "public-review",
            "provenance": "Synthetic test",
            "classification": "public",
        },
        ADMIN,
        db,
    )
    return project, asset


async def sample(db, project, asset, key="one", **changes):
    service.create_task(
        {
            "project_id": project["id"],
            "name": "Review",
            "goal": "Extract sources",
            "task_type": "source_summary",
            "input_refs": [asset["id"]],
            "idempotency_key": key,
            "quality": {"min_length": 3000},
            "max_revisions": 0,
            **changes,
        },
        ADMIN,
        db,
    )
    return await runtime.process_claim(db, db.claim("review-worker"), "review-worker")


async def candidate(db):
    project, asset = fixtures(db)
    samples = [await sample(db, project, asset, key=str(index)) for index in range(2)]
    agent = next(a for a in db.list("agents", project["id"]) if "source_summary" in a["capabilities"])
    body = {
        "project_id": project["id"],
        "name": "Coverage",
        "agent_id": agent["id"],
        "instructions": "",
        "tool_ids": agent["tool_ids"],
        "parameters": {"max_chars": 10000},
        "sample_task_ids": [t["id"] for t in samples],
    }
    return project, asset, agent, body


def test_mode_catalog_rejects_unapproved_modes_and_unsupported_inputs_before_acceptance(db):
    project, asset = fixtures(db)
    body = {
        "project_id": project["id"],
        "name": "Review",
        "goal": "Process",
        "task_type": "structured_transform",
        "input_refs": [asset["id"]],
        "idempotency_key": "mode",
        "mode": "cloud",
        "max_cost_cny": 1,
    }
    with pytest.raises(service.DomainError) as error:
        service.create_task(body, ADMIN, db)
    assert error.value.code == "unsupported_mode"
    with pytest.raises(service.DomainError) as error:
        service.create_task({**body, "task_type": "audio_transcribe", "mode": "local"}, ADMIN, db)
    assert error.value.code in {
        "unsupported_capability",
        "unsupported_mode",
        "unsupported_input",
        "no_compatible_agent",
    }


def test_lost_update_is_rejected_and_parallel_publishes_use_unique_versions(db):
    project, _ = fixtures(db)
    one = db.get("projects", project["id"])
    two = db.get("projects", project["id"])
    db.put("projects", {**one, "description": "first writer"})
    with pytest.raises(ConcurrentWrite):
        db.put("projects", {**two, "description": "stale second writer"})
    assert db.get("projects", project["id"])["description"] == "first writer"
    agent = db.list("agents", project["id"])[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(
            pool.map(
                lambda _: service.agent_action(agent["id"], "publish", ADMIN, db)["current_version"], range(4)
            )
        )
    assert sorted(versions) == [2, 3, 4, 5]
    assert len(db.list("agent_versions", definition_id=agent["id"])) == 5


def test_postgres_lock_query_is_explicit():
    queries = []
    connection = SimpleNamespace(
        execute=lambda query: queries.append(query) or SimpleNamespace(first=lambda: None)
    )
    repository = Repository(engine=SimpleNamespace(dialect=postgresql.dialect()), connection=connection)
    repository.get("agents", "agent-id", for_update=True)
    assert "FOR UPDATE" in str(queries[0].compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("state", ["revoked", "expired"])
async def test_withdrawn_sources_hide_task_derived_content_but_preserve_audit(db, state):
    project, asset = fixtures(db)
    task = await sample(db, project, asset, quality={"min_length": 1})
    assert "source-sentinel" in json.dumps(service.task_detail(task["id"], ADMIN, db))
    if state == "revoked":
        service.revoke_asset(asset["id"], ADMIN, db)
    else:
        current = db.get("data_assets", asset["id"])
        db.put(
            "data_assets", {**current, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        )
    detail = service.task_detail(task["id"], ADMIN, db)
    assert "source-sentinel" not in json.dumps(detail)
    assert detail["content_withheld"] is True
    assert detail["nodes"][0]["output"] is None
    assert detail["artifacts"][0]["content"] is None
    assert detail["events"] and detail["status"] == "succeeded"


async def test_improvement_rejects_multi_node_partial_replay(db):
    project, _, _agent, body = await candidate(db)
    task = db.get("tasks", body["sample_task_ids"][0])
    original = task["snapshot"]["workflow"]["nodes"][0]
    task["snapshot"]["workflow"]["nodes"].append({**original, "id": "second", "depends_on": [original["id"]]})
    db.put("tasks", task)
    with pytest.raises(service.DomainError) as error:
        improvement.create(body, ADMIN, db)
    assert error.value.code == "unsupported_evaluation_scope"
    assert db.list("improvements", project["id"]) == []


async def test_improvement_freeze_detects_changed_input_and_candidate(db):
    _, asset, _, body = await candidate(db)
    created = improvement.create(body, ADMIN, db)
    current = db.get("data_assets", asset["id"])
    db.put("data_assets", {**current, "source_version": "2"})
    with pytest.raises(service.DomainError) as error:
        await improvement.evaluate(created["id"], ADMIN, db)
    assert error.value.code == "input_changed"
    current = db.get("data_assets", asset["id"])
    db.put("data_assets", {**current, "source_version": "1"})
    await improvement.evaluate(created["id"], ADMIN, db)
    current = db.get("improvements", created["id"])
    current["candidate"]["parameters"] = {"max_chars": 100}
    db.put("improvements", current)
    with pytest.raises(service.DomainError) as error:
        improvement.promote(created["id"], ADMIN, db)
    assert error.value.code == "quality_gate_failed"


async def test_withdrawn_evaluation_cannot_promote_and_rollback_keeps_version_history(db):
    _, asset, agent, body = await candidate(db)
    created = improvement.create(body, ADMIN, db)
    await improvement.evaluate(created["id"], ADMIN, db)
    current = db.get("data_assets", asset["id"])
    db.put("data_assets", {**current, "status": "revoked"})
    with pytest.raises(service.DomainError) as error:
        improvement.promote(created["id"], ADMIN, db)
    assert error.value.code == "revoked_input"
    current = db.get("data_assets", asset["id"])
    db.put("data_assets", {**current, "status": "active"})
    improvement.promote(created["id"], ADMIN, db)
    improvement.rollback(created["id"], ADMIN, db)
    published = service.agent_action(agent["id"], "publish", ADMIN, db)
    assert published["current_version"] == 3
    assert sorted(v["version"] for v in db.list("agent_versions", definition_id=agent["id"])) == [1, 2, 3]


def test_memory_inherits_permissions_and_requires_real_parsed_source(db):
    project, asset = fixtures(db, "A verified fact.")
    memory = knowledge.create_memory(
        {
            "project_id": project["id"],
            "content": "verified fact",
            "source_refs": [asset["id"]],
            "retention_days": 100,
        },
        ADMIN,
    )
    assert memory["classification"] == "public"
    assert memory["expires_at"] <= asset["expires_at"]
    assert memory["lineage"][0]["checksum"] == asset["checksum"]
    current = db.get("data_assets", asset["id"])
    db.put("data_assets", {**current, "content": None, "modality": "image"})
    with pytest.raises(service.DomainError) as error:
        knowledge.create_memory(
            {"project_id": project["id"], "content": "None", "source_refs": [asset["id"]]}, ADMIN
        )
    assert error.value.code == "unverified_memory"


async def test_runtime_verifies_frozen_payload_and_composite_model_boundary(db, monkeypatch):
    project, asset = fixtures(db)
    task = service.create_task(
        {
            "project_id": project["id"],
            "name": "Integrity",
            "goal": "Extract",
            "task_type": "source_summary",
            "input_refs": [asset["id"]],
            "idempotency_key": "changed",
        },
        ADMIN,
        db,
    )
    task["snapshot"]["inputs"][0]["content"] = "Changed snapshot with old checksum"
    db.put("tasks", task)
    finished = await runtime.process_claim(db, db.claim("w"), "w")
    assert finished["reason_code"] == "input_changed"
    # Only admission sees a synthetic credential. The model provider remains
    # blocked above; the runtime policy must reject the composite before any call.
    monkeypatch.setattr(config, "get_api_key", lambda: "explicit-synthetic-fixture")
    task = service.create_task(
        {
            "project_id": project["id"],
            "name": "Composite",
            "goal": "Extract",
            "task_type": "source_summary",
            "input_refs": [asset["id"]],
            "idempotency_key": "composite",
            "mode": "cloud",
            "max_cost_cny": 1,
        },
        ADMIN,
        db,
    )
    child = task["snapshot"]["agents"][0]
    parent = {
        **child,
        "id": "parent",
        "agent_id": "parent",
        "structure": "composite",
        "model_policy": "local",
        "members": [{"agent_id": child["id"], "version": child["version"]}],
    }
    task["snapshot"]["agents"].append(parent)
    task["snapshot"]["workflow"]["nodes"][0]["agent_id"] = "parent"
    db.put("tasks", task)
    finished = await runtime.process_claim(db, db.claim("w"), "w")
    assert finished["reason_code"] == "model_denied"
    assert runtime.model_gateway.ledger.usage()["request_count"] == 0


def test_review_cloud_admission_requires_explicit_fixture_credentials(db):
    project, asset = fixtures(db)
    with pytest.raises(service.DomainError) as error:
        service.create_task(
            {
                "project_id": project["id"],
                "name": "Missing credential",
                "goal": "Extract",
                "task_type": "source_summary",
                "input_refs": [asset["id"]],
                "idempotency_key": "keyless",
                "mode": "cloud",
                "max_cost_cny": 1,
            },
            ADMIN,
            db,
        )
    assert error.value.code == "model_not_configured"
    assert db.list("tasks") == []
    assert gateway_module.model_gateway is runtime.model_gateway
    assert runtime.model_gateway.ledger.usage()["request_count"] == 0
