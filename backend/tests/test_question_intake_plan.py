from datetime import UTC, datetime, timedelta

import pytest

from backend.app.casework import intake_plan as intake
from backend.tests.test_casework_documents import samples as document_sample
from backend.tests.test_casework_geo_samples import media_samples
from backend.tests.test_casework_geo_samples import samples as geo_sample

QUERIES = {
    "B01": "观察团日报里，各地区比前一天多记录了多少起违反停火的情况？",
    "B02": "联合声明里各方答应做什么，什么时候生效，有哪些执行条件？",
    "B03": "能把这些老照片跟馆藏记录对应起来，注明每一张的出处吗？",
    "B04": "记者会上是谁提问、谁作答？请给出能回到录像对应发言的时间。",
    "B05": "把平民伤亡按月列出来，重新核对总数，开头和结尾是否覆盖整月？",
    "B06": "历史条约的两版条文逐条有什么不同，俄文名词如何对应？",
    "B07": "两期民生指标能直接比较吗？想了解地区、定义和统计口径的变化。",
    "B08": "调查机构提到的事实中，哪些后来得到了法院认定？",
    "B09": "把历史档案中事发、形成记录、公开和解密的时间分别排出来。",
    "B10": "事件报道从首份简报到后续调查，哪些说法发生了变化？",
    "T01": "请说明这篇论文的试验怎么做，哪些是实测、哪些是模型结果，结论有什么局限？",
    "T02": "新公布的项目里程碑中，哪些只是重复报道？帮我整理一份更新。",
    "T03": "跟上次的技术路线图相比，研发阶段和交付顺序怎么调整了？",
    "T04": "把项目资助和参与机构放在一起，哪些机构合作，拿了多少经费？",
    "T05": "图注中的不同排放情景分别是多少，哪些数值只是图上读数？",
    "T06": "请逐条核对技术标准的征求意见稿和定稿，说明最后改了什么。",
    "T07": "把专家访谈按主题整理，告诉我每段从第几分钟回听。",
    "T08": "项目结项时，项目方、审计和独立研究的说法在哪些地方相互矛盾？",
    "T09": "电池随着充放电次数增长，容量和内阻怎样变化？这批样本适用到什么条件？",
    "T10": "这篇论文被撤稿后，依赖它的简报中哪些结论仍有独立证据？",
    "G01": "这批卫星火点同前一天相比，哪些是新出现的，哪些是重复观测？",
    "G02": "对照不同时相的影像，请标出能定位到像素的变化。",
    "G03": "How unusual was the Arctic sea-ice extent relative to its long-term baseline?",
    "G04": "洪水淹了哪些路段？请核算水域变化和道路暴露的范围。",
    "G05": "灾前灾后，同一街区的房屋分别有多少完好、受损或者倒塌？",
    "G06": "哪几类土地利用转成了建设用地，三期的面积变化怎样？",
    "G07": "哪些采样点检出了污染物，哪些地方缺乏有效检测？",
    "G08": "按时间先后复原岛礁影像、潮位和调查报告里的观测。",
    "G09": "封路以后，救援车辆从起点到终点还能怎么走，绕行多远？",
    "G10": "哪些县的农田被淹了？想看不同日期重复受灾的面积。",
}


def asset(key, name, content, **changes):
    return {
        "id": key,
        "name": name,
        "project_id": "p",
        "status": "active",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "content": content,
        "modality": "structured",
        "media_type": "application/json",
        "classification": "local",
        "source_version": "1",
        **changes,
    }


def sample_asset(code):
    if code.startswith("G"):
        rows = geo_sample(code)
    else:
        rows = [r["fields"] for r in document_sample(code)]
    return asset(code, "中立名称的登记表", {"records": rows})


def media_assets(code):
    return [asset(key, "图像", None, modality="image", media_type="image/png") for key in media_samples(code)]


@pytest.mark.parametrize("code", QUERIES)
def test_natural_questions_choose_existing_profiles_without_case_ids(code, monkeypatch):
    monkeypatch.setattr(intake.reference_batches, "freeze", lambda *_: {})
    compatible = sample_asset(code)
    other = asset("unrelated", "门卫排班", {"records": [{"employee_no": "D", "shift": "night"}]})
    media = media_assets(code)
    answer = intake.plan_question(QUERIES[code], "p", [other, compatible, *media])
    assert (answer["status"], answer["case_id"], set(answer["input_refs"])) == (
        "ready",
        code,
        {code, *(x["id"] for x in media)},
    ), answer


def test_two_clause_profiles_are_not_guessed_when_both_contracts_and_inputs_fit():
    answer = intake.plan_question("请逐条对比两版条款。", "p", [sample_asset("B06"), sample_asset("T06")])
    assert answer["status"] == "ambiguous" and answer["input_refs"] == []
    assert {"B06", "T06"} <= {row["case_id"] for row in answer["candidates"]}


def test_thirty_mixed_sources_are_not_conflated(monkeypatch):
    monkeypatch.setattr(intake.reference_batches, "freeze", lambda *_: {})
    assets = [sample_asset(code) for code in QUERIES]
    assets.extend(x for code in QUERIES for x in media_assets(code))
    indistinct = {"B04", "B06", "T06", "T07"}
    for code, question in QUERIES.items():
        found = intake.plan_question(question, "p", assets)
        if code in indistinct:
            assert found["status"] == "ambiguous" and found["input_refs"] == [], (code, found)
        else:
            assert found["status"] == "ready" and set(found["input_refs"]) == {
                code,
                *(x["id"] for x in media_assets(code)),
            }, (code, found)
    names = {
        "B04": "记者会转录与逐字稿",
        "T07": "访谈回听转录",
        "B06": "历史声明新旧原件",
        "T06": "技术标准草案与定稿",
    }
    for item in assets:
        if item["id"] in names:
            item["name"] = names[item["id"]]
    for code in indistinct:
        assert intake.plan_question(QUERIES[code], "p", assets)["input_refs"] == [code]


def test_policy_selects_the_requested_year_and_refuses_unsupported_conditions():
    raw = [{"year": 2000, "month": 9, "hemisphere": "N", "extent": 10}]
    a = asset(
        "old",
        "北极海冰表甲",
        {
            "records": [
                {"kind": "policy", "start_year": 2000, "target_year": 2003, "baseline_years": [2000, 2002]},
                *raw,
            ]
        },
    )
    b = asset(
        "new",
        "北极海冰表乙",
        {
            "records": [
                {"kind": "policy", "start_year": 2000, "target_year": 2004, "baseline_years": [2000, 2002]},
                *raw,
            ]
        },
    )
    assert intake.plan_question("2004 年北极海冰比 2000–2002 的基线少了多少？", "p", [a, b])[
        "input_refs"
    ] == ["new"]
    for question, expected in [
        ("2005 年北极海冰比基线少了多少？", "目标年"),
        ("北极九月海冰比 1990–1995 基线少了多少？", "区间"),
        ("2003年海冰与 2000–2003 作为基准的均值相比差多少？", "区间"),
        ("南极海冰比常年低多少？", "北半球九月"),
        ("今年的北极海冰比常年低多少？", "目标年"),
        ("七月海冰范围和基线相比差多少？", "仅计算九月"),
        ("2003 年和2004 年海冰范围比较", "目标年"),
    ]:
        res = intake.plan_question(question, "p", [a, b])
        assert res["status"] == "missing_inputs" and not res["input_refs"] and expected in res["reason"], res


def test_route_selection_requires_the_requested_snapshot():
    a = sample_asset("G09")
    moment = next(r["snapshot"] for r in a["content"]["records"] if r.get("kind") == "policy")
    bad = intake.plan_question("计算2020-01-01道路封闭后的绕行路线。", "p", [a])
    assert bad["status"] == "missing_inputs" and "快照" in bad["reason"]
    accepted = intake.plan_question("计算" + moment[:10] + "道路封闭后的绕行路线。", "p", [a])
    assert accepted["input_refs"] == ["G09"]


def test_reference_scope_requires_the_registered_exact_input_set(monkeypatch):
    a = dict(sample_asset("G03"), checksum="verified")
    monkeypatch.setattr(
        intake.reference_batches,
        "freeze",
        lambda *_: {"entry": {"sources": [{"sha256": "verified", "source_version": "1"}]}},
    )
    assert intake.plan_question(QUERIES["G03"], "p", [a])["case_scope"] == "reference"
    assert intake.plan_question("缩小样本验证，" + QUERIES["G03"], "p", [a])["case_scope"] == "sample"
    assert intake.plan_question(QUERIES["G03"], "p", [dict(a, checksum="new")])["case_scope"] == "project"


def test_project_status_and_expiry_checked_before_any_local_bytes_are_read():
    valid = asset("good", "海冰年表", None, content_ref="controlled", media_type="text/csv", bytes=35)
    assets = [
        dict(valid, id="other", project_id="other"),
        dict(valid, id="gone", status="revoked"),
        dict(valid, id="past", expires_at="2020-01-01T00:00:00Z"),
        valid,
    ]
    calls = []

    def read(value):
        calls.append(value["id"])
        return b"year,month,hemisphere,extent\n2021,9,N,4\n"

    plan = intake.plan_question("北极九月海冰相对常年有多大变化？", "p", assets, read_bytes=read)
    assert calls == ["good"] and plan["input_refs"] == ["good"]


def test_open_question_restricts_public_text_and_preserves_topic_spelling():
    question = "根据维修通知，说明 ServoMotor 为什么停机？"
    public = asset(
        "notice",
        "维修通知",
        "编码器异常时 ServoMotor 会停机。",
        classification="public",
        modality="text",
        media_type="text/plain",
    )
    other = asset("wrong", "周末排班", "周末正常值班。")
    plan = intake.plan_question(question, "p", [other, public])
    assert plan["status"] == "semantic" and plan["input_refs"] == ["notice"]
    assert "ServoMotor" in plan["topics"]
    private = dict(public, id="private", classification="local")
    assert intake.plan_question(question, "p", [public, private])["status"] == "missing_inputs"
    assert intake.plan_question("替我买股票", "p", [public])["status"] == "unsupported"
    assert intake.plan_question("研究 G03", "p", [other])["status"] == "unsupported"


def test_large_project_narrows_by_explicit_title_or_semantic_metadata_without_reading_other_files(
    monkeypatch,
):
    monkeypatch.setattr(intake.reference_batches, "freeze", lambda *_: {})
    other = [
        asset(f"x{i}", f"中性文件{i}", None, content_ref="controlled", media_type="text/csv", bytes=50)
        for i in range(510)
    ]
    selected = asset(
        "target", "春溪年度补冰观测", None, content_ref="controlled", media_type="text/csv", bytes=50
    )
    calls = []

    def read(a):
        calls.append(a["id"])
        return b"year,month,hemisphere,extent\n2023,9,N,4.38\n"

    found = intake.plan_question(
        "按《春溪年度补冰观测》核对北极海冰范围的历史排名。", "p", [*other, selected], read_bytes=read
    )
    assert found["input_refs"] == ["target"] and calls == ["target"]
    calls.clear()
    selected["name"] = "北极九月海冰年度观测"
    found = intake.plan_question("北极海冰相对常年怎样变化？", "p", [*other, selected], read_bytes=read)
    assert found["input_refs"] == ["target"] and calls == ["target"]
    calls.clear()
    found = intake.plan_question("查一下这里有什么资料。", "p", other, read_bytes=read)
    assert found["status"] == "ambiguous" and not found["input_refs"] and not calls
