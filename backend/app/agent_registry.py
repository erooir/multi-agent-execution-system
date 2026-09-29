"""Agent definitions, durable lifecycle history and reference checks.

Definitions may be shared by workflows; execution contexts remain run-scoped.
The caller supplies its Store so API, scheduler and isolated tests share one lock.
"""

from __future__ import annotations

from copy import deepcopy

from .storage import Store, now

BUILT_IN_IDS = frozenset(
    {"agent-planner", "agent-parser", "agent-retriever", "agent-writer", "agent-coordinator"}
)
PENDING_RUN_STATES = frozenset({"queued", "running", "waiting_review", "interrupted"})


def lifecycle_status(agent: dict) -> str:
    # enabled remains authoritative for legacy records and integrations.
    if agent.get("lifecycle_status") == "destroyed":
        return "destroyed"
    return "active" if agent.get("enabled", True) else "disabled"


def node_agent_id(data: dict) -> str | None:
    if data.get("agent_id"):
        return data["agent_id"]
    if data.get("kind") == "parse" and not data.get("skill_id"):
        return "agent-parser"
    if data.get("kind") == "report":
        return "agent-writer"
    return None


def workflow_agent_ids(workflow: dict) -> set[str]:
    return {
        agent_id
        for node in workflow.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("data"), dict)
        if isinstance(agent_id := node_agent_id(node["data"]), str) and agent_id
    }


def run_agent_ids(database: Store, run: dict) -> set[str]:
    snapshots = run.get("agent_snapshots") or {}
    result = set(snapshots) if isinstance(snapshots, dict) else set()
    workflow = run.get("workflow_snapshot")
    if not isinstance(workflow, dict):
        workflow = database.get("workflows", run.get("workflow_id", "")) or {}
    return result | workflow_agent_ids(workflow)


def record_event(
    database: Store,
    agent_id: str,
    action: str,
    detail: dict | None = None,
    *,
    user: str = "system",
    instance_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    event = {"agent_id": agent_id, "action": action, "detail": detail or {}, "user": user}
    if instance_id:
        event["instance_id"] = instance_id
    if run_id:
        event["run_id"] = run_id
    return database.save("agent_events", event)


def workflow_references(database: Store, agent_id: str) -> list[dict]:
    result = []
    for workflow in database.list("workflows"):
        nodes = [
            {"id": node["id"], "label": node["data"].get("label", node["id"])}
            for node in workflow.get("nodes", [])
            if isinstance(node, dict)
            and isinstance(node.get("data"), dict)
            and node_agent_id(node["data"]) == agent_id
        ]
        if nodes:
            result.append(
                {
                    "id": workflow["id"],
                    "name": workflow.get("name", workflow["id"]),
                    "status": workflow.get("status", "draft"),
                    "nodes": nodes,
                }
            )
    return result


def enrich_agents(database: Store, agents: list[dict] | None = None) -> list[dict]:
    agents = database.list("agents") if agents is None else agents
    workflow_counts: dict[str, int] = {}
    instance_counts: dict[str, int] = {}
    run_ids: dict[str, set[str]] = {}
    for workflow in database.list("workflows"):
        for agent_id in workflow_agent_ids(workflow):
            workflow_counts[agent_id] = workflow_counts.get(agent_id, 0) + 1
    for run in database.list("runs"):
        for agent_id in run_agent_ids(database, run):
            run_ids.setdefault(agent_id, set()).add(run["id"])
    for instance in database.list("agent_instances"):
        agent_id = instance.get("agent_id")
        if not agent_id:
            continue
        if instance.get("status") != "released":
            instance_counts[agent_id] = instance_counts.get(agent_id, 0) + 1
        if instance.get("run_id"):
            run_ids.setdefault(agent_id, set()).add(instance["run_id"])
    return [
        {
            **agent,
            "lifecycle_status": lifecycle_status(agent),
            "enabled": lifecycle_status(agent) == "active",
            "origin": "built_in" if agent["id"] in BUILT_IN_IDS else "custom",
            "usage": {
                "workflow_count": workflow_counts.get(agent["id"], 0),
                "active_instance_count": instance_counts.get(agent["id"], 0),
                "run_count": len(run_ids.get(agent["id"], set())),
            },
        }
        for agent in agents
    ]


def lifecycle_detail(database: Store, agent_id: str) -> dict:
    with database.lock:
        agent = database.get("agents", agent_id)
        if agent is None:
            raise KeyError(agent_id)
        return {
            "agent": enrich_agents(database, [agent])[0],
            "workflows": workflow_references(database, agent_id),
            "instances": [
                item for item in database.list("agent_instances") if item.get("agent_id") == agent_id
            ][:100],
            "events": [item for item in database.list("agent_events") if item.get("agent_id") == agent_id][
                :100
            ],
        }


def destroy_agent(database: Store, agent_id: str, user: str) -> dict:
    """Keep an irreversible tombstone; terminal runs and their snapshots stay intact."""
    with database.lock:
        agent = database.get("agents", agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if lifecycle_status(agent) == "destroyed":
            raise ValueError("此智能体已销毁，不能再次操作")
        if agent_id in BUILT_IN_IDS:
            raise ValueError("平台内置智能体用于默认任务处理，不能销毁；可停用或复制后调整")
        references = workflow_references(database, agent_id)
        if references:
            names = "、".join(item["name"] for item in references[:3])
            raise ValueError(
                f"有 {len(references)} 个流程仍引用此智能体（{names}），请先在流程中解除或替换绑定"
            )
        if any(
            run.get("status") in PENDING_RUN_STATES and agent_id in run_agent_ids(database, run)
            for run in database.list("runs")
        ):
            raise ValueError("此智能体仍关联未结束的任务，请先完成或取消任务，再销毁智能体")
        if any(
            instance.get("agent_id") == agent_id and instance.get("status") != "released"
            for instance in database.list("agent_instances")
        ):
            raise ValueError("此智能体仍有未释放的运行实例，请等待执行结束并释放实例后再销毁")
        result = deepcopy(agent)
        result.update(
            lifecycle_status="destroyed",
            enabled=False,
            destroyed_at=now(),
            version=int(agent.get("version", 1)) + 1,
        )
        result = database.save("agents", result)
        record_event(database, agent_id, "destroy", {"version": result["version"]}, user=user)
        return result
