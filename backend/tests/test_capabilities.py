"""阶段 A 能力内核测试：Registry / Loader / ToolRuntime / SkillRuntime / Policy / MCP。

不触发任何付费模型调用：视觉理解使用 drill 预检、拦截路径或伪造的
model_gateway.complete；MCP 使用本地 fastmcp 子进程桩。
"""

from __future__ import annotations

import importlib
import io
import sys
import textwrap

import pytest

from backend.app import knowledge as knowledge_module
from backend.app.capabilities import (
    AuditLog,
    ExecutionContext,
    PolicyGate,
    SkillRuntime,
    ToolRuntime,
    load_default_registries,
    load_skills,
    load_tools,
)
from backend.app.capabilities.contracts import McpServerDefinition, SkillManifest, ToolDefinition
from backend.app.capabilities.errors import CapabilityError
from backend.app.capabilities.providers.mcp import McpProvider
from backend.app.capabilities.registry import McpRegistry, SkillRegistry, ToolRegistry
from backend.app.storage import Store

LIVE = ExecutionContext(mode="live", network_policy="allow")
DRILL = ExecutionContext(mode="drill", network_policy="deny")


@pytest.fixture
def env(tmp_path, monkeypatch):
    database = Store(tmp_path / "records.sqlite3")
    monkeypatch.setattr(knowledge_module, "store", database)
    knowledge = knowledge_module.Knowledge(tmp_path)
    monkeypatch.setattr(knowledge_module, "knowledge", knowledge)
    database.save("projects", {"id": "test-project", "name": "测试资料", "category": "technology"})
    skills, tools, mcp_servers = load_default_registries()
    audit = AuditLog()
    tool_runtime = ToolRuntime(tools, audit=audit, mcp_servers=mcp_servers)
    skill_runtime = SkillRuntime(skills, tool_runtime, audit=audit)
    return knowledge, database, skill_runtime, tool_runtime, audit


# ---------------------------------------------------------------- Registry


def test_registry_rejects_duplicate_id():
    registry = ToolRegistry()
    definition = ToolDefinition(
        id="local.test.dup", version="1.0.0", name="a", provider="local", entrypoint="time:time"
    )
    registry.register(definition)
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(definition.model_copy(update={"version": "2.0.0"}))


def test_registry_missing_capability_error():
    registry = SkillRegistry()
    with pytest.raises(CapabilityError) as caught:
        registry.get("missing")
    assert caught.value.code == "capability_not_found"


def test_loader_rejects_unknown_tool_reference(tmp_path):
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# 说明", encoding="utf-8")
    (skill_dir / "skill.yaml").write_text(
        textwrap.dedent(
            """
            id: bad_skill
            version: 1.0.0
            name: 坏技能
            execution_mode: recipe
            allowed_tools: [local.missing.tool]
            recipe:
              - tool: local.missing.tool
            """
        ),
        encoding="utf-8",
    )
    _, tools, _ = load_default_registries()
    with pytest.raises(CapabilityError, match="不存在的 Tool"):
        load_skills(skills_dir=tmp_path, tool_registry=tools)


def test_loader_rejects_invalid_schema_and_duplicate_tools(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: local.bad.schema
                version: 1.0.0
                name: 坏 schema
                provider: local
                entrypoint: time:time
                input_schema: {type: str}
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CapabilityError, match="不支持的 type"):
        load_tools(tools_dir=tmp_path)
    (tmp_path / "bad.yaml").write_text(
        textwrap.dedent(
            """
            tools:
              - id: local.bad.dup
                version: 1.0.0
                name: 甲
                provider: local
                entrypoint: time:time
              - id: local.bad.dup
                version: 2.0.0
                name: 乙
                provider: local
                entrypoint: time:time
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复注册"):
        load_tools(tools_dir=tmp_path)


def test_default_manifests_cover_nine_skills():
    skills, tools, mcp_servers = load_default_registries()
    assert skills.ids() == [
        "airport_lookup",
        "aviation_weather",
        "document_parse",
        "graph_query",
        "knowledge_search",
        "literature_search",
        "multimodal",
        "ocr",
        "semantic_search",
    ]
    assert "docling-local" in mcp_servers
    assert mcp_servers.get("docling-local").enabled is False
    assert mcp_servers.get("aviation-local").enabled is True
    vision = tools.get("model.vision.analyze")
    assert vision.network == "required" and vision.data_egress == "raw" and vision.uses_model_budget
    metar = tools.get("aviation.noaa.get_metar")
    assert metar.allowed_hosts == ["aviationweather.gov"] and metar.data_egress == "query"


# ------------------------------------------------------------ Tool contract


async def test_tool_input_schema_validation(env):
    _, _, _, tool_runtime, _ = env
    result = await tool_runtime.invoke("local.knowledge.keyword_search", {"query": 123}, DRILL)
    assert result.status == "failed"
    assert result.error.code == "schema_validation_failed"


async def test_tool_timeout(env):
    _, _, _, tool_runtime, _ = env
    tool_runtime.tools.register(
        ToolDefinition(
            id="local.test.slow",
            version="1.0.0",
            name="慢工具",
            provider="local",
            entrypoint="backend.tests.capability_fixtures:slow",
            input_schema={"type": "object", "properties": {"seconds": {"type": "number"}}},
            timeout_seconds=0.05,
        )
    )
    result = await tool_runtime.invoke("local.test.slow", {"seconds": 5}, DRILL)
    assert result.status == "failed"
    assert result.error.code == "tool_timeout"


async def test_tool_result_normalization_and_trace(env):
    _, _, _, tool_runtime, _ = env
    result = await tool_runtime.invoke("local.graph.query", {"query": ""}, DRILL)
    assert result.status == "completed"
    assert result.data == {"nodes": [], "edges": [], "evidence": []}
    call = result.trace.tool_calls[0]
    assert call.tool_id == "local.graph.query" and call.provider == "local"


async def test_unknown_tool_returns_capability_not_found(env):
    _, _, _, tool_runtime, _ = env
    result = await tool_runtime.invoke("local.missing.tool", {}, DRILL)
    assert result.status == "failed" and result.error.code == "capability_not_found"


# ------------------------------------------------------------------ Policy


async def test_local_data_blocked_from_network_tool(env):
    _, _, _, tool_runtime, _ = env
    context = ExecutionContext(mode="live", network_policy="allow", data_visibility="local")
    result = await tool_runtime.invoke(
        "model.vision.analyze", {"document_id": "doc_x"}, context
    )
    assert result.status == "blocked"
    assert result.error.code == "data_egress_blocked"


async def test_confirmation_required(env):
    _, _, _, tool_runtime, _ = env
    tool_runtime.tools.register(
        ToolDefinition(
            id="local.test.confirm",
            version="1.0.0",
            name="需确认",
            provider="local",
            entrypoint="backend.tests.capability_fixtures:ping",
            requires_confirmation=True,
        )
    )
    blocked = await tool_runtime.invoke("local.test.confirm", {}, DRILL)
    assert blocked.status == "blocked" and blocked.error.code == "confirmation_required"
    allowed = await tool_runtime.invoke(
        "local.test.confirm", {}, ExecutionContext(mode="drill", confirmed=True)
    )
    assert allowed.status == "completed"


async def test_budget_gate_blocks_model_tool(env):
    _, tools, _ = load_default_registries()
    runtime = ToolRuntime(
        tools, policy=PolicyGate(budget_provider=lambda: {"remaining_cny": 0}), audit=AuditLog()
    )
    result = await runtime.invoke("model.vision.analyze", {"document_id": "d"}, LIVE)
    assert result.status == "blocked" and result.error.code == "budget_exceeded"


def test_audit_redacts_sensitive_arguments():
    audit = AuditLog()
    audit.record(
        capability_type="tool",
        capability_id="local.test",
        status="completed",
        context=DRILL,
        arguments={"query": "航空", "text": "机密原文", "api_key": "sk-xxx"},
    )
    event = audit.events()[-1]
    assert event["arguments"]["text"] == "<redacted>"
    assert event["arguments"]["api_key"] == "<redacted>"
    assert event["arguments"]["query"] == "航空"


# ----------------------------------------------------- Six-skill migration


async def test_skill_knowledge_search(env):
    knowledge, _, skill_runtime, _, _ = env
    knowledge.ingest("研究.md", "复合材料回收采用加热重塑工艺。".encode(), "test-project", "local")
    result = await skill_runtime.execute(
        "knowledge_search", {"query": "复合材料回收", "project_id": "test-project"}, DRILL
    )
    assert result.status == "completed"
    assert result.data["method"] == "keyword" and result.data["count"] >= 1
    assert result.evidence[0]["visibility"] == "local"
    assert result.trace.tool_calls[0].tool_id == "local.knowledge.keyword_search"


async def test_skill_graph_query(env):
    _, database, skill_runtime, _, _ = env
    database.save("graph_nodes", {"id": "n1", "label": "复合材料", "project_id": "test-project"})
    database.save("graph_nodes", {"id": "n2", "label": "回收工艺", "project_id": "test-project"})
    database.save(
        "graph_edges",
        {"id": "e1", "source": "n1", "target": "n2", "relation": "应用于", "chunk_id": "c1"},
    )
    result = await skill_runtime.execute(
        "graph_query", {"query": "复合材料", "project_id": "test-project"}, DRILL
    )
    assert result.status == "completed"
    assert {node["id"] for node in result.data["nodes"]} == {"n1", "n2"}
    assert len(result.data["edges"]) == 1


async def test_skill_document_parse(env):
    knowledge, _, skill_runtime, _, _ = env
    document = knowledge.ingest("补充.md", "碳纤维回收中试记录。".encode(), "test-project", "external")
    result = await skill_runtime.execute(
        "document_parse",
        {"document_ids": [document["id"]], "project_id": "test-project"},
        DRILL,
    )
    assert result.status == "completed"
    assert result.data["count"] >= 1
    assert result.evidence[0]["document_id"] == document["id"]
    # Tool 不自动挑选项目文档：缺少 document_ids 是输入错误。
    bad = await skill_runtime.execute("document_parse", {"project_id": "test-project"}, DRILL)
    assert bad.status == "failed" and bad.error.code == "schema_validation_failed"


async def test_skill_ocr_real_rapidocr(env):
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw

    knowledge, _, skill_runtime, _, _ = env
    image = Image.new("RGB", (320, 80), "white")
    ImageDraw.Draw(image).text((10, 30), "12345", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    document = knowledge.ingest("识别.png", buffer.getvalue(), "test-project", "local")
    result = await skill_runtime.execute(
        "ocr", {"document_id": document["id"], "project_id": "test-project"}, DRILL
    )
    assert result.status == "completed"
    assert {"text", "evidence", "region_count", "elapsed"} <= set(result.data)
    assert result.data["region_count"] == len(result.evidence)


async def test_skill_semantic_search_and_prepare(env, monkeypatch):
    import numpy as np

    knowledge, _, skill_runtime, _, _ = env
    knowledge.ingest("a.txt", "飞机燃料研究".encode(), "test-project", "external")
    target = knowledge.ingest("b.txt", "低碳发展技术".encode(), "test-project", "external")

    class Embedding:
        def embed(self, texts, batch_size=16):
            return iter(np.array([1.0, 0.0]) if "低碳" in text else np.array([0.0, 1.0]) for text in texts)

        def query_embed(self, query):
            return iter([np.array([1.0, 0.0])])

    def fake_get_embedding():
        knowledge._embedding_state = "ready"
        return Embedding()

    monkeypatch.setattr(knowledge, "_get_embedding", fake_get_embedding)
    result = await skill_runtime.execute("semantic_search", {"query": "环境友好"}, DRILL)
    assert result.status == "completed"
    assert result.data["method"] == "bge_vector"
    assert result.evidence[0]["document_id"] == target["id"]
    assert result.evidence[0]["score"] == 1.0
    # prepare_only 走 local.embedding.prepare 分支并保留 embedding 状态字段。
    prepare = await skill_runtime.execute("semantic_search", {"prepare_only": True}, DRILL)
    assert prepare.status == "completed"
    assert prepare.data["embedding"]["status"] == "ready"
    assert prepare.data["prepare_only"] is True


async def test_skill_multimodal_drill_is_dry_run(env):
    _, _, skill_runtime, _, _ = env
    drill_allow = ExecutionContext(mode="drill", network_policy="allow")
    result = await skill_runtime.execute("multimodal", {"document_id": "doc_x"}, drill_allow)
    assert result.status == "dry_run"
    assert result.data["dry_run"] is True
    # 演练模式下网络策略禁止时，预检如实报告会被拦截，而不是假装成功。
    denied = await skill_runtime.execute("multimodal", {"document_id": "doc_x"}, DRILL)
    assert denied.status == "blocked" and denied.error.code == "permission_denied"


async def test_skill_multimodal_blocks_local_document(env):
    from PIL import Image

    knowledge, database, skill_runtime, _, _ = env
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    document = knowledge.ingest("本地.png", buffer.getvalue(), "test-project", "local")
    result = await skill_runtime.execute(
        "multimodal", {"document_id": document["id"], "mode": "live"}, LIVE
    )
    assert result.status == "blocked"
    assert result.error.code == "data_egress_blocked"
    assert "禁止发送" in result.error.message
    assert any(a["action"] == "model.blocked_local_document" for a in database.list("audits"))


async def test_skill_multimodal_rehearsal_never_simulates(env):
    from PIL import Image

    knowledge, _, skill_runtime, _, _ = env
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    document = knowledge.ingest("外部.png", buffer.getvalue(), "test-project", "external")
    result = await skill_runtime.execute(
        "multimodal", {"document_id": document["id"], "mode": "rehearsal"}, LIVE
    )
    assert result.status == "failed"
    assert "不模拟" in result.error.message


async def test_skill_multimodal_live_uses_budget_gateway(env, monkeypatch):
    from PIL import Image

    knowledge, _, skill_runtime, _, _ = env
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    document = knowledge.ingest("外部图.png", buffer.getvalue(), "test-project", "external")
    gateway_module = importlib.import_module("backend.app.model_gateway")
    calls = []

    async def fake_complete(prompt, **kwargs):
        calls.append(kwargs)
        return {"text": "图片中是合成测试图案。", "usage": {"prompt_tokens": 1}, "model": "deepseek-flash"}

    monkeypatch.setattr(gateway_module.model_gateway, "complete", fake_complete)
    result = await skill_runtime.execute(
        "multimodal", {"document_id": document["id"], "mode": "live"}, LIVE
    )
    assert result.status == "completed"
    assert result.data["text"] == "图片中是合成测试图案。"
    assert result.data["document_id"] == document["id"]
    assert calls and calls[0]["purpose"] == "multimodal"
    assert calls[0]["images"][0].startswith("data:image/png;base64,")


# ------------------------------------------------------------- Agent 模式


async def test_agent_mode_drill_precheck_and_live_disabled():
    skills = SkillRegistry()
    skills.register(
        SkillManifest(
            id="agent_skill",
            version="1.0.0",
            name="代理技能",
            execution_mode="agent",
            allowed_tools=["local.graph.query"],
        )
    )
    _, tools, _ = load_default_registries()
    runtime = SkillRuntime(skills, ToolRuntime(tools, audit=AuditLog()))
    dry = await runtime.execute("agent_skill", {"query": "复合材料"}, DRILL)
    assert dry.status == "dry_run"
    assert dry.data["bound_tools"] == ["local.graph.query"]
    live = await runtime.execute("agent_skill", {"query": "复合材料"}, LIVE)
    assert live.status == "failed"
    assert live.error.code == "capability_disabled"
    assert "不会伪造" in live.error.message


def test_build_tool_functions_call_through_runtime():
    skills, tools, _ = load_default_registries()
    tool_runtime = ToolRuntime(tools, audit=AuditLog())
    runtime = SkillRuntime(skills, tool_runtime)
    manifest = skills.get("graph_query")
    functions = runtime.build_tool_functions(manifest, DRILL)
    assert list(functions) == ["local.graph.query"]


# --------------------------------------------------------------------- MCP

_FASTMCP_SERVER = '''
from fastmcp import FastMCP

mcp = FastMCP("fixture")


@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b


@mcp.tool()
def hidden() -> str:
    return "不应被发现"

mcp.run()
'''


@pytest.fixture
def mcp_fixture(tmp_path):
    script = tmp_path / "mcp_fixture_server.py"
    script.write_text(_FASTMCP_SERVER, encoding="utf-8")
    servers = McpRegistry()
    servers.register(
        McpServerDefinition(
            id="fixture",
            transport="stdio",
            command=sys.executable,
            args=[str(script)],
            enabled=True,
            tool_allowlist=["add"],
            startup_timeout_seconds=30,
            call_timeout_seconds=30,
        )
    )
    return servers


async def test_mcp_discover_filters_allowlist(mcp_fixture):
    provider = McpProvider(mcp_fixture)
    discovered = await provider.discover("fixture")
    assert [tool["name"] for tool in discovered] == ["add"]
    health = await provider.health("fixture")
    assert health["status"] == "ready" and health["tools"] == 1
    definitions = provider.to_tool_definitions("fixture", discovered)
    assert definitions[0].id == "mcp.fixture.add"
    assert definitions[0].provider == "mcp"


async def test_mcp_call_and_allowlist_enforced(mcp_fixture):
    provider = McpProvider(mcp_fixture)
    result = await provider.call("fixture", "add", {"a": 2, "b": 3})
    # fastmcp 对标量返回值包 {"result": ...}，Provider 解包后直达载荷。
    assert result["result"] == 5
    with pytest.raises(CapabilityError) as caught:
        await provider.call("fixture", "hidden", {})
    assert caught.value.code == "permission_denied"


async def test_mcp_unavailable_server():
    servers = McpRegistry()
    servers.register(
        McpServerDefinition(
            id="missing",
            transport="stdio",
            command="definitely-not-a-real-command-xyz",
            enabled=True,
            startup_timeout_seconds=15,
        )
    )
    provider = McpProvider(servers)
    with pytest.raises(CapabilityError) as caught:
        await provider.discover("missing")
    assert caught.value.code in {"provider_unavailable", "mcp_connection_failed"}
    health = await provider.health("missing")
    assert health["status"] == "unavailable"


async def test_mcp_disabled_server_rejected():
    _, _, mcp_servers = load_default_registries()
    provider = McpProvider(mcp_servers)
    with pytest.raises(CapabilityError) as caught:
        await provider.discover("docling-local")
    assert caught.value.code == "capability_disabled"
    assert (await provider.health("docling-local"))["status"] == "disabled"
