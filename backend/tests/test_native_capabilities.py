"""External capabilities must execute through Agno native drivers."""

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import uvicorn
from agno.skills import Skills
from agno.tools.calculator import CalculatorTools
from agno.tools.function import Function
from agno.tools.mcp import MCPTools

from backend.app.platform import capability_service, runtime
from backend.app.platform.db import Repository
from backend.app.platform.mcp_runtime import connect_mcp, create_mcp_tools, discover
from backend.app.platform.native_capabilities import load_native_skills
from backend.app.platform.tool_registry import catalog, freeze_bindings, permissions
from backend.app.platform.tool_runtime import ToolSession
from backend.tests.conftest import skill_zip
from backend.tests.test_v2_runtime import seed


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repository = Repository(f"sqlite:///{(tmp_path / 'capabilities.sqlite3').as_posix()}")
    repository.put("projects", {"id": "p", "members": [], "status": "active"})
    monkeypatch.setattr(runtime, "model_gateway", SimpleNamespace(ledger=SimpleNamespace()))
    return repository


def _user():
    return {"id": "admin", "role": "admin", "username": "admin"}


def _session(repository, definition, tool_ref):
    task = seed(repository, task_type="text_analysis", mode="cloud", content="public")
    agent = {
        **task["snapshot"]["agents"][0],
        "project_id": "p",
        "execution_strategy": "tool_agent",
        "tool_refs": [tool_ref],
        "skill_refs": [],
    }
    agent["runtime_bindings"] = freeze_bindings(agent, repository)
    task["snapshot"]["agents"] = [agent]
    repository.put("tasks", task)
    claimed = repository.claim("fixture-worker")
    execution = runtime.Execution(repository, claimed, "fixture-worker", claimed["lease_token"])
    execution.active_node_id = "n1"
    return ToolSession(execution, agent, 0, permissions(agent))


def test_external_skill_uses_native_loader_and_read_only_skills(repository, tmp_path):
    definition = capability_service.import_skill(skill_zip(), "p", _user(), repository)
    assert definition["upstream_version"] == "1.2.0"
    capability_service.publish(definition["id"], _user(), repository, {"list_sources": "data.list_sources"})
    available = catalog("p", repository)
    spec = next(item for item in available["skills"] if item["id"] == definition["id"])
    skills = load_native_skills([spec], tmp_path)
    assert isinstance(skills, Skills)
    functions = {function.name: function for function in skills.get_tools()}
    assert set(functions) == {"get_skill_instructions", "get_skill_reference"}
    instructions = json.loads(functions["get_skill_instructions"].entrypoint("external-skill"))
    assert "list_sources" in instructions["instructions"]
    reference = json.loads(functions["get_skill_reference"].entrypoint("external-skill", "guide.md"))
    assert reference["content"] == "Synthetic reference"


@pytest.mark.asyncio
async def test_native_calculator_toolkit_executes_in_governed_session(repository):
    definition = capability_service.create_native_toolkit(
        {"project_id": "p", "name": "Calculator", "provider": "calculator", "include_tools": ["add"]},
        _user(),
        repository,
    )
    capability_service.publish(definition["id"], _user(), repository)
    ref = {"tool_id": f"toolkit.{definition['id']}.add", "version": 1}
    session = await _session(repository, definition, ref).initialize()
    try:
        toolkit = next(item for item in session.agent_tools() if isinstance(item, CalculatorTools))
        result = await toolkit.functions["add"].entrypoint(a=2, b=3)
        assert result["data"]["result"] == 5
        assert result["data"]["external_evidence"]["kind"] == "external_result"
        assert isinstance(toolkit.functions["add"], Function)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_http_tool_is_native_function_and_calls_real_service(repository, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length))
            payload = json.dumps({"answer": body["value"] + 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HANGZHI_TOOL_HOSTS", "127.0.0.1")
    monkeypatch.setenv("HANGZHI_ALLOW_LOCAL_HTTP", "true")
    try:
        definition = capability_service.create_http_tool(
            {
                "project_id": "p",
                "name": "Increment",
                "description": "Increment a synthetic value",
                "url": f"http://127.0.0.1:{server.server_port}/increment",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "integer"}},
                    "required": ["answer"],
                },
                "secret_ref": None,
                "timeout_seconds": 5,
                "max_result_bytes": 1000,
            },
            _user(),
            repository,
        )
        capability_service.publish(definition["id"], _user(), repository)
        session = await _session(
            repository, definition, {"tool_id": definition["id"], "version": 1}
        ).initialize()
        try:
            function = session.standalone_functions[next(iter(session.standalone_functions))]
            assert isinstance(function, Function)
            result = await function.entrypoint(value=4)
            assert result["data"]["answer"] == 5
        finally:
            await session.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _launcher(monkeypatch):
    monkeypatch.setenv(
        "HANGZHI_MCP_LAUNCHERS",
        json.dumps(
            {
                "fixture": {
                    "command": sys.executable,
                    "args": ["-m", "backend.tests.mcp_fixture_server"],
                    "cwd": os.getcwd(),
                }
            }
        ),
    )


@pytest.mark.asyncio
async def test_external_stdio_mcp_uses_native_agno_driver(monkeypatch):
    _launcher(monkeypatch)
    spec = {"transport": "stdio", "launcher_id": "fixture", "secret_ref": None}
    discovered = await discover(spec)
    assert discovered["native_driver"] == "agno.tools.mcp.MCPTools"
    assert discovered["tools"][0]["name"] == "echo_sum"
    toolkit = await connect_mcp(create_mcp_tools(spec, include_tools=["echo_sum"]))
    assert isinstance(toolkit, MCPTools)
    try:
        result = await toolkit.functions["echo_sum"].entrypoint(a=4, b=7)
        assert json.loads(result.content)["total"] == 11
        assert toolkit.functions["echo_sum"].parameters["properties"]["a"]["type"] == "integer"
    finally:
        await toolkit.close()


@pytest.mark.asyncio
async def test_external_streamable_http_mcp_uses_native_agno_driver(monkeypatch):
    from backend.tests.mcp_fixture_server import server as fixture

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = uvicorn.Config(fixture.streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        import asyncio

        await asyncio.sleep(0.05)
    assert server.started
    monkeypatch.setenv("HANGZHI_MCP_HOSTS", "127.0.0.1")
    monkeypatch.setenv("HANGZHI_ALLOW_LOCAL_HTTP", "true")
    spec = {
        "transport": "streamable_http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "secret_ref": None,
    }
    try:
        discovered = await discover(spec)
        assert discovered["tools"][0]["name"] == "echo_sum"
        toolkit = await connect_mcp(create_mcp_tools(spec))
        try:
            result = await toolkit.functions["echo_sum"].entrypoint(a=8, b=2)
            assert json.loads(result.content)["total"] == 10
        finally:
            await toolkit.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()


def test_external_versions_survive_republication_and_disable_but_not_revocation(repository):
    from backend.app.platform.tool_registry import validate_frozen

    definition = capability_service.create_native_toolkit(
        {"project_id": "p", "name": "Calculator", "provider": "calculator", "include_tools": ["add"]},
        _user(),
        repository,
    )
    capability_service.publish(definition["id"], _user(), repository)
    agent = {
        "project_id": "p",
        "execution_strategy": "tool_agent",
        "tool_refs": [{"tool_id": f"toolkit.{definition['id']}.add", "version": 1}],
        "skill_refs": [],
    }
    agent["runtime_bindings"] = freeze_bindings(agent, repository)
    capability_service.publish(definition["id"], _user(), repository)
    validate_frozen(agent, repository)
    capability_service.action(definition["id"], "disable", _user(), repository)
    validate_frozen(agent, repository)
    capability_service.action(definition["id"], "revoke", _user(), repository)
    with pytest.raises(ValueError, match="capability_revoked"):
        validate_frozen(agent, repository)
