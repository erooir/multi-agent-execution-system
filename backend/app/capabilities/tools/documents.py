"""文档类 Tool：读取已入库分块、按需重新解析原文。

只处理调用方显式给出的 document_ids，绝不自动挑选项目文档。
"""

from __future__ import annotations

from pathlib import Path

from ... import knowledge as _knowledge_module
from ..services.documents import build_evidence, extract_parts


def _checked_documents(document_ids: list[str] | None, project_id: str | None) -> list[dict]:
    if not document_ids:
        raise ValueError("请选择资料后调用该技能")
    store = _knowledge_module.store
    documents = []
    for document_id in document_ids:
        document = store.get("documents", document_id)
        if not document or (project_id and document.get("project_id") != project_id):
            raise ValueError("资料不存在或不属于当前项目")
        documents.append(document)
    return documents


def read_chunks(
    document_ids: list[str] | None = None,
    project_id: str | None = None,
) -> dict:
    knowledge = _knowledge_module.knowledge
    documents = _checked_documents(document_ids, project_id)
    evidence = [
        build_evidence(chunk, 1.0) for document in documents for chunk in knowledge.chunks(document["id"])
    ]
    return {"evidence": evidence, "count": len(evidence)}


def document_parse(document_id: str, project_id: str | None = None) -> dict:
    """直接解析原始文件为 (location, text) 片段，不写入存储。"""
    knowledge = _knowledge_module.knowledge
    documents = _checked_documents([document_id], project_id)
    document = documents[0]
    path = knowledge.document_path(document_id)
    parts = extract_parts(Path(document["name"]).suffix.lower(), path.read_bytes())
    return {
        "document_id": document_id,
        "parts": [{"location": location, "text": text} for location, text in parts],
        "count": len(parts),
    }
