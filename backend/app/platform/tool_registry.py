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
BASE_TOOL_IDS = frozenset(d[0] for d in DEFINITIONS)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _tool_specs(requested=None):
    base = [row for row in DEFINITIONS if requested is None or row[0] in requested]
    runtime_source = Path(__file__).with_name("tool_runtime.py").read_text(encoding="utf-8") if base else None
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
                    "runtime": runtime_source,
                    "schema": model.model_json_schema(),
                }
            ),
        }
        for tool_id, name, label, kind, model in base
    ]
    if requested is not None and not requested - BASE_TOOL_IDS:
        return tools
    from .domain_tool import operation_catalog

    operations = [op for op in operation_catalog().values() if requested is None or op.id in requested]
    if any(op.id in BASE_TOOL_IDS or op.name in {r[1] for r in DEFINITIONS} for op in operations):
        raise ValueError("duplicate_domain_tool")
    # Read shared dependencies once within this catalog snapshot; the next snapshot
    # still detects replaced code or package versions.
    checksums, installed = {}, {}
    tools.extend(operation.spec(checksums, installed) for operation in operations)
    return tools


def _skill_specs(requested=None):
    if requested is not None and not requested:
        return []
    skills = []
    for skill in sorted(LocalSkills(str(SKILL_ROOT)).load(), key=lambda item: item.name):
        if requested is not None and skill.name not in requested:
            continue
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
    return skills


def _mcp_servers():
    return [
        {
            "id": "project-tables",
            "version": 3,
            "transport": "stdio",
            "checksum": hashlib.sha256(Path(__file__).with_name("mcp_server.py").read_bytes()).hexdigest(),
        }
    ]


def catalog():
    return {"tools": _tool_specs(), "skills": _skill_specs(), "mcp_servers": _mcp_servers()}


def freeze_bindings(agent):
    def resolve(refs, id_key, lookup):
        identifiers = {ref[id_key] for ref in refs}
        if len(identifiers) != len(refs):
            raise ValueError("capability_version_unavailable")
        specs = {row["id"]: row for row in lookup(identifiers)}
        frozen = []
        for ref in refs:
            item = specs.get(ref[id_key])
            if not item or str(ref["version"]) != str(item["version"]):
                raise ValueError("capability_version_unavailable")
            frozen.append(item)
        return frozen

    tools = resolve(agent.get("tool_refs", []), "tool_id", _tool_specs)
    skills = resolve(agent.get("skill_refs", []), "skill_id", _skill_specs)
    allowed_names = {item["name"] for item in tools}
    if any(set(item["required_tools"]) - allowed_names for item in skills):
        raise ValueError("skill_tools_not_authorized")
    return {
        "tools": tools,
        "skills": skills,
        "mcp_servers": _mcp_servers() if any(item["kind"] == "mcp" for item in tools) else [],
    }


def permissions(agent):
    return (
        set(agent.get("tool_ids", []))
        | {f"tool:{item['tool_id']}@{item['version']}" for item in agent.get("tool_refs", [])}
        | {f"skill:{item['skill_id']}@{item['version']}" for item in agent.get("skill_refs", [])}
    )


def validate_frozen(agent):
    if agent.get("execution_strategy") != "tool_agent" and not agent.get("runtime_bindings"):
        return
    if agent.get("runtime_bindings") != freeze_bindings(agent):
        raise ValueError("capability_version_changed")
