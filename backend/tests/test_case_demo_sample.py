from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.casework.team import install
from backend.app.platform import service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_case_domain_executors import _patch_provider
from backend.tests.test_casework_geo_samples import samples
from backend.tests.test_reference_batches import batch
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, OTHER, asset, project, run, task_body
from scripts.register_reference_batches import install as install_reference

db = fixtures.db


async def test_reopen_only_reviewed_project_sample_with_unchanged_authorized_sources(db, monkeypatch):
    p = project(db)
    other_project = project(db)
    install(p["id"], ADMIN, db)
    rows = {"records": samples("G03")}
    a = asset(db, p, rows)
    _patch_provider(monkeypatch, [])
    request = task_body(
        p,
        a,
        task_type="case_analysis",
        case_id="G03",
        case_scope="sample",
        mode="cloud",
        max_cost_cny=1,
        allow_partial=False,
    )
    finished = await run(db, request)
    assert finished["status"] == "succeeded"
    app = application()
    path = "/api/v2/casework/G03/demo-sample"
    params = {"project_id": p["id"]}
    with TestClient(app) as client:
        body = client.get(path, params=params).json()
        assert body["available"] and body["verified_task_id"] == finished["id"]
        assert body["request"]["input_refs"] == [a["id"]]
        assert body["request"]["goal"] == request["goal"]
        assert not {"project_id", "idempotency_key", "result"} & body["request"].keys()
        assert not client.get(path, params={"project_id": other_project["id"]}).json()["available"]
        assert not client.get(path.replace("G03", "T11"), params=params).json()["available"]
        app.dependency_overrides[auth.get_current_user] = lambda: OTHER
        assert client.get(path, params=params).status_code == 403
        app.dependency_overrides[auth.get_current_user] = lambda: ADMIN

        for key, value in [
            ("task_case_id", "another-task"),
            ("source_version", "changed"),
            ("classification", "local"),
            ("status", "revoked"),
        ]:
            changed = db.put("data_assets", {**a, key: value})
            assert client.get(path, params=params).json() == {
                "available": False,
                "verified_task_id": None,
                "request": None,
            }
            a = db.put("data_assets", {**a, "updated_at": changed["updated_at"]})
        for patch in [
            {"status": "partial"},
            {"quality_verdict": {"passed": False}},
            {"result": {"usage": {"request_count": 0}}},
            {"deleted_at": "2026-01-01"},
            {"snapshot": {}},
            {"snapshot": {"inputs": [{}]}},
            {"request": {**finished["request"], "goal": ""}},
            {"request": {**finished["request"], "use_project_knowledge": True}},
            {"request": {**finished["request"], "case_scope": "reference"}},
        ]:
            changed = db.put("tasks", {**deepcopy(finished), **patch})
            assert not client.get(path, params=params).json()["available"]
            finished = db.put("tasks", {**finished, "updated_at": changed["updated_at"]})
        db.put(
            "tasks",
            {
                **finished,
                "id": "newer-run-without-snapshot",
                "idempotency_key": "newer-run",
                "request": {**finished["request"], "idempotency_key": "newer-run"},
                "finished_at": "2099-01-01T00:00:00+00:00",
                "snapshot": {},
            },
        )
        assert client.get(path, params=params).json()["verified_task_id"] == finished["id"]


def test_model_reference_with_missing_batch_is_rejected_before_execution(db, tmp_path, monkeypatch):
    monkeypatch.setenv("HANGZHI_REFERENCE_BATCHES_FILE", str(tmp_path / "unregistered.json"))
    p = project(db)
    a = asset(db, p)
    request = task_body(
        p,
        a,
        task_type="case_analysis",
        case_id="T11",
        mode="cloud",
        case_scope="reference",
        max_cost_cny=1,
        allow_partial=True,
    )
    for preview in (True, False):
        with pytest.raises(service.DomainError) as denied:
            service.create_task(request, ADMIN, db, preflight=preview)
        assert denied.value.code == "reference_inputs_unavailable" and "参考批次" in str(denied.value)
    assert not db.list("tasks", p["id"])


def test_registered_model_reference_preflight_requires_matching_sources(db, tmp_path, monkeypatch):
    _patch_provider(monkeypatch, [])
    path = tmp_path / "registry" / "reference-batches.json"
    monkeypatch.setenv("HANGZHI_REFERENCE_BATCHES_FILE", str(path))
    p = project(db)
    install(p["id"], ADMIN, db)
    records = {"records": samples("G07")}
    source = asset(db, p, records, source_version="1")
    other = asset(db, p, records, source_version="2")
    install_reference(path, [batch(p["id"], records)], False)
    request = task_body(
        p,
        source,
        task_type="case_analysis",
        case_id="G07",
        case_scope="reference",
        mode="cloud",
        max_cost_cny=1,
        allow_partial=False,
    )
    assert service.create_task(request, ADMIN, db, preflight=True)["ready"]
    with pytest.raises(service.DomainError) as changed:
        service.create_task({**request, "input_refs": [other["id"]]}, ADMIN, db, preflight=True)
    assert changed.value.code == "reference_inputs_unavailable"
    assert not db.list("tasks", p["id"])
