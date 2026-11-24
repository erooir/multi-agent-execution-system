"""Local workspace and requester entry points honor the project's active case edition."""

import pytest

from backend.app.casework import editions
from backend.app.platform import edition_intake, questions, service, task_cases
from backend.app.platform.contracts import QuestionInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import REQUESTER, create, setup
from backend.tests.test_v2_platform import asset
from scripts.publish_case_edition import archive_replaced_assets

db = fixtures.db


def test_republication_keeps_archived_originals_and_user_uploads(db, monkeypatch):
    p = setup(db, monkeypatch)
    uploaded = asset(db, p, name="公告.csv", source_id="published:G03:公告.csv")
    versions = []
    for checksum in ("first", "later"):
        key = service.digest([p["id"], "G03", uploaded["name"], uploaded["source_version"], checksum])[:32]
        versions.append(db.put("data_assets", {**uploaded, "id": key, "checksum": checksum}))
    for current, prior in (versions, versions[::-1]):
        with db.transaction() as tx:
            archive_replaced_assets(tx, p["id"], {"G03": {"input_refs": [current["id"]]}})
        assert db.get("data_assets", current["id"])["case_library_role"] == "primary"
        retired = db.get("data_assets", prior["id"])
        assert (retired["case_library_role"], retired["status"]) == ("supplementary", "active")
        assert db.get("data_assets", uploaded["id"]).get("case_library_role") == uploaded.get(
            "case_library_role"
        )


def published(db, monkeypatch):
    project = setup(db, monkeypatch)
    source = asset(db, project, {"records": [{"date": "2024-12", "total": 22}]}, source_id="release:table")
    case = {
        "id": "G03",
        "title": "冬季月度出行统计",
        "goal": "核对冬季月度出行。",
        "scope": "只涵盖发布的统计表。",
        "files": ["月度统计.csv"],
        "deliverables": "出行统计和来源",
        "completion": "逐项核对",
        "input_refs": [source["id"]],
        "checksums": {source["id"]: source["checksum"]},
    }
    project = db.put(
        "projects", {**db.get("projects", project["id"]), "scenario_edition": {"cases": {"G03": case}}}
    )
    with db.transaction() as tx:
        editions.provision(tx, project["id"], case)
    return project, source, case


def test_workbench_uses_only_published_originals_and_no_legacy_team(db, monkeypatch):
    p, source, case = published(db, monkeypatch)
    old = asset(db, p, name="G03 旧版海冰样本", source_id="legacy-ice")
    ready = create(db, p, title=case["title"], goal="请生成本次月度简报", public_egress_authorized=False)
    assert (
        ready["status"] == "ready" and ready["input_refs"] == [source["id"]] and not db.list("tasks", p["id"])
    )
    with pytest.raises(service.DomainError, match="授权"):
        task_cases.start(ready["id"], REQUESTER, db)
    with pytest.raises(service.DomainError, match="配套原件"):
        task_cases.submit(ready["id"], {"input_refs": [old["id"]], "idempotency_key": "wrong"}, REQUESTER, db)
    task_cases.submit(
        ready["id"],
        {"input_refs": [], "public_egress_authorized": True, "idempotency_key": "allow"},
        REQUESTER,
        db,
    )
    task_cases.start(ready["id"], REQUESTER, db)
    (task,) = db.list("tasks", p["id"])
    assert task["snapshot"]["case_profile"]["edition"] == editions.EDITION
    assert task["request"]["mode"] == "cloud" and task["request"]["input_refs"] == [source["id"]]
    assert task["snapshot"]["workflow"]["definition_id"] == editions.workflow_id(p["id"], "G03")
    assert not any(k["id"].endswith("case-G03") for k in db.list("workflows", p["id"]))


def test_declared_sources_and_model_setting_are_honored(db, monkeypatch):
    p, _, _ = published(db, monkeypatch)
    with pytest.raises(service.DomainError, match="指定来源"):
        create(db, p, goal="请运行 G03。", source_ids=["other"])
    with pytest.raises(service.DomainError, match="模型调用"):
        create(db, p, goal="请运行 G03。", mode="local")
    ordinary = create(
        db, p, goal="根据OHCHR报告复算每月平民死亡受伤人数并核对总数", idempotency_key="generic"
    )
    assert not db.get("task_cases", ordinary["id"]).get("matched_case_id")
    assert edition_intake.match(p, "请使用 AG03X 用于这份报告") is None
    with pytest.raises(service.DomainError, match="不在"):
        create(db, p, goal="请运行 G16。", idempotency_key="retired")


def test_authorized_workbench_submits_directly_and_retry_keeps_original_run(db, monkeypatch):
    p, source, _ = published(db, monkeypatch)
    payload = {
        "goal": "请运行当前项目的 G03。",
        "source_ids": [source["source_id"]],
        "public_egress_authorized": True,
        "idempotency_key": "published-run",
    }
    first = create(db, p, **payload)
    second = create(db, p, **payload)
    jobs = db.list("tasks", p["id"])
    assert second["id"] == first["id"] and len(jobs) == 1
    assert jobs[0]["request"]["task_type"] == "case_analysis"
    assert jobs[0]["snapshot"]["case_profile"]["edition"] == editions.EDITION


def test_edition_questions_gate_model_and_render_new_findings(db, monkeypatch):
    p, src, case = published(db, monkeypatch)

    def ask(goal, key):
        return questions.submit(
            QuestionInput(project_id=p["id"], question=goal, idempotency_key=key), REQUESTER, db
        )

    monkeypatch.setenv("HANGZHI_QUESTION_PUBLIC_EGRESS", "false")
    denied = ask("请执行 G03", "deny")
    assert denied["outcome"] == "unavailable" and not db.list("tasks", p["id"])
    monkeypatch.setenv("HANGZHI_QUESTION_PUBLIC_EGRESS", "true")
    new = ask("请按照" + case["title"] + "生成简报", "start")
    (task,) = db.list("tasks", p["id"])
    assert task["request"]["mode"] == "cloud" and task["request"]["input_refs"] == [src["id"]]
    report = {
        "edition": editions.EDITION,
        "case_id": case["id"],
        "evidence": [
            {
                "id": "E1",
                "source_id": src["id"],
                "source_version": src["source_version"],
                "quote": "12 月共 22 条出行记录",
                "location": {"原件位置": "第 2 行"},
            }
        ],
        "products": {
            "专题分析": [{"heading": "十二月出行", "text": "表中列出 22 条。", "evidence_ids": ["E1"]}]
        },
    }
    db.put(
        "artifacts",
        {
            "id": "test-artifact",
            "task_id": task["id"],
            "project_id": p["id"],
            "type": "case_report",
            "content": report,
            "checksum": service.digest(report),
            "quality_status": "passed",
        },
    )
    db.put("tasks", {**task, "status": "succeeded"})
    delivery = questions.get(new["id"], REQUESTER, db)
    assert delivery["status"] == "succeeded" and "22" in delivery["answer"]
    assert delivery["citations"][0]["location"] == "第 2 行"
    db.put("data_assets", {**db.get("data_assets", src["id"]), "status": "revoked"})
    assert questions.get(new["id"], REQUESTER, db)["content_withheld"]


def test_edition_open_question_preserves_semantic_planning(db, monkeypatch):
    p, _, _ = published(db, monkeypatch)
    article = asset(
        db,
        p,
        name="社区月报",
        source_id="public:monthly",
        modality="text",
        media_type="text/plain",
        content="社区月报记载本月开放了三个图书阅读点。",
    )
    plan = questions._edition_plan(db, p, "仅依据社区月报解释其中的图书阅读点有哪些？")
    assert plan["status"] == "semantic" and plan["input_refs"] == [article["id"]]
