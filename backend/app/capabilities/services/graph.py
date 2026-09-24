"""图谱数据访问服务：从 SQLite store 读取实体/关系并按查询词过滤。"""

from __future__ import annotations

from .documents import tokenize


def load_graph(store, project_id: str | None = None) -> dict:
    nodes = [
        node for node in store.list("graph_nodes") if not project_id or node.get("project_id") == project_id
    ]
    ids = {node["id"] for node in nodes}
    edges = [
        edge for edge in store.list("graph_edges") if edge.get("source") in ids and edge.get("target") in ids
    ]
    return {"nodes": nodes, "edges": edges}


def filter_graph(graph: dict, query: str) -> dict:
    """按查询词选中节点及其邻边（与既有 graph_query 技能语义一致）。"""
    query = str(query or "").strip()
    if not query:
        return graph
    terms = tokenize(query)
    selected = {
        node["id"]
        for node in graph["nodes"]
        if terms & tokenize(node.get("label", "") + " " + node.get("description", ""))
    }
    edges = [edge for edge in graph["edges"] if edge["source"] in selected or edge["target"] in selected]
    connected = selected | {edge[side] for edge in edges for side in ("source", "target")}
    return {
        "nodes": [node for node in graph["nodes"] if node["id"] in connected],
        "edges": edges,
    }
