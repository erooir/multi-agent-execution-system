"""Frozen, local, three-stage replay and atomic workflow rebinding for measured case improvements."""

from copy import deepcopy
from time import perf_counter

from ..platform import service as s
from ..platform.db import now
from . import engine, team

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
    if any(
        a.get("structure") != "atomic"
        or a.get("model_policy") != "local"
        or set(a.get("tool_ids", [])) != {"case_analysis"}
        for a in versions
    ):
        s.fail("unsupported_evaluation_scope", "评测只允许固定本地工具，不接受复合或模型替身", 422)
    team.verify_team(wf, versions, project, code)
    return next(a for a in versions if a["agent_id"] == agent_id)


def batch_checks(samples, baseline, candidate, tx):
    if candidate["instructions"] != baseline["instructions"] or set(candidate["tool_ids"]) != set(
        baseline["tool_ids"]
    ):
        s.fail("permission_denied", "用例候选只能调整本地分析策略，不得调整指令或工具", 403)
    # Only one bounded strategy dimension is currently executable and measurable.
    params = {**baseline["parameters"], **candidate.get("parameters", {})}
    if params.get("case_strategy", "strict") not in {"strict", "adaptive"} or {
        k: v for k, v in params.items() if k != "case_strategy"
    } != {k: v for k, v in baseline["parameters"].items() if k != "case_strategy"}:
        s.fail("permission_denied", "不能改变分析角色、用例、解析器或审核规则", 403)
    candidate["parameters"] = params
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
    return {"workflow_id": flow_id, "baseline_flow": deepcopy(first), "baseline_flow_hash": s.digest(first)}


def replay_sample(sample, version):
    from ..platform.quality import deterministic_quality
    from ..platform.runtime import _asset_bytes

    validate(sample, version["agent_id"])
    req, snap = sample["request"], sample["snapshot"]
    profile = snap["case_profile"]
    inputs = snap["inputs"]
    strategy = version.get("parameters", {}).get("case_strategy", "strict")
    feedback = []
    history = []
    start = perf_counter()
    if strategy not in {"strict", "adaptive"}:
        s.fail("invalid_candidate", "未知分析策略")
    for revision in range(req.get("max_revisions", 1) + 1):
        prepared = engine.execute("prepare", profile, inputs, [], _asset_bytes, feedback, revision)
        hints = feedback or (["按冻结策略执行受控二次检索"] if strategy == "adaptive" else [])
        analysis = engine.execute("analyze", profile, inputs, [prepared], _asset_bytes, hints, revision)
        report = engine.execute(
            "review", profile, inputs, [prepared, analysis], _asset_bytes, feedback, revision
        )
        quality = deterministic_quality(req, report, inputs)
        history.append(
            {
                "revision": revision,
                "passed": quality["passed"],
                "gaps": report["content"]["gaps"],
                "check_count": len(report["content"]["review"]),
            }
        )
        if quality["passed"] or quality.get("hard_failures"):
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
    new = tx.put(
        "workflow_versions", {**saved, "id": f"{fid}:{next_id}", "definition_id": fid, "version": next_id}
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
