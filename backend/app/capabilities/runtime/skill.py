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
from ..errors import (
    CAPABILITY_DISABLED,
    SCHEMA_VALIDATION_FAILED,
    TOOL_RESULT_INVALID,
    CapabilityError,
)
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
        merged_data: dict[str, Any] = {}
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
            for key, value in result.data.items():
                if isinstance(value, list):
                    merged_data.setdefault(key, []).extend(value)
                else:
                    merged_data[key] = value
        for key in ("reports", "airports", "results"):
            if isinstance(merged_data.get(key), list):
                merged_data["count"] = len(merged_data[key])
                break
        return ToolResult(
            status="completed",
            data={**last_data, **merged_data, "steps": steps},
            text=last_text,
            evidence=evidence,
            trace=ToolTrace(tool_calls=calls),
        )

    def build_tool_functions(
        self,
        manifest: SkillManifest,
        context: ExecutionContext,
        result_sink: list[tuple[str, ToolResult]] | None = None,
    ) -> dict[str, Callable]:
        """为该 Skill 生成受控工具 callable（供 agent 模式绑定真实 tools=）。

        每个 callable 带显式签名（由 Tool 的 input_schema 属性和 required 生成），
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
                if result_sink is not None:
                    result_sink.append((_definition.id, result))
                return result.model_dump()

            properties = definition.input_schema.get("properties", {})
            required = set(definition.input_schema.get("required", []))
            annotations = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            call_tool.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=inspect.Parameter.empty if name in required else None,
                    annotation=annotations.get(schema.get("type"), inspect.Parameter.empty),
                )
                for name, schema in properties.items()
            )
            call_tool.__annotations__ = {
                **{name: annotations.get(schema.get("type"), Any) for name, schema in properties.items()},
                "return": dict,
            }
            call_tool.__name__ = tool_id.replace(".", "_")
            call_tool.__doc__ = (
                f"{definition.name}（{definition.id}）。输入 JSON Schema：{definition.input_schema}"
            )
            functions[tool_id] = call_tool
        return functions

    async def _execute_agent(
        self, manifest: SkillManifest, skill_input: dict, context: ExecutionContext
    ) -> ToolResult:
        instructions = self.load_instructions(manifest)
        observed: list[tuple[str, ToolResult]] = []
        functions = self.build_tool_functions(manifest, context, observed)
        if context.mode == "drill":
            # 本地 agent Skill 可带确定性 recipe 回退；只有其必填工具参数都能由
            # 初始输入渲染出来时才执行。否则只做预检，避免把缺少 ICAO 等参数
            # 误报成整条演练流程失败。
            can_run_recipe = bool(manifest.recipe)
            for step in manifest.recipe:
                definition = self.tool_runtime.tools.get(step.tool)
                arguments = render_template(step.args, {"input": skill_input, "steps": []})
                required = definition.input_schema.get("required", [])
                if any(arguments.get(key) is None for key in required):
                    can_run_recipe = False
                    break
            if can_run_recipe:
                return await self._execute_recipe(manifest, skill_input, context)
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
            runtime_instructions = (
                instructions
                + "\n\n## 运行时决策规则\n"
                + "你收到的 JSON 包含原始任务、节点初始输入以及可用的上游节点结果。"
                + "初始 query/icao 只是候选输入，不是必须原样提交的固定参数。"
                + "先按本技能方法拆解任务，再自主选择并调用白名单工具；严格遵守每个工具的参数 schema。"
                + "每次调用后检查真实返回；空结果时可调整为更精确、符合数据源语言或代码格式的参数重试。"
                + "不得调用未绑定工具，不得编造工具没有返回的数据。"
            )
            raw = await self.agent_runner(manifest, runtime_instructions, functions, skill_input, context)
        except CapabilityError as error:
            return self._failure(error)
        if not isinstance(raw, dict):
            return self._failure(CapabilityError(SCHEMA_VALIDATION_FAILED, "agent 执行器返回了非对象结果"))
        completed = [(tool_id, result) for tool_id, result in observed if result.status == "completed"]
        if not completed:
            failed = next((result for _, result in reversed(observed) if result.error), None)
            if failed and failed.error:
                return ToolResult(
                    status=failed.status,
                    data={"agent_text": str(raw.get("text", ""))},
                    text=str(raw.get("text", "")),
                    error=failed.error,
                    trace=ToolTrace(
                        tool_calls=[call for _, result in observed for call in result.trace.tool_calls]
                    ),
                )
            return self._failure(
                CapabilityError(
                    TOOL_RESULT_INVALID,
                    f"技能 {manifest.id} 的智能体未调用任何白名单工具，不能把模型文本当作真实技能结果",
                )
            )

        evidence = [item for _, result in completed for item in result.evidence]
        calls = [call for _, result in observed for call in result.trace.tool_calls]
        tool_results = [
            {"tool_id": tool_id, "status": result.status, "data": result.data}
            for tool_id, result in completed
        ]
        # 除保留逐次调用结果外，合并常见数组字段，便于后续节点直接消费多次调用结果。
        merged: dict[str, Any] = {"tool_results": tool_results}
        for _, result in completed:
            for key, value in result.data.items():
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                elif key not in merged:
                    merged[key] = value
        for key, value in list(merged.items()):
            if isinstance(value, list) and key != "tool_results":
                unique = []
                seen = set()
                for item in value:
                    marker = repr(item)
                    if marker not in seen:
                        seen.add(marker)
                        unique.append(item)
                merged[key] = unique
        if "airports" in merged:
            merged["count"] = len(merged["airports"])
        elif "reports" in merged:
            merged["count"] = len(merged["reports"])
        merged["agent_text"] = str(raw.get("text", ""))
        return ToolResult(
            status="completed",
            data=merged,
            text=str(raw.get("text", "")),
            evidence=evidence,
            trace=ToolTrace(tool_calls=calls),
        )
