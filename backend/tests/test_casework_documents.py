"""Small synthetic, redistributable acceptance inputs. No customer material or canned answers."""

from copy import deepcopy

import pytest

from backend.app.casework.document_cases import CATALOG, analyze, audit_products


def samples(code: str) -> list[dict]:
    """Frozen-record examples; source_id identifies a distinct logical input asset, NOT a client ID."""
    rows = []

    def add(source="source-a", text="", **fields):
        rows.append(
            {
                "source_id": source,
                "source_version": "1",
                "location": {"row": len(rows) + 1},
                "text": text,
                "fields": fields,
            }
        )

    if code == "B01":
        add(
            region="North",
            current=27,
            previous=35,
            report_number="test-4",
            published_date="2024-07-03",
            observed_until="2024-07-02T18:00:00+02:00",
            timezone="UTC+02:00",
        )
        add(region="South", current=10, previous=4)
        add(text="民用设施维护已记录。观察限制包括设备故障。受访者报告设施检修未完。")
    elif code == "B02":
        add(
            kind="document",
            parties=["甲方", "乙方"],
            published_date="2024-07-03",
            document_name="公告",
            topic="cessation",
            commitment="议定书自次日生效",
            effective_date="2024-07-04",
        )
        add(
            "source-b",
            kind="document",
            parties=["甲方", "乙方"],
            published_date="2024-07-03",
            document_name="声明",
            topic="humanitarian",
            commitment="承诺开放人道运输通道",
        )
        add("source-b", topic="services", commitment="承诺恢复供水")
    elif code == "B03":
        add(
            kind="image",
            image_id="archive-a",
            visible_features="已知标注显示直翼轮廓",
            candidates=["型号组A"],
            bbox=[10, 20, 25, 35],
            width=100,
            height=80,
            coordinate_space="pixels",
        )
        add("catalog", kind="catalog", image_id="archive-a", model_name="型号组A", url="catalog:entry-41")
    elif code in {"B04", "T07"}:
        add(
            "audio",
            kind="timed_asr",
            media_id="episode",
            utterance="When did the trial finish?",
            start_ms=100,
            end_ms=2000,
            speaker="主持人",
            turn_type="question",
            topic="验证计划",
            translation_zh="试验何时结束？",
        )
        add(
            "audio",
            kind="timed_asr",
            media_id="episode",
            utterance="The trial finished in June.",
            start_ms=2100,
            end_ms=4900,
            speaker="嘉宾",
            turn_type="answer",
            topic="已完成工作",
            translation_zh="试验在六月结束。",
        )
        add("script", text="When did the trial finish?", kind="transcript")
        add("script", text="The trial finished in June.", kind="transcript")
        add("script", kind="glossary", term="trial", meaning="试验")
    elif code == "B05":
        add(
            month="2024-01", start_date="2024-01-26", end_date="2024-01-31", deaths=2, injured=4, basis="核验"
        )
        add(
            month="2024-02", start_date="2024-02-01", end_date="2024-02-29", deaths=3, injured=7, basis="核验"
        )
        add(kind="reported_total", deaths=5, injured=11, text="核验数量不代表全部实际数量。")
    elif code in {"B06", "T06"}:
        add(
            side="before",
            clause_id="1",
            topic="监督",
            clause_text="检查人员应记录来源。",
            translation_zh="检查人员应记录来源。",
        )
        add(
            "source-b",
            side="after",
            clause_id="3",
            match_id="1",
            topic="监督",
            clause_text="检查人员应记录来源。并记录版本。",
            translation_zh="增加来源版本记录。",
        )
        add("source-b", kind="response", text="机构回应：修改有利于维护可追溯性。")
    elif code == "B07":
        for i, n in enumerate([20, 44]):
            add(
                "report-" + str(i),
                indicator="登记人数",
                region="R",
                definition="已登记个案",
                coverage="本区域",
                unit="人",
                method="全期登记",
                cutoff=f"2024-06-{15 + i * 7:02}",
                published_date=f"2024-06-{16 + i * 7:02}",
                value=n,
            )
    elif code in {"B08", "B10", "T08"}:
        roles = {
            "B08": ["judicial", "investigation"],
            "B10": ["brief", "investigation"],
            "T08": ["project", "audit", "study"],
        }[code]
        add("questions", kind="question", question_id="q1", question="限定试验是否完成？")
        for i, role in enumerate(roles):
            add(
                role,
                question_id="q1",
                role=role,
                statement=f"{role}记载一次试验完成",
                published_date=f"2024-06-{i + 1:02}",
                scope="限定试验",
                value="completed",
                basis="来源认定",
            )
    elif code == "B09":
        add(
            event_id="e1",
            event="召开会议",
            event_date="2024-05-01",
            formed_at="2024-05-01",
            published_date="2024-05-10",
            declassified_at="2025-05-10",
            channel="会议档案",
            timezone="+08:00",
            time_precision="day",
        )
        add(
            "source-b",
            event_id="e2",
            event="公开发布",
            event_date="2024-05-11",
            published_date="2024-05-11",
            channel="公告",
        )
    elif code == "T01":
        add(
            title="Synthetic trial",
            doi="10.1234/example",
            authors="A",
            institution="Synthetic Lab",
            published_date="2024-05-01",
            research_question="两种试验条件下结果是否一致？",
            comparison="A与B工况相同",
            measurement="条件A测得15个颗粒；条件B测得20个",
            model_result="模型估计下降20%",
            limitation="仅涵盖本次试验条件",
        )
    elif code == "T02":
        add(
            "history",
            role="baseline",
            project="demo",
            event_type="first_trial",
            event_date="2024-01-04",
            published_date="2024-01-08",
            outcome="完成",
        )
        add(
            "release",
            role="news",
            project="demo",
            event_type="first_trial",
            event_date="2024-01-04",
            published_date="2024-01-10",
            outcome="完成",
        )
        add(
            "release",
            role="news",
            project="demo",
            event_type="second_trial",
            event_date="2024-01-09",
            published_date="2024-01-10",
            outcome="完成",
        )
    elif code == "T03":
        add(project="demo", feature="推进", date="2022-03", value="路线A", stage="概念")
        add("source-b", project="demo", feature="推进", date="2024-07", value="路线B", stage="计划")
    elif code == "T04":
        add(kind="taxonomy", theme="储能", keywords=["电池"])
        add(
            project_id="P1",
            objective="研究电池储能",
            coordinator="Demo Institute",
            participants=["Partner A"],
            total_cost=90,
            contribution=65,
            currency="EUR",
        )
        add(
            project_id="P2",
            objective="电池研究",
            coordinator="Partner A",
            participants=["Demo Institute"],
            total_cost=12,
            contribution=8,
            currency="EUR",
        )
    elif code == "T05":
        for n, yr, kind, num in [
            (4, 2021, "historical", 3),
            (6, 2030, "forecast", 12),
            (7, 2040, "forecast", 25),
        ]:
            add(
                figure=n,
                title=f"示例图{n}",
                year=yr,
                value_kind=kind,
                value_source="label",
                value=num,
                unit="Mt",
                scenario="场景A",
            )
    elif code == "T09":
        for i in range(3):
            add(
                battery_id="cell-A",
                cycle=i + 1,
                capacity=12 - i * 0.5,
                capacity_unit="Ah",
                resistance=2 + 0.2 * i,
                resistance_unit="mOhm",
                load=1,
                temperature=22,
            )
        add(
            "paper",
            kind="paper",
            text="受控工况与设备实际使用环境不同。",
            conditions="22℃恒定负载",
            population="试验样本",
        )
    elif code == "T10":
        add(kind="status", doi="10.1234/example", date="2024-06-01", status="published")
        add(kind="notice", doi="10.1234/example", date="2024-07-01", status="retracted")
        add("brief", kind="brief_claim", claim_id="C1", statement="该效应已经确证", cites=["10.1234/example"])
        add(
            "other",
            kind="independent",
            doi="10.1234/independent",
            supports_claims=["C2"],
            text="独立样本显示另一结果。",
        )
        add(
            "brief",
            kind="brief_claim",
            claim_id="C2",
            statement="独立样本结果",
            cites=["10.1234/independent"],
        )
    else:
        raise ValueError(code)
    return rows


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_document_case_normal_and_empty_input(code):
    records = samples(code)
    res = analyze(code, records)
    assert not res["gaps"], res["gaps"]
    assert set(res["products"]) == set(CATALOG[code]["outputs"])
    assert res["evidence"]
    assert all(x["passed"] for x in audit_products(code, res["products"], records))
    empty = analyze(code, [])
    assert empty["gaps"] and not empty["evidence"]


def test_monthly_reconciliation_partial_month_and_revision_integrity():
    records = samples("B05")
    r = analyze("B05", records)
    assert [row["complete_month"] for row in r["products"]["monthly"]] == [False, True]
    assert r["products"]["reconciliation"][0]["computed"] == [5, 11]
    records[2]["fields"]["injured"] = 16
    assert analyze("B05", records)["gaps"]
    records.append(deepcopy(records[0]))
    assert analyze("B05", records)["products"]["reconciliation"][0]["computed"] is None


def test_two_period_comparison_rejects_incomparable_scope():
    records = samples("B07")
    assert analyze("B07", records)["products"]["comparison"][0]["change"] == 24
    records[1]["fields"]["coverage"] = "部分区域"
    row = analyze("B07", records)["products"]["comparison"][0]
    assert not row["comparable"] and row["change"] is None and row["reasons"]


def test_clause_alignment_retry_only_with_unique_explicit_topic():
    records = samples("T06")
    records[1]["fields"].pop("match_id")
    first = analyze("T06", records)
    assert first["gaps"] and not first["products"]["clause_map"]
    new = analyze("T06", records, feedback=first["gaps"])
    assert not new["gaps"]
    assert new["products"]["clause_map"][0]["method"] == "unique_topic_suggestion"
    records.append(deepcopy(records[1]))
    assert analyze("T06", records, feedback=first["gaps"])["gaps"]


def test_media_does_not_invent_time_or_promote_questions_to_answers():
    rs = samples("B04")
    good = analyze("B04", rs)["products"]
    assert [r["turn_type"] for r in good["timed_index"]] == ["question", "answer"]
    rs = [r for r in rs if "start_ms" not in r["fields"]]
    r = analyze("B04", rs)
    assert not r["products"]["timed_index"] and r["gaps"]


def test_unique_funding_and_separate_budget_columns():
    rs = samples("T04")
    rs.append(deepcopy(rs[1]))
    p = analyze("T04", rs)["products"]
    sumrow = p["theme_summary"]["known_funding"][0]
    assert sumrow["contribution"]["known_amount"] == 73 and sumrow["total_cost"]["known_amount"] == 102
    assert p["theme_summary"]["projects"]["储能"]["count"] == 2 and len(p["organization_graph"]["edges"]) == 4


def test_retraction_only_affects_dependent_claims():
    rs = samples("T10")
    p = analyze("T10", rs)["products"]
    assert [r["action"] for r in p["citation_impact"]] == ["withdraw", "unaffected"]
    rs[2]["fields"]["cites"].append("10.1234/independent")
    assert analyze("T10", rs)["products"]["citation_impact"][0]["action"] == "downgrade"


@pytest.mark.parametrize(
    "code,mutate",
    [
        ("B01", lambda p: p["observations"][0].update(count=999, change=964)),
        ("B05", lambda p: p["monthly"][0].update(deaths=12, total=16)),
        ("B07", lambda p: p["comparison"][0].update(change=77)),
        ("B04", lambda p: p["timed_index"][0].update(start_ms=170)),
        ("T06", lambda p: p["clause_map"][0]["before"].update(evidence_ids=[])),
        ("T04", lambda p: p["organization_graph"]["edges"][0].update(to="Unlisted Group")),
        ("T10", lambda p: p["citation_impact"][0].update(action="unaffected")),
    ],
)
def test_independent_audit_rejects_plausible_but_altered_result(code, mutate):
    records = samples(code)
    result = analyze(code, records)
    mutate(result["products"])
    assert any(not check["passed"] for check in audit_products(code, result["products"], records))


def test_chart_ocr_respects_population_and_date_bounds(monkeypatch):
    from PIL import Image

    from backend.app.casework import document_chart_ocr as ocr

    assert len(ocr._months("total from 24 February 2024 to 15 March 2024")) == 2
    assert not ocr._months("from 30 February 2024 to 15 March 2024")
    # A subset chart may use exactly the same calendar and series as the overall graph.
    monkeypatch.setattr(
        ocr,
        "_words",
        lambda *args, **kw: [
            (
                "Civilian casualties from one cause per month, from 1 January 2024 to 1 March 2024",
                0,
                0,
                10,
                10,
                99,
            )
        ],
    )
    assert ocr.extract_monthly_labels(Image.new("RGB", (700, 350), "white")) == []
