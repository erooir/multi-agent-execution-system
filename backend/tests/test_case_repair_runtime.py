"""Exercise production repair routing from task intake through the real Agno worker."""

import pytest

from backend.app.casework import team
from backend.app.platform import service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_documents import samples
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = fixtures.db


def case_request(p, source, case_id, *more_sources):
    return task_body(
        p,
        source,
        task_type="case_analysis",
        case_id=case_id,
        case_scope="sample",
        input_refs=[source["id"], *(a["id"] for a in more_sources)],
        max_revisions=2,
        allow_partial=True,
    )


def clauses():
    return [
        {"side": "before", "topic": "交付期限", "clause_id": "1", "clause_text": "提交记录。"},
        {"side": "after", "topic": "交付期限", "clause_id": "3", "clause_text": "提交记录并注明日期。"},
    ]


async def test_unique_clause_failure_changes_route_and_reuses_the_actual_parse(db):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    source = asset(db, p, clauses(), classification="local")
    result = await run(db, case_request(p, source, "T06"))
    assert result["status"] == "succeeded" and result["revision"] == 1
    (plan,) = result["repair_history"]
    assert plan["action"] == "match_unique_clauses" and plan["targets"] == ["交付期限"]
    assert plan["rerun_stages"] == ["analyze", "review"] and plan["reused_stages"] == ["prepare"]
    assert plan["outcome"]["passed"] and "case_gaps" in plan["outcome"]["resolved_checks"]
    nodes = db.list("nodes", p["id"], task_id=result["id"])
    assert len(nodes) == len(db.list("instances", p["id"], task_id=result["id"])) == 5
    (first_parse,) = [n for n in nodes if n["node_id"] == "prepare"]
    assert plan["reused_nodes"] == {"prepare": first_parse["id"]}
    revised_analysis = next(n for n in nodes if n["node_id"] == "analyze" and n["attempt"] == 2)
    assert revised_analysis["context_manifest"]["upstream"][0]["node_record_id"] == first_parse["id"]
    assert revised_analysis["context_manifest"]["revision"] == 1
    (artifact,) = db.list("artifacts", p["id"], task_id=result["id"])
    (pair,) = artifact["content"]["products"]["clause_map"]
    assert (
        pair["method"] == "unique_topic_suggestion"
        and pair["before"]["id"] == "1"
        and pair["after"]["id"] == "3"
    )
    assert result["result"]["usage"]["request_count"] == 0
    service.revoke_asset(source["id"], ADMIN, db)
    assert not service.task_detail(result["id"], ADMIN, db).get("repair_history")


@pytest.mark.parametrize("case_id", ["T06", "G04"])
async def test_no_resolvable_match_or_upstream_geometry_does_not_repeat_the_failed_route(db, case_id):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    rows = clauses() if case_id == "T06" else [{"note": "只有背景资料，没有洪水范围或道路图层。"}]
    if case_id == "T06":
        rows.append({**rows[1], "clause_id": "4"})
    source = asset(db, p, rows, classification="local")
    result = await run(db, case_request(p, source, case_id))
    assert result["status"] in {"partial", "failed"} and result["revision"] == 0
    (stopped,) = result["repair_history"]
    assert stopped["decision"] == "stop" and stopped["failed_checks"] and not stopped["rerun_stages"]
    assert len(db.list("instances", p["id"], task_id=result["id"])) == 3


async def test_missing_measurement_uses_scoped_dictionary_and_rechecks_the_result(db):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    rows = [
        {
            "kind": "policy",
            "start_year": 2000,
            "target_year": 2002,
            "baseline_years": [2000, 2001],
            "extent_unit": "km²",
        },
        *[
            {"year": y, "month": 9, "hemisphere": "N", "measured": v}
            for y, v in [(2000, 4), (2001, 6), (2002, 3)]
        ],
    ]
    src = asset(db, p, rows, classification="local")
    mapping = asset(
        db,
        p,
        [{"kind": "field_dictionary", "target_source_id": src["id"], "aliases": {"measured": "extent"}}],
        source_id="sheet-schema",
        classification="local",
    )
    result = await run(db, case_request(p, src, "G03", mapping))
    assert result["status"] == "succeeded" and result["revision"] == 1
    (plan,) = result["repair_history"]
    assert plan["action"] == "apply_field_dictionary" and plan["targets"] == [src["id"]]
    (report,) = db.list("artifacts", p["id"], task_id=result["id"])
    assert report["content"]["products"]["anomaly"]["difference"] == -2
    assert any(e["source_id"] == mapping["id"] for e in report["content"]["evidence"])


async def test_research_repair_targets_only_the_empty_slot_and_cites_an_exact_sentence(db):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    fields = dict(samples("T01")[0]["fields"])
    fields.pop("research_question")
    original = "Our objective was to characterize the two operating conditions."
    source = asset(db, p, [{**fields, "text": original}], classification="local")
    result = await run(db, case_request(p, source, "T01"))
    assert result["status"] == "succeeded" and result["revision"] == 1
    (plan,) = result["repair_history"]
    assert plan["action"] == "expand_research_evidence" and plan["targets"] == ["question"]
    assert plan["outcome"]["passed"]
    (artifact,) = db.list("artifacts", p["id"], task_id=result["id"])
    card = artifact["content"]["products"]["literature_card"]
    assert card["question"][0]["text"] == original
    assert card["measurements"][0]["supplied_value"] == fields["measurement"]
    assert any(
        e["quote"] == original and e["source_id"] == source["id"] and e["location"] == {"row": 1}
        for e in artifact["content"]["evidence"]
    )
