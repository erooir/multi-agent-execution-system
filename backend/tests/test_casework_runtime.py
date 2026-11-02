"""Full intake → immutable roles → Agno worker → independent review → publication."""

from collections import defaultdict
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.casework.engine import audit
from backend.app.casework.monitor import activity
from backend.app.casework.team import catalog, install
from backend.app.platform import runtime, service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_documents import samples as document_samples
from backend.tests.test_casework_geo_samples import assert_expected, media_samples
from backend.tests.test_casework_geo_samples import samples as geo_samples
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = fixtures.db


@pytest.mark.parametrize("code", sorted(catalog()))
async def test_frozen_specialist_team_delivers_each_profile(db, tmp_path, monkeypatch, code):
    p = project(db)
    install(p["id"], ADMIN, db)
    assert install(p["id"], ADMIN, db)["created"] == []
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path / "assets")
    refs = []
    if code.startswith("G"):
        ids = {}
        for label, data in media_samples(code).items():
            a = service.create_asset(
                {
                    "project_id": p["id"],
                    "source_id": label,
                    "name": label,
                    "modality": "image",
                    "media_type": "image/png",
                    "provenance": "synthetic pixels",
                    "content": None,
                },
                ADMIN,
                db,
                file_data=data,
            )
            ids[label] = a["id"]
            refs.append(a["id"])
        groups = {"known": geo_samples(code, ids)}
    else:
        groups = defaultdict(list)
        for row in document_samples(code):
            fields = dict(row["fields"])
            if row["text"]:
                fields["text"] = row["text"]
            groups[row["source_id"]].append(fields)
    for label, records in groups.items():
        a = asset(db, p, {"records": records}, source_id=label, classification="local")
        refs.append(a["id"])
    request = task_body(
        p,
        a,
        task_type="case_analysis",
        case_id=code,
        case_scope="sample",
        input_refs=refs,
        max_revisions=1,
        allow_partial=False,
    )
    preview = service.create_task(request, ADMIN, db, preflight=True)
    assert len(preview["selected_agents"]) == 3 and db.list("tasks", p["id"]) == []
    final = await run(db, request)
    assert final["status"] == "succeeded", (
        code,
        final["reason_code"],
        final.get("quality_verdict"),
        final["result"]["warnings"],
    )
    published = db.list("artifacts", p["id"], task_id=final["id"])
    assert len(published) == 1 and published[0]["type"] == "case_report"
    report = published[0]["content"]
    assert not report["gaps"] and all(c["passed"] for c in report["review"])
    assert set(report["products"]) == set(catalog()[code]["outputs"]) and report["evidence"]
    if code[0] == "G":
        assert_expected(code, report)
    assert {e["source_id"] for e in report["evidence"]} <= set(refs)
    assert len(final["snapshot"]["agents"]) == 3 and final["snapshot"]["case_profile"]["case_id"] == code
    assert all(i["status"] == "released" for i in db.list("instances", p["id"]))
    metrics = activity(p["id"], ADMIN, db)
    assert sum(a["attempts"] for a in metrics["agents"]) == len(db.list("nodes", p["id"]))
    assert not any(a["running"] for a in metrics["agents"])
    assert final["result"]["usage"]["request_count"] == 0
    service.revoke_asset(refs[0], ADMIN, db)
    detail = service.task_detail(final["id"], ADMIN, db)
    assert all(a["content"] is None for a in detail["artifacts"])
    assert all(n["output"] is None for n in detail["nodes"])


def test_mismatched_team_and_cross_project_are_rejected(db):
    p = project(db)
    a = asset(db, p)
    req = task_body(p, a, task_type="case_analysis", case_id="B01")
    with pytest.raises(service.DomainError, match="协作组"):
        service.create_task(req, ADMIN, db, preflight=True)
    install(p["id"], ADMIN, db)
    with pytest.raises(service.DomainError, match="协作组"):
        service.create_task({**req, "workflow_id": f"{p['id']}-case-B02"}, ADMIN, db)
    outsider = {"id": "other", "role": "operator"}
    with pytest.raises(service.DomainError):
        activity(p["id"], outsider, db)
    with pytest.raises(service.DomainError):
        install(p["id"], outsider, db)
    with TestClient(application()) as c:
        assert len(c.get("/api/v2/casework/catalog", params={"project_id": p["id"]}).json()) == 30


def test_review_rejects_forged_source_version_location_and_quote():
    from backend.app.casework.document_cases import analyze

    records = document_samples("B01")
    original = analyze("B01", records)
    for key, value in (
        ("source_version", "other"),
        ("location", {"row": 900}),
        ("quote", "not in the input"),
    ):
        bad = deepcopy(original)
        bad["evidence"][0][key] = value
        assert any(
            c["name"] == "evidence_locations" and not c["passed"] and c["hard"]
            for c in audit("B01", bad, records)
        )
