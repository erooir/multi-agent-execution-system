"""ToolRuntime：参数 schema 校验、Policy Gate、超时、调用 Provider、规范化为 ToolResult。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..audit import AuditLog, audit_log
from ..contracts import (
    ExecutionContext,
    SkillManifest,
    ToolCallRecord,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolTrace,
)
from ..errors import (
    BUDGET_EXCEEDED,
    CONFIRMATION_REQUIRED,
    DATA_EGRESS_BLOCKED,
    PERMISSION_DENIED,
    PROVIDER_UNAVAILABLE,
    TOOL_RESULT_INVALID,
    TOOL_TIMEOUT,
    CapabilityError,
)
from ..policy import PolicyGate
from ..providers.local import LocalProvider
from ..registry import McpRegistry, ToolRegistry
from ..schema import validate_value

# 被 Policy Gate 拦下的前置错误，结果状态为 blocked 而不是 failed。
_BLOCKED_CODES = {PERMISSION_DENIED, DATA_EGRESS_BLOCKED, CONFIRMATION_REQUIRED, BUDGET_EXCEEDED}


class ToolRuntime:
    def __init__(
        self,
        tools: ToolRegistry,
        policy: PolicyGate | None = None,
        audit: AuditLog | None = None,
        mcp_servers: McpRegistry | None = None,
        providers: dict[str, Any] | None = None,
    ):
        self.tools = tools
        self.policy = policy or PolicyGate()
        self.audit = audit or audit_log
        if providers is not None:
            self.providers = providers
        else:
            from ..providers.http import HttpProvider
            from ..providers.mcp import McpProvider

            self.providers = {
                "local": LocalProvider(),
                "http": HttpProvider(),
                "mcp": McpProvider(mcp_servers or McpRegistry()),
            }

    def _result(
        self,
        definition: ToolDefinition | None,
        status: str,
        *,
        data: dict | None = None,
        text: str = "",
        evidence: list | None = None,
        error: CapabilityError | None = None,
        duration_ms: int = 0,
    ) -> ToolResult:
        return ToolResult(
            status=status,  # type: ignore[arg-type]
            data=data or {},
            text=text,
            evidence=evidence or [],
            error=ToolError(**error.as_dict()) if error else None,
            trace=ToolTrace(
                tool_id=definition.id if definition else "",
                tool_version=definition.version if definition else "",
                provider=definition.provider if definition else "",
                duration_ms=duration_ms,
                tool_calls=[
                    ToolCallRecord(
                        tool_id=definition.id,
                        tool_version=definition.version,
                        provider=definition.provider,
                        status=status,
                        duration_ms=duration_ms,
                    )
                ]
                if definition
                else [],
            ),
        )

    async def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None,
        context: ExecutionContext,
        skill: SkillManifest | None = None,
    ) -> ToolResult:
        arguments = dict(arguments or {})
        started = time.monotonic()
        try:
            definition = self.tools.get(tool_id)
        except CapabilityError as error:
            result = self._result(None, "failed", error=error)
            self.audit.record(
                capability_type="tool",
                capability_id=tool_id,
                status="failed",
                context=context,
                error_code=error.code,
                arguments=arguments,
            )
            return result
        try:
            validate_value(definition.input_schema, arguments)
            self.policy.check_tool(definition, context, skill)
        except CapabilityError as error:
            status = "blocked" if error.code in _BLOCKED_CODES else "failed"
            result = self._result(definition, status, error=error)
            self.audit.record(
                capability_type="tool",
                capability_id=tool_id,
                status=status,
                context=context,
                error_code=error.code,
                arguments=arguments,
            )
            return result
        # 演练模式：需要网络的 Tool 只做预检，不发起真实外部调用。
        if context.mode == "drill" and definition.network != "none":
            result = self._result(
                definition,
                "dry_run",
                data={
                    "dry_run": True,
                    "checks": ["schema_valid", "policy_passed"],
                    "note": "演练模式仅完成 schema 与权限预检，未发起网络/模型调用",
                },
            )
            self.audit.record(
                capability_type="tool",
                capability_id=tool_id,
                status="dry_run",
                context=context,
                arguments=arguments,
            )
            return result
        provider = self.providers.get(definition.provider)
        try:
            if provider is None:
                raise CapabilityError(PROVIDER_UNAVAILABLE, f"Provider {definition.provider} 未注册")
            raw = await asyncio.wait_for(
                provider.invoke(definition, arguments, context),
                timeout=definition.timeout_seconds,
            )
            result = self._normalize(definition, raw)
            result.trace.tool_calls = [
                ToolCallRecord(
                    tool_id=definition.id,
                    tool_version=definition.version,
                    provider=definition.provider,
                    status=result.status,
                )
            ]
        except TimeoutError:
            result = self._result(
                definition, "failed", error=CapabilityError(TOOL_TIMEOUT, f"Tool {tool_id} 调用超时")
            )
        except CapabilityError as error:
            status = "blocked" if error.code in _BLOCKED_CODES else "failed"
            result = self._result(definition, status, error=error)
        except Exception as error:  # noqa: BLE001 - Provider 业务错误统一收敛为失败结果
            result = self._result(
                definition,
                "failed",
                error=CapabilityError("tool_failed", str(error) or type(error).__name__),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        result.trace.duration_ms = duration_ms
        if result.trace.tool_calls:
            result.trace.tool_calls[0].duration_ms = duration_ms
        self.audit.record(
            capability_type="tool",
            capability_id=tool_id,
            status=result.status,
            context=context,
            duration_ms=duration_ms,
            error_code=result.error.code if result.error else None,
            arguments=arguments,
        )
        return result

    def _normalize(self, definition: ToolDefinition, raw: Any) -> ToolResult:
        if isinstance(raw, ToolResult):
            result = raw
            result.trace.tool_id = definition.id
            result.trace.tool_version = definition.version
            result.trace.provider = definition.provider
            data = result.data
        elif isinstance(raw, dict):
            data = raw
            result = ToolResult(
                status="completed",
                data=raw,
                text=str(raw.get("text", "")),
                evidence=raw.get("evidence") if isinstance(raw.get("evidence"), list) else [],
            )
            result.trace.tool_id = definition.id
            result.trace.tool_version = definition.version
            result.trace.provider = definition.provider
        else:
            raise CapabilityError(TOOL_RESULT_INVALID, f"Tool {definition.id} 返回了无法规范化的结果类型")
        try:
            validate_value(definition.output_schema, data, where=f"{definition.id}.output")
        except CapabilityError as error:
            raise CapabilityError(TOOL_RESULT_INVALID, str(error)) from error
        return result
