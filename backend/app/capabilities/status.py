"""面向 API 的 Skill 摘要：保持原 knowledge.skills() 的输出字段不变。

字段：id/name/description/version/execution/status/note/input_schema/
output_schema/enabled/execution_mode/node_kinds/allowed_tools/evidence_required。
execution 依据 Skill 声明的 Tool 的 network 字段推导（需要网络即 external）；
依赖 MCP 工具的 Skill 在 Server 最近已知不可用时标 degraded。
"""

from __future__ import annotations

import importlib.util

from .registry import SkillRegistry


def skill_summaries(skills: SkillRegistry, knowledge, tools=None, server_health=None) -> list[dict]:
    server_health = server_health or {}
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
        allowed = [tools.get(tool_id) for tool_id in manifest.allowed_tools] if tools else []
        allowed = [tool for tool in allowed if tool is not None]
        for tool in allowed:
            if tool.provider != "mcp":
                continue
            server_id = tool.entrypoint.removeprefix("mcp://").split("/")[0]
            health = server_health.get(server_id)
            if health is not None and health != "ready":
                status = "degraded"
                note = f"依赖的 MCP 服务 {server_id} 当前不可用（{health}），其余本地能力不受影响"
        execution = (
            "external" if any(tool.network == "required" for tool in allowed) else "local"
        )
        results.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "description": manifest.description,
                "version": manifest.version,
                "execution": execution,
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
