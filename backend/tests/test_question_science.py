import json

import pytest

from backend.app.casework.document_cases import analyze
from backend.app.platform.contracts import AnswerTable
from backend.app.platform.question_science import project_science
from backend.tests.test_casework_documents import samples
from backend.tests.test_question_intake_plan import QUERIES


@pytest.mark.parametrize(
    ("code", "facts"),
    [
        ("T01", ("条件A测得15个颗粒", "条件B测得20个", "模型估计下降20%", "仅涵盖本次试验条件")),
        ("T02", ("2024-01-04", "重复报道", "2024-01-09", "新增事件", "第二次试验")),
        ("T03", ("路线A", "路线B", "概念", "计划", "2024-07")),
        ("T04", ("总成本 102 EUR", "资助额 73 EUR", "Demo Institute", "Partner A", "65 EUR", "8 EUR")),
        ("T05", ("图 4", "3 Mt", "图 6", "12 Mt", "图 7", "25 Mt", "图上标注")),
        ("T06", ("检查人员应记录来源。", "并记录版本。", "定稿第 3 条")),
        ("T07", ("验证计划", "00:00.100", "00:02.100", "00:04.900")),
        ("T08", ("未确认实质矛盾", "限定试验是否完成", "项目方", "审计方", "独立研究")),
        ("T09", ("12 Ah", "11 Ah", "-1 Ah", "2.4 mOhm", "22℃恒定负载")),
        ("T10", ("该效应已经确证", "应撤回", "独立样本结果", "有独立支持，可保留")),
    ],
)
def test_each_science_question_delivers_concrete_audited_facts(code, facts):
    record = analyze(code, samples(code))
    delivery = project_science(code, record["products"], QUERIES[code])
    assert all(fact in delivery["answer"] for fact in facts), delivery["answer"]
    assert all(AnswerTable.model_validate(t).total_rows == len(t["rows"]) for t in delivery["tables"])
    text = json.dumps(delivery, ensure_ascii=False)
    assert not any(
        k in text
        for k in ("supplied_value", "evidence_ids", "literature_card", "route_history", "repeat_report", "…")
    )
    if code == "T04":
        assert any("各机构" in gap for gap in delivery["missing"])


def test_project_funding_never_combines_currencies_or_implies_partner_receipts():
    products = {
        "portfolio": [
            {"project_id": "P7", "theme": "材料", "currency": "EUR", "total_cost": 15, "contribution": 0},
            {"project_id": "P8", "theme": "材料", "currency": "USD", "contribution": 2},
        ],
        "organization_graph": {"edges": [{"from": "P7", "to": "实验室", "role": "participant"}]},
        "theme_summary": {
            "known_funding": [
                {
                    "theme": "材料",
                    "currency": "EUR",
                    "total_cost": {"known_amount": 15, "records": 1},
                    "contribution": {"known_amount": 0, "records": 1},
                },
                {"theme": "材料", "currency": "USD", "contribution": {"known_amount": 2, "records": 1}},
            ]
        },
    }
    result = project_science("T04", products, "参与机构各自拿了多少资助？")
    assert "资助额 0 EUR" in result["answer"] and "资助额 2 USD" in result["answer"]
    assert "15 EUR" in result["answer"] and "17" not in result["answer"]
    assert "未列各机构实际分得的金额" in " ".join(result["missing"])


def test_term_and_scope_gaps_remain_visible_and_complete_tables_keep_later_rows():
    card = project_science(
        "T01", {"literature_card": {"measurements": [{"text": "读取值 6 ppm"}]}}, "结果是什么"
    )
    assert "6 ppm" in card["answer"] and any("模型结果" in v for v in card["missing"])
    rows = [{"figure": i, "value": i * 2, "scenario": f"情景{i}", "unit": "t"} for i in range(1, 125)]
    result = project_science("T05", {"comparison": rows}, "所有情景分别是多少？")
    assert result["tables"][0]["rows"][-1][4] == 248 and result["tables"][0]["total_rows"] == 124
    assert project_science("B01", {}, "") is None
