import io
import json

import pytest
from pypdf import PdfWriter

from backend.app.casework.team import install
from backend.app.platform import runtime, service
from backend.app.platform.contracts import FILE_INDEX_MEDIA_TYPE
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_case_domain_executors import _patch_provider
from backend.tests.test_casework_geo_samples import samples
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = fixtures.db


async def test_late_case_source_runs_with_explicit_input_and_frozen_project_scope(db, monkeypatch):
    p = project(db)
    install(p["id"], ADMIN, db)
    rounds = []
    _patch_provider(monkeypatch, rounds)
    policy, *observations = samples("G03")
    explicit = asset(db, p, {"records": [policy]}, name="计算约定", source_id="window-policy")
    with db.transaction() as tx:
        for i in range(1000):
            asset(
                tx,
                p,
                "图书借阅汇总。",
                name=f"馆藏月报 {i}",
                source_id=f"library-{i}",
                modality="text",
                media_type="text/plain",
            )

    name = "北极九月海冰范围年度基线与异常"
    data = {"records": observations}
    target = asset(db, p, data, name=name, source_id="G03-M03")
    canary = "synthetic-ineligible-source-marker"
    decoy = {"records": [{**row, "note": canary} for row in observations]}
    foreign = asset(db, project(db), decoy, name=name, source_id="G03-M03")
    local = asset(db, p, decoy, name=name, source_id="G03-private", classification="local")
    withdrawn = asset(db, p, decoy, name=name, source_id="G03-withdrawn")
    service.revoke_asset(withdrawn["id"], ADMIN, db)
    expired = asset(db, p, decoy, name=name, source_id="G03-expired")
    db.put("data_assets", {**expired, "expires_at": "2000-01-01T00:00:00+00:00"})
    task_private = asset(db, p, decoy, name=name, source_id="G03-task-only")
    db.put("data_assets", {**task_private, "task_case_id": "another-task"})
    index = asset(
        db,
        p,
        {"filename": f"{canary}.csv"},
        name=name,
        source_id="G03-index",
        media_type=FILE_INDEX_MEDIA_TYPE,
    )
    other_case = asset(db, p, decoy, name=name, source_id="G04-M02")
    excluded = {a["id"] for a in (foreign, local, withdrawn, expired, task_private, index, other_case)}
    request = task_body(
        p,
        explicit,
        name="年度海冰范围基线与异常",
        goal=f"用 {target['source_id']} 核算北极九月海冰范围。",
        task_type="case_analysis",
        case_id="G03",
        case_scope="sample",
        mode="cloud",
        use_project_knowledge=True,
        max_cost_cny=2,
        allow_partial=False,
    )

    preview = service.create_task(request, ADMIN, db, preflight=True)
    assert preview["ready"] and not db.list("tasks", p["id"])
    scope = preview["knowledge_scope"]
    assert scope["eligible"] >= 1002
    assert scope["matched"] == scope["selected"] == 2
    assert not rounds
    task = service.create_task(request, ADMIN, db)
    frozen = task["snapshot"]
    assert frozen["knowledge_scope"] == scope
    assert [item["id"] for item in frozen["inputs"]] == [explicit["id"], target["id"]]

    asset(db, p, data, name=name, source_id="G03-M04")
    replay_preview = service.create_task(request, ADMIN, db, preflight=True)
    assert replay_preview["existing_task_id"] == task["id"] and replay_preview["knowledge_scope"] == scope
    replay = service.create_task(request, ADMIN, db)
    assert replay["id"] == task["id"] and replay["snapshot"] == frozen
    assert len(db.list("tasks", p["id"])) == 1
    claim = db.claim("knowledge-test")
    assert claim["id"] == task["id"]
    finished = await runtime.process_claim(db, claim, "knowledge-test")
    assert finished["status"] == "succeeded", finished["result"]
    report = db.list("artifacts", p["id"], task_id=task["id"])[0]["content"]
    assert report["products"]["anomaly"]["difference"] == -3
    assert report["products"]["anomaly"]["ascending_rank"] == 1
    assert {e["source_id"] for e in report["evidence"]} == {explicit["id"], target["id"]}
    assert not report["gaps"] and all(c["passed"] for c in report["review"])
    assert rounds and finished["result"]["usage"]["request_count"] == len(rounds)
    prompts = json.dumps(rounds, ensure_ascii=False)
    assert canary not in prompts and all(source_id not in prompts for source_id in excluded)
    assert db.get("tasks", task["id"])["snapshot"] == frozen


def test_text_task_ranks_before_its_input_limit_and_excludes_unsupported_files(db, tmp_path, monkeypatch):
    p = project(db)
    _patch_provider(monkeypatch, [])
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")

    def text_asset(source_id, title, content):
        return asset(
            db, p, content, name=title, source_id=source_id, modality="text", media_type="text/plain"
        )

    explicit = text_asset("instructions", "格式说明", "按观测日期排列。")
    query = "estuary salinity transect conductivity"
    request = task_body(
        p,
        explicit,
        name="Estuary survey",
        goal=query,
        task_type="text_analysis",
        input_refs=[explicit["id"], explicit["id"]],
        mode="cloud",
        max_cost_cny=1,
        use_project_knowledge=True,
    )
    unrelated = {
        **request,
        "idempotency_key": "small-project",
        "input_refs": [],
        "name": "Incunabula",
        "goal": "Incunabula orthography ligatures",
    }
    small = service.create_task(unrelated, ADMIN, db)
    assert [r["id"] for r in small["snapshot"]["inputs"]] == [explicit["id"]]
    assert small["snapshot"]["knowledge_scope"] == {"eligible": 1, "matched": 1, "selected": 1}

    weak = [text_asset(f"bulletin-{i}", f"Monthly bulletin {i}", "Estuary.") for i in range(60)]
    titled = text_asset("survey-catalog", query, "测量记录甲。")
    body_only = text_asset("field-sheet", "Field sheet", query)
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    raw = io.BytesIO()
    writer.write(raw)
    incompatible = service.create_asset(
        {
            "project_id": p["id"],
            "name": query,
            "source_id": "survey-pdf",
            "modality": "text",
            "media_type": "application/pdf",
            "content": None,
            "classification": "public",
            "provenance": "Synthetic PDF",
        },
        ADMIN,
        db,
        file_data=raw.getvalue(),
    )
    preview = service.create_task(request, ADMIN, db, preflight=True)
    assert preview["ready"]
    scope = preview["knowledge_scope"]
    assert scope == {"eligible": 63, "matched": 63, "selected": 50}
    task = service.create_task(request, ADMIN, db)
    selected = [v["id"] for v in task["snapshot"]["inputs"]]
    assert task["snapshot"]["knowledge_scope"] == scope
    assert selected[0] == explicit["id"] and len(set(selected)) == len(selected) == 50
    assert {titled["id"], body_only["id"]} <= set(selected)
    assert incompatible["id"] not in selected
    assert len({a["id"] for a in weak} & set(selected)) == 47

    with pytest.raises(service.DomainError) as error:
        service.create_task({**unrelated, "idempotency_key": "large-project"}, ADMIN, db, preflight=True)
    assert error.value.code == "insufficient_data"


def test_case_keeps_pinned_revision_and_only_adds_named_unambiguous_supplement(db, monkeypatch):
    p = project(db)
    install(p["id"], ADMIN, db)
    rounds = []
    _patch_provider(monkeypatch, rounds)

    def receipt(source, version, text, role="supplementary"):
        row = asset(
            db,
            p,
            text,
            name="合成资料",
            source_id=source,
            source_version=version,
            modality="text",
            media_type="text/plain",
        )
        return db.put("data_assets", {**row, "case_library_role": role})

    primary = receipt("G03-M03", "v1", "原统计口径", "primary")
    receipt("G03-M03", "v2", "新统计口径", "primary")
    receipt("G03-A01", "z", "旧说明")
    latest = receipt("G03-A01", "a", "修订说明")
    receipt("G03-A02", "v1", "第一版注释")
    receipt("G03-A02", "v2", "第二版注释甲")
    receipt("G03-A02", "v2", "第二版注释乙")

    body = task_body(
        p,
        primary,
        name="海冰观测",
        goal="执行 G03 海冰观测",
        task_type="case_analysis",
        case_id="G03",
        case_scope="sample",
        mode="cloud",
        use_project_knowledge=True,
        max_cost_cny=1,
    )
    automatic = service.create_task(body, ADMIN, db)
    assert [r["id"] for r in automatic["snapshot"]["inputs"]] == [primary["id"]]
    named = service.create_task(
        {**body, "idempotency_key": "named-guides", "goal": "执行 G03，结合 G03-A01 与 G03-A02 的说明。"},
        ADMIN,
        db,
    )
    assert [r["id"] for r in named["snapshot"]["inputs"]] == [primary["id"], latest["id"]]
    assert named["snapshot"]["knowledge_scope"] == {"eligible": 7, "matched": 2, "selected": 2}
    assert not rounds
