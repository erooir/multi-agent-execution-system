"""进程级能力运行时装配：注册表、Policy Gate、ToolRuntime、SkillRuntime 的单例。

engine/api 统一从这里取运行时，避免各自重复装配。预算闸门直接读取
model_gateway.budget()，agent 模式的实时执行器同样只走预算网关。
"""

from __future__ import annotations

import json

from ..model_gateway import model_gateway
from .audit import audit_log
from .loader import load_default_registries
from .policy import PolicyGate
from .registry import McpRegistry, SkillRegistry, ToolRegistry
from .runtime.skill import SkillRuntime
from .runtime.tool import ToolRuntime


class CapabilityRuntime:
    def __init__(self) -> None:
        self.skills: SkillRegistry
        self.tools: ToolRegistry
        self.mcp_servers: McpRegistry
        self.skills, self.tools, self.mcp_servers = load_default_registries()
        self.policy = PolicyGate(budget_provider=lambda: model_gateway.budget())
        self.audit = audit_log
        self.tool_runtime = ToolRuntime(
            self.tools, policy=self.policy, audit=self.audit, mcp_servers=self.mcp_servers
        )
        self.mcp_provider = self.tool_runtime.providers["mcp"]
        # MCP Server 最近已知健康状态（由 health/refresh 端点更新），供技能 degraded 标注。
        self.server_health: dict[str, str] = {}
        self.skill_runtime = SkillRuntime(
            self.skills,
            self.tool_runtime,
            policy=self.policy,
            audit=self.audit,
            agent_runner=self._run_skill_agent,
        )

    async def _run_skill_agent(self, manifest, instructions, functions, skill_input, context) -> dict:
        return await model_gateway.run_agent(
            {"name": manifest.name, "instructions": instructions},
            json.dumps(skill_input, ensure_ascii=False),
            functions,
            context,
            max_rounds=8,
        )


_RUNTIME: CapabilityRuntime | None = None


def capability_runtime() -> CapabilityRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = CapabilityRuntime()
    return _RUNTIME


async def discover_enabled_servers(runtime: CapabilityRuntime | None = None) -> dict[str, str]:
    """启动期自动发现 enabled:true 的 MCP Server 并注册其 allowlist 工具。

    每个 server 受 min(startup_timeout_seconds, 20s) 上限约束；失败时标
    unavailable（依赖它的 Skill 随之标 degraded），平台照常启动。
    """
    import asyncio

    runtime = runtime or capability_runtime()
    results: dict[str, str] = {}
    for server in runtime.mcp_servers.list():
        if not server.enabled:
            continue
        try:
            async with asyncio.timeout(min(float(server.startup_timeout_seconds), 20.0)):
                discovered = await runtime.mcp_provider.discover(server.id)
            for definition in runtime.mcp_provider.to_tool_definitions(server.id, discovered):
                runtime.tools.upsert(definition)
            runtime.server_health[server.id] = "ready"
            results[server.id] = f"ready({len(discovered)})"
        except Exception as error:  # noqa: BLE001 - 单个 MCP 失败不得影响平台启动
            runtime.server_health[server.id] = "unavailable"
            results[server.id] = f"unavailable:{getattr(error, 'code', type(error).__name__)}"
    return results


def validate_registered_skills(store) -> None:
    """启动期只读校验：已存 Agent/Workflow 引用的 Skill 必须都已注册。"""
    runtime = capability_runtime()
    missing: list[str] = []
    for agent in store.list("agents"):
        for skill_id in agent.get("skill_ids", []):
            if skill_id not in runtime.skills:
                missing.append(f"智能体 {agent.get('id')} 引用了未注册技能 {skill_id}")
    for workflow in store.list("workflows"):
        for node in workflow.get("nodes", []):
            skill_id = node.get("data", {}).get("skill_id")
            if skill_id and skill_id not in runtime.skills:
                missing.append(f"工作流 {workflow.get('id')} 节点 {node.get('id')} 引用了未注册技能 {skill_id}")
    if missing:
        raise RuntimeError("能力注册表缺少已登记的技能引用：" + "；".join(sorted(set(missing))))
