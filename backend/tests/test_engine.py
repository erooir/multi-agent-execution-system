import asyncio
import importlib
from copy import deepcopy

import pytest

from backend.app import engine, workflows
from backend.app.storage import Store


@pytest.fixture
def isolated_engine(tmp_path, monkeypatch):
    isolated = Store(tmp_path / "records.db")
    module_names = (
        "storage",
        "config",
        "auth",
        "knowledge",
        "seeds",
        "reports",
        "engine",
        "workflows",
        "api",
    )
    for name in module_names:
        module = importlib.import_module("backend.app." + name)
        if hasattr(module, "store"):
            monkeypatch.setattr(module, "store", isolated)
    knowledge_module = importlib.import_module("backend.app.knowledge")
    knowledge = knowledge_module.Knowledge(tmp_path)
    monkeypatch.setattr(knowledge_module, "knowledge", knowledge)
    api = importlib.import_module("backend.app.api")
    monkeypatch.setattr(api, "knowledge", knowledge)
    monkeypatch.setattr(engine, "_runtime_workflow", None)
    monkeypatch.setattr(engine, "TASKS", {})
    monkeypatch.setattr(engine, "EVALUATION_TASKS", {})

    async def no_paid_calls(*args, **kwargs):
        raise AssertionError("Tests must not contact a paid model")

    monkeypatch.setattr(engine.model_gateway, "complete", no_paid_calls)
    monkeypatch.setattr(
        engine.model_gateway,
        "budget",
        lambda: {
            "limit_cny": 300,
            "spent_cny": 0,
            "remaining_cny": 300,
            "reserved_cny": 0,
            "request_count": 0,
        },
    )
    importlib.import_module("backend.app.seeds").seed_all()
    return isolated, knowledge


async def settle(run_id):
    for _ in range(500):
        await asyncio.sleep(0.01)
        if run_id not in engine.TASKS:
            return engine.store.get("runs", run_id)
    raise AssertionError("run did not settle")


def test_graph_validation_and_immutable_versions(isolated_engine):
    store, _ = isolated_engine
    original = store.get("workflows", "workflow-tech-trends")
    assert workflows.validate_workflow(original)["valid"]
    broken = deepcopy(original)
    broken["edges"].append({"id": "cycle", "source": "end", "target": "start"})
    assert not workflows.validate_workflow(broken)["valid"]
    changed = workflows.save_workflow({"name": "edited"}, original["id"])
    assert changed["version"] == original["version"] + 1
    assert (
        store.get("workflow_versions", f"{original['id']}:{original['version']}")["snapshot"]["name"]
        == original["name"]
    )
    assert workflows.restore(original["id"], original["version"])["name"] == original["name"]


@pytest.mark.asyncio
async def test_real_agno_execution_review_report_and_rework(isolated_engine):
    store, _ = isolated_engine
    run = engine.create_run(
        {
            "workflow_id": "workflow-tech-trends",
            "project_id": "project-technology",
            "prompt": "复合材料研究趋势和证据限制",
            "mode": "rehearsal",
        },
        {"username": "operator"},
    )
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    assert run.get("report_id")
    assert store.get("reports", run["report_id"])["status"] == "draft"
    assert engine.runtime_workflow().id == "research-node-executor"
    retrieval_started = next(s for s in run["steps"] if s["kind"] == "retrieve")["started_at"]
    engine.review_run(run["id"], {"decision": "reject", "feedback": "补充证据限制"}, {"username": "reviewer"})
    rejected = store.get("runs", run["id"])
    assert rejected["review_rework_node"] == "analyze"
    engine.retry_run(run["id"], {"username": "operator"})
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    assert next(s for s in run["steps"] if s["kind"] == "retrieve")["started_at"] == retrieval_started
    assert store.get("reports", run["report_id"])["version"] == 2
    engine.review_run(
        run["id"],
        {"decision": "approve", "content": "# 人工修订\n\n合成演示资料，仅作功能核验。"},
        {"username": "reviewer"},
    )
    run = await settle(run["id"])
    assert run["status"] == "completed", run.get("error")
    report = store.get("reports", run["report_id"])
    assert report["status"] == "reviewed"
    assert "人工修订" in report["content"]
    assert report["version"] == 3
    assert len(engine.events(run["id"])) >= 20


@pytest.mark.asyncio
async def test_branch_failure_does_not_execute_analysis(isolated_engine):
    store, _ = isolated_engine
    project = store.save("projects", {"name": "Empty", "category": "technology"})
    run = engine.create_run(
        {
            "workflow_id": "workflow-tech-trends",
            "project_id": project["id"],
            "prompt": "没有对应资料的主题",
            "mode": "rehearsal",
        }
    )
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    by_kind = {s["kind"]: s for s in run["steps"]}
    assert by_kind["condition"]["payload"]["branch"] == "fail"
    assert by_kind["batch"]["status"] == by_kind["analyze"]["status"] == "skipped"
    assert by_kind["report"]["status"] == "completed"


@pytest.mark.asyncio
async def test_bound_skill_invoked_and_local_documents_blocked(isolated_engine, monkeypatch):
    store, knowledge = isolated_engine
    workflow = store.get("workflows", "workflow-tech-trends")
    next(n for n in workflow["nodes"] if n["id"] == "retrieve")["data"]["skill_id"] = "graph_query"
    store.save("workflows", workflow)
    original_execute = knowledge.execute
    called = []

    async def track(skill_id, params):
        called.append(skill_id)
        return await original_execute(skill_id, params)

    monkeypatch.setattr(knowledge, "execute", track)
    run = engine.create_run({"workflow_id": workflow["id"], "prompt": "复合材料", "mode": "rehearsal"})
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    assert "graph_query" in called
    doc = knowledge.ingest("local.txt", "仅本地机密资料".encode(), "project-technology", "local")
    with pytest.raises(ValueError, match="仅本地"):
        engine.create_run(
            {"workflow_id": workflow["id"], "prompt": "复合材料", "mode": "live", "document_ids": [doc["id"]]}
        )


@pytest.mark.asyncio
async def test_restart_requires_explicit_resume(isolated_engine):
    store, _ = isolated_engine
    run = engine.create_run(
        {"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "rehearsal"}
    )
    task = engine.TASKS[run["id"]]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    engine.recover_interrupted()
    assert store.get("runs", run["id"])["status"] == "interrupted"
    assert run["id"] not in engine.TASKS
    engine.retry_run(run["id"], {"username": "admin"})
    assert (await settle(run["id"]))["status"] == "waiting_review"


@pytest.mark.asyncio
async def test_dynamic_plan_uses_model_topology_and_rejects_bad_graph(isolated_engine, monkeypatch):
    import json

    store, _ = isolated_engine
    planner = store.get("agents", "agent-planner")
    planner["instructions"] = "规划配置实际生效标记"
    store.save("agents", planner)
    calls = []
    model_graph = {
        "name": "模型生成分支",
        "nodes": [
            {"id": "s", "kind": "start"},
            {"id": "k", "kind": "retrieve", "skill_id": "graph_query"},
            {"id": "b", "kind": "batch", "config": {"items": ["证据", "限制"]}},
            {"id": "r", "kind": "report"},
            {"id": "h", "kind": "review"},
            {"id": "e", "kind": "end"},
        ],
        "edges": [
            {"source": a, "target": b} for a, b in zip(["s", "k", "b", "r", "h"], ["k", "b", "r", "h", "e"])
        ],
    }

    async def fake_complete(*args, **kwargs):
        assert "规划配置实际生效标记" in kwargs["system"]
        calls.append(kwargs["purpose"])
        return {"text": json.dumps(model_graph)}

    monkeypatch.setattr(engine.model_gateway, "complete", fake_complete)
    result = await engine.plan(
        {"prompt": "按照图谱查询批量整理", "project_id": "project-technology", "mode": "live"}
    )
    assert [n["id"] for n in result["nodes"]] == ["s", "k", "b", "r", "h", "e"]
    assert result["nodes"][1]["data"]["skill_id"] == "graph_query"
    model_graph["edges"].append({"source": "e", "target": "s"})
    with pytest.raises(ValueError, match="校验"):
        await engine.plan({"prompt": "非法图", "mode": "live"})
    assert calls == ["workflow_plan", "workflow_plan", "workflow_plan_repair"]
    planner["enabled"] = False
    store.save("agents", planner)
    with pytest.raises(ValueError, match="智能体已禁用"):
        await engine.plan({"prompt": "停用规划器不应调用模型", "mode": "live"})
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_thirty_sample_evaluation_records_observed_rules(isolated_engine):
    store, _ = isolated_engine
    evaluation = engine.create_evaluation({"mode": "rehearsal"}, {"username": "operator"})
    await engine.EVALUATION_TASKS[evaluation["id"]]
    result = store.get("evaluations", evaluation["id"])
    assert result["status"] == "completed"
    assert result["total"] == result["completed"] == len(result["results"]) == 30
    assert all(r["checks"]["expected_stop_reached"] for r in result["results"])
    assert all(r["report_generated"] for r in result["results"])
    assert all(store.get("runs", r["run_id"])["status"] == "waiting_review" for r in result["results"])
    assert result["metrics"]["requires_human_review"] == 30
    assert not any(a["status"] == "approved" for a in store.list("approvals"))


@pytest.mark.asyncio
async def test_cancel_inflight_model_never_starts_next_paid_node(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def model_request(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        entered.set()
        await release.wait()
        return {"text": "请求已结束，用户已取消。"}

    monkeypatch.setattr(engine.model_gateway, "complete", model_request)
    run = engine.create_run({"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "live"})
    await asyncio.wait_for(entered.wait(), 5)
    # Cancellation can originate from a FastAPI worker thread.
    await asyncio.to_thread(engine.cancel_run, run["id"], {"username": "operator"})
    stale = store.get("runs", run["id"])
    stale["status"] = "running"
    assert store.save("runs", stale)["status"] == "cancelled"
    release.set()
    finished = await settle(run["id"])
    assert finished["status"] == "cancelled"
    assert calls == ["workflow_analysis"]
    assert not finished.get("report_id")


@pytest.mark.asyncio
async def test_live_writer_uses_budget_gateway_with_real_evidence(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    purposes = []

    async def fake_model(prompt, **kwargs):
        purposes.append(kwargs["purpose"])
        assert kwargs.get("evidence")
        assert all(item["visibility"] == "external" for item in kwargs["evidence"])
        source = kwargs["evidence"][0]["id"]
        return {
            "text": f"## 研究发现\n\n合成资料显示测试主题 [{source}]。",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(engine.model_gateway, "complete", fake_model)
    run = engine.create_run({"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "live"})
    run = await settle(run["id"])
    assert run["status"] == "waiting_review", run.get("error")
    assert purposes.count("workflow_analysis") == 4
    assert purposes[-1] == "report_generation"
    assert store.get("reports", run["report_id"])["mode"] == "live"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
async def test_report_writer_configuration_and_disabled_block(isolated_engine, monkeypatch, bound, enabled):
    store, _ = isolated_engine
    agent_id = "custom-writer" if bound else "agent-writer"
    store.save(
        "agents",
        {
            "id": agent_id,
            "name": "报告测试智能体",
            "enabled": enabled,
            "instructions": "自定义报告写作指令标记",
        },
    )
    workflow = store.get("workflows", "workflow-tech-trends")
    report_data = next(n for n in workflow["nodes"] if n["data"]["kind"] == "report")["data"]
    if bound:
        report_data["agent_id"] = agent_id
    else:
        report_data.pop("agent_id", None)
    store.save("workflows", workflow)
    project = store.save("projects", {"name": "资料不足直达报告", "category": "technology"})
    calls = []

    async def fake_model(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        assert "自定义报告写作指令标记" in kwargs["system"]
        assert "不得虚构" in kwargs["system"]
        assert "必须完成最后一节" in kwargs["system"]
        return {"text": "## 信息缺口\n\n没有来源证据，须补充资料后复核。"}

    monkeypatch.setattr(engine.model_gateway, "complete", fake_model)
    run = engine.create_run(
        {
            "workflow_id": workflow["id"],
            "project_id": project["id"],
            "prompt": "资料不足的研究任务",
            "mode": "live",
        }
    )
    run = await settle(run["id"])
    if enabled:
        assert run["status"] == "waiting_review", run.get("error")
        assert calls == ["report_generation"]
    else:
        assert run["status"] == "failed"
        assert "智能体已禁用" in run["error"]
        assert not calls
        assert not run.get("report_id")


@pytest.mark.asyncio
async def test_scheduler_limits_active_node_execution_to_two(isolated_engine, monkeypatch):
    original = engine._execute_node
    active, peak = 0, 0

    async def delayed(run_id, node_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.015)
            return await original(run_id, node_id)
        finally:
            active -= 1

    monkeypatch.setattr(engine, "_execute_node", delayed)
    runs = [
        engine.create_run({"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "rehearsal"})
        for _ in range(3)
    ]
    await asyncio.gather(*(settle(run["id"]) for run in runs))
    assert peak == 2


@pytest.mark.asyncio
async def test_truncated_report_fails_run_without_creating_draft_or_approval(isolated_engine, monkeypatch):
    from backend.app.model_gateway import ModelOutputTruncated

    store, _ = isolated_engine
    report_attempts = []

    async def fake_model(prompt, **kwargs):
        if kwargs["purpose"] == "report_generation":
            report_attempts.append(kwargs["purpose"])
            assert "600至900汉字" in prompt
            raise ModelOutputTruncated({"finish_reasons": ["length"], "request_id": "test-request"})
        source = kwargs["evidence"][0]["id"]
        return {"text": f"合成演示分析 [{source}]。"}

    monkeypatch.setattr(engine.model_gateway, "complete", fake_model)
    run = engine.create_run({"workflow_id": "workflow-tech-trends", "prompt": "复合材料", "mode": "live"})
    run = await settle(run["id"])
    assert run["status"] == "failed"
    assert "finish_reason=length" in run["error"]
    assert not run.get("report_id")
    assert not any(report.get("run_id") == run["id"] for report in store.list("reports"))
    assert not any(approval.get("run_id") == run["id"] for approval in store.list("approvals"))
    assert len(report_attempts) == 1, "Agno must not automatically repeat a failed paid request"
