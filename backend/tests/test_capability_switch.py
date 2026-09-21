"""阶段 B 切换测试：engine/api/workflows/model_gateway 全部走新能力层。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from backend.app import engine, workflows
from backend.app.capabilities.facade import capability_runtime
from backend.tests.test_engine import isolated_engine as _isolated_engine_fixture
from backend.tests.test_engine import settle

isolated_engine = _isolated_engine_fixture


# ---------------------------------------------------------------- engine


@pytest.mark.asyncio
async def test_run_records_nested_capability_trace(isolated_engine):
    run = engine.create_run(
        {"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "rehearsal"}
    )
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    calls = run.get("capability_calls", [])
    assert calls and all(call["status"] == "completed" for call in calls)
    retrieve_call = next(call for call in calls if call["skill_id"] == "knowledge_search")
    tool_call = retrieve_call["tool_calls"][0]
    assert tool_call["tool_id"] == "local.knowledge.keyword_search"
    assert tool_call["provider"] == "local"
    retrieve_step = next(s for s in run["steps"] if s["kind"] == "retrieve")
    assert retrieve_step["payload"]["trace"]["tool_calls"][0]["tool_id"].startswith("local.")
    assert retrieve_step["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_skill_failure_carries_stable_error_code(isolated_engine):
    store, _ = isolated_engine
    workflow = store.get("workflows", "workflow-tech-trends")
    # document_parse 绑定到 parse 节点，但运行未选资料：明确失败而不是自动挑选项目文档。
    next(n for n in workflow["nodes"] if n["id"] == "parse")["data"]["skill_id"] = "document_parse"
    store.save("workflows", workflow)
    run = engine.create_run(
        {"workflow_id": workflow["id"], "prompt": "无资料解析", "mode": "rehearsal"}
    )
    run = await settle(run["id"])
    assert run["status"] == "failed"
    assert "schema_validation_failed" in run["error"]


def test_plan_prompt_catalog_comes_from_registry():
    catalog = engine._skill_catalog_text()
    for skill_id in (
        "knowledge_search",
        "graph_query",
        "document_parse",
        "ocr",
        "semantic_search",
        "multimodal",
    ):
        assert skill_id in catalog
    prompt = engine._plan_initial_prompt("测试任务")
    assert "已注册技能" in prompt
    assert "允许knowledge_search,graph_query" not in prompt


# ------------------------------------------------------------- workflows


def test_workflow_skill_binding_validated_against_registry(isolated_engine):
    store, _ = isolated_engine
    base = store.get("workflows", "workflow-tech-trends")

    def validate_with(skill_id, kind="retrieve"):
        workflow = dict(base)
        nodes = [dict(n) for n in base["nodes"]]
        target = next(n for n in nodes if n["id"] == "retrieve")
        target["data"] = {**target["data"], "skill_id": skill_id, "kind": kind}
        workflow["nodes"] = nodes
        return workflows.validate_workflow(workflow)

    assert validate_with("knowledge_search")["valid"]
    assert not validate_with("not_registered")["valid"]
    # document_parse 声明 node_kinds=[parse]，绑到 retrieve 节点不兼容。
    assert not validate_with("document_parse", kind="retrieve")["valid"]
    # multimodal 兼容 parse/retrieve。
    assert validate_with("multimodal", kind="retrieve")["valid"]


# ------------------------------------------------------------ model_gateway


@pytest.mark.asyncio
async def test_run_agent_binds_real_tools_and_records_calls(monkeypatch):
    gateway_module = importlib.import_module("backend.app.model_gateway")
    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "test-key")
    from backend.app.capabilities.contracts import ExecutionContext as Ctx

    runtime = capability_runtime()
    context = Ctx(mode="live", network_policy="allow")
    manifest = runtime.skills.get("graph_query")
    bindings = runtime.skill_runtime.build_tool_functions(manifest, context)
    seen = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def arun(self, message, **kwargs):
            seen["tools"] = self.kwargs.get("tools")
            seen["tool_call_limit"] = self.kwargs.get("tool_call_limit")
            tool = seen["tools"][0]
            result = await tool(query="复合材料")
            return SimpleNamespace(content=f"已调用工具，状态：{result['status']}")

    monkeypatch.setattr("agno.agent.Agent", FakeAgent)
    result = await gateway_module.model_gateway.run_agent(
        {"name": "测试", "instructions": "测试"}, "查询图谱", bindings, context, max_rounds=2
    )
    assert result["text"].endswith("completed")
    assert result["tool_calls"][0]["tool_id"] == "local.graph.query"
    assert seen["tool_call_limit"] == 2
    # 没有经过真实 HTTP 传输，不产生任何费用记录。
    assert result["usage"] == {}


@pytest.mark.asyncio
async def test_run_agent_rejects_truncated_output(monkeypatch):
    gateway_module = importlib.import_module("backend.app.model_gateway")
    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "test-key")

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, message, **kwargs):
            return SimpleNamespace(content="截断")

    monkeypatch.setattr("agno.agent.Agent", FakeAgent)
    original_transport = gateway_module.MeteredTransport

    class TruncatedTransport(original_transport):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.result = {"finish_reasons": ["length"], "request_id": "t"}

    monkeypatch.setattr(gateway_module, "MeteredTransport", TruncatedTransport)
    with pytest.raises(gateway_module.ModelOutputTruncated):
        await gateway_module.model_gateway.run_agent({"name": "t"}, "你好", {}, None)


# -------------------------------------------------------------------- api


def test_api_capability_endpoints(isolated_engine):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        tools = client.get("/api/tools").json()
        tool_ids = {tool["id"] for tool in tools}
        assert "local.knowledge.keyword_search" in tool_ids
        assert "model.vision.analyze" in tool_ids
        assert all("entrypoint" not in tool for tool in tools)

        result = client.post(
            "/api/tools/local.knowledge.keyword_search/test",
            json={"input": {"query": "复合材料"}, "mode": "rehearsal"},
        ).json()
        assert result["status"] == "completed"
        assert result["trace"]["tool_calls"][0]["provider"] == "local"

        bad = client.post(
            "/api/tools/local.knowledge.keyword_search/test",
            json={"input": {"query": 123}, "mode": "rehearsal"},
        ).json()
        assert bad["status"] == "failed"
        assert bad["error"]["code"] == "schema_validation_failed"

        servers = client.get("/api/mcp-servers").json()
        assert servers[0]["id"] == "docling-local" and servers[0]["enabled"] is False
        assert "command" not in servers[0]
        health = client.get("/api/mcp-servers/docling-local/health").json()
        assert health["status"] == "disabled"

        events = client.get("/api/capability-events").json()
        assert any(e["capability_id"] == "local.knowledge.keyword_search" for e in events)

        skills = client.get("/api/skills").json()
        assert {s["id"] for s in skills} >= {"knowledge_search", "multimodal"}
        assert all("allowed_tools" in s for s in skills)

        skill_result = client.post(
            "/api/skills/knowledge_search/test",
            json={"query": "复合材料", "mode": "rehearsal"},
        ).json()
        assert skill_result["skill_id"] == "knowledge_search"
        assert skill_result["status"] == "completed"
        assert skill_result["trace"]["tool_calls"]
        assert client.post(
            "/api/skills/unknown_skill/test", json={"query": "x"}
        ).status_code == 404


def test_api_agent_live_test_uses_run_agent(isolated_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    gateway_module = importlib.import_module("backend.app.model_gateway")
    captured = {}

    async def fake_run_agent(agent_spec, messages, tool_bindings, context=None, max_rounds=4):
        captured["bindings"] = sorted(tool_bindings)
        captured["agent_spec"] = agent_spec
        return {
            "text": "已通过工具检索到 3 条证据。",
            "usage": {"prompt_tokens": 1},
            "cost_cny": 0,
            "model": "deepseek-flash",
            "tool_calls": [
                {"tool_id": "local.knowledge.keyword_search", "status": "completed", "duration_ms": 3}
            ],
        }

    monkeypatch.setattr(gateway_module.model_gateway, "run_agent", fake_run_agent)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        result = client.post(
            "/api/agents/agent-coordinator/test",
            json={"message": "复合材料研究进展如何？", "mode": "live"},
        ).json()
        assert result["mode"] == "live"
        assert result["tool_calls"][0]["tool_id"] == "local.knowledge.keyword_search"
        assert result["note"] == ""
        assert captured["bindings"] == ["local.graph.query", "local.knowledge.keyword_search"]
        assert "协同调度智能体" in captured["agent_spec"]["name"]

    async def no_call_run_agent(agent_spec, messages, tool_bindings, context=None, max_rounds=4):
        return {"text": "仅凭已有知识回答。", "usage": {}, "cost_cny": 0, "tool_calls": []}

    monkeypatch.setattr(gateway_module.model_gateway, "run_agent", no_call_run_agent)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "demo12345"})
        result = client.post(
            "/api/agents/agent-coordinator/test",
            json={"message": "随便聊聊", "mode": "live"},
        ).json()
        assert "未调用能力" in result["note"]
