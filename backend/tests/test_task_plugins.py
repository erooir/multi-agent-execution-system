"""Task plugin contracts over real local decoders and the leased Agno worker."""

import copy
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.app.platform import api, capability_service, runtime, service, task_plugins
from backend.app.platform.contracts import TaskPluginInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = fixtures.db


def plugin(db, p, provider="structured_table", **changes):
    definition = capability_service.create_task_plugin(
        {"project_id": p["id"], "name": "Synthetic task plugin", "provider": provider, **changes}, ADMIN, db
    )
    return capability_service.publish(definition["id"], ADMIN, db)


def image_asset(db, p, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path / "assets")
    buffer = io.BytesIO()
    Image.new("RGB", (13, 7), color="purple").save(buffer, format="PNG")
    return service.create_asset(
        {
            "project_id": p["id"],
            "name": "Synthetic PNG",
            "modality": "image",
            "media_type": "image/png",
            "content": None,
            "source_id": "synthetic:png",
            "provenance": "Generated 13 by 7 test image",
            "classification": "public",
        },
        ADMIN,
        db,
        file_data=buffer.getvalue(),
    )


@pytest.mark.asyncio
async def test_image_plugin_runs_real_png_through_worker(db, tmp_path, monkeypatch):
    p = project(db)
    definition = plugin(db, p, "image_metadata")
    a = image_asset(db, p, tmp_path, monkeypatch)
    task = service.create_task(task_body(p, a, task_type="image_inspect"), ADMIN, db)
    frozen = task["snapshot"]["task_plugin"]
    assert frozen["id"] == definition["id"]
    claim = db.claim("plugin-worker")
    finished = await runtime.process_claim(db, claim, "plugin-worker")
    assert finished["status"] == "succeeded", finished.get("reason_code")
    node = db.list("nodes", p["id"], task_id=task["id"])[0]
    assert node["output"]["content"]["images"][0]["width"] == 13
    assert node["output"]["content"]["images"][0]["height"] == 7
    assert node["output"]["task_plugin"]["checksum"] == frozen["checksum"]
    assert finished["result"]["usage"]["request_count"] == 0
    assert "未进行 OCR" in node["output"]["warnings"][0]


@pytest.mark.asyncio
async def test_plugin_revoked_after_intake_blocks_worker(db):
    p = project(db)
    definition = plugin(db, p)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)
    capability_service.action(definition["id"], "revoke", ADMIN, db)
    claim = db.claim("plugin-worker")
    finished = await runtime.process_claim(db, claim, "plugin-worker")
    assert finished["status"] == "failed"
    assert finished["reason_code"] == "plugin_revoked"
    assert not db.list("artifacts", p["id"], task_id=task["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,task_type,content",
    [
        ("source_digest", "source_summary", "Public synthetic source sentence."),
        ("structured_table", "structured_transform", [{"name": " One ", "value": 4}]),
    ],
)
async def test_text_and_table_plugins_share_real_execution_and_evidence(db, provider, task_type, content):
    p = project(db)
    plugin(db, p, provider)
    a = asset(
        db,
        p,
        content,
        **({"modality": "text", "media_type": "text/plain"} if isinstance(content, str) else {}),
    )
    task = service.create_task(task_body(p, a, task_type=task_type), ADMIN, db)
    claim = db.claim("plugin-worker")
    finished = await runtime.process_claim(db, claim, "plugin-worker")
    assert finished["status"] == "succeeded", finished.get("reason_code")
    node = db.list("nodes", p["id"], task_id=task["id"])[0]
    assert node["output"]["source_refs"] == [a["id"]]
    if provider == "source_digest":
        assert node["output"]["content"]["fragments"][0]["text"] == content
    else:
        assert node["output"]["content"]["records"] == [{"name": "One", "value": 4}]


def test_plugin_input_limits_are_enforced_before_execution(db):
    p = project(db)
    definition = plugin(db, p, max_input_bytes=1)
    a = asset(db, p)
    with pytest.raises(service.DomainError) as error:
        service.create_task(task_body(p, a, plugin_id=definition["id"]), ADMIN, db)
    assert error.value.code == "plugin_input_too_large"
    # An incompatible optional plugin must not prevent the established task path.
    ordinary = service.create_task(task_body(p, a), ADMIN, db)
    assert "task_plugin" not in ordinary["snapshot"]


@pytest.mark.asyncio
async def test_file_backed_dag_merge_cannot_bypass_plugin_input_limit(db, tmp_path, monkeypatch):
    from backend.app.platform.workflow_improvement import replay_sample

    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path / "assets")
    p = project(db)
    records = [{"value": "abcdefghijklmnopqrst"}]
    original = json.dumps(records, ensure_ascii=False).encode()
    limit = len(json.dumps({"records": records}, ensure_ascii=False).encode())
    assert len(original) < limit < len(json.dumps({"records": records * 2}, ensure_ascii=False).encode())
    definition = plugin(db, p, max_input_bytes=limit)
    source = service.create_asset(
        {
            "project_id": p["id"],
            "name": "Synthetic JSON file",
            "modality": "structured",
            "media_type": "application/json",
            "content": records,
            "source_id": "synthetic:file-merge",
            "provenance": "Public-safe DAG size regression",
        },
        ADMIN,
        db,
        file_data=original,
    )
    agent = next(
        a
        for a in db.list("agents", p["id"])
        if a["structure"] == "atomic" and "structured_transform" in a["capabilities"]
    )
    flow = service.save_workflow(
        {
            "project_id": p["id"],
            "name": "Synthetic fan-out and merge",
            "task_type": "structured_transform",
            "nodes": [
                {
                    "id": node_id,
                    "label": node_id,
                    "agent_id": agent["id"],
                    "depends_on": ["first", "second"] if node_id == "merge" else [],
                    "config": {},
                }
                for node_id in ("first", "second", "merge")
            ],
        },
        ADMIN,
        repository=db,
    )
    service.publish_workflow(flow["id"], ADMIN, db)
    task = service.create_task(
        task_body(p, source, workflow_id=flow["id"], plugin_id=definition["id"], max_revisions=0),
        ADMIN,
        db,
    )
    claim = db.claim("plugin-worker")
    finished = await runtime.process_claim(db, claim, "plugin-worker")
    assert finished["status"] == "failed"
    assert finished["reason_code"] == "plugin_input_too_large"
    nodes = {node["node_id"]: node for node in db.list("nodes", p["id"], task_id=task["id"])}
    assert nodes["first"]["status"] == nodes["second"]["status"] == "succeeded"
    assert not db.list("artifacts", p["id"], task_id=task["id"])
    # Evaluation must apply the identical limit to the derived records, too.
    with pytest.raises(task_plugins.TaskPluginError) as error:
        replay_sample(
            {"request": task["request"], "snapshot": task["snapshot"]}, task["snapshot"]["workflow"]
        )
    assert error.value.code == "plugin_input_too_large"


def test_plugin_versions_are_immutable_and_disable_preserves_frozen_run(db):
    p = project(db)
    definition = plugin(db, p)
    a = asset(db, p)
    task = service.create_task(task_body(p, a), ADMIN, db)
    frozen = task["snapshot"]["task_plugin"]
    capability_service.publish(definition["id"], ADMIN, db)
    capability_service.action(definition["id"], "disable", ADMIN, db)
    task_plugins.validate_frozen(frozen, db, p["id"])
    second = service.create_task(task_body(p, a, idempotency_key="two"), ADMIN, db)
    assert "task_plugin" not in second["snapshot"]
    with pytest.raises(service.DomainError) as error:
        capability_service.action(definition["id"], "destroy", ADMIN, db)
    assert error.value.code == "reference_protected"
    version = db.get("capability_versions", f"{definition['id']}:1")
    with pytest.raises(ValueError, match="immutable"):
        db.put("capability_versions", {**version, "spec": {}})


def test_plugin_input_contract_and_permission_cannot_be_extended(db):
    p = project(db)
    with pytest.raises(service.DomainError) as error:
        capability_service.create_task_plugin(
            {
                "project_id": p["id"],
                "name": "Invalid",
                "provider": "image_metadata",
                "input_media_types": ["audio/wav"],
            },
            ADMIN,
            db,
        )
    assert error.value.code == "plugin_input_contract_invalid"
    with pytest.raises(ValidationError):
        TaskPluginInput(project_id=p["id"], name="No code", provider="image_metadata", command="python")
    with pytest.raises(service.DomainError) as error:
        capability_service.create_task_plugin(
            {"project_id": p["id"], "name": "Denied", "provider": "source_digest"},
            {"id": "operator", "role": "operator"},
            db,
        )
    assert error.value.code == "permission_denied"


def test_explicit_plugin_selection_checks_project_and_type(db):
    p, other = project(db), project(db)
    definition = plugin(db, other)
    a = asset(db, p)
    with pytest.raises(service.DomainError) as error:
        service.create_task(task_body(p, a, plugin_id=definition["id"]), ADMIN, db)
    assert error.value.code == "plugin_permission_denied"
    image = plugin(db, p, "image_metadata")
    with pytest.raises(service.DomainError) as error:
        service.create_task(task_body(p, a, plugin_id=image["id"]), ADMIN, db)
    assert error.value.code == "plugin_task_mismatch"


def test_plugin_fingerprint_and_snapshot_tampering_are_detected(db, monkeypatch):
    p = project(db)
    plugin(db, p)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    frozen = task["snapshot"]["task_plugin"]
    corrupt = copy.deepcopy(frozen)
    corrupt["spec"]["model_calls"] = True
    with pytest.raises(task_plugins.TaskPluginError) as error:
        task_plugins.validate_frozen(corrupt, db, p["id"])
    assert error.value.code == "plugin_version_changed"
    original = task_plugins.provider_contract
    monkeypatch.setattr(
        task_plugins,
        "provider_contract",
        lambda name: {
            **original(name),
            "implementation_checksum": "changed",
        },
    )
    with pytest.raises(task_plugins.TaskPluginError) as error:
        task_plugins.validate_frozen(frozen, db, p["id"])
    assert error.value.code == "plugin_version_changed"


def test_plugin_output_contract_and_size_are_hard_gates(db):
    p = project(db)
    plugin(db, p, max_output_bytes=100)
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    frozen = task["snapshot"]["task_plugin"]
    inputs = task["snapshot"]["inputs"]
    with pytest.raises(task_plugins.TaskPluginError) as error:
        task_plugins.execute(frozen, "structured_transform", inputs, {}, runtime.execute_local)
    assert error.value.code == "plugin_output_too_large"

    def invalid_output(*_):
        return {"content": {"records": "invented"}, "source_refs": [inputs[0]["id"]]}

    with pytest.raises(task_plugins.TaskPluginError) as error:
        task_plugins.execute(frozen, "structured_transform", inputs, {}, invalid_output)
    assert error.value.code == "plugin_output_contract_invalid"


def test_no_plugin_keeps_historical_task_and_idempotency(db):
    p = project(db)
    a = asset(db, p)
    body = task_body(p, a)
    task = service.create_task(body, ADMIN, db)
    assert "task_plugin" not in task["snapshot"]
    plugin(db, p)
    assert service.create_task(body, ADMIN, db)["id"] == task["id"]
    assert "task_plugin" not in db.get("tasks", task["id"])["snapshot"]
    second = service.create_task(task_body(p, a, idempotency_key="new"), ADMIN, db)
    assert second["snapshot"]["task_plugin"]


def test_plugin_api_publishes_catalog_manifest(db, monkeypatch):
    p = project(db)
    monkeypatch.setattr(api, "repo", db)
    with TestClient(application()) as client:
        response = client.post(
            "/api/v2/capability-definitions/task-plugins",
            json={
                "project_id": p["id"],
                "name": "Public PNG inspector",
                "provider": "image_metadata",
            },
        )
        assert response.status_code == 201, response.text
        definition = response.json()
        assert client.post(f"/api/v2/capability-definitions/{definition['id']}/publish").status_code == 200
        catalog = client.get("/api/v2/capabilities", params={"project_id": p["id"]}).json()
        assert catalog["task_plugins"][0]["spec"]["input_modalities"] == ["image"]
        assert catalog["task_plugins"][0]["spec"]["model_calls"] is False
