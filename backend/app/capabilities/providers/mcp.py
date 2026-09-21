"""MCP Provider 基座：stdio / streamable_http 会话、发现、健康检查与调用。

MCP Server 只负责连接与发现；发现的工具经 tool_allowlist 过滤后才转换为
标准 ToolDefinition。本期没有启用的 Server（docling-local 为 enabled:false），
但发现/健康/调用路径完整，可用内存或子进程桩测试。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from ..contracts import ExecutionContext, McpServerDefinition, ToolDefinition, ToolResult
from ..errors import (
    CAPABILITY_DISABLED,
    MCP_CONNECTION_FAILED,
    PERMISSION_DENIED,
    PROVIDER_UNAVAILABLE,
    TOOL_FAILED,
    TOOL_TIMEOUT,
    CapabilityError,
)
from ..registry import McpRegistry


class McpProvider:
    def __init__(self, servers: McpRegistry):
        self.servers = servers

    @asynccontextmanager
    async def _session(self, server: McpServerDefinition):
        if not server.enabled:
            raise CapabilityError(CAPABILITY_DISABLED, f"MCP Server {server.id} 未启用")
        try:
            if server.transport == "stdio":
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client

                parameters = StdioServerParameters(command=server.command, args=server.args)
                async with (
                    stdio_client(parameters) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
            else:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamablehttp_client

                async with (
                    streamablehttp_client(server.url) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
        except CapabilityError:
            raise
        except (FileNotFoundError, OSError) as error:
            raise CapabilityError(
                PROVIDER_UNAVAILABLE, f"MCP Server {server.id} 进程不可用（{type(error).__name__}）"
            ) from error
        except Exception as error:
            raise CapabilityError(
                MCP_CONNECTION_FAILED,
                f"MCP Server {server.id} 连接/握手失败（{type(error).__name__}）",
            ) from error

    async def discover(self, server_id: str) -> list[dict]:
        """发现并返回 allowlist 内的工具描述。"""
        server = self.servers.get(server_id)

        async def _list(session) -> list[dict]:
            result = await session.list_tools()
            allowlist = set(server.tool_allowlist)
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": getattr(tool, "inputSchema", None)
                    or getattr(tool, "input_schema", None)
                    or {},
                }
                for tool in result.tools
                if not allowlist or tool.name in allowlist
            ]

        try:
            async with asyncio.timeout(server.startup_timeout_seconds):
                async with self._session(server) as session:
                    return await _list(session)
        except TimeoutError as error:
            raise CapabilityError(
                MCP_CONNECTION_FAILED, f"MCP Server {server.id} 启动发现超时"
            ) from error

    async def health(self, server_id: str) -> dict:
        server = self.servers.get(server_id)
        if not server.enabled:
            return {"id": server.id, "status": "disabled", "tools": 0}
        try:
            tools = await self.discover(server_id)
            return {"id": server.id, "status": "ready", "tools": len(tools)}
        except CapabilityError as error:
            return {"id": server.id, "status": "unavailable", "error": error.code}

    def to_tool_definitions(self, server_id: str, discovered: list[dict]) -> list[ToolDefinition]:
        """把发现的 MCP 工具转换为注册用 ToolDefinition。"""
        server = self.servers.get(server_id)
        return [
            ToolDefinition(
                id=f"mcp.{server.id}.{tool['name']}",
                version="1.0.0",
                name=tool["name"],
                provider="mcp",
                entrypoint=f"mcp://{server.id}/{tool['name']}",
                input_schema=tool.get("input_schema") or {"type": "object"},
                read_only=True,
                network="none" if server.transport == "stdio" else "required",
                data_egress="derived",
                timeout_seconds=server.call_timeout_seconds,
            )
            for tool in discovered
        ]

    async def call(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | ToolResult:
        """调用 MCP 工具并把 SDK 信封解包为规范化载荷。

        优先取 structuredContent（dict 直接作为 data）；否则尝试把文本内容解析为
        JSON（对象直接作为 data，其他 JSON 值包为 {"result": ...}）；都不是时返回
        ToolResult(data={}, text=原文)，不丢结果也不报错。isError 报 tool_failed。
        """
        server = self.servers.get(server_id)
        if server.tool_allowlist and tool_name not in server.tool_allowlist:
            raise CapabilityError(
                PERMISSION_DENIED, f"MCP 工具 {tool_name} 不在 {server.id} 的 tool_allowlist 中"
            )
        try:
            async with asyncio.timeout(server.call_timeout_seconds):
                async with self._session(server) as session:
                    result = await session.call_tool(tool_name, arguments)
        except TimeoutError as error:
            raise CapabilityError(
                TOOL_TIMEOUT, f"MCP 工具 {tool_name} 调用超时"
            ) from error
        structured = getattr(result, "structured_content", None)
        if structured is None:
            structured = getattr(result, "structuredContent", None)
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)
        text = "\n".join(
            getattr(item, "text", "") for item in (result.content or []) if getattr(item, "text", None)
        )
        if is_error:
            raise CapabilityError(TOOL_FAILED, f"MCP 工具 {tool_name} 返回错误：{text[:500]}")
        if isinstance(structured, dict):
            # fastmcp 对非对象返回值包一层 {"result": ...}；字符串可能是 JSON 文本。
            inner = structured.get("result") if set(structured) == {"result"} else None
            if isinstance(inner, str):
                return self._unwrap_text(inner)
            return structured
        if structured is not None:
            return {"result": structured}
        return self._unwrap_text(text)

    @staticmethod
    def _unwrap_text(text: str) -> dict[str, Any] | ToolResult:
        """无结构化内容时：JSON 文本解析为 data；非 JSON 原文作 text、data 为空。"""
        stripped = text.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return ToolResult(status="completed", data={}, text=text)
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    async def invoke(
        self, definition: ToolDefinition, arguments: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        # entrypoint 形式：mcp://<server_id>/<tool_name>
        rest = definition.entrypoint.removeprefix("mcp://")
        server_id, _, tool_name = rest.partition("/")
        if not server_id or not tool_name:
            raise CapabilityError(
                PROVIDER_UNAVAILABLE, f"非法的 MCP entrypoint: {definition.entrypoint!r}"
            )
        return await self.call(server_id, tool_name, arguments)
