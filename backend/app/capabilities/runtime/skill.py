"""SkillRuntime：recipe / agent 两种执行模式。

recipe：按 manifest 声明的顺序经 ToolRuntime 调用 Tool，支持
``{input.xxx}`` / ``{steps[0].data.yyy}`` 模板插值与 ``when``/``unless`` 条件步骤。
agent：创建一个只加载该 Skill 的 SKILL.md 指令和 allowed_tools 的受控执行体。
实时 agent 执行依赖预算网关提供工具调用能力（run_agent），接入前 live 模式
明确报 capability_disabled，绝不伪造调用；drill 模式返回 dry_run 预检结果。
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..audit import AuditLog, audit_log
from ..contracts import ExecutionContext, SkillManifest, ToolCallRecord, ToolError, ToolResult, ToolTrace
from ..errors import CAPABILITY_DISABLED, SCHEMA_VALIDATION_FAILED, CapabilityError
from ..policy import PolicyGate
from ..registry import SkillRegistry
from ..schema import validate_value
from .tool import ToolRuntime

AgentRunner = Callable[[SkillManifest, str, dict[str, Callable], dict, ExecutionContext], Awaitable[dict]]

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_FALSY_STRINGS = {"", "false", "no", "0", "none", "null"}


def _lookup(expression: str, scope: dict) -> Any:
    value: Any = scope
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", expression):
        if token.startswith("["):
            index = int(token[1:-1])
            if not isinstance(value, list) or index >= len(value):
                return None
            value = value[index]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(token)
    return value


def render_template(value: Any, scope: dict) -> Any:
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1).strip(), scope)
        return _PLACEHOLDER.sub(
            lambda match: "" if (found := _lookup(match.group(1).strip(), scope)) is None else str(found),
            value,
        )
    if isinstance(value, list):
        return [render_template(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, scope) for key, item in value.items()}
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    return bool(value)


class SkillRuntime:
    def __init__(
        self,
        skills: SkillRegistry,
        tool_runtime: ToolRuntime,
        policy: PolicyGate | None = None,
        audit: AuditLog | None = None,
        agent_runner: AgentRunner | None = None,
    ):
        self.skills = skills
        self.tool_runtime = tool_runtime
        self.policy = policy or tool_runtime.policy
        self.audit = audit or audit_log
        self.agent_runner = agent_runner

    def load_instructions(self, manifest: SkillManifest) -> str:
        if not manifest.instructions_path:
            return ""
        return Path(manifest.instructions_path).read_text(encoding="utf-8")

    def _failure(self, error: CapabilityError, status="failed") -> ToolResult:
        return ToolResult(
            status=status,  # type: ignore[arg-type]
            error=ToolError(**error.as_dict()),
            trace=ToolTrace(),
        )

    async def execute(
        self, skill_id: str, skill_input: dict[str, Any] | None, context: ExecutionContext
    ) -> ToolResult:
        skill_input = dict(skill_input or {})
        started = time.monotonic()
        try:
            manifest = self.skills.get(skill_id)
            self.policy.check_skill(manifest, context)
            validate_value(manifest.input_schema, skill_input, where=f"{manifest.id}.input")
        except CapabilityError as error:
            status = "blocked" if error.code == "permission_denied" else "failed"
            self.audit.record(
                capability_type="skill",
                capability_id=skill_id,
                status=status,
                context=context,
                error_code=error.code,
                arguments=skill_input,
            )
            return self._failure(error, status)
        if manifest.execution_mode == "agent":
            result = await self._execute_agent(manifest, skill_input, context)
        else:
            result = await self._execute_recipe(manifest, skill_input, context)
        duration_ms = int((time.monotonic() - started) * 1000)
        result.trace.duration_ms = duration_ms
        self.audit.record(
            capability_type="skill",
            capability_id=skill_id,
            status=result.status,
            context=context,
            duration_ms=duration_ms,
            error_code=result.error.code if result.error else None,
            arguments=skill_input,
        )
        return result

    async def _execute_recipe(
        self, manifest: SkillManifest, skill_input: dict, context: ExecutionContext
    ) -> ToolResult:
        steps: list[dict] = []
        calls: list[ToolCallRecord] = []
        evidence: list[dict] = []
        last_data: dict = {}
        last_text = ""
        for index, step in enumerate(manifest.recipe):
            scope = {"input": skill_input, "steps": steps}
            if step.when is not None and not _truthy(render_template(step.when, scope)):
                continue
            if step.unless is not None and _truthy(render_template(step.unless, scope)):
                continue
            arguments = render_template(step.args, scope)
            result = await self.tool_runtime.invoke(step.tool, arguments, context, skill=manifest)
            steps.append({"tool": step.tool, "status": result.status, "data": result.data})
            calls.extend(result.trace.tool_calls)
            if result.status != "completed":
                return ToolResult(
                    status=result.status,
                    data={"steps": steps, **result.data},
                    text=result.text,
                    evidence=evidence + result.evidence,
                    error=result.error,
                    trace=ToolTrace(tool_calls=calls),
                )
            evidence.extend(result.evidence)
            last_data = result.data
            last_text = result.text
        return ToolResult(
            status="completed",
            data={**last_data, "steps": steps},
            text=last_text,
            evidence=evidence,
            trace=ToolTrace(tool_calls=calls),
        )

    def build_tool_functions(self, manifest: SkillManifest, context: ExecutionContext) -> dict[str, Callable]:
        """为该 Skill 生成受控工具 callable（供 agent 模式绑定真实 tools=）。

        每个 callable 带显式签名（由 Tool 的 input_schema 属性生成，全部可选），
        使 Agno 能为模型生成真实的 JSON Schema 参数描述。
        """
        import inspect

        functions: dict[str, Callable] = {}
        for tool_id in manifest.allowed_tools:
            definition = self.tool_runtime.tools.get(tool_id)

            async def call_tool(_definition=definition, **arguments) -> dict:
                result = await self.tool_runtime.invoke(
                    _definition.id,
                    {key: value for key, value in arguments.items() if value is not None},
                    context,
                    skill=manifest,
                )
                return result.model_dump()

            properties = definition.input_schema.get("properties", {})
            call_tool.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
                for name in properties
            )
            call_tool.__name__ = tool_id.replace(".", "_")
            call_tool.__doc__ = f"{definition.name}（{definition.id}）"
            functions[tool_id] = call_tool
        return functions

    async def _execute_agent(
        self, manifest: SkillManifest, skill_input: dict, context: ExecutionContext
    ) -> ToolResult:
        instructions = self.load_instructions(manifest)
        functions = self.build_tool_functions(manifest, context)
        if context.mode == "drill":
            return ToolResult(
                status="dry_run",
                data={
                    "dry_run": True,
                    "skill_id": manifest.id,
                    "instructions_loaded": bool(instructions),
                    "bound_tools": sorted(functions),
                    "note": "演练模式仅完成指令加载与工具绑定预检，未发起模型调用",
                },
                trace=ToolTrace(),
            )
        if self.agent_runner is None:
            return self._failure(
                CapabilityError(
                    CAPABILITY_DISABLED,
                    "agent 模式的实时执行将在预算网关提供工具调用能力后接入；当前不会伪造模型调用",
                )
            )
        try:
            raw = await self.agent_runner(manifest, instructions, functions, skill_input, context)
        except CapabilityError as error:
            return self._failure(error)
        if not isinstance(raw, dict):
            return self._failure(
                CapabilityError(SCHEMA_VALIDATION_FAILED, "agent 执行器返回了非对象结果")
            )
        return ToolResult(
            status="completed",
            data=raw,
            text=str(raw.get("text", "")),
            evidence=raw.get("evidence") if isinstance(raw.get("evidence"), list) else [],
            trace=ToolTrace(),
        )
