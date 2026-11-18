"""Source-safe workflow reuse through real planner/worker paths with a synthetic model."""

import pytest

from backend.app.platform import agent_candidates, runtime, service, task_cases
from backend.app.platform import workflow_learning as learning
from backend.app.platform import workflow_learning_api as views
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_agent_candidates import prepare
from backend.tests.test_task_cases import REQUESTER, create, drain
from backend.tests.test_v2_platform import ADMIN, OTHER

db = fixtures.db


def test_explicit_decimal_assertion_fails_even_with_positive_model_review():
    from backend.app.platform.quality import combine_review, deterministic_quality

    task = {"task_type": "autonomous_analysis"}
    output = {"content": {"text": "复算：4 + 3 = 6，核对一致。", "citations": []}, "source_refs": []}
    verdict = combine_review(deterministic_quality(task, output, []), {"verdict": "passed"})
    assert not verdict["passed"] and any(
        i["code"] == "decimal_reconciliation" for i in verdict["hard_failures"]
    )
    output["content"]["text"] = "复算：0.1 + 0.2 = 0.3\n所谓‘4+3=6’是错误的。"
    assert deterministic_quality(task, output, [])["passed"]


async def test_matching_run_reuses_qualified_workflow_and_invalidated_sample_forces_replan(db, monkeypatch):
    project, cases = await prepare(db, monkeypatch, ["data.list_sources"])
    cid = agent_candidates.create(cases[0]["id"], REQUESTER, db)["id"]
    agent_candidates.evaluate(cid, [c["id"] for c in cases[1:]], 4, ADMIN, db)
    for _ in range(4):
        worker = "learning-eval"
        run = await runtime.process_claim(db, db.claim(worker), worker)
        assert run["status"] == "succeeded"
    cid_row = agent_candidates.promote(cid, ADMIN, db)
    monkeypatch.setattr(views, "repo", db)
    detail = views.publication_detail(cid, ADMIN)
    assert detail["id"] == cid_row["promoted_workflow_id"]
    ref = detail["steps"][0]["components"]["tools"][0]
    assert ref["id"] == "data.list_sources" and ref["checksum"]

    async def repeat(key):
        case = create(
            db,
            project,
            goal=cases[0]["goal"],
            data_requirement="none",
            public_egress_authorized=True,
            idempotency_key=key,
        )
        task_cases.start(case["id"], REQUESTER, db)
        return await drain(db, "learning-reuse")

    run = await repeat("repeat-evaluated-publication")
    assert run["status"] == "succeeded", run.get("result")
    assert run["snapshot"]["workflow"]["id"] == detail["id"]
    plan_id = run["autonomous_plan_id"]
    plan = views.plan_detail(plan_id, ADMIN)
    assert (
        plan["publication"]["checksum"] == detail["checksum"]
        and plan["steps"][0]["components"]["tools"][0] == ref
    )
    assert db.get("plan_versions", plan_id)["request_id"] is None
    assert run["quality_verdict"]["passed"] and db.list("instances", project["id"], task_id=run["id"])
    with pytest.raises(service.DomainError) as denied:
        views.plan_detail(plan_id, OTHER)
    assert denied.value.status == 403
    assert any(p["id"] == plan_id for p in views.index(project["id"], ADMIN)["plans"])

    source = db.get("task_cases", cases[1]["id"])
    db.put("task_cases", {**source, "deleted_at": source["created_at"]})
    with pytest.raises(service.DomainError, match="已变化"):
        views.publication_detail(cid, ADMIN)
    new_run = await repeat("repeat-after-withdrawal")
    assert new_run["status"] == "succeeded"
    assert new_run["snapshot"]["workflow"]["id"] != detail["id"]
    assert db.get("plan_versions", new_run["autonomous_plan_id"])["request_id"]


def test_experience_requires_actual_changed_plan_and_passing_recheck_and_excludes_source_text():
    task = {
        "status": "succeeded",
        "quality_verdict": {"passed": True},
        "snapshot": {"agents": []},
        "repair_history": [
            {
                "action": "targeted_replanning",
                "revision": 1,
                "failed_checks": ["grounded_citations", "private note"],
                "plan_diff": {
                    "before": [{"instructions": "private target"}],
                    "after": [{"instructions": "private answer"}],
                },
                "before": {"failed_checks": 1},
                "outcome": {"passed": True, "failed_checks": 0},
            }
        ],
    }
    (lesson,) = learning.verified_repairs(task)
    assert "private" not in str(lesson)
    assert lesson["check"] == "grounded_citations" and lesson["after_failed"] == 0
    task["repair_history"][0]["outcome"]["passed"] = False
    assert learning.verified_repairs(task) == []
    task["repair_history"][0]["outcome"]["passed"] = True
    task["repair_history"][0]["plan_diff"]["after"] = task["repair_history"][0]["plan_diff"]["before"]
    assert learning.verified_repairs(task) == []
