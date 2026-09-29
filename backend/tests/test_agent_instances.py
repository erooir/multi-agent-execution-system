"""Lifecycle guarantees are verified without sending paid model requests."""

import asyncio
from itertools import pairwise

import pytest

from backend.app import agent_instances, engine
from backend.tests.test_engine import isolated_engine as _isolated_engine_fixture
from backend.tests.test_engine import settle

isolated_engine = _isolated_engine_fixture


def workflow(store, identity="context-workflow", *, analysis=False):
    nodes = (
        [("start", "start", None), ("analyze", "analyze", "context-agent"), ("end", "end", None)]
        if analysis
        else [("start", "start", "context-agent"), ("end", "end", "context-agent")]
    )
    return store.save(
        "workflows",
        {
            "id": identity,
            "name": identity,
            "status": "published",
            "version": 1,
            "nodes": [
                {"id": node_id, "data": {"kind": kind, **({"agent_id": agent} if agent else {})}}
                for node_id, kind, agent in nodes
            ],
            "edges": [{"id": f"{a[0]}-{b[0]}", "source": a[0], "target": b[0]} for a, b in pairwise(nodes)],
        },
    )


def definition(store):
    return store.save(
        "agents",
        {
            "id": "context-agent",
            "name": "上下文核验智能体",
            "role": "writer",
            "instructions": "原始版本指令",
            "skill_ids": [],
            "enabled": True,
            "version": 1,
        },
    )


@pytest.mark.asyncio
async def test_queued_run_keeps_config_after_edit_and_disable(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    agent = definition(store)
    flow = workflow(store, analysis=True)
    monkeypatch.setattr(engine, "launch", lambda run_id: None)
    run = engine.create_run({"workflow_id": flow["id"], "mode": "live", "prompt": "公开合成核验"})
    agent.update(instructions="已修改的新指令", version=2, enabled=False)
    store.save("agents", agent)
    observed = []

    async def model_mock(*args, **kwargs):
        observed.append(kwargs["system"])
        return {"text": "核验输出"}

    monkeypatch.setattr(engine.model_gateway, "complete", model_mock)
    await engine._drive(run["id"])
    saved = store.get("runs", run["id"])
    assert saved["status"] == "completed", saved.get("error")
    assert len(observed) == 1 and "原始版本指令" in observed[0]
    assert "已修改的新指令" not in observed[0]
    assert saved["agent_snapshots"][agent["id"]]["version"] == 1
    assert saved["agent_snapshots_source"] == "run_creation"
    with pytest.raises(ValueError, match="禁用|停用"):
        engine.create_run({"workflow_id": flow["id"], "mode": "rehearsal"})


@pytest.mark.asyncio
async def test_context_reused_within_run_and_isolated_across_workflows(isolated_engine):
    store, _ = isolated_engine
    definition(store)
    first_flow = workflow(store, "context-flow-one")
    second_flow = workflow(store, "context-flow-two")
    first = engine.create_run({"workflow_id": first_flow["id"], "mode": "rehearsal"})
    second = engine.create_run({"workflow_id": second_flow["id"], "mode": "rehearsal"})
    first, second = await asyncio.gather(settle(first["id"]), settle(second["id"]))
    assert first["status"] == second["status"] == "completed"
    contexts = store.list("agent_instances")
    assert len(contexts) == 2
    assert {item["run_id"] for item in contexts} == {first["id"], second["id"]}
    assert all(item["use_count"] == 2 and item["status"] == "released" for item in contexts)
    assert all(item["release_reason"] == "completed" and item["mode"] == "rehearsal" for item in contexts)
    assert len({step["agent_instance_id"] for step in first["steps"]}) == 1
    assert first["steps"][0]["agent_instance_id"] != second["steps"][0]["agent_instance_id"]
    assert len([e for e in store.list("agent_events") if e["action"] == "context_reused"]) == 2


@pytest.mark.asyncio
async def test_failure_retry_uses_original_version_in_new_context(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    agent = definition(store)
    flow = workflow(store, analysis=True)
    monkeypatch.setattr(engine, "launch", lambda run_id: None)
    run = engine.create_run({"workflow_id": flow["id"], "mode": "live"})

    async def fail(*args, **kwargs):
        raise ValueError("受控测试失败")

    monkeypatch.setattr(engine.model_gateway, "complete", fail)
    await engine._drive(run["id"])
    assert store.get("runs", run["id"])["status"] == "failed"
    initial = store.list("agent_instances")[0]
    assert initial["status"] == "released" and initial["release_reason"] == "failed"
    agent.update(version=2, instructions="新版本指令", enabled=False)
    store.save("agents", agent)
    with pytest.raises(ValueError, match="已禁用"):
        engine.retry_run(run["id"], {"username": "operator"})
    agent["enabled"] = True
    store.save("agents", agent)

    async def succeed(*args, **kwargs):
        assert "原始版本指令" in kwargs["system"] and "新版本指令" not in kwargs["system"]
        return {"text": "恢复成功"}

    monkeypatch.setattr(engine.model_gateway, "complete", succeed)
    retried = engine.retry_run(run["id"], {"username": "operator"})
    assert not retried["steps"][1].get("agent_instance_id")
    await engine._drive(run["id"])
    assert store.get("runs", run["id"])["status"] == "completed"
    contexts = store.list("agent_instances")
    assert len(contexts) == 2 and all(item["agent_version"] == 1 for item in contexts)
    assert store.get("agent_instances", initial["id"])["release_reason"] == "failed"


@pytest.mark.asyncio
async def test_cancel_keeps_awaited_context_busy_until_request_finishes(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    definition(store)
    flow = workflow(store)
    entered, complete = asyncio.Event(), asyncio.Event()

    async def pending_node(*args):
        entered.set()
        await complete.wait()
        return {"notice": "受控等待，无模型请求"}

    monkeypatch.setattr(engine, "_execute_node_body", pending_node)
    run = engine.create_run({"workflow_id": flow["id"], "mode": "rehearsal"})
    try:
        await asyncio.wait_for(entered.wait(), 5)
        instance = store.list("agent_instances")[0]
        engine.cancel_run(run["id"], {"username": "operator"})
        assert store.get("agent_instances", instance["id"])["status"] == "running"
        with pytest.raises(ValueError, match="仍在结束"):
            engine.retry_run(run["id"], {"username": "operator"})
    finally:
        complete.set()
        await settle(run["id"])
    instance = store.get("agent_instances", instance["id"])
    assert instance["status"] == "released" and instance["release_reason"] == "cancelled"
    assert instance["last_outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_coroutine_interruption_releases_context_after_unwinding(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    definition(store)
    flow = workflow(store)
    entered = asyncio.Event()

    async def interrupted_node(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_execute_node_body", interrupted_node)
    run = engine.create_run({"workflow_id": flow["id"], "mode": "rehearsal"})
    await asyncio.wait_for(entered.wait(), 5)
    task = engine.TASKS[run["id"]]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert store.get("runs", run["id"])["status"] == "interrupted"
    instance = store.list("agent_instances")[0]
    assert instance["status"] == "released" and instance["release_reason"] == "interrupted"
    assert instance["last_outcome"] == "interrupted"


def test_restart_releases_dangling_context_without_replaying_node(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    definition(store)
    flow = workflow(store)
    monkeypatch.setattr(engine, "launch", lambda run_id: None)
    run = engine.create_run({"workflow_id": flow["id"], "mode": "rehearsal"})
    instance = agent_instances.start_context(store, run["id"], "start", "context-agent")
    engine.recover_interrupted()
    assert store.get("runs", run["id"])["status"] == "interrupted"
    assert store.get("agent_instances", instance["id"])["release_reason"] == "process_restarted"
    event_count = len(store.list("agent_events"))
    engine.recover_interrupted()
    assert len(store.list("agent_events")) == event_count


def test_legacy_snapshot_capture_does_not_invent_historical_versions(isolated_engine, monkeypatch):
    store, _ = isolated_engine
    definition(store)
    flow = workflow(store)
    legacy = store.save(
        "runs",
        {
            "id": "legacy-context-run",
            "workflow_snapshot": flow,
            "mode": "rehearsal",
            "status": "interrupted",
            "steps": [{"node_id": "start", "status": "completed"}],
        },
    )
    run = agent_instances.ensure_snapshots(store, legacy["id"])
    assert run["agent_snapshots_source"] == "legacy_resume"
    assert run["agent_snapshots_captured_at"]
    assert "agent_version" not in run["steps"][0]
    event = store.list("agent_events")[0]
    assert event["action"] == "legacy_snapshot_captured" and "未知" in event["detail"]["notice"]
    agent_instances.ensure_snapshots(store, legacy["id"])
    assert len(store.list("agent_events")) == 1


@pytest.mark.asyncio
async def test_waiting_review_releases_context_and_resume_allocates_fresh(isolated_engine):
    store, _ = isolated_engine
    run = engine.create_run({"workflow_id": "workflow-tech-trends", "mode": "rehearsal"})
    paused = await settle(run["id"])
    assert paused["status"] == "waiting_review"
    retrieval = next(step for step in paused["steps"] if step["kind"] == "retrieve")
    capability_call = next(
        call for call in paused["capability_calls"] if call["node_id"] == retrieval["node_id"]
    )
    assert capability_call["agent_id"] == retrieval["agent_id"]
    assert capability_call["agent_instance_id"] == retrieval["agent_instance_id"]
    assert capability_call["agent_version"] == retrieval["agent_version"]
    from backend.app.capabilities.facade import capability_runtime

    audit_events = [event for event in capability_runtime().audit.events() if event["run_id"] == run["id"]]
    assert audit_events and all(
        event["agent_instance_id"] == retrieval["agent_instance_id"] for event in audit_events
    )
    contexts = store.list("agent_instances")
    assert contexts and all(item["release_reason"] == "waiting_review" for item in contexts)
    prior = {item["id"] for item in contexts}
    engine.review_run(run["id"], {"decision": "approve"}, {"username": "reviewer"})
    completed = await settle(run["id"])
    assert completed["status"] == "completed"
    end = next(step for step in completed["steps"] if step["kind"] == "end")
    assert end["agent_instance_id"] not in prior
    assert store.get("agent_instances", end["agent_instance_id"])["release_reason"] == "completed"
