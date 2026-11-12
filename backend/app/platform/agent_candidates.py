"""Transferable task teams require real development/holdout replay before reuse.

Only tool/skill combinations transfer. Source text, questions, generated answers,
source IDs and task-specific instructions never enter the reusable definition.
"""

import copy
import uuid

from . import service as s
from .db import TERMINAL
from .task_cases import _principal, required
from .tool_registry import freeze_bindings, validate_frozen


def _safe_agent(source, index):
    keys = [
        "project_id",
        "purpose",
        "structure",
        "capabilities",
        "modalities",
        "tool_ids",
        "model_policy",
        "parameters",
        "execution_strategy",
        "tool_refs",
        "skill_refs",
        "executor_bindings",
    ]
    spec = {k: copy.deepcopy(source[k]) for k in keys if k in source}
    spec.update(
        name=f"可复用资料分析步骤 {index + 1}",
        description="来自通过审核任务的工具与技能组合；已移除任务原文、来源与产物。",
        instructions=(
            "仅根据本次用户目标、授权输入及前一步结果完成本步骤。先阅读适用技能，使用已授权工具。"
            "输出可溯源证据与明确缺口，不复用任何历史任务的数值、来源或结论。"
            "最终步骤须覆盖本次全部交付要求；无输入的概念推理不得编造引用。"
        ),
        status="published",
        current_version=1,
        explicit_workflow_only=True,
        scope="candidate",
        candidate_status="candidate",
    )
    return spec


def create_from_run(tx, task):
    if (
        task["request"]["task_type"] != "autonomous_analysis"
        or task["status"] != "succeeded"
        or task.get("candidate_evaluation_id")
    ):
        return None
    if not task.get("task_case_id") or task.get("task_case_phase"):
        return None
    cid = "candidate-" + task["id"]
    existing = tx.get("agent_candidates", cid)
    if existing:
        return existing
    frozen = {a["id"]: a for a in task["snapshot"]["agents"]}
    nodes = task["snapshot"]["workflow"]["nodes"]
    specs = [_safe_agent(frozen[n["agent_id"]], i) for i, n in enumerate(nodes)]
    for spec in specs:
        spec["runtime_bindings"] = freeze_bindings(spec, tx)
    candidate = {
        "id": cid,
        "project_id": task["project_id"],
        "status": "candidate",
        "kind": "sequential_agent_team",
        "name": "任务工具流程候选 " + task["id"][:8],
        "source_task_id": task["id"],
        "source_task_case_id": task["task_case_id"],
        "specs": specs,
        "checksum": s.digest(specs),
        "evaluation_ids": [],
        "promoted_workflow_id": None,
        "policy": "independent-development-holdout-v1",
        "limitations": [
            "候选只提取工具/技能组合，不包含来源数据、原问题或任务专用指令。",
            "至少两个独立样本分别进行基线与候选真实执行，未达到质量/费用门槛不进入复用库。",
        ],
    }
    for original in frozen.values():
        definition = tx.get("agents", original["id"])
        if definition and definition.get("scope") == "task":
            tx.put("agents", {**definition, "candidate_id": cid, "candidate_status": "candidate"})
    return tx.put("agent_candidates", candidate)


def create(case_id, user, repository):
    with repository.transaction() as tx:
        case = required(tx, case_id, user, write=True)
        task = tx.get("tasks", case.get("task_id")) if case.get("task_id") else None
        candidate = create_from_run(tx, task) if task else None
        if not candidate:
            s.fail("candidate_not_ready", "仅可从已通过审核的自主分析任务提取候选", 409)
        return _view(candidate)


def _required(tx, cid, user):
    candidate = s.required("agent_candidates", cid, user, repository=tx)
    if user.get("role") not in {"admin", "operator"}:
        s.fail("permission_denied", "评测与资产晋升需项目管理权限", 403)
    if candidate["checksum"] != s.digest(candidate["specs"]):
        s.fail("candidate_changed", "候选内容与冻结校验和不符", 409)
    return candidate


def _view(candidate):
    return {k: v for k, v in candidate.items() if k != "specs"} | {
        "steps": [
            {
                "name": s["name"],
                "tool_ids": [r["tool_id"] for r in s.get("tool_refs", [])],
                "skill_ids": [r["skill_id"] for r in s.get("skill_refs", [])],
            }
            for s in candidate["specs"]
        ]
    }


def _install_evaluation_flow(tx, source_case, source_task, evaluation_id, arm, specs):
    nodes = []
    for index, original in enumerate(specs):
        spec = copy.deepcopy(original)
        try:
            validate_frozen(spec, tx)
        except ValueError:
            s.fail("candidate_binding_changed", "候选或基线依赖已变化，请以当前有效版本重新形成样本", 409)
        aid = f"eval-{evaluation_id}-{arm}-{index}"
        spec.update(
            id=aid,
            project_id=source_case["project_id"],
            status="published",
            current_version=1,
            scope="evaluation",
            task_case_id=source_case["id"],
            explicit_workflow_only=True,
        )
        # Independent immutable evaluation definitions; never mutate original agents.
        spec["runtime_bindings"] = freeze_bindings(spec, tx)
        tx.put("agents", spec)
        tx.put(
            "agent_versions",
            {
                **spec,
                "id": aid + ":1",
                "definition_id": aid,
                "agent_id": aid,
                "version": 1,
                "checksum": s.digest(spec),
            },
        )
        nodes.append(
            {
                "id": f"step{index + 1}",
                "label": spec["name"],
                "agent_id": aid,
                "agent_version": 1,
                "depends_on": [f"step{index}"] if index else [],
                "config": {},
            }
        )
    wid = f"eval-{evaluation_id}-{arm}"
    flow = {
        "id": wid,
        "project_id": source_case["project_id"],
        "name": "独立评测 " + arm,
        "task_type": "autonomous_analysis",
        "status": "published",
        "current_version": 1,
        "scope": "evaluation",
        "task_case_id": source_case["id"],
        "nodes": nodes,
    }
    tx.put("workflows", flow)
    tx.put(
        "workflow_versions",
        {**flow, "id": wid + ":1", "definition_id": wid, "version": 1, "checksum": s.digest(flow)},
    )
    return wid, nodes


def evaluation_sample_eligible(task, repository):
    """A completed business attempt can be replayed even when its quality failed.

    This is structural eligibility only. Evaluation still rechecks permissions,
    public-data consent, frozen input hashes and sample independence below.
    A planner/bootstrap or requirements failure supplies no executable baseline.
    """
    if (
        not task
        or task.get("status") not in {"succeeded", "partial", "failed"}
        or task.get("task_case_phase")
        or not task.get("task_case_id")
        or task.get("request", {}).get("task_type") != "autonomous_analysis"
        or not (task.get("autonomous_plan") or {}).get("steps")
    ):
        return False
    snapshot = task.get("snapshot") or {}
    frozen_agents = {agent["id"] for agent in snapshot.get("agents", [])}
    frozen_nodes = {
        node["id"]: node
        for node in (snapshot.get("workflow") or {}).get("nodes", [])
        if node.get("agent_id") in frozen_agents
    }
    if not frozen_nodes:
        return False
    return any(
        node.get("node_id") in frozen_nodes
        and node.get("agent_id") == frozen_nodes[node["node_id"]]["agent_id"]
        and isinstance(node.get("output"), dict)
        and bool(node["output"].get("content"))
        for node in repository.list("nodes", task["project_id"], task_id=task["id"])
    )


def evaluate(cid, case_ids, max_cost_cny, user, repository):
    with repository.transaction() as tx:
        candidate = _required(tx, cid, user)
        if candidate["status"] == "evaluating":
            return get(cid, user, tx)
        if len(set(case_ids)) != len(case_ids) or candidate["source_task_case_id"] in case_ids:
            s.fail(
                "independent_samples_required",
                "评测必须使用两个以上独立任务，不能重复或使用候选来源任务",
                422,
            )
        origin = tx.get("tasks", candidate["source_task_id"])
        if not origin:
            s.fail("candidate_origin_missing", "候选来源执行记录不可用，无法核验独立样本", 409)
        origin_hashes = {a["checksum"] for a in origin["snapshot"]["inputs"]}
        origin_signature = s.digest(
            [" ".join(origin["request"]["goal"].casefold().split()), sorted(origin_hashes)]
        )
        samples, signatures, seen_hashes = [], {origin_signature}, set(origin_hashes)
        for case_id in case_ids:
            case = required(tx, case_id, user)
            if case["project_id"] != candidate["project_id"]:
                s.fail("permission_denied", "评测样本不能跨项目", 403)
            task = tx.get("tasks", case.get("task_id")) if case.get("task_id") else None
            if not evaluation_sample_eligible(task, tx):
                s.fail(
                    "evaluation_sample_invalid",
                    "评测样本须已完成业务规划并有真实节点产物，允许质量失败样本",
                    422,
                )
            if not case.get("public_egress_authorized"):
                s.fail("data_egress_blocked", "样本未授权云模型评测", 403)
            if any(a.get("classification") != "public" for a in task["snapshot"]["inputs"]):
                s.fail("data_egress_blocked", "云评测只可使用公开资料", 403)
            for a in task["snapshot"]["inputs"]:
                s.verify_frozen_asset(a, tx.get("data_assets", a["id"]))
            hashes = {a["checksum"] for a in task["snapshot"]["inputs"]}
            signature = s.digest([" ".join(task["request"]["goal"].casefold().split()), sorted(hashes)])
            if signature in signatures or hashes & seen_hashes:
                s.fail(
                    "independent_samples_required",
                    "来源任务、开发样本和留出样本存在重复目标/输入或共享数据字节，不能视为独立评测",
                    422,
                )
            signatures.add(signature)
            seen_hashes.update(hashes)
            samples.append((case, task, signature))
        eid = uuid.uuid4().hex
        records = []
        per_run = round(max_cost_cny / (2 * len(samples)), 6)
        for sample_index, (case, baseline, signature) in enumerate(samples):
            frozen = {a["id"]: a for a in baseline["snapshot"]["agents"]}
            baseline_specs = [frozen[n["agent_id"]] for n in baseline["snapshot"]["workflow"]["nodes"]]
            sample_id = f"{eid}-{sample_index}"
            arms = {}
            for arm, specs in (("baseline", baseline_specs), ("candidate", candidate["specs"])):
                wid, nodes = _install_evaluation_flow(tx, case, baseline, sample_id, arm, specs)
                body = {
                    **baseline["request"],
                    "workflow_id": wid,
                    "max_cost_cny": per_run,
                    "max_revisions": 0,
                    "idempotency_key": f"candidate-evaluation:{sample_id}:{arm}",
                    "deadline_seconds": 1800,
                    "allow_partial": True,
                    "name": "候选独立评测 " + arm,
                }
                task = s.create_task(body, _principal(user), tx, task_case_id=case["id"])
                task.update(
                    candidate_evaluation_id=eid,
                    candidate_arm=arm,
                    autonomous_plan_revision=0,
                    autonomous_plan={
                        "rationale": "冻结评测配置真实重放；禁止在评测中自动更换候选",
                        "steps": [
                            {
                                "label": node["label"],
                                "instructions": spec["instructions"],
                                "tool_ids": [t["tool_id"] for t in spec.get("tool_refs", [])],
                                "skill_ids": [k["skill_id"] for k in spec.get("skill_refs", [])],
                                "success_criteria": ["符合样本目标与完整审核"],
                            }
                            for node, spec in zip(nodes, specs, strict=True)
                        ],
                    },
                )
                tx.put("tasks", task)
                arms[arm] = task["id"]
            records.append(
                {
                    "task_case_id": case["id"],
                    "sample_signature": signature,
                    "split": "holdout" if sample_index == len(samples) - 1 else "development",
                    **arms,
                }
            )
        evaluation = {
            "id": eid,
            "project_id": candidate["project_id"],
            "candidate_id": cid,
            "status": "running",
            "kind": "task_team_replay",
            "candidate_checksum": candidate["checksum"],
            "samples": records,
            "max_cost_cny": max_cost_cny,
            "policy": candidate["policy"],
            "checks": [],
            "verdict": "pending",
        }
        tx.put("evaluations", evaluation)
        candidate.update(
            status="evaluating", evaluation_ids=[*candidate["evaluation_ids"], eid], current_evaluation_id=eid
        )
        tx.put("agent_candidates", candidate)
        _mark_agents(tx, candidate, "evaluating")
        s.audit(tx, candidate["project_id"], "candidate.evaluation_started", eid, user)
        return {**_view(candidate), "evaluation": evaluation}


def _mark_agents(tx, candidate, status):
    for agent in tx.list("agents", candidate["project_id"]):
        if agent.get("candidate_id") == candidate["id"]:
            tx.put("agents", {**agent, "candidate_status": status})


def _refresh(tx, candidate):
    eid = candidate.get("current_evaluation_id")
    if not eid:
        return candidate, None
    evaluation = tx.get("evaluations", eid)
    if evaluation["status"] != "running":
        return candidate, evaluation
    complete, observations = True, []
    for sample in evaluation["samples"]:
        arms = {arm: tx.get("tasks", sample[arm]) for arm in ("baseline", "candidate")}
        if any(task["status"] not in TERMINAL for task in arms.values()):
            complete = False
            continue
        values = {
            arm: {
                "passed": task["status"] == "succeeded"
                and bool((task.get("quality_verdict") or {}).get("passed")),
                "hard_failures": len((task.get("quality_verdict") or {}).get("hard_failures", [])),
                "cost_cny": (task.get("result") or {}).get("usage", {}).get("cost_cny"),
                "accounting_complete": (task.get("result") or {})
                .get("usage", {})
                .get("accounting_complete", False),
                "artifact_checksums": [
                    a["checksum"] for a in (task.get("result") or {}).get("artifacts", [])
                ],
            }
            for arm, task in arms.items()
        }
        observations.append({**sample, "results": values})
    if not complete:
        return candidate, {**evaluation, "observations": observations}
    quality_ok = all(
        o["results"]["candidate"]["passed"] and not o["results"]["candidate"]["hard_failures"]
        for o in observations
    )
    billing_ok = all(
        all(a["accounting_complete"] and a["cost_cny"] is not None for a in o["results"].values())
        for o in observations
    )
    baseline_cost = sum(o["results"]["baseline"]["cost_cny"] or 0 for o in observations)
    candidate_cost = sum(o["results"]["candidate"]["cost_cny"] or 0 for o in observations)
    quality_gain = any(
        not o["results"]["baseline"]["passed"] and o["results"]["candidate"]["passed"] for o in observations
    )
    cost_gain = baseline_cost > 0 and candidate_cost <= baseline_cost * 0.9
    checks = [
        {
            "name": "independent_development_and_holdout",
            "passed": len(observations) >= 2 and len({o["split"] for o in observations}) == 2,
        },
        {"name": "all_candidate_quality_passed", "passed": quality_ok},
        {"name": "all_charges_settled", "passed": billing_ok},
        {
            "name": "bounded_cost_regression",
            "passed": billing_ok and candidate_cost <= baseline_cost * 1.25 + 0.02,
        },
        {"name": "measured_quality_or_cost_gain", "passed": quality_gain or cost_gain},
    ]
    passed = all(c["passed"] for c in checks)
    evaluation.update(
        status="completed",
        verdict="passed" if passed else "failed",
        checks=checks,
        observations=observations,
        totals={"baseline_cost_cny": baseline_cost, "candidate_cost_cny": candidate_cost},
    )
    tx.put("evaluations", evaluation)
    candidate.update(status="evaluated" if passed else "candidate", evaluation_verdict=evaluation["verdict"])
    candidate = tx.put("agent_candidates", candidate)
    _mark_agents(tx, candidate, candidate["status"])
    return candidate, evaluation


def get(cid, user, repository):
    with repository.transaction() as tx:
        candidate = _required(tx, cid, user)
        candidate, evaluation = _refresh(tx, candidate)
        return {**_view(candidate), "evaluation": evaluation}


def history(project_id, user, repository):
    s.project_access(project_id, user, repository=repository)
    if user.get("role") not in {"admin", "operator"}:
        s.fail("permission_denied", "候选评测仅对项目管理人员开放", 403)
    return [get(c["id"], user, repository) for c in repository.list("agent_candidates", project_id)]


def promote(cid, user, repository):
    with repository.transaction() as tx:
        candidate = _required(tx, cid, user)
        candidate, evaluation = _refresh(tx, candidate)
        if candidate.get("promoted_workflow_id"):
            return _view(candidate)
        if (
            not evaluation
            or evaluation["verdict"] != "passed"
            or evaluation["candidate_checksum"] != candidate["checksum"]
        ):
            s.fail("evaluation_required", "尚未通过真实独立评测，不可进入复用库", 409)
        for observation in evaluation["observations"]:
            for arm in ("baseline", "candidate"):
                task = tx.get("tasks", observation[arm])
                for frozen in task["snapshot"]["inputs"]:
                    s.verify_frozen_asset(frozen, tx.get("data_assets", frozen["id"]))
                checksums = [
                    a["checksum"] for a in tx.list("artifacts", task["project_id"], task_id=task["id"])
                ]
                if sorted(checksums) != sorted(observation["results"][arm]["artifact_checksums"]):
                    s.fail("evaluation_evidence_changed", "评测产物已变化，拒绝晋升", 409)
        nodes = []
        for index, source in enumerate(candidate["specs"]):
            spec = copy.deepcopy(source)
            validate_frozen(spec, tx)
            aid = f"reusable-{cid.removeprefix('candidate-')}-{index}"
            spec.update(
                id=aid,
                scope="reusable",
                explicit_workflow_only=False,
                candidate_id=cid,
                candidate_status="promoted",
                evaluation_id=evaluation["id"],
            )
            tx.put("agents", spec)
            tx.put(
                "agent_versions",
                {
                    **spec,
                    "id": aid + ":1",
                    "definition_id": aid,
                    "agent_id": aid,
                    "version": 1,
                    "checksum": s.digest(spec),
                },
            )
            nodes.append(
                {
                    "id": f"step{index + 1}",
                    "label": spec["name"],
                    "agent_id": aid,
                    "agent_version": 1,
                    "depends_on": [f"step{index}"] if index else [],
                    "config": {},
                }
            )
        wid = "reusable-" + cid.removeprefix("candidate-")
        flow = {
            "id": wid,
            "project_id": candidate["project_id"],
            "name": candidate["name"],
            "description": "通过独立开发/留出真实评测的顺序流程",
            "task_type": "autonomous_analysis",
            "scope": "reusable",
            "status": "published",
            "current_version": 1,
            "candidate_id": cid,
            "evaluation_id": evaluation["id"],
            "nodes": nodes,
        }
        tx.put("workflows", flow)
        tx.put(
            "workflow_versions",
            {**flow, "id": wid + ":1", "definition_id": wid, "version": 1, "checksum": s.digest(flow)},
        )
        candidate.update(status="promoted", promoted_workflow_id=wid)
        tx.put("agent_candidates", candidate)
        _mark_agents(tx, candidate, "promoted")
        s.audit(tx, candidate["project_id"], "candidate.promoted", cid, user)
        return _view(candidate)
