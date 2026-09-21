"""检索类 Tool：关键词与语义检索，复用 Knowledge 的存储检索实现。

运行时解析 backend.app.knowledge 单例，保证测试对 knowledge 的隔离生效。
"""

from __future__ import annotations

from ... import knowledge as _knowledge_module


def keyword_search(
    query: str = "",
    project_id: str | None = None,
    document_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    evidence = _knowledge_module.knowledge.search(
        query or "", project_id, document_ids, 6 if limit is None else limit, semantic=False
    )
    return {"evidence": evidence, "count": len(evidence), "method": "keyword"}


def semantic_search(
    query: str = "",
    project_id: str | None = None,
    document_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    evidence = _knowledge_module.knowledge.search(
        query or "", project_id, document_ids, 6 if limit is None else limit, semantic=True
    )
    return {"evidence": evidence, "count": len(evidence), "method": "bge_vector"}


def embedding_prepare() -> dict:
    """准备本地中文 embedding 模型并返回状态；失败时如实抛出，不静默降级。"""
    knowledge = _knowledge_module.knowledge
    knowledge._get_embedding()
    return {"embedding": knowledge.embedding_status(), "prepare_only": True}
