"""面向 API 的 Skill 摘要：保持原 knowledge.skills() 的输出字段不变。

字段：id/name/description/version/execution/status/note/input_schema/
output_schema/enabled。状态逻辑与原实现一致（依赖探测 + 模型配置）。
"""

from __future__ import annotations

import importlib.util

from .registry import SkillRegistry

_EXECUTION_LABEL = {"knowledge_search": "local", "graph_query": "local", "document_parse": "local",
                    "ocr": "local", "semantic_search": "local", "multimodal": "external"}


def skill_summaries(skills: SkillRegistry, knowledge) -> list[dict]:
    results = []
    for manifest in skills.list():
        status, note = "ready", "可调用"
        if manifest.id == "semantic_search":
            dependency = importlib.util.find_spec("fastembed") is not None
            embedding = knowledge.embedding_status()
            status = embedding["status"] if dependency else "unavailable"
            note = embedding["error"] or "中文 BGE 在 CPU 本地执行，首次使用准备模型"
        elif manifest.id == "ocr":
            dependency = importlib.util.find_spec("rapidocr_onnxruntime") is not None
            status = "ready" if dependency else "unavailable"
            note = "仅处理已上传图片，CPU 本地识别" if dependency else "RapidOCR 依赖未安装"
        elif manifest.id == "multimodal":
            from ..config import get_settings

            configured = get_settings().get("key_configured", False)
            status = "ready" if configured else "requires_model"
            note = (
                "仅支持获准外发图片；外部模型按量计费，所有调用经过预算网关"
                if configured
                else "需配置支持图像输入的预算网关模型"
            )
        results.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "description": manifest.description,
                "version": manifest.version,
                "execution": _EXECUTION_LABEL.get(manifest.id, "local"),
                "execution_mode": manifest.execution_mode,
                "node_kinds": manifest.node_kinds,
                "allowed_tools": manifest.allowed_tools,
                "evidence_required": manifest.evidence_required,
                "status": status,
                "note": note,
                "input_schema": manifest.input_schema,
                "output_schema": manifest.output_schema,
                "enabled": status != "unavailable",
            }
        )
    return results
