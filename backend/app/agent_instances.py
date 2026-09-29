"""Versioned per-run execution contexts, not a pool of persistent model sessions."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from . import agent_registry
from .storage import Store


def now() -> str:
    return datetime.now(UTC).isoformat()


def require_active(store: Store, agent_id: str) -> dict:
    agent = store.get("agents", agent_id)
    if not agent:
        raise ValueError(f"智能体不存在：{agent_id}；请在流程编辑器重新绑定可用智能体")
    status = agent_registry.lifecycle_status(agent)
    if status != "active":
        description = "已销毁" if status == "destroyed" else "已禁用"
        raise ValueError(
            f"智能体{description}：{agent.get('name', agent_id)}；"
            "请启用智能体，或复制流程并更换绑定后新建任务"
        )
    return agent


def capture_snapshots(store: Store, workflow: dict) -> dict[str, dict]:
    """Caller holds the same Store lock used by definition mutations."""
    with store.lock:
        return {
            agent_id: deepcopy(require_active(store, agent_id))
            for agent_id in agent_registry.workflow_agent_ids(workflow)
        }


def ensure_snapshots(store: Store, run_id: str) -> dict:
    """Legacy capture is explicitly dated; it never claims to reconstruct past config."""
    with store.lock:
        run = store.get("runs", run_id)
        if run is None:
            raise ValueError("任务不存在")
        if "agent_snapshots" not in run:
            run["agent_snapshots"] = capture_snapshots(store, run["workflow_snapshot"])
            run["agent_snapshots_source"] = "legacy_resume"
            run["agent_snapshots_captured_at"] = now()
            run = store.save("runs", run)
            for agent_id, agent in run["agent_snapshots"].items():
                agent_registry.record_event(
                    store,
                    agent_id,
                    "legacy_snapshot_captured",
                    {
                        "version": agent.get("version", 1),
                        "notice": "旧任务首次恢复时留存当前配置；此前节点的智能体配置版本未知",
                    },
                    run_id=run_id,
                )
        return run


def resolve_agent(store: Store, run: dict, agent_id: str) -> dict:
    if "agent_snapshots" not in run:
        run = ensure_snapshots(store, run["id"])
    agent = run["agent_snapshots"].get(agent_id)
    if not agent:
        raise ValueError(f"任务快照缺少智能体 {agent_id}；请复制流程并创建新任务")
    return deepcopy(agent)


def start_context(store: Store, run_id: str, node_id: str, agent_id: str) -> dict:
    with store.lock:
        run = ensure_snapshots(store, run_id)
        if run.get("status") == "cancelled":
            raise ValueError("用户已取消任务")
        agent = resolve_agent(store, run, agent_id)
        instance = next(
            (
                item
                for item in store.list("agent_instances")
                if item.get("run_id") == run_id
                and item.get("agent_id") == agent_id
                and item.get("status") != "released"
            ),
            None,
        )
        if instance and instance["status"] == "running":
            raise ValueError("智能体执行上下文仍在处理上一节点，请等待请求结束")
        if instance is None:
            instance = store.save(
                "agent_instances",
                {
                    "id": str(uuid4()),
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", agent_id),
                    "agent_version": agent.get("version", 1),
                    "run_id": run_id,
                    "workflow_id": run.get("workflow_id"),
                    "workflow_name": run.get("workflow_name"),
                    "status": "idle",
                    "mode": run["mode"],
                    "kind": "execution_context",
                    "node_ids": [],
                    "use_count": 0,
                },
            )
            agent_registry.record_event(
                store,
                agent_id,
                "context_created",
                {"version": instance["agent_version"], "mode": run["mode"]},
                instance_id=instance["id"],
                run_id=run_id,
            )
        reused = instance["use_count"] > 0
        instance.update(status="running", active_node_id=node_id)
        instance["use_count"] += 1
        if node_id not in instance["node_ids"]:
            instance["node_ids"].append(node_id)
        instance = store.save("agent_instances", instance)
        for step in run.get("steps", []):
            if step["node_id"] == node_id:
                step.update(
                    agent_id=agent_id,
                    agent_instance_id=instance["id"],
                    agent_version=instance["agent_version"],
                )
        store.save("runs", run)
        agent_registry.record_event(
            store,
            agent_id,
            "context_reused" if reused else "context_started",
            {"node_id": node_id, "use_count": instance["use_count"]},
            instance_id=instance["id"],
            run_id=run_id,
        )
        return instance


def finish_context(store: Store, instance_id: str, outcome: str) -> None:
    with store.lock:
        instance = store.get("agent_instances", instance_id)
        if not instance or instance.get("status") == "released":
            return
        node_id = instance.pop("active_node_id", None)
        instance.update(status="idle", last_outcome=outcome)
        store.save("agent_instances", instance)
        agent_registry.record_event(
            store,
            instance["agent_id"],
            "context_finished",
            {"node_id": node_id, "outcome": outcome},
            instance_id=instance_id,
            run_id=instance["run_id"],
        )


def release_contexts(store: Store, run_id: str, reason: str, *, process_restarted: bool = False) -> None:
    """A user cancel cannot release a context whose external request is still awaited."""
    with store.lock:
        for instance in store.list("agent_instances"):
            if instance.get("run_id") != run_id or instance.get("status") == "released":
                continue
            if instance.get("status") == "running" and not process_restarted:
                continue
            instance.update(status="released", released_at=now(), release_reason=reason)
            instance.pop("active_node_id", None)
            store.save("agent_instances", instance)
            agent_registry.record_event(
                store,
                instance["agent_id"],
                "context_released",
                {"reason": reason},
                instance_id=instance["id"],
                run_id=run_id,
            )


def recover_contexts(store: Store) -> None:
    with store.lock:
        run_ids = {
            item["run_id"] for item in store.list("agent_instances") if item.get("status") != "released"
        }
        for run_id in run_ids:
            release_contexts(store, run_id, "process_restarted", process_restarted=True)
