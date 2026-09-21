"""AgentRuntime：顶层智能体经预算网关真实调用被授权的 Skill 工具链。

- rehearsal：保持既有的显著标注行为，不调用模型。
- live：把智能体授权的 Skill 的 allowed_tools 绑定为真实 tools=，
  经 model_gateway.run_agent 执行；模型未调用任何能力时如实标注。
"""

from __future__ import annotations

from ..contracts import ExecutionContext
from ..errors import CAPABILITY_NOT_FOUND, CapabilityError
from ..facade import capability_runtime


async def run_agent_test(agent: dict, message: str, mode: str) -> dict:
    runtime = capability_runtime()
    if mode == "rehearsal":
        return {
            "text": f"【演练模式；未调用模型】\n智能体：{agent['name']}\n角色：{agent.get('role')}\n收到问题：{message}\n已验证配置和调用路径；真实能力需切换真实模型测试。",
            "mode": mode,
        }
    manifests = []
    for skill_id in agent.get("skill_ids", []):
        if skill_id not in runtime.skills:
            raise CapabilityError(CAPABILITY_NOT_FOUND, f"智能体引用了未注册技能 {skill_id}")
        manifests.append(runtime.skills.get(skill_id))
    context = ExecutionContext(
        mode="live",
        network_policy="allow",
        agent_id=agent.get("id"),
        allowed_skill_ids=[manifest.id for manifest in manifests],
    )
    tool_bindings: dict = {}
    for manifest in manifests:
        tool_bindings.update(runtime.skill_runtime.build_tool_functions(manifest, context))
    return await _run(agent, message, tool_bindings, context)


async def _run(agent: dict, message: str, tool_bindings: dict, context: ExecutionContext) -> dict:
    from ...model_gateway import model_gateway

    result = await model_gateway.run_agent(
        {
            "name": agent.get("name", "智能体测试"),
            "instructions": str(agent.get("instructions", ""))[:10000],
        },
        message,
        tool_bindings,
        context,
        max_rounds=4,
    )
    tool_calls = result.get("tool_calls") or []
    return {
        "text": result["text"],
        "mode": "live",
        "usage": result.get("usage"),
        "cost_cny": result.get("cost_cny"),
        "tool_calls": tool_calls,
        "skills": sorted({call.get("skill_id") for call in tool_calls if call.get("skill_id")}),
        "note": ""
        if tool_calls
        else "本次模型未调用能力（未发生任何 Skill/Tool 调用，以上回答不含工具结果）",
    }
