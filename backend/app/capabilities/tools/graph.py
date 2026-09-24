"""图谱查询 Tool：返回 nodes/edges，并把命中的边所引用的分块补为证据。

边引用的分块补证据是原 engine 中 graph_query 的既有行为，迁移到 Tool 层，
使 engine 不再需要按技能 ID 写特殊分支。
"""

from __future__ import annotations

from ... import knowledge as _knowledge_module
from ..services import graph as graph_service


def query(query: str = "", project_id: str | None = None) -> dict:
    store = _knowledge_module.store
    graph = graph_service.filter_graph(graph_service.load_graph(store, project_id), query or "")
    evidence: dict[str, dict] = {}
    for edge in graph["edges"]:
        chunk = store.get("chunks", edge.get("chunk_id", ""))
        if chunk:
            evidence.setdefault(chunk["id"], {**chunk, "score": 1.0})
    return {**graph, "evidence": list(evidence.values())}
