"""Thin governed factory for Agno's native MCPTools."""

from __future__ import annotations

import json
import os

from agno.tools.mcp import MCPTools
from mcp import StdioServerParameters

from .network_policy import auth_headers, secret_value, validate_target
from .tool_registry import ROOT


def _launcher(launcher_id):
    try:
        configured = json.loads(os.environ.get("HANGZHI_MCP_LAUNCHERS", "{}"))
        launcher = configured[launcher_id]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValueError("mcp_launcher_not_allowed") from None
    command = launcher.get("command", "")
    if not os.path.isabs(command) or not os.path.isfile(command):
        raise ValueError("mcp_launcher_not_allowed")
    args = launcher.get("args", [])
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError("mcp_launcher_not_allowed")
    return launcher


def _stdio_parameters(spec):
    launcher = _launcher(spec.get("launcher_id", ""))
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE"}
    }
    env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", AGNO_TELEMETRY="false")
    for key, value in (launcher.get("env") or {}).items():
        if isinstance(key, str) and isinstance(value, str) and "KEY" not in key and "TOKEN" not in key:
            env[key] = value
    if spec.get("secret_ref") and launcher.get("secret_env"):
        env[launcher["secret_env"]] = secret_value(spec["secret_ref"])
    return StdioServerParameters(
        command=launcher["command"],
        args=launcher["args"],
        cwd=launcher.get("cwd") or str(ROOT),
        env=env,
    )


def create_builtin_mcp(include_tools=None):
    import sys

    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE"}
    }
    env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", AGNO_TELEMETRY="false")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.app.platform.mcp_server"],
        cwd=str(ROOT),
        env=env,
    )
    return MCPTools(
        server_params=params,
        transport="stdio",
        timeout_seconds=15,
        include_tools=include_tools,
    )


def create_mcp_tools(spec, *, include_tools=None, tool_name_prefix=None):
    if spec["transport"] == "stdio":
        return MCPTools(
            server_params=_stdio_parameters(spec),
            transport="stdio",
            timeout_seconds=15,
            include_tools=include_tools,
            tool_name_prefix=tool_name_prefix,
        )
    if spec["transport"] == "streamable_http":
        import httpx2
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        validate_target(spec["url"], "HANGZHI_MCP_HOSTS")
        headers = auth_headers(spec.get("secret_ref"))

        def http_client_factory(*, headers=None, auth=None, **_):
            return httpx2.AsyncClient(
                headers=headers,
                auth=auth,
                timeout=httpx2.Timeout(15, read=60),
                trust_env=False,
                follow_redirects=False,
            )

        transport = StreamableHttpTransport(
            spec["url"], headers=headers, httpx_client_factory=http_client_factory
        )
        client = Client(transport, timeout=15)
        tools = MCPTools(
            session=client,
            transport="streamable-http",
            include_tools=include_tools,
            tool_name_prefix=tool_name_prefix,
        )
        tools._session_context = client
        return tools
    raise ValueError("mcp_transport_invalid")


async def connect_mcp(tools):
    if tools.session is not None and tools._session_context is not None and not tools.initialized:
        await tools._session_context.__aenter__()
    await tools.connect()
    if not tools.initialized:
        if tools.session is not None and tools._session_context is not None:
            await tools._session_context.__aexit__(None, None, None)
            tools._session_context = None
        await tools.close()
        raise RuntimeError("mcp_unavailable")
    return tools


async def discover(spec):
    tools = await connect_mcp(create_mcp_tools(spec))
    try:
        discovered = [
            {
                "name": function.name,
                "description": function.description or "",
                "inputSchema": function.parameters,
            }
            for function in tools.functions.values()
        ]
        if not discovered:
            raise ValueError("mcp_no_tools")
        return {
            "native_driver": "agno.tools.mcp.MCPTools",
            "tools": discovered,
        }
    finally:
        await tools.close()
