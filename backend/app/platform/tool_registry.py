"""Versioned deployment catalog. Runtime never downloads or discovers capabilities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agno.skills import LocalSkills

from .contracts import (
    Contract,
    EvidenceLocate,
    SourceRead,
    SourceSearch,
    TableAggregate,
    TableOperation,
    ToolResult,
)

ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills"
DEFINITIONS = [
    ("data.list_sources", "list_sources", "授权资料目录", "tool", Contract),
    ("data.read_source", "read_source", "阅读原文片段", "tool", SourceRead),
    ("data.search_sources", "search_sources", "检索授权资料", "tool", SourceSearch),
    ("data.locate_evidence", "locate_evidence", "定位原文证据", "tool", EvidenceLocate),
    ("mcp.tables.transform", "table_transform", "MCP 表格筛选/排序/去重", "mcp", TableOperation),
    ("mcp.tables.aggregate", "table_aggregate", "MCP 分组与统计", "mcp", TableAggregate),
]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def catalog():
    runtime_path = Path(__file__).with_name("tool_runtime.py")
    tools = [
        {
            "id": tool_id,
            "name": name,
            "label": label,
            "kind": kind,
            "version": 4,
            "input_schema": model.model_json_schema(),
            "output_schema": ToolResult.model_json_schema(),
            "read_only": True,
            "checksum": digest(
                {
                    "id": tool_id,
                    "runtime": runtime_path.read_text(encoding="utf-8"),
                    "schema": model.model_json_schema(),
                }
            ),
        }
        for tool_id, name, label, kind, model in DEFINITIONS
    ]
    skills = []
    for skill in sorted(LocalSkills(str(SKILL_ROOT)).load(), key=lambda item: item.name):
        path = Path(skill.source_path).resolve()
        if not path.is_relative_to(SKILL_ROOT.resolve()) or skill.scripts:
            raise ValueError("skill_package_not_allowed")
        files = {"SKILL.md": (path / "SKILL.md").read_text(encoding="utf-8")}
        for name in skill.references:
            target = (path / "references" / name).resolve()
            if not target.is_relative_to(path / "references"):
                raise ValueError("skill_reference_not_allowed")
            files[f"references/{name}"] = target.read_text(encoding="utf-8")
        skills.append(
            {
                "id": skill.name,
                "name": "证据分析" if skill.name == "evidence-analysis" else "表格分析",
                "description": skill.description,
                "version": (skill.metadata or {}).get("version", "1.0.0"),
                "checksum": digest(files),
                "references": skill.references,
                "required_tools": skill.allowed_tools or [],
            }
        )
    return {
        "tools": tools,
        "skills": skills,
        "mcp_servers": [
            {
                "id": "project-tables",
                "version": 3,
                "transport": "stdio",
                "checksum": hashlib.sha256(
                    Path(__file__).with_name("mcp_server.py").read_bytes()
                ).hexdigest(),
            }
        ],
    }


def freeze_bindings(agent):
    available = catalog()
    tools, skills = [], []
    for key, id_key, target in (("tool_refs", "tool_id", tools), ("skill_refs", "skill_id", skills)):
        pool = available["tools" if key == "tool_refs" else "skills"]
        for ref in agent.get(key, []):
            found = next(
                (
                    item
                    for item in pool
                    if item["id"] == ref[id_key] and str(item["version"]) == str(ref["version"])
                ),
                None,
            )
            if not found or any(item["id"] == found["id"] for item in target):
                raise ValueError("capability_version_unavailable")
            target.append(found)
    allowed_names = {item["name"] for item in tools}
    if any(set(item["required_tools"]) - allowed_names for item in skills):
        raise ValueError("skill_tools_not_authorized")
    return {
        "tools": tools,
        "skills": skills,
        "mcp_servers": available["mcp_servers"] if any(item["kind"] == "mcp" for item in tools) else [],
    }


def permissions(agent):
    return (
        set(agent.get("tool_ids", []))
        | {f"tool:{item['tool_id']}@{item['version']}" for item in agent.get("tool_refs", [])}
        | {f"skill:{item['skill_id']}@{item['version']}" for item in agent.get("skill_refs", [])}
    )


def validate_frozen(agent):
    if agent.get("execution_strategy") != "tool_agent":
        return
    if agent.get("runtime_bindings") != freeze_bindings(agent):
        raise ValueError("capability_version_changed")
