"""Frozen, local, three-stage replay and atomic workflow rebinding for measured case improvements."""

from copy import deepcopy
from time import perf_counter

from ..platform import service as s
from ..platform.db import now
from . import engine, team
from .repair_plan import allowed_action, build_plan, hard_blocked, published_plan

SCOPE = "local_case_team"


def validate(sample, agent_id):
    req, snap = sample["request"], sample["snapshot"]
    code = req.get("case_id")
    project = req.get("project_id")
    if (
        req.get("task_type") != "case_analysis"
        or req.get("mode") != "local"
        or code not in team.catalog()
        or agent_id != team.role_id(project, "analyze", code)
    ):
        s.fail("unsupported_evaluation_scope", "仅支持本项目固定用例流程中的分析专员", 422)
    wf = snap.get("workflow", {})
    versions = snap.get("agents", [])
    profile = snap.get("case_profile", {})
    if (
        wf.get("definition_id") != team.assigned_workflow(project, code)
        or len(versions) != 3
        or {k: v for k, v in profile.items() if k not in {"scope", "reference_batch"}}
        != team.frozen_profile(code)
        or req.get("case_scope", "reference") != "reference"
        or profile.get("scope", "reference") != "reference"
    ):
        s.fail("unsupported_evaluation_scope", "用例合同、代码签名或冻结流程已变化", 422)
    from .reference_batches import source_checks

    if not all(check["passed"] for check in source_checks(profile, snap.get("inputs", []), project)):
        s.fail("unsupported_evaluation_scope", "自我改进只可使用已冻结且来源匹配的可信参考批次", 422)
    from .executors.registry import binding as executor_binding

    for agent in versions:
        if agent.get("structure") != "atomic" or set(agent.get("tool_ids", [])) != {"case_analysis"}:
            s.fail("unsupported_evaluation_scope", "评测只允许原子本地工具，不接受复合或模型替身", 422)
        stage = agent.get("parameters", {}).get("case_stage")
        expected = executor_binding(stage, code if stage == "analyze" else None)["local"]
        bindings = agent.get("executor_bindings")
        if bindings:
            if bindings.get("local") != expected:
                s.fail("unsupported_evaluation_scope", "冻结本地执行器已变化，不能用于确定性重放", 422)
        elif agent.get("model_policy") != "local" or agent.get("execution_strategy") != "fixed":
            s.fail("unsupported_evaluation_scope", "旧版重放只接受固定本地执行器", 422)
    team.verify_team(wf, versions, project, code)
    return next(a for a in versions if a["agent_id"] == agent_id)


def source_diagnosis(sample, repository):
    """Only development-task observations may select a policy; never infer it from a case ID alone."""
    from ..platform.quality import deterministic_quality

    request = sample["request"]
    task_id = sample.get("task_id") or sample["id"]
    outputs = {}
    for stage, expected_type in (("prepare", "case_records"), ("review", "case_report")):
        node = repository.get("nodes", f"{task_id}:0:{stage}")
        if not node or node["project_id"] != request["project_id"] or node["status"] != "succeeded":
            return None
        output = node.get("output") or {}
        if output.get("type") != expected_type:
            return None
        outputs[stage] = output["content"]
        if stage == "review" and hard_blocked(
            deterministic_quality(request, output, sample["snapshot"]["inputs"])
        ):
            return None
    plan = build_plan(request["case_id"], outputs["review"], records=outputs["prepare"]["records"])
    if plan["decision"] != "revise":
        return None
    return {
        "task_id": task_id,
        "source_revision": 0,
        "prepared_node_id": f"{task_id}:0:prepare",
        "review_node_id": f"{task_id}:0:review",
        "review_checksum": s.digest(outputs["review"]),
        # Source targets (clause topics, record fields) stay inside source-governed
        # task nodes/evaluation samples, not in persistent published metadata.
        "policy_version": plan["policy_version"],
        "action": plan["action"],
        "label": plan["label"],
        "failed_checks": plan["failed_checks"],
        "rerun_stages": plan["rerun_stages"],
        "reused_stages": plan["reused_stages"],
    }


def batch_checks(samples, baseline, candidate, tx):
    if candidate["instructions"] != baseline["instructions"] or set(candidate["tool_ids"]) != set(
        baseline["tool_ids"]
    ):
        s.fail("permission_denied", "用例候选只能调整本地分析策略，不得调整指令或工具", 403)
    params = {**baseline["parameters"], **candidate.get("parameters", {})}
    code = samples[0]["request"]["case_id"]
    strategy = params.get("case_strategy", "strict")
    if (strategy not in {"strict", "adaptive"} and not allowed_action(code, strategy)) or {
        k: v for k, v in params.items() if k != "case_strategy"
    } != {k: v for k, v in baseline["parameters"].items() if k != "case_strategy"}:
        s.fail("permission_denied", "不能改变分析角色、用例、解析器或审核规则", 403)
    first = samples[0]["snapshot"]["workflow"]
    flow_id = first["definition_id"]
    current = tx.get("workflows", flow_id, for_update=True)
    if not current or current["status"] != "published" or current["current_version"] != first["version"]:
        s.fail("baseline_changed", "冻结流程不是当前已发布版本", 409)
    source_sets = []
    for item in samples:
        frozen = validate(item, baseline["agent_id"])
        wf = item["snapshot"]["workflow"]
        if wf != first or frozen != {**baseline, "id": baseline["agent_id"]}:
            s.fail("baseline_changed", "冻结样本必须使用同一套当前不可变流程及分析专员", 409)
        hashes = {a["checksum"] for a in item["snapshot"]["inputs"]}
        if not hashes or any(hashes & prior for prior in source_sets):
            s.fail(
                "insufficient_evaluation_data",
                "开发样本与保留样本必须使用互不重叠的实际来源内容，换文件名或资产ID不算独立样本",
                422,
            )
        source_sets.append(hashes)
    fields = {"workflow_id": flow_id, "baseline_flow": deepcopy(first), "baseline_flow_hash": s.digest(first)}
    if strategy != "strict":
        # Compatibility for old API clients: resolve a new "adaptive" request against the observed defect.
        # Already published adaptive versions and saved candidates retain their historical execution strategy.
        for sample in samples:
            if sample["split"] != "development":
                continue
            diagnosis = source_diagnosis(sample, tx)
            if diagnosis and strategy in {"adaptive", diagnosis["action"]}:
                params["case_strategy"] = diagnosis["action"]
                fields["source_repair"] = diagnosis
                break
        else:
            s.fail("unsupported_repair_strategy", "开发样本没有该动作能够修正的真实审核失败", 422)
    candidate["parameters"] = params
    return fields


def replay_sample(sample, version):
    from ..platform.quality import deterministic_quality
    from ..platform.runtime import _asset_bytes
    from .role_tools import run_stage

    validate(sample, version["agent_id"])
    req, snap = sample["request"], sample["snapshot"]
    profile = snap["case_profile"]
    inputs = snap["inputs"]
    roles = {a["parameters"]["case_stage"]: a for a in snap["agents"]}
    # Use the same role grants, supplementary tools and independent checks as a real task.
    roles["analyze"] = version
    strategy = version.get("parameters", {}).get("case_strategy", "strict")
    feedback = []
    history = []
    start = perf_counter()
    if strategy not in {"strict", "adaptive"} and not allowed_action(profile["case_id"], strategy):
        s.fail("invalid_candidate", "未知分析策略")
    plan = published_plan(profile["case_id"], strategy, inputs)
    applied = []
    prepared = None
    for revision in range(req.get("max_revisions", 1) + 1):
        kwargs = {"repair_plan": plan} if plan else {}
        if prepared is None or not plan or "prepare" in plan["rerun_stages"]:
            prepared = run_stage(
                engine.execute,
                "prepare",
                profile,
                inputs,
                [],
                _asset_bytes,
                feedback,
                revision,
                agent=roles["prepare"],
                **kwargs,
            )
        hints = feedback or (["按冻结策略执行受控二次检索"] if strategy == "adaptive" and not plan else [])
        analysis = run_stage(
            engine.execute,
            "analyze",
            profile,
            inputs,
            [prepared],
            _asset_bytes,
            hints,
            revision,
            agent=roles["analyze"],
            **kwargs,
        )
        report = run_stage(
            engine.execute,
            "review",
            profile,
            inputs,
            [prepared, analysis],
            _asset_bytes,
            feedback,
            revision,
            agent=roles["review"],
            **kwargs,
        )
        quality = deterministic_quality(req, report, inputs)
        if plan:
            applied.append(plan["action"])
        history.append(
            {
                "revision": revision,
                "passed": quality["passed"],
                "gaps": report["content"]["gaps"],
                "check_count": len(report["content"]["review"]),
                "plan": plan,
            }
        )
        if quality["passed"] or hard_blocked(quality) or revision >= req.get("max_revisions", 1):
            break
        plan = build_plan(
            profile["case_id"],
            report["content"],
            records=prepared["content"]["records"],
            attempted_actions=applied,
        )
        if plan["decision"] != "revise":
            history[-1]["next_plan"] = plan
            break
        feedback = [c["message"] for c in report["content"]["review"] if not c["passed"]]
        if not feedback:
            feedback = quality.get("issues", [])
    return {
        "output": report,
        "quality": quality,
        "duration_ms": round((perf_counter() - start) * 1000, 3),
        "attempts": len(history),
        "history": history,
        "cost_cny": 0,
    }


def quality_gain(results):
    def delta(p):
        b, a = p["baseline"], p["candidate"]
        bp, ap = b["quality"]["passed"], a["quality"]["passed"]
        if not ap and b.get("output") and a.get("output"):
            prior = {c["name"] for c in b["output"]["content"]["review"] if not c["passed"]}
            current = {c["name"] for c in a["output"]["content"]["review"] if not c["passed"]}
            if current - prior:
                return -1  # A partial result lost a previously satisfied check.
        if bp != ap:
            return 1 if ap else -1
        if ap:
            return (b.get("attempts", 1) > a.get("attempts", 1)) - (
                b.get("attempts", 1) < a.get("attempts", 1)
            )
        return 0

    steps = [(p["split"], delta(p)) for p in results]
    regression = any(d < 0 for _, d in steps)
    gained = any(d > 0 and split == "development" for split, d in steps)
    return (
        gained,
        regression,
        sum(p["baseline"].get("attempts", 1) - p["candidate"].get("attempts", 1) for p in results),
    )


def bind_promoted(record, version, tx):
    base = record["baseline_flow"]
    fid = record["workflow_id"]
    flow = tx.get("workflows", fid, for_update=True)
    stored = tx.get("workflow_versions", base["id"])
    if (
        not flow
        or flow["status"] != "published"
        or flow["current_version"] != base["version"]
        or flow["nodes"] != base["nodes"]
        or not stored
        or s.digest(stored) != record["baseline_flow_hash"]
        or s.digest(base) != record["baseline_flow_hash"]
    ):
        s.fail("baseline_changed", "流程版本或编排内容已改变，拒绝覆盖", 409)
    nodes = deepcopy(base["nodes"])
    nodes[1]["agent_version"] = version
    next_id = max(v["version"] for v in tx.list("workflow_versions", definition_id=fid)) + 1
    flow.update(nodes=nodes, current_version=next_id)
    saved = tx.put("workflows", flow)
    version_fields = {}
    if diagnosis := record.get("source_repair"):
        version_fields["repair_origin"] = {
            "improvement_id": record["id"],
            "source_task_id": diagnosis["task_id"],
            "review_node_id": diagnosis["review_node_id"],
            "failed_checks": diagnosis["failed_checks"],
            "action": diagnosis["action"],
        }
    new = tx.put(
        "workflow_versions",
        {**saved, "id": f"{fid}:{next_id}", "definition_id": fid, "version": next_id, **version_fields},
    )
    record.update(candidate_flow_version=next_id, candidate_flow_hash=s.digest(new))


def restore_binding(record, tx):
    fid = record["workflow_id"]
    flow = tx.get("workflows", fid, for_update=True)
    v = tx.get("workflow_versions", f"{fid}:{record['candidate_flow_version']}")
    base = tx.get("workflow_versions", record["baseline_flow"]["id"])
    if (
        not flow
        or flow["status"] != "published"
        or flow["current_version"] != record["candidate_flow_version"]
        or not v
        or s.digest(v) != record["candidate_flow_hash"]
        or flow["nodes"] != v["nodes"]
        or not base
        or s.digest(base) != record["baseline_flow_hash"]
    ):
        s.fail("version_changed", "流程指针、草稿或后续发布已变化，不能覆盖", 409)
    flow.update(nodes=deepcopy(base["nodes"]), current_version=base["version"])
    tx.put("workflows", flow)


def regression_evidence(candidate, task, repository):
    """A failure, missing source or outage alone is not a measured reason to roll back."""
    if task["snapshot"]["workflow"].get("version") != candidate["candidate_flow_version"]:
        return None
    try:
        for a in task["snapshot"]["inputs"]:
            s.verify_frozen_asset(a, repository.get("data_assets", a["id"]))
        pair = {label: replay_sample(task, candidate[label]) for label in ("baseline", "candidate")}
    except (s.DomainError, ValueError, OSError, RuntimeError, TypeError, KeyError):
        return None
    if pair["baseline"]["quality"]["passed"] and not pair["candidate"]["quality"]["passed"]:
        return {
            "task_id": task["id"],
            "created_at": now(),
            "source_checksums": [a["checksum"] for a in task["snapshot"]["inputs"]],
            **{
                label + "_quality": {
                    "passed": pair[label]["quality"]["passed"],
                    "issue_codes": [
                        issue.get("code")
                        for issue in pair[label]["quality"].get("issues", [])
                        if isinstance(issue, dict)
                    ],
                }
                for label in ("baseline", "candidate")
            },
            "baseline_result_hash": s.digest(pair["baseline"]["output"]),
            "candidate_result_hash": s.digest(pair["candidate"]["output"]),
        }
    return None
