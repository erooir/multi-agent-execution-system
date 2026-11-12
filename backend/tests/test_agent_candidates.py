"""Independent candidate replay uses real workers; gateway responses are explicit test doubles."""

import json

import pytest

from backend.app.platform import agent_candidates, runtime, service, task_cases
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import (
    REQUESTER,
    changed_tool_publication,
    create,
    drain,
    no_data_analysis,
    setup,
)
from backend.tests.test_v2_platform import ADMIN

db = fixtures.db


def gateway(monkeypatch, tool_ids=None, rejected_goals=()):
    async def complete(prompt, *, purpose, **kwargs):
        execution = runtime.context()
        if purpose == "task_case_requirements":
            data = no_data_analysis()
        elif purpose == "task_case_planning":
            data = {
                "rationale": "不需外部资料",
                "missing_data": [],
                "steps": [
                    {
                        "label": "回答",
                        "instructions": "按当次目标回答并给三个具体建议。",
                        "tool_ids": tool_ids or [],
                        "skill_ids": [],
                        "success_criteria": ["三条建议"],
                    }
                ],
            }
        else:
            data = {"verdict": "passed", "issues": [], "evidence_refs": [], "suggested_changes": []}
            if execution.request["goal"] in rejected_goals and not execution.task.get(
                "candidate_evaluation_id"
            ):
                data.update(
                    verdict="failed",
                    issues=["测试替身明确拒绝初次答案的完整性"],
                    suggested_changes=["补全建议"],
                )
        charge = runtime.model_gateway.ledger.reserve(
            0.01, "test-double-review", execution.task["id"], task_limit=execution.request["max_cost_cny"]
        )
        runtime.model_gateway.ledger.settle(charge, {"prompt_tokens": 100, "completion_tokens": 100})
        return {"text": json.dumps(data), "model": "test-model", "request_id": charge}

    async def run_agent(agent, prompt, bindings, **kwargs):
        execution = runtime.context()
        charge = runtime.model_gateway.ledger.reserve(
            0.01, "test-double-execution", execution.task["id"], task_limit=execution.request["max_cost_cny"]
        )
        tokens = 100 if execution.task.get("candidate_arm") == "candidate" else 1000
        runtime.model_gateway.ledger.settle(charge, {"prompt_tokens": tokens, "completion_tokens": 100})
        return {
            "text": json.dumps(
                {
                    "text": "一、明确目标。二、检查输入。三、确认结果。",
                    "citations": [],
                    "missing_information": [],
                }
            ),
            "model": "test-model",
            "request_id": charge,
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)


async def prepare(db, monkeypatch, tool_ids=None):
    p = setup(db, monkeypatch)
    gateway(monkeypatch, tool_ids)
    cases = []
    for index, goal in enumerate(["给出写作的三条建议", "给出沟通的三条建议", "给出阅读的三条建议"]):
        case = create(
            db,
            p,
            goal=goal,
            data_requirement="none",
            public_egress_authorized=True,
            idempotency_key=f"sample-{index}",
        )
        task_cases.start(case["id"], REQUESTER, db)
        task = await drain(db, "candidate-test")
        assert task["status"] == "succeeded", task.get("result")
        cases.append(task_cases.get(case["id"], REQUESTER, db))
    return p, cases


async def test_real_development_holdout_replay_then_promote_without_task_data(db, monkeypatch):
    _p, cases = await prepare(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    specs = db.get("agent_candidates", candidate["id"])["specs"]
    assert cases[0]["goal"] not in json.dumps(specs, ensure_ascii=False)
    assert all("task_case_id" not in spec and "source_refs" not in spec for spec in specs)
    with pytest.raises(service.DomainError, match="独立评测"):
        agent_candidates.promote(candidate["id"], ADMIN, db)
    evaluation = agent_candidates.evaluate(candidate["id"], [c["id"] for c in cases[1:]], 4, ADMIN, db)
    assert evaluation["status"] == "evaluating"
    samples = evaluation["evaluation"]["samples"]
    assert [s["split"] for s in samples] == ["development", "holdout"]
    run_ids = {s[arm] for s in samples for arm in ("baseline", "candidate")}
    assert len(run_ids) == 4
    for _ in range(4):
        claimed = db.claim("candidate-test")
        assert claimed["id"] in run_ids
        result = await runtime.process_claim(db, claimed, "candidate-test")
        assert result["status"] == "succeeded", result.get("result")
        assert result["request"]["max_revisions"] == 0
        assert result.get("autonomous_plan_revision") == 0
    verified = agent_candidates.get(candidate["id"], ADMIN, db)
    assert verified["evaluation"]["verdict"] == "passed", verified
    assert all(c["passed"] for c in verified["evaluation"]["checks"])
    promoted = agent_candidates.promote(candidate["id"], ADMIN, db)
    assert promoted["status"] == "promoted"
    flow = db.get("workflows", promoted["promoted_workflow_id"])
    reusable = db.get("agents", flow["nodes"][0]["agent_id"])
    assert reusable["scope"] == "reusable" and not reusable.get("task_case_id")
    assert (
        db.get("agents", db.get("tasks", cases[0]["task_id"])["snapshot"]["agents"][0]["id"])["scope"]
        == "task"
    )
    assert runtime.model_gateway.ledger.usage()["request_count"] > 0


async def test_reject_training_case_duplicate_goal_and_unfinished_evaluation(db, monkeypatch):
    _, cases = await prepare(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    for ids in ([cases[0]["id"], cases[1]["id"]], [cases[1]["id"], cases[1]["id"]]):
        with pytest.raises(service.DomainError, match="独立任务"):
            agent_candidates.evaluate(candidate["id"], ids, 4, ADMIN, db)
    with pytest.raises(service.DomainError, match="管理权限"):
        agent_candidates.evaluate(candidate["id"], [c["id"] for c in cases[1:]], 4, REQUESTER, db)
    agent_candidates.evaluate(candidate["id"], [c["id"] for c in cases[1:]], 4, ADMIN, db)
    with pytest.raises(service.DomainError, match="独立评测"):
        agent_candidates.promote(candidate["id"], ADMIN, db)
    assert agent_candidates.get(candidate["id"], ADMIN, db)["status"] == "evaluating"


async def test_copying_origin_to_new_case_id_is_not_independent_holdout(db, monkeypatch):
    p, cases = await prepare(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    copy = create(
        db,
        p,
        goal=cases[0]["goal"],
        data_requirement="none",
        public_egress_authorized=True,
        idempotency_key="copied-origin",
    )
    await drain(db, "candidate-test")
    assert copy["id"] != cases[0]["id"]
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(candidate["id"], [cases[1]["id"], copy["id"]], 4, ADMIN, db)
    assert error.value.code == "independent_samples_required"
    assert not db.list("evaluations", p["id"])


def test_shared_data_checksums_reject_development_holdout_relabeling(db, monkeypatch):
    """Stored records here are explicit fixtures, not reported execution evidence."""
    p = setup(db, monkeypatch)
    source = __import__("backend.tests.test_v2_platform", fromlist=["asset"]).asset(db, p, [{"value": 1}])
    origin = db.put(
        "tasks",
        {
            "project_id": p["id"],
            "status": "succeeded",
            "request": {"goal": "origin"},
            "snapshot": {"inputs": []},
        },
    )
    cid = "candidate-synthetic-source-isolation"
    db.put(
        "agent_candidates",
        {
            "id": cid,
            "project_id": p["id"],
            "status": "candidate",
            "source_task_id": origin["id"],
            "source_task_case_id": "origin-case",
            "specs": [],
            "checksum": service.digest([]),
        },
    )
    ids = []
    for index in range(2):
        case = create(db, p, idempotency_key=f"shared-{index}")
        task = db.put(
            "tasks",
            {
                "project_id": p["id"],
                "status": "succeeded",
                "task_case_id": case["id"],
                "request": {"goal": f"different goal {index}", "task_type": "autonomous_analysis"},
                "autonomous_plan": {"steps": [{"label": "fixture analysis"}]},
                "snapshot": {
                    "inputs": [source],
                    "agents": [{"id": "fixture-agent"}],
                    "workflow": {"nodes": [{"id": "step1", "agent_id": "fixture-agent"}]},
                },
            },
        )
        db.put(
            "nodes",
            {
                "project_id": p["id"],
                "task_id": task["id"],
                "node_id": "step1",
                "agent_id": "fixture-agent",
                "output": {"content": {"text": "explicit source isolation fixture"}},
            },
        )
        stored = db.get("task_cases", case["id"])
        db.put(
            "task_cases",
            {**stored, "task_id": task["id"], "task_ids": [task["id"]], "public_egress_authorized": True},
        )
        ids.append(case["id"])
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(cid, ids, 4, ADMIN, db)
    assert error.value.code == "independent_samples_required"


async def test_changed_frozen_candidate_or_baseline_cannot_be_rebound_for_evaluation(db, monkeypatch):
    p, cases = await prepare(db, monkeypatch, ["data.list_sources"])
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    saved = db.get("agent_candidates", candidate["id"])
    previous_tasks = db.list("tasks", p["id"])
    previous_agents = db.list("agents", p["id"])
    changed_tool_publication(monkeypatch)
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(candidate["id"], [case["id"] for case in cases[1:]], 4, ADMIN, db)
    assert error.value.code == "candidate_binding_changed"
    assert db.list("tasks", p["id"]) == previous_tasks
    assert db.list("agents", p["id"]) == previous_agents
    assert not db.list("evaluations", p["id"])
    with pytest.raises(service.DomainError) as error, db.transaction() as tx:
        agent_candidates._install_evaluation_flow(
            tx,
            cases[1],
            db.get("tasks", cases[1]["task_id"]),
            "changed-fixture",
            "candidate",
            saved["specs"],
        )
    assert error.value.code == "candidate_binding_changed"
    assert db.get("agent_candidates", candidate["id"])["specs"] == saved["specs"]


async def prepare_failed_quality_samples(db, monkeypatch):
    """Run real workers with explicit model doubles and no-partial test policy."""
    p = setup(db, monkeypatch)
    goals = ["给出写作的三条建议", "给出沟通的三条建议", "给出阅读的三条建议"]
    gateway(monkeypatch, rejected_goals=goals[1:])
    cases = []
    for index, goal in enumerate(goals):
        case = create(
            db,
            p,
            goal=goal,
            data_requirement="none",
            public_egress_authorized=True,
            idempotency_key=f"failed-sample-{index}",
        )
        task_cases.start(case["id"], REQUESTER, db)
        requirements = db.claim("failed-sample-test")
        assert requirements["task_case_phase"] == "requirements"
        await runtime.process_claim(db, requirements, "failed-sample-test")
        business = db.claim("failed-sample-test")
        assert not business.get("task_case_phase")
        # Explicit fixture policy before execution: a rejected review must terminate
        # as failed instead of the portal's default partial-result delivery.
        business["request"].update(allow_partial=False, max_revisions=0)
        business = db.put("tasks", business)
        result = await runtime.process_claim(db, business, "failed-sample-test")
        assert result["status"] == ("succeeded" if index == 0 else "failed"), result.get("result")
        if index:
            assert result["reason_code"] == "quality_not_met"
            assert result["quality_verdict"]["model_review"]["verdict"] == "failed"
        assert agent_candidates.evaluation_sample_eligible(result, db)
        cases.append(task_cases.get(case["id"], REQUESTER, db))
    return p, cases


async def test_failed_quality_samples_are_reexecuted_in_four_fresh_arms(db, monkeypatch):
    _p, cases = await prepare_failed_quality_samples(db, monkeypatch)
    assert all(case["evaluation_eligible"] for case in cases[1:])
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    original_failed_ids = {case["task_id"] for case in cases[1:]}
    evaluation = agent_candidates.evaluate(candidate["id"], [case["id"] for case in cases[1:]], 4, ADMIN, db)
    samples = evaluation["evaluation"]["samples"]
    fresh_ids = {sample[arm] for sample in samples for arm in ("baseline", "candidate")}
    assert len(fresh_ids) == 4 and not fresh_ids & original_failed_ids
    for task_id in fresh_ids:
        task = db.get("tasks", task_id)
        assert task["status"] == "accepted" and not task.get("quality_verdict")
        assert not task.get("result") and not task.get("repair_history")
        assert task["request"]["max_revisions"] == 0
    for _ in range(4):
        claimed = db.claim("failed-sample-test")
        assert claimed["id"] in fresh_ids
        result = await runtime.process_claim(db, claimed, "failed-sample-test")
        assert result["status"] == "succeeded", result.get("result")
        assert result["result"]["usage"]["request_count"] > 0
    verified = agent_candidates.get(candidate["id"], ADMIN, db)
    assert verified["evaluation"]["verdict"] == "passed"
    assert all(
        observation["results"]["baseline"]["passed"] for observation in verified["evaluation"]["observations"]
    )
    assert all(db.get("tasks", task_id)["status"] == "failed" for task_id in original_failed_ids)


@pytest.mark.parametrize("invalid", ["requirements", "unplanned", "no_output"])
async def test_failed_requirements_or_unexecuted_plans_are_not_samples(db, monkeypatch, invalid):
    _p, cases = await prepare_failed_quality_samples(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    task = db.get("tasks", cases[1]["task_id"])
    if invalid == "requirements":
        task["task_case_phase"] = "requirements"
    elif invalid == "unplanned":
        task.pop("autonomous_plan")
    else:
        for node in db.list("nodes", task["project_id"], task_id=task["id"]):
            db.put("nodes", {**node, "output": None})
    db.put("tasks", task)
    assert not agent_candidates.evaluation_sample_eligible(task, db)
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(candidate["id"], [case["id"] for case in cases[1:]], 4, ADMIN, db)
    assert error.value.code == "evaluation_sample_invalid"


async def test_failed_quality_samples_still_reject_changed_frozen_data(db, monkeypatch):
    p, cases = await prepare_failed_quality_samples(db, monkeypatch)
    candidate = agent_candidates.create(cases[0]["id"], REQUESTER, db)
    source = fixtures.asset(db, p, [{"value": 1}])
    task = db.get("tasks", cases[1]["task_id"])
    # Explicit tampering fixture: the new policy must not relax frozen-byte checks.
    task["snapshot"]["inputs"] = [source]
    task["request"]["input_refs"] = [source["id"]]
    db.put("tasks", task)
    db.put("data_assets", {**source, "content": [{"value": 2}], "checksum": "changed"})
    with pytest.raises(service.DomainError) as error:
        agent_candidates.evaluate(candidate["id"], [case["id"] for case in cases[1:]], 4, ADMIN, db)
    assert error.value.code == "input_changed"
    assert not db.list("evaluations", p["id"])
