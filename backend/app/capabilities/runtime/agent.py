"""受控业务 Agent 运行时。

业务 Agent 只看到其 ``skill_ids`` 中授权的 Skill callable；Skill 再经
``SkillRuntime`` 调用白名单 Tool。这样工作流节点里的 Agent 可以根据任务和
上游结果自主选择、组合和修正 Skill 输入，同时不会越过 Skill/Tool 权限边界。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any

from ..contracts import ExecutionContext, ToolResult
from ..errors import CAPABILITY_NOT_FOUND, TOOL_RESULT_INVALID, CapabilityError
from ..facade import capability_runtime


def _annotation(schema: dict) -> type:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(schema.get("type"), Any)


def _merge_usage(items: list[dict]) -> dict:
    """递归合并模型 usage；真实供应商可能包含嵌套 token 明细对象。"""
    merged: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                previous = merged.get(key, 0)
                merged[key] = (previous if isinstance(previous, (int, float)) else 0) + value
            elif isinstance(value, dict):
                previous = merged.get(key, {})
                merged[key] = _merge_usage(
                    [previous if isinstance(previous, dict) else {}, value]
                )
    return merged


def build_skill_functions(
    agent: dict,
    context: ExecutionContext,
    observed: list[tuple[str, ToolResult]],
    *,
    node_kind: str | None = None,
) -> dict[str, Callable]:
    """把 Agent 授权的 Skill 暴露成带真实 JSON Schema 签名的 callable。"""
    runtime = capability_runtime()
    functions: dict[str, Callable] = {}
    for skill_id in agent.get("skill_ids", []):
        if skill_id not in runtime.skills:
            raise CapabilityError(CAPABILITY_NOT_FOUND, f"智能体引用了未注册技能 {skill_id}")
        manifest = runtime.skills.get(skill_id)
        if node_kind and manifest.node_kinds and node_kind not in manifest.node_kinds:
            continue
        if manifest.requires_documents and not context.document_ids:
            continue

        async def call_skill(_manifest=manifest, **arguments) -> dict:
            cleaned = {key: value for key, value in arguments.items() if value is not None}
            result = await runtime.skill_runtime.execute(_manifest.id, cleaned, context)
            observed.append((_manifest.id, result))
            return result.model_dump()

        properties = manifest.input_schema.get("properties", {})
        required = set(manifest.input_schema.get("required", []))
        method = runtime.skill_runtime.load_instructions(manifest)[:6000]
        call_skill.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if name in required else None,
                annotation=_annotation(schema),
            )
            for name, schema in properties.items()
        )
        call_skill.__annotations__ = {
            **{name: _annotation(schema) for name, schema in properties.items()},
            "return": dict,
        }
        call_skill.__name__ = manifest.id.replace(".", "_").replace("-", "_")
        call_skill.__doc__ = (
            f"Skill：{manifest.name}（{manifest.id}）。{manifest.description}。"
            f"输入 JSON Schema：{manifest.input_schema}。"
            f"\n使用方法与边界：\n{method}\n"
            "返回值只代表真实 Skill/Tool 执行结果；失败或空结果时请调整参数后重试。"
        )
        call_skill.__skill_id__ = manifest.id  # type: ignore[attr-defined]
        functions[manifest.id] = call_skill
    return functions


async def run_business_agent(
    agent: dict,
    message: dict | str,
    context: ExecutionContext,
    *,
    node_kind: str | None = None,
    preferred_skill_id: str | None = None,
    require_skill_call: bool = True,
    max_rounds: int = 8,
) -> tuple[dict, list[tuple[str, ToolResult]]]:
    """运行顶层业务 Agent；没有真实 Skill 调用时进行一次有界修复。"""
    from ...model_gateway import model_gateway

    observed: list[tuple[str, ToolResult]] = []
    functions = build_skill_functions(agent, context, observed, node_kind=node_kind)
    if require_skill_call and not functions:
        raise CapabilityError(CAPABILITY_NOT_FOUND, "当前智能体没有适用于此节点的可用技能")

    preferred = (
        f"本节点建议优先评估 Skill `{preferred_skill_id}`，但它只是初始提示；"
        "若任务和上游结果表明其他已授权 Skill 更合适，可以自主选择或组合调用。\n"
        if preferred_skill_id
        else ""
    )
    instructions = (
        str(agent.get("instructions", ""))[:10000]
        + "\n\n## 运行时能力规则\n"
        + "你是工作流中的业务智能体，不是固定提示词节点。先理解任务、当前节点目标和上游真实结果，"
        + "再自主选择一个或多个已授权 Skill。Skill 参数是你在调用时生成的，节点 config 只是候选值，"
        + "不得机械照抄。集合任务要拆成单个对象逐次调用；如果数据源要求英文名称或标准代码，先转换成"
        + "适配格式。每次读取真实返回，空结果或参数错误时应调整后重试。不得编造未由 Skill 返回的数据。\n"
        + preferred
    )
    encoded = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, default=str)
    raw = await model_gateway.run_agent(
        {"name": agent.get("name", "业务智能体"), "instructions": instructions},
        encoded,
        functions,
        context,
        max_rounds=max_rounds,
    )
    attempts = [raw]
    if require_skill_call and not observed:
        repair_message = (
            encoded
            + "\n\n上一次回答没有实际调用任何 Skill，因此不能作为任务结果。"
            + "现在必须先调用至少一个最合适的已授权 Skill，并基于真实返回完成本节点；"
            + "不要只解释计划或直接给出常识性答案。"
        )
        raw = await model_gateway.run_agent(
            {"name": agent.get("name", "业务智能体"), "instructions": instructions},
            repair_message,
            functions,
            context,
            max_rounds=max_rounds,
        )
        attempts.append(raw)
    if require_skill_call and not observed:
        raise CapabilityError(
            TOOL_RESULT_INVALID,
            f"智能体 {agent.get('name', agent.get('id', 'unknown'))} 两次均未调用任何授权 Skill",
        )

    combined = dict(raw)
    combined["attempts"] = len(attempts)
    combined["usage"] = _merge_usage([item.get("usage") or {} for item in attempts])
    combined["cost_cny"] = sum(
        value
        for item in attempts
        if isinstance((value := item.get("cost_cny", 0)), (int, float))
        and not isinstance(value, bool)
    )
    return combined, observed


async def run_agent_test(agent: dict, message: str, mode: str) -> dict:
    runtime = capability_runtime()
    if mode == "rehearsal":
        return {
            "text": f"【演练模式；未调用模型】\n智能体：{agent['name']}\n角色：{agent.get('role')}\n收到问题：{message}\n已验证配置和调用路径；真实能力需切换真实模型测试。",
            "mode": mode,
        }
    authorized = [skill_id for skill_id in agent.get("skill_ids", []) if skill_id in runtime.skills]
    context = ExecutionContext(
        mode="live",
        network_policy="allow",
        agent_id=agent.get("id"),
        allowed_skill_ids=authorized,
    )
    result, observed = await run_business_agent(
        agent, message, context, require_skill_call=False, max_rounds=4
    )
    return {
        "text": result["text"],
        "mode": "live",
        "usage": result.get("usage"),
        "cost_cny": result.get("cost_cny"),
        "tool_calls": result.get("tool_calls") or [],
        "skills": sorted({skill_id for skill_id, _ in observed}),
        "note": "" if observed else "本次模型未调用能力（未发生任何 Skill/Tool 调用，以上回答不含工具结果）",
    }
