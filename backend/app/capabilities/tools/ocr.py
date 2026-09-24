"""OCR Tool：RapidOCR ONNX CPU 识别，复用 Knowledge._ocr_document 的完整行为。"""

from __future__ import annotations

from ... import knowledge as _knowledge_module
from .documents import _checked_documents


def rapidocr(
    document_id: str | None = None,
    project_id: str | None = None,
    document_ids: list[str] | None = None,
) -> dict:
    document_id = document_id or (document_ids or [None])[0]
    if not document_id:
        raise ValueError("请选择资料后调用该技能")
    _checked_documents([document_id], project_id)
    result = _knowledge_module.knowledge._ocr_document(document_id)
    return {
        "text": result["text"],
        "evidence": result["evidence"],
        "region_count": result["region_count"],
        "elapsed": result["elapsed"],
    }
