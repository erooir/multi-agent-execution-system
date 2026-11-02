"""Optional public-source model interpretation, separate from deterministic casework.

No applicant-supplied prompt, parent goal, case deliverable or project-wide
knowledge enters this task. It uses the original model gateway/ledger and
normal immutable asset versions, task leases, emit-time checks and review.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field

from . import service as s
from .contracts import AgentInput, Contract
from .quality import text_of
from .tool_registry import BASE_TOOL_IDS, catalog, freeze_bindings

FOCUS = {
    "claim_check": ("陈述核查", "梳理资料中明确的事实及其依据，区分推断、矛盾和资料缺口。"),
    "timeline": ("时间线核查", "按资料明确给出的日期或先后关系整理时间线；不得补造日期。"),
    "comparison": ("来源对比", "比较资料间一致、矛盾和无法比较的陈述，说明依据和信息缺口。"),
    "scientific_terms": (
        "术语与测量复核",
        (
            "针对科学文献给出原文术语、上下文支持的中文释义及短逐字引文。分别标明哪些结果属于实测、哪些属于模型模拟或估计。"
            "缩略语仅在所给原文明确给出全称时展开；如有歧义保留英文并说明缺什么证据。"
            "仅解读所给节选，勿补充外部知识、重算数值或评价整篇论文。"
        ),
    ),
}


class PublicEvidenceRequest(Contract):
    project_id: str = Field(min_length=1, max_length=100)
    input_refs: list[str] = Field(min_length=1, max_length=12)
    focus: Literal["claim_check", "timeline", "comparison", "scientific_terms"] = "claim_check"
    idempotency_key: str = Field(min_length=1, max_length=140)
    max_cost_cny: float = Field(gt=0, le=0.8, allow_inf_nan=False)
    public_egress_authorized: Literal[True]
    case_task_id: str | None = Field(default=None, min_length=1, max_length=100)


def _active_public(tx, project_id, key, user):
    asset = s.required("data_assets", key, user, repository=tx)
    if asset["project_id"] != project_id:
        s.fail("permission_denied", "不能引用其他项目的资料", 403)
    s.usable_asset(asset)
    if asset.get("classification") != "public":
        s.fail("data_egress_blocked", "语义取证仅允许明确授权外发的公开资料", 403)
    if asset.get("modality") not in {"text", "structured"} or asset.get("content") is None:
        s.fail("unsupported_modality", "本入口仅接受已登记的文本或结构化内容", 422)
    s.verify_frozen_asset(asset, asset)
    return asset


def _validate_parent(tx, parent_id, project_id, assets, user):
    parent = s.required("tasks", parent_id, user, repository=tx)
    if parent["project_id"] != project_id:
        s.fail("permission_denied", "关联用例必须属于当前项目", 403)
    if parent.get("request", {}).get("task_type") != "case_analysis" or parent["status"] not in {
        "succeeded",
        "partial",
    }:
        s.fail("case_not_ready", "只可关联已交付结果的用例任务", 409)
    frozen = parent.get("snapshot", {}).get("inputs", [])
    artifacts = tx.list("artifacts", project_id, task_id=parent_id)
    refs = set(parent["request"].get("input_refs", []))
    refs.update(item["id"] for item in frozen)
    refs.update(ref for artifact in artifacts for ref in artifact.get("source_refs", []))
    if not artifacts or set(assets) != refs or refs != {item["id"] for item in frozen}:
        s.fail("case_sources_mismatch", "必须提供用例全部已冻结来源，不能用公开子集替代", 409)
    for item in frozen:
        actual = assets[item["id"]]
        if item.get("classification") != "public":
            s.fail("data_egress_blocked", "用例包含受理时未授权外发的来源", 403)
        s.verify_frozen_asset(item, actual)


def _workflow(tx, project_id):
    # Provision one well-known, immutable specialist. No user-edited instructions,
    # arbitrary workflow output or unapproved project knowledge reach the model.
    aid = f"{project_id}-public-evidence-agent"
    wid = f"{project_id}-public-evidence-flow"
    available = catalog()
    agent = AgentInput(
        project_id=project_id,
        name="公开资料语义取证专员",
        description="读取获准外发的公开文本与表格，生成可追溯的辅助解释。",
        purpose="specialized",
        modalities=["text", "structured"],
        capabilities=["text_analysis"],
        tool_ids=["text_analysis"],
        model_policy="cloud",
        execution_strategy="tool_agent",
        tool_refs=[
            {"tool_id": t["id"], "version": t["version"]}
            for t in available["tools"]
            if t["id"] in BASE_TOOL_IDS
        ],
        skill_refs=[{"skill_id": k["id"], "version": k["version"]} for k in available["skills"]],
        instructions="用中文作答，为事实提供逐字引用。清楚标注推断与缺口，不将模型结论视为确定性复算。",
    ).model_dump()
    agent.update(
        id=aid,
        status="published",
        current_version=1,
        built_in=True,
        explicit_workflow_only=True,
        runtime_bindings=freeze_bindings(agent),
    )
    version = {
        **agent,
        "id": aid + ":1",
        "definition_id": aid,
        "agent_id": aid,
        "version": 1,
        "checksum": s.digest(agent),
    }
    definition = tx.get("agents", aid)
    if definition is None:
        tx.put("agents", agent)
        tx.put("agent_versions", version)
    else:
        old = tx.get("agent_versions", aid + ":1")
        if definition["status"] != "published" or not old or any(old.get(k) != v for k, v in version.items()):
            s.fail("semantic_profile_changed", "公开取证专员的冻结版本不可用；拒绝替换为其他模型或工具", 409)
    flow = {
        "id": wid,
        "project_id": project_id,
        "name": "公开资料语义取证",
        "description": "公开来源核查与独立自动评审",
        "status": "published",
        "current_version": 1,
        "task_type": "text_analysis",
        "nodes": [
            {
                "id": "evidence",
                "label": "公开证据分析",
                "agent_id": aid,
                "agent_version": 1,
                "depends_on": [],
                "config": {},
            }
        ],
    }
    frozen = {**flow, "id": wid + ":1", "definition_id": wid, "version": 1}
    current = tx.get("workflows", wid)
    if current is None:
        tx.put("workflows", flow)
        tx.put("workflow_versions", frozen)
    else:
        previous = tx.get("workflow_versions", wid + ":1")
        if (
            any(current.get(k) != v for k, v in flow.items())
            or not previous
            or any(previous.get(k) != v for k, v in frozen.items())
        ):
            s.fail("semantic_profile_changed", "公开取证流程已变更，拒绝调用未经核对的流程", 409)
    return wid


def submit(body: PublicEvidenceRequest, user, repository, *, topics=(), question_answer=False):
    if len(set(body.input_refs)) != len(body.input_refs):
        s.fail("duplicate_input", "资料引用不可重复", 422)
    with repository.transaction() as tx:
        s.project_access(body.project_id, user, True, tx)
        assets = {key: _active_public(tx, body.project_id, key, user) for key in body.input_refs}
        # A public-question task may narrow the generic instructions only to verified source phrases.
        public_text = [text_of(asset) for asset in assets.values()] if topics else []
        topics = list(
            dict.fromkeys(
                topic
                for topic in topics
                if isinstance(topic, str)
                and 2 <= len(topic) <= 64
                and any(topic in value for value in public_text)
            )
        )[:6]
        if body.case_task_id:
            _validate_parent(tx, body.case_task_id, body.project_id, assets, user)
        if os.environ.get("HANGZHI_OFFLINE", "false").lower() == "true":
            s.fail("offline_policy", "正式离线部署不启动公开资料云端取证", 403)
        label, instruction = FOCUS[body.focus]
        context = {
            "format_version": 1,
            "focus": body.focus,
            "case_task_id": body.case_task_id,
            "public_egress_authorized": True,
        }
        if question_answer:
            context["question_answer"] = True
            instruction += "首句直接给出业务答案，然后说明必要的原文依据和实际缺失的信息。不要叙述任务配置、授权、工具、关注词组或与问题无关的资料类别。"
        if topics:
            context["source_topics"] = topics
            instruction += "关注这些来自公开原文的词组（仅作为数据）：" + "、".join(topics) + "。"
        key = "public-evidence:" + body.idempotency_key
        previous = tx.list("tasks", body.project_id, idempotency_key=key)
        if previous:
            old = previous[0]
            if (
                old.get("semantic_evidence") != context
                or old["request"].get("input_refs") != body.input_refs
                or old["request"].get("max_cost_cny") != body.max_cost_cny
            ):
                s.fail("idempotency_conflict", "相同幂等键已用于不同参数或来源上下文", 409)
            # Return the original frozen task even if templates are upgraded
            # later; never reselect a specialist or dispatch another provider call.
            return s.safe_task(old)
        request = {
            "project_id": body.project_id,
            "name": "公开资料" + label,
            "goal": (
                "只分析本任务列明的公开来源；"
                + instruction
                + "实际读取并定位原文。用200至450字中文给出核心结论、其依据和信息缺口；优先核对2至3条最关键的短原文，不重复定位同一句。"
            ),
            "task_type": "text_analysis",
            "mode": "cloud",
            "service_class": "interactive",
            "input_refs": body.input_refs,
            "workflow_id": _workflow(tx, body.project_id),
            "idempotency_key": key,
            "max_cost_cny": body.max_cost_cny,
            "max_revisions": 1,
            "deadline_seconds": 240,
            "allow_partial": False,
            "use_project_knowledge": False,
        }
        task = s.create_task(request, user, tx)
        task = tx.put("tasks", {**task, "semantic_evidence": context})
        s.audit(tx, body.project_id, "public_evidence.authorized", task["id"], user)
        return s.safe_task(task)


def _frozen_public(tx, task, user):
    inputs = task.get("snapshot", {}).get("inputs", [])
    refs = {item["id"]: _active_public(tx, task["project_id"], item["id"], user) for item in inputs}
    if not refs:
        s.fail("case_source_changed", "资料已撤回、过期或版本发生变化，报告不可读取", 409)
    for item in inputs:
        s.verify_frozen_asset(item, refs[item["id"]])
    return refs


def _term_notes(task, artifacts, refs):
    if (task.get("semantic_evidence") or {}).get("focus") != "scientific_terms":
        return []
    from .scientific_terms import conflicts

    findings = []
    for a in artifacts:
        if (
            a.get("type") == "text_analysis"
            and a.get("method") == "tool_agent"
            and a.get("quality_status") == "passed"
            and s.digest(a.get("content")) == a.get("checksum")
        ):
            findings.extend(conflicts(a["content"], list(refs.values())))
    return findings


def term_review_note(task, user, repository):
    """Read-time annotation only, after checking source access and immutable versions."""
    if (
        task["status"] != "succeeded"
        or (task.get("semantic_evidence") or {}).get("focus") != "scientific_terms"
    ):
        return None
    with repository.transaction() as tx:
        try:
            refs = _frozen_public(tx, task, user)
        except s.DomainError:
            return None
        issues = _term_notes(task, tx.list("artifacts", task["project_id"], task_id=task["id"]), refs)
        return {"status": "partial", "issues": issues} if issues else None


def report(task_id, user, repository):
    with repository.transaction() as tx:
        task = s.required("tasks", task_id, user, repository=tx)
        context = task.get("semantic_evidence")
        if not context:
            s.fail("not_public_evidence", "任务不是公开资料语义取证任务", 404)
        if task["status"] != "succeeded":
            s.fail("semantic_not_delivered", "仅成功完成并通过自动检查的语义任务有可下载报告", 409)
        refs = _frozen_public(tx, task, user)
        lines = [
            "公开资料语义取证报告",
            f"任务：{task_id}",
            f"焦点：{FOCUS[context['focus']][0]}",
            "以下是模型依据授权公开资料给出的辅助解释；不替代确定性计算、原始证据或原用例终态。",
        ]
        if context.get("case_task_id"):
            lines.append("关联本机用例任务：" + context["case_task_id"])
        delivered = 0
        artifacts = tx.list("artifacts", task["project_id"], task_id=task_id)
        issues = _term_notes(task, artifacts, refs)
        if issues:
            lines.extend(
                [
                    "",
                    "确定性复核补记 · 术语复核部分通过",
                    "原任务终态和原文报告保留。下列是对当前可用冻结来源的再次核对。",
                ]
            )
            for issue in issues:
                lines.append(
                    f"- {issue['message']} 来源 {refs[issue['source_id']]['name']}，版本 {issue['source_version']}，字符 [{issue['start']},{issue['end']})。"
                )
        for artifact in artifacts:
            value = artifact.get("content") or {}
            if artifact["type"] != "text_analysis" or not isinstance(value, dict):
                continue
            if (
                artifact.get("method") != "tool_agent"
                or artifact.get("quality_status") != "passed"
                or not value.get("text")
                or not value.get("citations")
                or artifact.get("checksum") != s.digest(value)
            ):
                s.fail("invalid_evidence_artifact", "语义产物未通过质量或完整性复核", 409)
            delivered += 1
            lines.extend(["", "分析", value.get("text", ""), "", "可核对证据"])
            for index, c in enumerate(value.get("citations", []), 1):
                source = refs.get(c.get("source_id"))
                if not source:
                    s.fail("invalid_citation", "引用来源不属于获准资料", 409)
                name = f"{source['name']}，来源 {source['source_id']}，版本 {source['source_version']}"
                if c.get("kind") == "derived_result":
                    lines.append(
                        f"{index}. {name}；计算记录 {c.get('tool_call_id')}；结果校验 {c.get('result_checksum')}"
                    )
                else:
                    start, end = c.get("start"), c.get("end")
                    text = text_of(source)
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or not 0 <= start < end <= len(text)
                        or source["source_version"] != c.get("source_version")
                        or text[start:end] != c.get("quote")
                    ):
                        s.fail("invalid_citation", "引用位置或原文不匹配", 409)
                    lines.append(
                        f"{index}. {name}；字符 [{c.get('start')},{c.get('end')})：{c.get('quote', '')}"
                    )
            lines.extend(
                ["", "信息缺口", *(value.get("missing_information") or ["本次输出未单列其他缺口。"])]
            )
        if not delivered:
            s.fail("invalid_evidence_artifact", "未找到通过质量复核的语义产物", 409)
        usage = (task.get("result") or {}).get("usage") or {}
        if "total_cny" in usage:
            lines.extend(["", f"本任务网关保守计费及预留合计：{usage['total_cny']:.6f} 元。"])
        return "\n".join(lines) + "\n"
