"""Reusable workflow publications and non-content-bearing repair experience."""

from . import service as s
from .tool_registry import validate_frozen

# Only platform-defined checks can cross a task boundary. Never copy free-form model
# corrections, source snippets, task goals, figures or file names into a later prompt.
REPAIR_GUIDANCE = {
    "grounded_citations": "先定位并核对原文引用，再根据证据写结论。",
    "source_refs": "先核对使用的来源属于本次冻结输入，再登记引用。",
    "nonempty_output": "核验每一步确有产物，然后继续下游步骤。",
    "decimal_reconciliation": "重新核算明确列出的加减等式，修正摘要与表格中的冲突。",
    "min_length": "在交付步骤核验文本覆盖要求，同时保留来源。",
    "automatic_review_failed": "将当前交付要求逐项列为验收条件，独立复核完整性。",
    "review_protocol_failed": "先核对审核响应的结构，再判断产物质量。",
}


def input_shape(task):
    return sorted(
        (str(a.get("modality", "")), str(a.get("media_type", "")).lower())
        for a in task["snapshot"].get("inputs", [])
    )


def match_key(task):
    return s.digest([task["request"]["goal"].strip(), task["request"].get("quality"), input_shape(task)])


def verified_repairs(task):
    if not (task.get("quality_verdict") or {}).get("passed") or task["status"] != "succeeded":
        return []
    lessons = []
    approved = {
        ref["tool_id"] for agent in task["snapshot"].get("agents", []) for ref in agent.get("tool_refs", [])
    }
    for entry in task.get("repair_history", []):
        if entry.get("action") != "targeted_replanning" or not (entry.get("outcome") or {}).get("passed"):
            continue
        delta = entry.get("plan_diff") or {}
        if not delta.get("after") or delta["after"] == delta.get("before"):
            continue
        before_tools = {tid for step in delta.get("before", []) for tid in step.get("tool_ids", [])}
        after_tools = {tid for step in delta["after"] for tid in step.get("tool_ids", [])}
        for code in entry.get("failed_checks", []):
            if code in REPAIR_GUIDANCE:
                added = sorted((after_tools - before_tools) & approved)
                guidance = REPAIR_GUIDANCE[code]
                if added:
                    guidance += (
                        " 此前通过复核的修订增加了 " + "、".join(added) + "；须在本次授权范围内判断是否适用。"
                    )
                lessons.append(
                    {
                        "check": code,
                        "guidance": guidance,
                        "revision": entry["revision"],
                        "before_failed": (entry.get("before") or {}).get("failed_checks"),
                        "after_failed": entry["outcome"].get("failed_checks"),
                    }
                )
    return lessons


def source_available(tx, task, project):
    if not task or task["project_id"] != project or task.get("deleted_at"):
        return False
    case = tx.get("task_cases", task.get("task_case_id"))
    if not case or case.get("deleted_at") or case["project_id"] != project:
        return False
    try:
        for asset in task["snapshot"].get("inputs", []):
            if asset["project_id"] != project:
                return False
            s.verify_frozen_asset(asset, tx.get("data_assets", asset["id"]))
    except s.DomainError:
        return False
    return True


def publication(tx, candidate):
    """Read the evaluated publication, never an editor's newer draft or a later version."""
    project = candidate["project_id"]
    if candidate.get("status") != "promoted" or not candidate.get("promoted_workflow_id"):
        return None
    wid = candidate["promoted_workflow_id"]
    flow = tx.get("workflow_versions", wid + ":1")
    head = tx.get("workflows", wid)
    if not flow or not head or head["status"] != "published" or head["current_version"] != 1:
        return None
    if any(
        row.get("project_id") != project or row.get("candidate_id") != candidate["id"] for row in (head, flow)
    ):
        return None
    evaluation = tx.get("evaluations", flow.get("evaluation_id"))
    if (
        not evaluation
        or evaluation["project_id"] != project
        or evaluation.get("candidate_id") != candidate["id"]
    ):
        return None
    if (
        evaluation.get("verdict") != "passed"
        or evaluation.get("status") != "completed"
        or evaluation.get("candidate_checksum") != s.digest(candidate["specs"])
    ):
        return None
    origin = tx.get("tasks", candidate["source_task_id"])
    if not source_available(tx, origin, project) or not (origin.get("quality_verdict") or {}).get("passed"):
        return None
    for sample in evaluation["samples"]:
        # Both arms have the same inputs by construction. Check the actual baseline
        # snapshot so withdrawing a holdout sample also withdraws reuse eligibility.
        if not source_available(tx, tx.get("tasks", sample["baseline"]), project):
            return None
    agents = []
    for node in flow["nodes"]:
        aid = node["agent_id"]
        current = tx.get("agents", aid)
        version = tx.get("agent_versions", f"{aid}:{node['agent_version']}")
        if (
            not current
            or not version
            or current["status"] != "published"
            or current["current_version"] != node["agent_version"]
        ):
            return None
        if any(
            a.get("scope") != "reusable"
            or a.get("candidate_id") != candidate["id"]
            or a.get("project_id") != project
            for a in (current, version)
        ):
            return None
        try:
            validate_frozen(version, tx)
        except ValueError:
            return None
        agents.append({**version, "id": aid})
    return {"flow": flow, "agents": agents, "origin": origin, "evaluation": evaluation}


def prior_experience(tx, task, ready_tools, ready_skills):
    agents, lessons, match = {}, [], None
    for candidate in reversed(tx.list("agent_candidates", task["project_id"], status="promoted")):
        item = publication(tx, candidate)
        if not item:
            continue
        if any(
            any(ref["tool_id"] not in ready_tools for ref in a.get("tool_refs", []))
            or any(ref["skill_id"] not in ready_skills for ref in a.get("skill_refs", []))
            for a in item["agents"]
        ):
            continue
        agents.update({a["id"]: a for a in item["agents"]})
        origin = item["origin"]
        if input_shape(origin) != input_shape(task):
            continue
        if match is None and match_key(origin) == match_key(task):
            match = item
        for lesson in verified_repairs(origin):
            if lesson["guidance"] not in lessons:
                lessons.append(lesson["guidance"])
    return list(agents.values()), lessons, match


def reused_plan(item):
    return {
        "rationale": "任务目标、验收配置和资料格式与已评测流程匹配；使用已发布版本处理本次输入。",
        "missing_data": [],
        "steps": [
            {
                "label": node["label"],
                "instructions": agent["instructions"],
                "reuse_agent_id": agent["id"],
                "tool_ids": [t["tool_id"] for t in agent.get("tool_refs", [])],
                "skill_ids": [t["skill_id"] for t in agent.get("skill_refs", [])],
                "success_criteria": ["满足本次交付要求并通过独立审核"],
            }
            for node, agent in zip(item["flow"]["nodes"], item["agents"], strict=True)
        ],
    }


def frozen_components(agent):
    bindings = agent.get("runtime_bindings") or {}
    return {
        k: [
            {"id": item["id"], "version": item["version"], "checksum": item.get("checksum") or s.digest(item)}
            for item in bindings.get(k, [])
        ]
        for k in ("tools", "skills", "mcp_servers")
    }
