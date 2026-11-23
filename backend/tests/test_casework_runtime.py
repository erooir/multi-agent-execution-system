"""Full intake → immutable roles → Agno worker → independent review → publication."""

from collections import defaultdict
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.app.casework.engine import audit
from backend.app.casework.monitor import activity
from backend.app.casework.team import assigned_workflow, catalog, install, role_id
from backend.app.platform import runtime, service
from backend.app.platform.api import case_catalog
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_documents import samples as document_samples
from backend.tests.test_casework_geo_samples import assert_expected, media_samples
from backend.tests.test_casework_geo_samples import samples as geo_samples
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = fixtures.db


def test_catalog_binding_batch_matches_every_frozen_profile_with_one_provider_read(monkeypatch):
    from backend.app.casework import provider_versions, team
    from backend.app.casework.executors import registry

    reads = 0

    def providers():
        nonlocal reads
        reads += 1
        return {"tools": {"合成": "v1"}, "packages": {"example": "1.0"}}

    monkeypatch.setattr(provider_versions, "profile", providers)
    expected = {code: team.frozen_profile(code)["signature"] for code in catalog()}
    shared = registry.digest(expected)
    reads = 0
    registry._profile_signature.cache_clear()
    try:
        for _ in range(2):
            with registry.catalog_binding_batch():
                for code, signature in expected.items():
                    assert registry._profile_signature("analyze", code) == signature
                assert registry._profile_signature("prepare", None) == shared
                assert registry._profile_signature("review", None) == shared
            assert reads == 1
    finally:
        registry._profile_signature.cache_clear()


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
        assert len(c.get("/api/v2/casework/catalog", params={"project_id": p["id"]}).json()) == 50


def test_case_catalog_batches_reads_of_published_pins(db, monkeypatch):
    from backend.app.platform import tool_registry

    pid = project(db)["id"]
    codes = sorted(catalog())
    statements = []

    def add_flow(code):
        wid = assigned_workflow(pid, code)
        db.put("workflows", {"id": wid, "project_id": pid, "status": "published", "current_version": 2})
        db.put(
            "workflow_versions",
            {
                "id": wid + ":2",
                "project_id": pid,
                "definition_id": wid,
                "version": 2,
                "nodes": [
                    {"id": stage, "agent_id": role_id(pid, stage, code), "agent_version": 1}
                    for stage in ("prepare", "analyze", "review")
                ],
            },
        )

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def load():
        statements.clear()
        event.listen(db.engine, "before_cursor_execute", observe)
        try:
            rows = case_catalog(pid, ADMIN)
        finally:
            event.remove(db.engine, "before_cursor_execute", observe)
        return rows, len(statements)

    monkeypatch.setattr(
        tool_registry, "capability_readiness", lambda _: pytest.fail("unused readiness probe")
    )
    add_flow(codes[0])
    small, small_count = load()
    assert sum(row["installed"] for row in small) == 1
    for code in codes[1:]:
        add_flow(code)
    full, full_count = load()
    assert len(full) == len(codes) and all(row["installed"] for row in full)
    assert full_count == small_count
    for table in ("workflows", "workflow_versions", "agents", "agent_versions"):
        reads = [sql for sql in statements if f"FROM {table} " in " ".join(sql.split()) + " "]
        assert len(reads) == 1 and f"{table}.id IN (" in reads[0] and f"{table}.project_id =" in reads[0]


def test_case_catalog_detects_stale_binding_and_upgrades_neighbouring_cases_independently(db, monkeypatch):
    from backend.app.casework.executors import registry

    p = project(db)
    pid = p["id"]
    current_binding = registry.binding

    def old_binding(stage, code):
        value = current_binding(stage, code)
        if stage == "prepare":
            value["cloud"]["implementation"] = "0" * 64
        return value

    with monkeypatch.context() as older_deployment:
        older_deployment.setattr(registry, "binding", old_binding)
        for code in ("B05", "B07"):
            install(pid, ADMIN, db, case_id=code)

    aid = role_id(pid, "prepare", "B05")
    first = assigned_workflow(pid, "B05")
    second = assigned_workflow(pid, "B07")
    frozen = db.get("workflow_versions", first + ":1")

    with TestClient(application()) as client:
        def catalog_status():
            response = client.get("/api/v2/casework/catalog", params={"project_id": pid})
            assert response.status_code == 200
            return {row["id"]: row["project_tools_current"] for row in response.json()}

        assert not catalog_status()["B05"]
        response = client.post("/api/v2/casework/upgrade", params={"project_id": pid, "case_id": "B05"})
        assert response.status_code == 200
        assert set(response.json()["updated"]) == {first, aid}
        current = catalog_status()
        assert current["B05"] and not current["B07"]
        assert db.get("workflows", second)["current_version"] == 1
        response = client.post("/api/v2/casework/upgrade", params={"project_id": pid, "case_id": "B07"})
        assert response.status_code == 200
        assert response.json()["updated"] == [second]
        assert catalog_status()["B07"]
    assert db.get("workflow_versions", first + ":1") == frozen
    assert not db.list("tasks", pid)


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
