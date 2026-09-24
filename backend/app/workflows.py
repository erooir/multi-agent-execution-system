"""Validated, versioned declarative workflows. Configuration is never executed as code."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .storage import store

KINDS = {"start", "parse", "retrieve", "condition", "batch", "analyze", "review", "report", "end"}


def validate_workflow(workflow: dict) -> dict:
    errors, warnings = [], []
    nodes, edges = workflow.get("nodes", []), workflow.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"valid": False, "errors": ["nodes 和 edges 必须为数组"], "warnings": []}
    if not 2 <= len(nodes) <= 64:
        errors.append("工作流必须包含 2 至 64 个节点")
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if (
        len(ids) != len(nodes)
        or any(not isinstance(i, str) or not i for i in ids)
        or len(set(ids)) != len(ids)
    ):
        return {"valid": False, "errors": errors + ["节点 ID 必须是非空且唯一的字符串"], "warnings": []}
    if any(not isinstance(n.get("data"), dict) for n in nodes):
        return {"valid": False, "errors": errors + ["节点 data 必须为对象"], "warnings": []}
    by_id = {n["id"]: n for n in nodes}
    incoming, outgoing = {i: [] for i in ids}, {i: [] for i in ids}
    for node in nodes:
        data = node.get("data")
        if not isinstance(data, dict) or data.get("kind") not in KINDS:
            errors.append(f"节点 {node['id']} 类型不受支持")
            continue
        config = data.get("config", {})
        if not isinstance(config, dict):
            errors.append(f"节点 {node['id']} 配置必须为对象")
            continue
        skill_id = data.get("skill_id")
        execution_strategy = data.get("execution_strategy")
        if execution_strategy not in {None, "direct_skill", "agent"}:
            errors.append(f"节点 {node['id']} 的 execution_strategy 无效")
        if execution_strategy == "agent" and data["kind"] != "retrieve":
            errors.append(f"节点 {node['id']} 当前仅检索节点支持业务智能体执行")
        if execution_strategy == "agent" and not data.get("agent_id"):
            errors.append(f"节点 {node['id']} 使用智能体执行但未绑定智能体")
        if skill_id:
            invalid = data["kind"] not in {"parse", "retrieve"}
            if not invalid:
                from .capabilities.facade import capability_runtime

                registry = capability_runtime().skills
                if skill_id not in registry:
                    invalid = True
                else:
                    manifest = registry.get(skill_id)
                    if manifest.node_kinds and data["kind"] not in manifest.node_kinds:
                        invalid = True
            if invalid:
                errors.append(f"节点 {node['id']} 的技能绑定无效，技能仅可绑定解析/检索节点")
        for key in ("min_evidence", "limit", "count", "batch_size"):
            if key in config and (
                not isinstance(config[key], int)
                or isinstance(config[key], bool)
                or not 0 <= config[key] <= 30
            ):
                errors.append(f"节点 {node['id']} 的 {key} 必须为 0 至 30 的整数")
        if "items" in config and (
            not isinstance(config["items"], list)
            or not config["items"]
            or len(config["items"]) > 10
            or any(not isinstance(v, str) for v in config["items"])
        ):
            errors.append(f"节点 {node['id']} 的 items 必须为 1 至 10 个字符串")
        if data.get("agent_id"):
            agent = store.get("agents", data["agent_id"])
            if not agent:
                errors.append(f"节点 {node['id']} 引用了不存在的智能体")
            elif skill_id and skill_id not in agent.get("skill_ids", []):
                errors.append(
                    f"节点 {node['id']} 的技能 {skill_id} 未授权给智能体 {agent.get('name', agent['id'])}"
                )
    seen_edges = set()
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or not isinstance(edge.get("source"), str)
            or not isinstance(edge.get("target"), str)
            or edge["source"] not in by_id
            or edge["target"] not in by_id
        ):
            errors.append("连线引用了不存在的节点")
            continue
        key = (edge["source"], edge["target"], edge.get("sourceHandle", ""))
        if key in seen_edges:
            errors.append("工作流包含重复连线")
        seen_edges.add(key)
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    starts = [n["id"] for n in nodes if n.get("data", {}).get("kind") == "start"]
    ends = [n["id"] for n in nodes if n.get("data", {}).get("kind") == "end"]
    if len(starts) != 1 or len(ends) != 1:
        errors.append("必须恰好有一个开始节点和一个结束节点")
    for i in starts:
        if incoming[i]:
            errors.append("开始节点不能有入边")
    for i in ends:
        if outgoing[i]:
            errors.append("结束节点不能有出边")
    for node in nodes:
        if node.get("data", {}).get("kind") == "condition":
            handles = [e.get("sourceHandle") for e in outgoing[node["id"]]]
            if sorted(h for h in handles if isinstance(h, str)) != ["fail", "pass"] or len(handles) != 2:
                errors.append(f"条件节点 {node['id']} 必须各有一条 pass 和 fail 分支")
    degree = {i: len(incoming[i]) for i in ids}
    queue = [i for i in ids if degree[i] == 0]
    ordered = []
    while queue:
        i = queue.pop(0)
        ordered.append(i)
        for e in outgoing[i]:
            degree[e["target"]] -= 1
            if degree[e["target"]] == 0:
                queue.append(e["target"])
    if len(ordered) != len(ids):
        errors.append("首版只支持无环流程，请移除循环连线")
    if len(starts) == 1 and len(ends) == 1:

        def reachable(first, mapping, field):
            found, pending = set(), [first]
            while pending:
                current = pending.pop()
                if current in found:
                    continue
                found.add(current)
                pending.extend(e[field] for e in mapping[current])
            return found

        if len(reachable(starts[0], outgoing, "target")) != len(nodes):
            errors.append("所有节点必须可从开始节点到达")
        if len(reachable(ends[0], incoming, "source")) != len(nodes):
            errors.append("所有节点必须能够到达结束节点")
    if not any(n.get("data", {}).get("kind") == "review" for n in nodes):
        warnings.append("此流程未设置人工审核节点")
    if not any(n.get("data", {}).get("kind") == "retrieve" for n in nodes):
        warnings.append("此流程没有知识检索节点，输出可能没有来源依据")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "order": ordered}


def snapshot(workflow: dict) -> dict:
    version = int(workflow.get("version", 1))
    identity = f"{workflow['id']}:{version}"
    existing = store.get("workflow_versions", identity)
    if existing:
        return existing
    return store.save(
        "workflow_versions",
        {"id": identity, "workflow_id": workflow["id"], "version": version, "snapshot": deepcopy(workflow)},
    )


def versions(workflow_id: str) -> list:
    return sorted(
        [v for v in store.list("workflow_versions") if v["workflow_id"] == workflow_id],
        key=lambda v: v["version"],
        reverse=True,
    )


def save_workflow(data: dict, workflow_id: str | None = None) -> dict:
    previous = store.get("workflows", workflow_id) if workflow_id else None
    if workflow_id and previous is None:
        raise KeyError(workflow_id)
    if previous:
        snapshot(previous)
    result = deepcopy(previous or {})
    for key in (
        "name",
        "description",
        "category",
        "project_id",
        "nodes",
        "edges",
        "source_prompt",
        "preferred_mode",
    ):
        if key in data:
            result[key] = deepcopy(data[key])
    result.update(
        id=workflow_id or str(uuid4()), version=int((previous or {}).get("version", 0)) + 1, status="draft"
    )
    result.setdefault("name", "未命名流程")
    result.setdefault("category", "technology")
    result.setdefault("nodes", [])
    result.setdefault("edges", [])
    return store.save("workflows", result)


def publish(workflow_id: str) -> dict:
    workflow = store.get("workflows", workflow_id)
    if not workflow:
        raise KeyError(workflow_id)
    result = validate_workflow(workflow)
    if not result["valid"]:
        raise ValueError("；".join(result["errors"]))
    workflow["status"] = "published"
    workflow = store.save("workflows", workflow)
    snapshot(workflow)
    return workflow


def restore(workflow_id: str, version: int) -> dict:
    source = store.get("workflow_versions", f"{workflow_id}:{version}")
    if not source:
        current = store.get("workflows", workflow_id)
        if current and current.get("version") == version:
            source = snapshot(current)
    if not source:
        raise ValueError("未找到此流程版本")
    return save_workflow(source["snapshot"], workflow_id)


def simple_plan(prompt: str, project_id: str | None = None) -> dict:
    kinds = ["start", "parse", "retrieve", "analyze", "report", "review", "end"]
    labels = ["接收任务", "任务解析", "知识检索", "综合分析", "报告生成", "人工审核", "完成"]
    nodes = [
        {
            "id": f"n{i}",
            "type": "task",
            "position": {"x": i * 210, "y": 120},
            "data": {"label": label, "kind": kind, "config": {}},
        }
        for i, (kind, label) in enumerate(zip(kinds, labels))
    ]
    edges = [{"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"} for i in range(len(nodes) - 1)]
    return {
        "name": prompt[:30] or "研究流程",
        "description": prompt,
        "project_id": project_id,
        "category": "technology",
        "nodes": nodes,
        "edges": edges,
    }
