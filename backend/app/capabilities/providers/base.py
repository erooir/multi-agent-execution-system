"""Provider 协议：所有 Provider 都以 ToolDefinition + 参数 + 上下文为输入，返回 dict 或 ToolResult。"""

from __future__ import annotations

from typing import Any, Protocol

from ..contracts import ExecutionContext, ToolDefinition


class Provider(Protocol):
    async def invoke(
        self, definition: ToolDefinition, arguments: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]: ...
