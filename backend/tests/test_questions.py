"""The ordinary question path executes real work without exposing or granting management access."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import questions, runtime, service
from backend.app.platform.contracts import QuestionInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_documents import samples as document_samples
from backend.tests.test_casework_geo_samples import samples
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project

db = fixtures.db
REQUESTER = {"id": "customer", "role": "requester", "username": "customer", "enabled": True}
QUESTION = "请计算北半球9月海冰范围相对基线的差异和排名。"


def setup_question(db):
    p = project(db)
    db.put("projects", {**p, "members": [ADMIN["id"], REQUESTER["id"]]})
    with auth.store.transaction() as tx:
        auth._insert_record(tx, "users", dict(REQUESTER))
    data = asset(db, p, {"records": samples("G03")}, name="逐年海冰观测", classification="local")
    return p["id"], data


def request(pid, question=QUESTION, key="shared"):
    return QuestionInput(project_id=pid, question=question, idempotency_key=key)


async def test_real_worker_question_only_owner_can_read_and_revocation_hides_every_derived_field(db):
    pid, source = setup_question(db)
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        asked = client.post("/api/v2/questions", json=request(pid).model_dump())
        assert asked.status_code == 202, asked.text
        inbox = asked.json()
        assert inbox["status"] == "accepted"
        assert len([a for a in db.list("agents", pid) if a.get("case_role")]) == 3
        repeat = client.post("/api/v2/questions", json=request(pid).model_dump()).json()
        assert repeat["id"] == inbox["id"] and len(db.list("tasks", pid)) == 1
        t = await runtime.process_claim(db, db.claim("questions-worker"), "questions-worker")
        assert t["status"] == "succeeded"
        result = client.get("/api/v2/questions/" + inbox["id"]).json()
        assert result["status"] == "succeeded" and "均值" in result["answer"] and "排名" in result["answer"]
        assert "缩小样本" not in result["answer"] and result["tables"] and result["citations"]
        assert t["request"]["case_scope"] == "project"
        assert (
            not {"tool_calls", "snapshot", "nodes", "request", "task_id", "case_id", "strategy"}
            & result.keys()
        )
        assert source["classification"] == "local" and any(
            c["source"] == source["name"] and c["location"] for c in result["citations"]
        )
        assert not any(source["id"] in item["source"] for item in result["citations"])
        assert client.get("/api/v2/tasks/" + t["id"]).status_code == 403
        assert client.get("/api/v2/questions", params={"project_id": pid}).json()[0]["id"] == inbox["id"]
        assert (
            client.post("/api/v2/questions", json={**request(pid).model_dump(), "mode": "cloud"}).status_code
            == 422
        )
        app.dependency_overrides[auth.get_current_user] = lambda: ADMIN
        assert client.get("/api/v2/questions/" + inbox["id"]).status_code == 404
        assert not client.get("/api/v2/questions", params={"project_id": pid}).json()
        app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
        service.revoke_asset(source["id"], ADMIN, db)
        withheld = client.get("/api/v2/questions/" + inbox["id"]).json()
        assert withheld["content_withheld"] and not any(
            withheld[k] for k in ("answer", "citations", "tables", "maps")
        )


async def test_natural_questions_build_then_reuse_roles_and_repair_failed_clause_pairing(db):
    pid, _ = setup_question(db)
    asset(
        db,
        {"id": pid},
        [
            {"side": "before", "clause_id": "1", "topic": "交付期限", "clause_text": "交付记录。"},
            {"side": "after", "clause_id": "3", "topic": "交付期限", "clause_text": "交付记录并注明日期。"},
        ],
        name="技术标准草案与定稿",
        classification="local",
    )
    versions = None
    for index, prompt in enumerate(
        ("技术标准草案和定稿中交付期限的条款有什么变化？", "这份技术标准的定稿对交付期限作了什么修改？")
    ):
        asked = questions.submit(request(pid, prompt, "compare-" + str(index)), REQUESTER, db)
        assert asked["status"] == "accepted"
        task = await runtime.process_claim(db, db.claim("question-repair"), "question-repair")
        assert task["status"] == "succeeded" and task["request"]["case_id"] == "T06"
        assert task["revision"] == 1
        assert len(db.list("nodes", pid, task_id=task["id"])) == 5
        history = service.task_detail(task["id"], ADMIN, db)["repair_history"]
        assert any(item["reused_stages"] == ["prepare"] for item in history)
        result = questions.get(asked["id"], REQUESTER, db)
        assert "注明日期" in result["answer"] and result["citations"]
        current = {item["id"]: item["checksum"] for item in db.list("agent_versions", pid)}
        if versions is not None:
            assert current == versions
        versions = current


def test_concurrent_submission_and_no_material_gap_do_not_fabricate_work(db):
    pid, _ = setup_question(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        items = list(pool.map(lambda _: questions.submit(request(pid), REQUESTER, db), range(2)))
    assert items[0]["id"] == items[1]["id"] and len(db.list("tasks", pid)) == 1
    with pytest.raises(service.DomainError, match="不同问题"):
        questions.submit(request(pid, "另一个问题"), REQUESTER, db)
    other = project(db)
    with pytest.raises(service.DomainError, match="权限"):
        questions.submit(request(other["id"]), REQUESTER, db)
    unknown = questions.submit(request(pid, "制作一份蛋糕的购买清单", "other"), REQUESTER, db)
    assert unknown["status"] == "failed" and unknown["missing"] and len(db.list("tasks", pid)) == 1


async def test_requester_losing_project_permission_fences_running_task(db):
    pid, _ = setup_question(db)
    q = questions.submit(request(pid), REQUESTER, db)
    assert q["status"] == "accepted"
    p = db.get("projects", pid)
    db.put("projects", {**p, "members": [ADMIN["id"]]})
    t = await runtime.process_claim(db, db.claim("worker"), "worker")
    assert t["status"] == "failed" and t["reason_code"] == "permission_denied"
    with pytest.raises(service.DomainError, match="权限"):
        questions.get(q["id"], REQUESTER, db)


def test_semantic_fallback_never_sends_local_material_or_uses_unapproved_egress(db, monkeypatch):
    pid, _ = setup_question(db)
    local = asset(db, {"id": pid}, ["私有说明"], classification="local")
    plan = {
        "status": "semantic",
        "input_refs": [local["id"]],
        "focus": "claim_check",
        "gaps": [],
        "topics": [],
    }
    monkeypatch.setattr(questions.intake_plan, "plan_question", lambda *a: plan)
    monkeypatch.delenv("HANGZHI_QUESTION_PUBLIC_EGRESS", raising=False)
    assert questions.submit(request(pid), REQUESTER, db)["status"] == "failed"
    monkeypatch.setenv("HANGZHI_QUESTION_PUBLIC_EGRESS", "true")
    assert questions.submit(request(pid, key="new"), REQUESTER, db)["status"] == "failed"
    public = asset(db, {"id": pid}, [{"reading": "public"}], classification="public")
    plan["input_refs"] = [public["id"]]
    monkeypatch.setenv("HANGZHI_OFFLINE", "true")
    assert questions.submit(request(pid, key="offline"), REQUESTER, db)["status"] == "failed"
    assert db.list("tasks", pid) == []


def test_allowed_semantic_job_uses_only_verified_public_terms_and_never_the_private_question(db, monkeypatch):
    from backend.app import config
    from backend.app.model_gateway import model_gateway

    pid, _ = setup_question(db)
    src = asset(
        db,
        {"id": pid},
        "The public reading calls this optical thickness.",
        modality="text",
        media_type="text/plain",
    )
    monkeypatch.setenv("HANGZHI_QUESTION_PUBLIC_EGRESS", "true")
    monkeypatch.setenv("HANGZHI_OFFLINE", "false")
    monkeypatch.setattr(config, "get_api_key", lambda: "only-synthetic-no-transport")
    monkeypatch.setattr(model_gateway, "budget", lambda: {"remaining_cny": 1.0})
    monkeypatch.setattr(
        questions.intake_plan,
        "plan_question",
        lambda *args: {
            "status": "semantic",
            "focus": "scientific_terms",
            "input_refs": [src["id"]],
            "topics": ["optical thickness", "private marker"],
            "gaps": [],
        },
    )
    q = questions.submit(request(pid, "private marker: 此处的术语是什么意思？"), REQUESTER, db)
    assert q["status"] == "accepted"
    [t] = db.list("tasks", pid)
    assert t["request"]["mode"] == "cloud" and t["request"]["max_cost_cny"] == 0.8
    assert "private marker" not in t["request"]["goal"] and "optical thickness" in t["request"]["goal"]
    assert t["semantic_evidence"]["source_topics"] == ["optical thickness"]
    assert t["authorization"]["id"] == REQUESTER["id"] and t["question_id"] == q["id"]


async def test_wrong_requested_year_never_delivers_another_year_as_the_answer(db):
    pid, _ = setup_question(db)
    q = questions.submit(request(pid, QUESTION + "请回答2019年。"), REQUESTER, db)
    if q["status"] == "accepted":
        await runtime.process_claim(db, db.claim("worker"), "worker")
    answer = questions.get(q["id"], REQUESTER, db)
    assert answer["status"] in ("partial", "failed") and answer["missing"] and not answer.get("answer")


@pytest.mark.parametrize("interval", ["2000–2002年的基线均值是多少", "2000–2004年全期的海冰范围和排名"])
async def test_requested_baseline_or_full_window_matches_actual_product_without_naming_target_year(
    db, interval
):
    pid, _ = setup_question(db)
    q = questions.submit(request(pid, "北半球9月海冰，" + interval + "？"), REQUESTER, db)
    assert q["status"] == "accepted", q
    await runtime.process_claim(db, db.claim("worker"), "worker")
    answer = questions.get(q["id"], REQUESTER, db)
    assert answer["status"] == "succeeded" and "均值为12" in answer["answer"]
    assert questions.history(pid, REQUESTER, db)[0]["status"] == answer["status"]


def test_all_event_answers_show_actual_claims_and_missing_business_fields():
    from backend.app.casework.document_cases import analyze
    from backend.app.platform.question_events import project_events

    cases = [
        ("B01", "各地记录增加还是减少", ["减少 8 起", "增加 6 起"]),
        ("B02", "什么时间生效，有哪些执行条件", ["承诺开放人道运输通道", "2024-07-04"]),
        ("B03", "每张照片对应的馆藏出处", ["已知标注显示直翼轮廓", "型号组A"]),
        ("B04", "谁提问、谁回答，时间分别是什么", ["主持人提问", "嘉宾回答", "00:00:02.100"]),
        ("B05", "死亡人数、受伤人数合计，首尾是否整月", ["死亡5人、受伤11人", "部分月份", "整月"]),
        ("B06", "条款表述和俄文术语对应", ["记录版本"]),
        ("B07", "两期指标能直接比较吗", ["2024-06-15", "变化 +24"]),
        ("B08", "法院是否后来接受调查材料的说法", ["司法材料早于调查材料", "限定试验"]),
        (
            "B09",
            "档案的四类日期",
            ["事发 2024-05-01", "形成记录 2024-05-01", "公开 2024-05-10", "解密 2025-05-10"],
        ),
        ("B10", "简报与调查结论的变化", ["简报记载", "调查材料记载", "可比事项的结论未发生变化"]),
    ]
    for case, prompt, expected in cases:
        result = project_events(case, analyze(case, document_samples(case))["products"], prompt)
        assert all(s in result["answer"] for s in expected), result
        assert result["tables"] and "…" not in result["answer"]
        if case in ("B02", "B06"):
            assert result["missing"]


async def test_business_gap_makes_list_and_detail_partial_without_bulk_products(db):
    pid, _ = setup_question(db)
    p = {"id": pid}
    records = document_samples("B06")
    srcs = {r["source_id"] for r in records}
    for src in srcs:
        asset(
            db,
            p,
            {"records": [r["fields"] for r in records if r["source_id"] == src]},
            name="历史条约的版本资料" + src,
        )
    q = questions.submit(request(pid, "历史条约的两版条文逐条有什么不同，俄文名词如何对应？"), REQUESTER, db)
    assert q["status"] == "accepted"
    await runtime.process_claim(db, db.claim("worker"), "worker")
    detail = questions.get(q["id"], REQUESTER, db)
    head = questions.history(pid, REQUESTER, db)[0]
    assert detail["status"] == head["status"] == "partial" and detail["tables"]
    assert any("术语" in item for item in detail["missing"])
    assert all(not head.get(k) for k in ("answer", "tables", "maps", "citations"))


@pytest.mark.parametrize(
    ("code", "prompt", "expected"),
    [
        ("T06", "请逐条核对技术标准草案和定稿，监督条款的表述有什么变化？", "并记录版本"),
        ("B05", "逐月核对平民伤亡总数，列出死亡和受伤的合计。", "死亡5人、受伤11人"),
    ],
)
async def test_regular_business_answers_contain_the_actual_words_or_numbers(db, code, prompt, expected):
    p = project(db)
    pid = p["id"]
    db.put("projects", {**p, "members": [ADMIN["id"], REQUESTER["id"]]})
    with auth.store.transaction() as tx:
        auth._insert_record(tx, "users", dict(REQUESTER))
    records = document_samples(code)
    for src in {r["source_id"] for r in records}:
        asset(
            db,
            p,
            {"records": [r["fields"] for r in records if r["source_id"] == src]},
            name=("标准草案与定稿" if code == "T06" else "月度平民伤亡统计") + src,
        )
    q = questions.submit(request(pid, prompt), REQUESTER, db)
    assert q["status"] == "accepted", q
    t = await runtime.process_claim(db, db.claim("worker"), "worker")
    assert t["status"] in {"succeeded", "partial"}
    a = questions.get(q["id"], REQUESTER, db)
    assert expected in a["answer"], a
    assert a["tables"] and a["citations"]


async def test_incomplete_project_data_keeps_a_business_gap(db):
    pid, src = setup_question(db)
    service.revoke_asset(src["id"], ADMIN, db)
    project_data = samples("G03")
    asset(db, {"id": pid}, {"records": project_data[:-1]}, name="不完整海冰序列")
    q = questions.submit(request(pid), REQUESTER, db)
    assert q["status"] == "accepted"
    await runtime.process_claim(db, db.claim("worker"), "worker")
    a = questions.get(q["id"], REQUESTER, db)
    assert a["status"] in ("partial", "failed") and a["missing"]
