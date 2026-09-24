"""Policy Gate：Agent/Skill/节点三层授权交集、外发、确认与预算检查。"""

from __future__ import annotations

from collections.abc import Callable

from .contracts import ExecutionContext, SkillManifest, ToolDefinition
from .errors import (
    BUDGET_EXCEEDED,
    CONFIRMATION_REQUIRED,
    DATA_EGRESS_BLOCKED,
    PERMISSION_DENIED,
    CapabilityError,
)


class PolicyGate:
    """所有 Skill/Tool 调用必须通过的授权检查。

    budget_provider 返回 model_gateway.budget() 形态的字典；注入以便测试不触碰真实账本。
    """

    def __init__(self, budget_provider: Callable[[], dict] | None = None):
        self._budget_provider = budget_provider

    def check_skill(self, skill: SkillManifest, context: ExecutionContext) -> None:
        if context.allowed_skill_ids is not None and skill.id not in context.allowed_skill_ids:
            raise CapabilityError(PERMISSION_DENIED, f"Skill {skill.id} 不在当前上下文授权范围")

    def check_tool(
        self,
        tool: ToolDefinition,
        context: ExecutionContext,
        skill: SkillManifest | None = None,
    ) -> None:
        # 三层授权交集：Skill 声明 → 上下文授权。
        if skill is not None and tool.id not in skill.allowed_tools:
            raise CapabilityError(
                PERMISSION_DENIED, f"Tool {tool.id} 不在 Skill {skill.id} 的 allowed_tools 中"
            )
        if context.allowed_tool_ids is not None and tool.id not in context.allowed_tool_ids:
            raise CapabilityError(PERMISSION_DENIED, f"Tool {tool.id} 不在当前上下文授权范围")
        # 本地限定资料永不进入需要网络或会产生外发的 Tool。
        if context.data_visibility == "local" and (
            tool.network != "none" or tool.data_egress in {"query", "raw"}
        ):
            raise CapabilityError(DATA_EGRESS_BLOCKED, f"本地限定资料禁止进入会外发的 Tool {tool.id}")
        if tool.network == "required" and context.network_policy == "deny":
            raise CapabilityError(PERMISSION_DENIED, f"当前上下文禁止网络访问，无法调用 {tool.id}")
        if tool.requires_confirmation and not context.confirmed:
            raise CapabilityError(CONFIRMATION_REQUIRED, f"Tool {tool.id} 需要人工确认后才能执行")
        if tool.uses_model_budget and self._budget_provider is not None:
            budget = self._budget_provider()
            if float(budget.get("remaining_cny", 0)) <= 0:
                raise CapabilityError(BUDGET_EXCEEDED, "模型预算不足，已阻止调用")
