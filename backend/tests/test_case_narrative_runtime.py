"""Synthetic gateway responses reproduce the observed B05 prose defect, without paid calls."""

from backend.app.casework.team import install
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_case_domain_executors import _inputs, _patch_provider
from backend.tests.test_v2_platform import ADMIN, project, run, task_body

db = fixtures.db


async def test_wrong_month_prose_is_recorded_and_repaired_without_rerunning_valid_tools(
    db, tmp_path, monkeypatch
):
    p = project(db)
    install(p["id"], ADMIN, db, case_id="B05")
    last, refs = _inputs(db, p, "B05", tmp_path, monkeypatch)
    rounds = []
    _patch_provider(
        monkeypatch,
        rounds,
        decide=lambda stage, decision: {
            **decision,
            "assessment": "已读取99个月度记录，13个完整月加首尾部分月。",
        },
    )
    final = await run(
        db,
        task_body(
            p,
            last,
            task_type="case_analysis",
            case_id="B05",
            case_scope="sample",
            input_refs=refs,
            mode="cloud",
            max_cost_cny=2,
            max_revisions=0,
        ),
    )
    assert final["status"] == "succeeded", final.get("reason_code")
    assert len(rounds) == 6  # Tools are reused; this repair does not pay for a repeat analysis.
    events = db.list("events", task_id=final["id"], type="model.assessment_corrected")
    assert len(events) == 3
    for node in db.list("nodes", task_id=final["id"]):
        review = node["output"]["assessment_review"]
        assert review["status"] == "corrected"
        assert "99个月度记录" in review["original_assessment"]
        assert "99个月度记录" not in review["assessment"]
        assert all(check["passed"] for check in review["post_checks"])
    assert all(e["payload"]["reused_artifact"]["result_checksum"] for e in events)
