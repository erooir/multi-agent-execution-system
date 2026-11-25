"""Ground a published case method in the operations recorded for that run."""

import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from . import edition_guidance as original


@lru_cache(maxsize=1)
def guidance_signature():
    # The first signed edition is still executable. A new edition depends on
    # both that frozen tool adapter and this version of the method contract.
    return sha256(
        original.guidance_signature().encode() + Path(__file__).read_text(encoding="utf8").encode()
    ).hexdigest()


def supported_signature(signature):
    return signature in (original.guidance_signature(), guidance_signature())


_METHOD = (
    "\n上面的 Skill 是可选方法文档。其示例、授权的工具名和准备节点的研究步骤都不能证明操作已经执行。"
    "撰写正文与审核时以本次目标、scope、公开原件及‘本次执行依据’为准。"
    "描述平台实际操作时，仅采用成功的工具轨迹及其结果；独立原件核查的结论仅限核查说明明确验证的内容。"
    "资料或随包表已列的数值可标明其来源，不能据此声称本次调用了产生该数值的算法。"
    "列举随包研究附件时，使用已核验的附件文件名。修订时用原件支持改正后的发现，审核意见只用于修改稿件。"
    "沿用资料的对象、字段和判据，不能把落入空间覆盖或裁剪的数量解释为已通过几何有效性检验。"
    "正文聚焦本案有依据的发现，无须逐条声明 Skill 的一般步骤是否执行。"
)

_REVIEW = (
    "独立审核请对照前序产物、实际轨迹与引用原文。候选正文和计划只是待核对的陈述。"
    "逐项区分本包范围内须完成的要求和 scope 明列的材料限制；对声称完成却无记录支持的操作指出所在段落，"
    "对数值问题列明原值与候选值，确保最终判定与理由一致。"
)

# Only explicit affirmative claims of using a named, frozen catalog function.
# A methodological suggestion, source quotation or mention of a limitation
# is not evidence that the author claimed to execute it.
_TOOL_CLAIM = re.compile(
    r"^\s*(?:(?:(?:本次|本轮)(?:分析|任务|核算)?|我们)(?:已|已实际)?(?:调用|执行|使用|运行)了?"
    r"|(?:已(?:经)?(?:实际)?(?:调用|执行|使用|运行)了?|实际(?:调用|执行|使用|运行)了?|(?:调用|运行|使用)了)"
    r"|(?!(?:.*(?:未|没有|不|待|若|建议|拟|可能|如需|应|需要|原文|资料|原件|来源|引述|假设).*计算))"
    r"[\u3400-\u9fff]{0,8}计算(?:结果)?(?:基于|采用))"
    r"\s*[`“]?([a-zA-Z][a-zA-Z0-9_.]*[a-zA-Z0-9_])[`”]?\s*"
    r"(?=$|[，,]|完成|得到|获得|生成|提取|读取|计算|核查|进行)"
)


def _unsupported_claims(findings, unused):
    issues = []
    for finding in findings:
        for sentence in re.split(r"[。！？；;\n]", finding["text"]):
            match = _TOOL_CLAIM.match(sentence)
            if (
                match
                and match[1] in unused
                and not re.search(
                    r"说法|无法证实|没有证据|证据不足|未获证实|无记录支持|不成立", sentence[match.end() :]
                )
            ):
                issues.append(f"“{finding['heading']}”声称本次使用 {match[1]}，对应工具没有成功执行记录。")
    return list(dict.fromkeys(issues))


def _run_facts(execution, previous, revision):
    task = execution.current()
    upstream = {execution.output_origins.get(id(out), {}).get("instance_id") for out in previous}
    upstream.discard(None)
    # Do not pass traces from discarded drafts to the new review as current work.
    calls = [
        call
        for call in task.get("tool_calls", [])
        if call["status"] == "completed"
        and call.get("result_checksum")
        and (call["revision"] == revision or call.get("instance_id") in upstream)
    ]
    counts = {}
    for call in calls:
        key = (call["node_id"], call["revision"], call["kind"], call["capability_id"])
        counts[key] = counts.get(key, 0) + 1
    prep = next(out["content"] for out in previous if out["type"] == "case_records")
    facts = {
        "本次输入原件": [asset["original_file"]["filename"] for asset in execution.inputs],
        "已核验的研究附件文件名": list(prep["file_context"]),
        "成功调用": [
            {"节点": node, "轮次": rev + 1, "类型": kind, "能力ID": capability, "次数": count}
            for (node, rev, kind, capability), count in counts.items()
        ],
        "原件核查项目与实测说明": prep["source_checks"],
    }
    called = {call["capability_id"] for call in calls if call["kind"] in {"tool", "mcp"}}
    bindings = {
        spec["id"]: spec["name"]
        for role in task["snapshot"]["agents"]
        for spec in (role.get("runtime_bindings") or {}).get("tools", [])
    }
    used_aliases = called | {bindings[c] for c in called if c in bindings}
    return facts, (set(bindings) | set(bindings.values())) - used_aliases


class GroundedMethod(original.StageGuidance):
    def __init__(self, context, facts, issues, review):
        super().__init__(context.instructions, {**context.data, "本次执行依据": facts})
        self.review = review
        self.issues = issues
        if issues:
            self.data["候选正文中待纠正的工具执行声明"] = issues

    def apply(self, system, prompt):
        system, prompt = super().apply(system, prompt)
        return system + _METHOD + (_REVIEW if self.review else ""), prompt

    def check_report(self, output):
        if self.issues and output["type"] == "case_report":
            message = " ".join(self.issues)
            output["content"]["review"].append(
                {"name": "recorded_tool_claims", "passed": False, "hard": True, "message": message}
            )
            output["content"]["gaps"].append(message)


async def stage_guidance(execution, agent, permissions, revision, instance_id, previous):
    from ..platform.runtime import TaskFailure

    signature = execution.task["snapshot"]["workflow"]["edition_guidance"]
    if not supported_signature(signature):
        raise TaskFailure("case_profile_changed", "冻结的岗位工具流程实现已变化，请重新受理")
    context = await original.stage_guidance(execution, agent, permissions, revision, instance_id, previous)
    if signature == original.guidance_signature():
        return context
    facts, unused = _run_facts(execution, previous, revision)
    review = execution.active_node_id == "review"
    issues = []
    if review:
        candidate = next(out["content"] for out in previous if out["type"] == "case_candidate")
        issues = _unsupported_claims(candidate["findings"], unused)
    return GroundedMethod(context, facts, issues, review)
