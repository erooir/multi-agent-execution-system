"""阶段 D 测试：真实外部 HTTP 工具（MockTransport，不发真实请求）、OurAirports
离线工具（fixture 快照）、首个 MCP Server 真实 stdio 链路、外发策略。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from backend.app.capabilities import (
    AuditLog,
    ExecutionContext,
    PolicyGate,
    SkillRuntime,
    ToolRuntime,
    load_default_registries,
)
from backend.app.capabilities.contracts import McpServerDefinition, SkillManifest
from backend.app.capabilities.errors import CapabilityError
from backend.app.capabilities.providers.http import HttpProvider
from backend.app.capabilities.providers.local import LocalProvider
from backend.app.capabilities.providers.mcp import McpProvider
from backend.app.capabilities.registry import McpRegistry, SkillRegistry
from backend.app.capabilities.tools import airports as airport_tools

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ourairports"

LIVE = ExecutionContext(mode="live", network_policy="allow")
DRILL = ExecutionContext(mode="drill", network_policy="deny")
DRILL_ALLOW = ExecutionContext(mode="drill", network_policy="allow")

METAR_PAYLOAD = [
    {
        "icaoId": "ZBAA",
        "reportTime": "2026-09-21T06:00:00Z",
        "rawOb": "METAR ZBAA 210600Z 36005KT CAVOK 22/08 Q1013 NOSIG",
        "temp": 22,
        "wdir": 360,
        "wspd": 5,
        "visib": "10+",
        "fltCat": "VFR",
    }
]
TAF_PAYLOAD = [
    {
        "icaoId": "ZBAA",
        "issueTime": "2026-09-21T05:00:00Z",
        "validTimeFrom": "2026-09-21T06:00:00Z",
        "validTimeTo": "2026-09-22T12:00:00Z",
        "rawTAF": "TAF ZBAA 210500Z 2106/2212 36005KT CAVOK",
    }
]
SIGMET_PAYLOAD = [
    {
        "airSigmetId": "WS01",
        "hazard": "TS",
        "severity": "MOD",
        "validTimeFrom": "2026-09-21T06:00:00Z",
        "validTimeTo": "2026-09-21T10:00:00Z",
        "rawAirSigmet": "WSNT01 ZBAA SIGMET 1 VALID 210600/211000 EMBD TS",
    }
]
OPENALEX_PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/W1",
            "title": "低空气象对 eVTOL 运行的影响",
            "doi": "https://doi.org/10.0000/demo1",
            "publication_year": 2025,
            "authorships": [{"author": {"display_name": "张三"}}],
            "cited_by_count": 7,
            "primary_location": {"source": {"display_name": "合成期刊"}},
        }
    ]
}
CROSSREF_PAYLOAD = {
    "message": {
        "items": [
            {
                "title": ["城市空中交通运行概念研究"],
                "DOI": "10.0000/demo2",
                "published": {"date-parts": [[2024]]},
                "author": [{"family": "李", "given": "四"}],
                "is-referenced-by-count": 3,
                "container-title": ["合成交通运输研究"],
            }
        ]
    }
}


def _mock_transport(sleep_seconds: float = 0) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        host = request.url.host
        path = request.url.path
        if host == "aviationweather.gov" and path.endswith("/metar"):
            return httpx.Response(200, json=METAR_PAYLOAD)
        if host == "aviationweather.gov" and path.endswith("/taf"):
            return httpx.Response(200, json=TAF_PAYLOAD)
        if host == "aviationweather.gov" and path.endswith("/airsigmet"):
            return httpx.Response(200, json=SIGMET_PAYLOAD)
        if host == "api.openalex.org":
            return httpx.Response(200, json=OPENALEX_PAYLOAD)
        if host == "api.crossref.org":
            return httpx.Response(200, json=CROSSREF_PAYLOAD)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def runtime():
    skills, tools, mcp_servers = load_default_registries()
    tool_runtime = ToolRuntime(
        tools,
        policy=PolicyGate(),
        audit=AuditLog(),
        providers={
            "local": LocalProvider(),
            "http": HttpProvider(transport=_mock_transport()),
            "mcp": McpProvider(mcp_servers),
        },
    )
    return skills, tools, mcp_servers, tool_runtime


# ------------------------------------------------------------- HTTP 工具


async def test_noaa_metar_real_request_path(runtime):
    _, _, _, tool_runtime = runtime
    result = await tool_runtime.invoke("aviation.noaa.get_metar", {"icao": "zbaa"}, LIVE)
    assert result.status == "completed", result.error
    assert result.data["station"] == "ZBAA"
    assert result.data["reports"][0]["flight_category"] == "VFR"
    evidence = result.evidence[0]
    assert evidence["origin"] == "external"
    assert "aviationweather.gov" in evidence["source_uri"]
    assert evidence["retrieved_at"]
    assert "METAR ZBAA" in evidence["text"]


async def test_noaa_taf_and_sigmet(runtime):
    _, _, _, tool_runtime = runtime
    taf = await tool_runtime.invoke("aviation.noaa.get_taf", {"icao": "ZBAA"}, LIVE)
    assert taf.status == "completed" and taf.data["count"] == 1
    sigmet = await tool_runtime.invoke("aviation.noaa.get_sigmet", {"bbox": "40,110,30,120"}, LIVE)
    assert sigmet.status == "completed" and sigmet.data["reports"][0]["hazard"] == "TS"
    bad = await tool_runtime.invoke("aviation.noaa.get_sigmet", {"bbox": "不是坐标"}, LIVE)
    assert bad.status == "failed"


async def test_openalex_and_crossref(runtime):
    _, _, _, tool_runtime = runtime
    openalex = await tool_runtime.invoke(
        "research.openalex.search_works", {"query": "eVTOL", "limit": 5}, LIVE
    )
    assert openalex.status == "completed"
    work = openalex.data["works"][0]
    assert work["title"] == "低空气象对 eVTOL 运行的影响"
    assert work["doi"] == "https://doi.org/10.0000/demo1"
    assert work["cited_by_count"] == 7
    assert openalex.evidence[0]["source_uri"] == "https://doi.org/10.0000/demo1"
    crossref = await tool_runtime.invoke("research.crossref.search_doi", {"query": "城市空中交通"}, LIVE)
    assert crossref.status == "completed"
    assert crossref.data["works"][0]["doi"] == "10.0000/demo2"


async def test_http_tool_drill_is_dry_run(runtime):
    _, _, _, tool_runtime = runtime
    # drill + 放行网络策略：仅预检返回 dry_run，不发真实请求；drill + 禁止网络：如实报拦截。
    result = await tool_runtime.invoke("aviation.noaa.get_metar", {"icao": "ZBAA"}, DRILL_ALLOW)
    assert result.status == "dry_run"
    denied = await tool_runtime.invoke("aviation.noaa.get_metar", {"icao": "ZBAA"}, DRILL)
    assert denied.status == "blocked" and denied.error.code == "permission_denied"


async def test_http_tool_timeout(runtime):
    _, tools, mcp_servers, _ = runtime
    slow = tools.get("aviation.noaa.get_metar").model_copy(update={"timeout_seconds": 0.2})
    tools.upsert(slow)
    tool_runtime = ToolRuntime(
        tools,
        policy=PolicyGate(),
        audit=AuditLog(),
        providers={
            "local": LocalProvider(),
            "http": HttpProvider(transport=_mock_transport(sleep_seconds=2)),
            "mcp": McpProvider(mcp_servers),
        },
    )
    result = await tool_runtime.invoke("aviation.noaa.get_metar", {"icao": "ZBAA"}, LIVE)
    assert result.status == "failed"
    assert result.error.code == "tool_timeout"


async def test_http_allowed_hosts_enforced(runtime):
    _, _, _, tool_runtime = runtime
    provider = tool_runtime.providers["http"]
    definition = tool_runtime.tools.get("aviation.noaa.get_metar")
    with pytest.raises(CapabilityError) as caught:
        provider.check_allowed(definition, "https://evil.example.com/api")
    assert caught.value.code == "data_egress_blocked"


async def test_local_data_blocked_from_http_tool(runtime):
    _, _, _, tool_runtime = runtime
    context = ExecutionContext(mode="live", network_policy="allow", data_visibility="local")
    result = await tool_runtime.invoke("aviation.noaa.get_metar", {"icao": "ZBAA"}, context)
    assert result.status == "blocked"
    assert result.error.code == "data_egress_blocked"


async def test_http_upstream_error_is_provider_unavailable(runtime):
    _, tools, mcp_servers, _ = runtime
    bad = tools.get("aviation.noaa.get_metar").model_copy(update={"id": "aviation.noaa.bad"})
    tools.upsert(bad)

    async def handler(request):
        return httpx.Response(503, text="upstream down")

    tool_runtime = ToolRuntime(
        tools,
        policy=PolicyGate(),
        audit=AuditLog(),
        providers={
            "local": LocalProvider(),
            "http": HttpProvider(transport=httpx.MockTransport(handler)),
            "mcp": McpProvider(mcp_servers),
        },
    )
    result = await tool_runtime.invoke("aviation.noaa.bad", {"icao": "ZBAA"}, LIVE)
    assert result.status == "failed"
    assert result.error.code == "provider_unavailable"


# --------------------------------------------------------- OurAirports 工具


@pytest.fixture
def fixture_snapshot(monkeypatch):
    monkeypatch.setenv("OURAIRPORTS_DIR", str(FIXTURE_DIR))
    airport_tools.reset_cache()
    yield
    airport_tools.reset_cache()


async def test_ourairports_lookup_fixture(runtime, fixture_snapshot):
    _, _, _, tool_runtime = runtime
    result = await tool_runtime.invoke("aviation.ourairports.lookup_airport", {"query": "ZBAA"}, DRILL)
    assert result.status == "completed", result.error
    assert result.data["count"] == 1
    airport = result.data["airports"][0]
    assert airport["iata"] == "PEK" and airport["municipality"] == "Beijing"
    assert result.data["snapshot_date"] == "2026-01-15"
    assert "2026-01-15" in result.evidence[0]["retrieved_at"]
    # 名称模糊匹配；closed 机场不出现。
    fuzzy = await tool_runtime.invoke("aviation.ourairports.lookup_airport", {"query": "beijing"}, DRILL)
    assert fuzzy.data["count"] == 2
    assert all(a["type"] != "closed" for a in fuzzy.data["airports"])


async def test_ourairports_nearby_fixture(runtime, fixture_snapshot):
    _, _, _, tool_runtime = runtime
    result = await tool_runtime.invoke(
        "aviation.ourairports.nearby_airports",
        {"latitude": 40.08, "longitude": 116.60, "radius_km": 80},
        DRILL,
    )
    assert result.status == "completed"
    idents = [a["ident"] for a in result.data["airports"]]
    assert idents[0] == "ZBAA" and "ZBAD" in idents and "ZSPD" not in idents
    assert result.data["airports"][0]["distance_km"] < 5


async def test_ourairports_missing_snapshot(runtime, monkeypatch, tmp_path):
    monkeypatch.setenv("OURAIRPORTS_DIR", str(tmp_path / "empty"))
    airport_tools.reset_cache()
    _, _, _, tool_runtime = runtime
    result = await tool_runtime.invoke("aviation.ourairports.lookup_airport", {"query": "ZBAA"}, DRILL)
    assert result.status == "failed"
    assert result.error.code == "provider_unavailable"
    assert "download_ourairports" in result.error.message
    airport_tools.reset_cache()


# ----------------------------------------------------------- 新 Skill 链路


async def test_airport_lookup_skill_runs_local_in_drill(runtime, fixture_snapshot):
    skills, _, _, tool_runtime = runtime
    skill_runtime = SkillRuntime(skills, tool_runtime, audit=AuditLog())
    result = await skill_runtime.execute("airport_lookup", {"query": "ZBAA"}, DRILL)
    assert result.status == "completed"
    assert result.data["airports"][0]["icao"] == "ZBAA"
    assert result.trace.tool_calls[0].tool_id == "aviation.ourairports.lookup_airport"


async def test_airport_lookup_agent_adapts_queries_and_merges_real_tool_results(
    runtime, fixture_snapshot, monkeypatch
):
    from types import SimpleNamespace

    from backend.app import model_gateway as gateway_module
    from backend.app.capabilities.runtime import agent as agent_runtime

    skills, _, _, tool_runtime = runtime
    received = {}

    skill_runtime = SkillRuntime(skills, tool_runtime, audit=AuditLog())
    monkeypatch.setattr(
        agent_runtime,
        "capability_runtime",
        lambda: SimpleNamespace(skills=skills, skill_runtime=skill_runtime),
    )

    async def fake_run_agent(agent_spec, messages, functions, context=None, max_rounds=4):
        received.update(
            instructions=agent_spec["instructions"],
            message=messages,
            tools=sorted(functions),
        )
        # 模拟业务 Agent 把中文集合任务拆成英文城市名，并多次调用 Skill。
        await functions["airport_lookup"](query="Beijing", limit=5)
        await functions["airport_lookup"](query="Shanghai", limit=5)
        return {"text": "已按城市拆分并完成检索。", "usage": {}, "cost_cny": 0}

    monkeypatch.setattr(gateway_module.model_gateway, "run_agent", fake_run_agent)
    raw, observed = await agent_runtime.run_business_agent(
        {
            "id": "agent-retriever",
            "name": "知识检索智能体",
            "instructions": "根据任务检索真实数据。",
            "skill_ids": ["airport_lookup"],
        },
        {
            "task": "给我检索一下当前大城市的机场状况。",
            "initial_config": {"query": "大城市机场 ICAO IATA 名称 坐标 跑道 海拔"},
            "upstream": [],
        },
        ExecutionContext(
            mode="live",
            network_policy="allow",
            agent_id="agent-retriever",
            allowed_skill_ids=["airport_lookup"],
        ),
        node_kind="retrieve",
        preferred_skill_id="airport_lookup",
    )

    airports = [airport for _, result in observed for airport in result.data["airports"]]
    assert raw["text"].startswith("已按城市拆分")
    assert len(airports) == 3
    assert {airport["municipality"] for airport in airports} == {
        "Beijing",
        "Shanghai",
    }
    assert len(observed) == 2
    assert received["tools"] == ["airport_lookup"]
    assert "转换成适配格式" in received["instructions"]
    assert "给我检索" in received["message"]


async def test_aviation_weather_skill_drill_precheck(runtime):
    skills, _, _, tool_runtime = runtime
    skill_runtime = SkillRuntime(skills, tool_runtime, audit=AuditLog())
    result = await skill_runtime.execute("aviation_weather", {"icao": "ZBAA"}, DRILL_ALLOW)
    assert result.status == "dry_run"


async def test_literature_search_skill_live(runtime):
    skills, _, _, tool_runtime = runtime
    skill_runtime = SkillRuntime(skills, tool_runtime, audit=AuditLog())
    result = await skill_runtime.execute("literature_search", {"query": "eVTOL", "limit": 3}, LIVE)
    assert result.status == "completed"
    assert len(result.trace.tool_calls) == 2
    sources = {e["source_uri"].split("/")[2] for e in result.evidence}
    assert "doi.org" in sources


def test_planning_catalog_includes_new_skills():
    from backend.app import engine

    catalog = engine._skill_catalog_text()
    for skill_id in ("aviation_weather", "airport_lookup", "literature_search"):
        assert skill_id in catalog


def test_new_skills_bindable_on_retrieve():
    from backend.app.capabilities.facade import capability_runtime

    skills = capability_runtime().skills
    for skill_id in ("aviation_weather", "airport_lookup", "literature_search"):
        assert "retrieve" in skills.get(skill_id).node_kinds


# ------------------------------------------------------ MCP 真实 stdio 链路


@pytest.fixture
def aviation_mcp_registry():
    servers = McpRegistry()
    servers.register(
        McpServerDefinition(
            id="aviation-local",
            transport="stdio",
            command=sys.executable,
            args=["-m", "backend.app.mcp_servers.aviation_server"],
            enabled=True,
            tool_allowlist=["lookup_airport", "nearby_airports"],
            startup_timeout_seconds=30,
            call_timeout_seconds=30,
        )
    )
    return servers


async def test_mcp_aviation_discover_call_health(aviation_mcp_registry, fixture_snapshot):
    provider = McpProvider(aviation_mcp_registry)
    discovered = await provider.discover("aviation-local")
    assert sorted(tool["name"] for tool in discovered) == ["lookup_airport", "nearby_airports"]
    health = await provider.health("aviation-local")
    assert health["status"] == "ready" and health["tools"] == 2
    # 真实 stdio 调用，子进程继承测试夹具指向的小型合成快照；结果已解包为真实载荷。
    result = await provider.call("aviation-local", "lookup_airport", {"query": "ZBAA", "limit": 3})
    assert result["count"] >= 1
    assert result["airports"][0]["ident"] == "ZBAA"
    assert result["evidence"] and result["evidence"][0]["origin"] == "external"
    with pytest.raises(CapabilityError) as caught:
        await provider.call("aviation-local", "drop_table", {})
    assert caught.value.code == "permission_denied"


async def test_mcp_tools_registered_and_invoked_via_runtime(aviation_mcp_registry, fixture_snapshot):
    provider = McpProvider(aviation_mcp_registry)
    discovered = await provider.discover("aviation-local")
    _, tools, _ = load_default_registries()
    for definition in provider.to_tool_definitions("aviation-local", discovered):
        tools.upsert(definition)
    assert "mcp.aviation-local.lookup_airport" in tools
    tool_runtime = ToolRuntime(
        tools,
        policy=PolicyGate(),
        audit=AuditLog(),
        providers={
            "local": LocalProvider(),
            "http": HttpProvider(transport=_mock_transport()),
            "mcp": provider,
        },
    )
    result = await tool_runtime.invoke(
        "mcp.aviation-local.lookup_airport", {"query": "ZBAA", "limit": 2}, LIVE
    )
    assert result.status == "completed", result.error
    assert result.trace.provider == "mcp"
    # data 是解包后的真实载荷，evidence 提升到 ToolResult 顶层。
    assert result.data["airports"][0]["ident"] == "ZBAA"
    assert result.evidence and result.evidence[0]["source_uri"]


def test_mcp_dependent_skill_degraded_when_server_down():
    from backend.app import knowledge as knowledge_module
    from backend.app.capabilities.status import skill_summaries

    _, tools, _ = load_default_registries()
    tools.upsert(
        provider_tool := tools.get("local.graph.query").model_copy(
            update={
                "id": "mcp.aviation-local.lookup_airport",
                "provider": "mcp",
                "entrypoint": "mcp://aviation-local/lookup_airport",
            }
        )
    )
    assert provider_tool.provider == "mcp"
    skills = SkillRegistry()
    skills.register(
        SkillManifest(
            id="airport_lookup",
            version="1.0.0",
            name="机场要素检索",
            execution_mode="recipe",
            allowed_tools=["mcp.aviation-local.lookup_airport"],
            node_kinds=["retrieve"],
        )
    )
    skills.register(
        SkillManifest(
            id="graph_query",
            version="1.0.0",
            name="图谱查询",
            execution_mode="recipe",
            allowed_tools=[],
            node_kinds=["retrieve"],
        )
    )
    summaries = {
        item["id"]: item
        for item in skill_summaries(
            skills,
            knowledge_module.knowledge,
            tools=tools,
            server_health={"aviation-local": "unavailable"},
        )
    }
    assert summaries["airport_lookup"]["status"] == "degraded"
    assert summaries["airport_lookup"]["enabled"] is True
    assert summaries["graph_query"]["status"] == "ready"


# ------------------------------------------------- MCP 结果规范化与启动发现

_MCP_SHAPES_SERVER = """
import json
from fastmcp import FastMCP

mcp = FastMCP("shapes")


@mcp.tool()
def rich() -> dict:
    return {"items": [1], "evidence": [{"id": "e1", "text": "证据片段"}], "text": "结构化摘要"}


@mcp.tool()
def json_text() -> str:
    return json.dumps({"items": [2], "evidence": [{"id": "e2"}]}, ensure_ascii=False)


@mcp.tool()
def plain() -> str:
    return "这是一段普通文本，不是 JSON"

mcp.run()
"""


@pytest.fixture
def shapes_mcp(tmp_path):
    script = tmp_path / "shapes_server.py"
    script.write_text(_MCP_SHAPES_SERVER, encoding="utf-8")
    servers = McpRegistry()
    servers.register(
        McpServerDefinition(
            id="shapes",
            transport="stdio",
            command=sys.executable,
            args=[str(script)],
            enabled=True,
            tool_allowlist=["rich", "json_text", "plain"],
            startup_timeout_seconds=30,
            call_timeout_seconds=30,
        )
    )
    return servers


async def test_mcp_result_unwrap_structured_json_and_plain(shapes_mcp):
    provider = McpProvider(shapes_mcp)
    # 1) dict 返回值：structured content 直接作为 data。
    rich = await provider.call("shapes", "rich", {})
    assert rich["items"] == [1] and rich["evidence"][0]["id"] == "e1"
    # 2) JSON 文本（fastmcp 包成 {"result": "<json>"}）：解析为 data。
    parsed = await provider.call("shapes", "json_text", {})
    assert parsed["items"] == [2] and parsed["evidence"][0]["id"] == "e2"
    # 3) 非 JSON 文本：不报错不丢结果，data 为空对象、text 为原文。
    plain = await provider.call("shapes", "plain", {})
    assert isinstance(plain.data, dict) and plain.data == {}
    assert "普通文本" in plain.text


async def test_mcp_unwrapped_payload_promoted_to_tool_result(shapes_mcp):
    provider = McpProvider(shapes_mcp)
    discovered = await provider.discover("shapes")
    _, tools, _ = load_default_registries()
    for definition in provider.to_tool_definitions("shapes", discovered):
        tools.upsert(definition)
    tool_runtime = ToolRuntime(
        tools,
        policy=PolicyGate(),
        audit=AuditLog(),
        providers={"local": LocalProvider(), "http": HttpProvider(), "mcp": provider},
    )
    result = await tool_runtime.invoke("mcp.shapes.rich", {}, LIVE)
    assert result.status == "completed"
    assert result.data["items"] == [1]
    assert result.text == "结构化摘要"
    assert result.evidence == [{"id": "e1", "text": "证据片段"}]
    plain = await tool_runtime.invoke("mcp.shapes.plain", {}, LIVE)
    assert plain.status == "completed"
    assert plain.data == {} and "普通文本" in plain.text


async def test_startup_autodiscovery_registers_enabled_server(shapes_mcp):
    from backend.app.capabilities.facade import CapabilityRuntime, discover_enabled_servers

    runtime = CapabilityRuntime()
    runtime.mcp_servers = shapes_mcp
    runtime.mcp_provider = McpProvider(shapes_mcp)
    assert "mcp.shapes.rich" not in runtime.tools
    results = await discover_enabled_servers(runtime)
    assert results == {"shapes": "ready(3)"}
    assert runtime.server_health["shapes"] == "ready"
    assert "mcp.shapes.rich" in runtime.tools
    assert runtime.tools.get("mcp.shapes.plain").provider == "mcp"


async def test_startup_autodiscovery_timeout_marks_unavailable(tmp_path):
    import time

    from backend.app.capabilities.facade import CapabilityRuntime, discover_enabled_servers

    script = tmp_path / "slow_server.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    servers = McpRegistry()
    servers.register(
        McpServerDefinition(
            id="slow",
            transport="stdio",
            command=sys.executable,
            args=[str(script)],
            enabled=True,
            startup_timeout_seconds=1,
        )
    )
    runtime = CapabilityRuntime()
    runtime.mcp_servers = servers
    runtime.mcp_provider = McpProvider(servers)
    started = time.monotonic()
    results = await discover_enabled_servers(runtime)
    elapsed = time.monotonic() - started
    assert results["slow"].startswith("unavailable")
    assert runtime.server_health["slow"] == "unavailable"
    assert elapsed < 15  # 受 startup_timeout 约束，不得拖住启动
