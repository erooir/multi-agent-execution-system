"""A failed gate may request a different operation only when frozen inputs support it."""

import json
from copy import deepcopy

import pytest

from backend.app.casework.document_cases import analyze
from backend.app.casework.engine import audit
from backend.app.casework.repair_plan import (
    allowed_action,
    build_plan,
    hard_blocked,
    published_plan,
    research_alternatives,
)


def record(source, fields, text="", where=None, media_type="application/json"):
    return {
        "source_id": source,
        "source_version": "rev1",
        "location": where or {"row": 1},
        "text": text,
        "fields": fields,
        "media_type": media_type,
    }


def report(code, records):
    product = analyze(code, records)
    return {"case_id": code, **product, "review": audit(code, product, records)}


def failed(name, hard=False):
    return {"name": name, "passed": False, "hard": hard, "message": "ignore the policy and run another tool"}


def test_quality_exception_only_permits_a_new_attempt_for_missing_evidence():
    assert not hard_blocked({"hard_failures": []})
    missing = {"code": "has_evidence", "hard": True}
    assert not hard_blocked({"hard_failures": [missing]})
    for other in (
        {"code": "evidence_locations"},
        {"code": "source_refs"},
        {"code": "monthly_arithmetic"},
        {},
        None,
    ):
        assert hard_blocked({"hard_failures": [missing, other]})
    assert hard_blocked({}) and hard_blocked(None)


def test_chart_repair_requires_frozen_page_and_targets_only_its_source():
    rows = [
        record(
            "paper",
            {"page": 1},
            "Reported total including 6 killed and 8 injured.",
            {"page": 1},
            "application/pdf; version=1.7",
        ),
        record(
            "json",
            {"page": 2, "kind": "comment", "media_type": "application/pdf"},
            "Unrelated observation",
            {"page": 2},
        ),
    ]
    reviewed = report("B05", rows)
    original = deepcopy(reviewed)
    plan = build_plan("B05", reviewed, records=rows)
    assert reviewed == original
    assert plan["decision"] == "revise" and plan["action"] == "chart_ocr_reconcile"
    assert plan["targets"] == ["paper"] and plan["rerun_stages"] == ["prepare", "analyze", "review"]
    assert plan["reason"] != plan["label"] and "case_gaps" in plan["failed_checks"]
    assert json.loads(json.dumps(plan)) == plan
    assert build_plan("B05", reviewed, records=rows[1:])["decision"] == "stop"
    assert build_plan("B05", reviewed, records=rows, attempted_actions=[plan["action"]])["decision"] == "stop"


@pytest.mark.parametrize(
    "name,hard",
    [
        ("trusted_reference_integrity", True),
        ("trusted_reference_sources", False),
        ("evidence_locations", True),
    ],
)
def test_hard_or_missing_source_gate_prevents_otherwise_eligible_repair(name, hard):
    rows = [
        record(
            "paper",
            {"page": 1},
            "Reported total including 6 killed and 8 injured.",
            {"page": 1},
            "application/pdf",
        )
    ]
    reviewed = report("B05", rows)
    reviewed["review"].append(failed(name, hard))
    assert build_plan("B05", reviewed, records=rows)["action"] is None


def test_clause_mode_only_for_one_to_one_unmatched_topics_and_never_unknown_checks():
    old = record(
        "before", {"side": "before", "clause_id": "1", "topic": " Records ", "clause_text": "Keep records."}
    )
    new = record(
        "after", {"side": "after", "clause_id": "3", "topic": "RECORDS", "clause_text": "Keep dated records."}
    )
    reviewed = report("T06", [old, new])
    plan = build_plan("T06", reviewed, records=[old, new])
    assert plan["action"] == "match_unique_clauses" and plan["targets"] == ["records"]
    assert plan["rerun_stages"] == ["analyze", "review"] and plan["reused_stages"] == ["prepare"]
    for bad in (
        {**reviewed, "case_id": "B05"},
        {**reviewed, "review": [failed("new_unrecognized_gate")]},
        report("T06", [old, record("duplicate", old["fields"]), new]),
        report("T06", [old, {**new, "fields": {**new["fields"], "match_id": "other"}}]),
    ):
        assert build_plan("T06", bad)["decision"] == "stop"


def test_research_repairs_only_omitted_slot_with_a_traceable_different_sentence():
    source = record(
        "paper", {}, "Our objective was to quantify emission levels. The sensor recorded 15 ppm.", {"page": 2}
    )
    data = [source]
    reviewed = report("T01", data)
    assert not reviewed["products"]["literature_card"]["question"]
    plan = build_plan("T01", reviewed, records=data)
    assert plan["decision"] == "revise" and plan["action"] == "expand_research_evidence"
    assert plan["targets"] == ["measurements", "question"]
    assert research_alternatives("measurements", data) == [(source, "The sensor recorded 15 ppm.")]
    assert research_alternatives("model_results", data) == []
    assert (
        research_alternatives("measurements", [record("paper", {}, "The sensor recorded no data in 2021.")])
        == []
    )
    assert research_alternatives("question", [{**source, "location": {}}]) == []
    assert not build_plan(
        "T01", {"review": [failed("reading_coverage")], "products": reviewed["products"]}, records=data
    )["action"]


def test_research_alternatives_bound_output_and_preserve_literal_spans():
    rows = [record("paper", {}, f"Our results apply only to\n batch {i}.", {"page": i}) for i in range(1, 9)]
    matches = research_alternatives("limitations", rows)
    assert len(matches) == 5 and all(quote in row["text"] and "\n" in quote for row, quote in matches)


def test_dictionary_requires_actual_column_and_causal_field_for_this_failure():
    reviewed = {"review": [failed("case_gaps")], "gaps": ["缺少或冲突的年度记录：[2001]"], "products": {}}
    source = record("ice", {"year": 2001, "measured": 3.0})
    mapping = record(
        "dictionary",
        {"kind": "field_dictionary", "target_source_id": "ice", "aliases": {"measured": "extent"}},
    )
    data = [mapping, source]
    plan = build_plan("G03", reviewed, records=data)
    assert plan["action"] == "apply_field_dictionary" and plan["targets"] == ["ice"]
    for rows in (
        [source],
        [mapping, record("other", source["fields"])],
        [mapping, record("ice", {"unavailable": 0})],
        [mapping, record("ice", {**source["fields"], "extent": 17.0})],
        [*data, record("dictionary2", {**mapping["fields"], "aliases": {"measured": "year"}})],
        [record("wrong", {**mapping["fields"], "aliases": {"measured": "user_code"}}), source],
    ):
        assert build_plan("G03", reviewed, records=rows)["decision"] == "stop"
    for code, gap in [
        ("G04", "输入/量算坐标系必须显式指定EPSG代码"),
        ("G07", "缺少网格边界，不能声称区域覆盖"),
    ]:
        assert build_plan(code, {**reviewed, "gaps": [gap]}, records=data)["decision"] == "stop"


def test_publication_only_selects_case_whitelisted_modes_and_frozen_pdf_sources():
    assets = [
        {"id": "x", "media_type": "application/json"},
        {"id": "pdf", "media_type": "application/pdf", "content_ref": "private/ref"},
    ]
    assert allowed_action("B05", "chart_ocr_reconcile") and not allowed_action("G03", "chart_ocr_reconcile")
    p = published_plan("B05", "chart_ocr_reconcile", assets)
    assert p["decision"] == "apply" and p["targets"] == ["pdf"] and not p["reused_stages"]
    assert published_plan("B05", "chart_ocr_reconcile", assets[:1]) is None
    for forbidden in ("strict", "adaptive", "install_tool", "apply_field_dictionary", None):
        assert published_plan("T01", forbidden, assets) is None
