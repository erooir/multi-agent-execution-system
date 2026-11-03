"""Built-in, domain and project-owned capabilities with immutable runtime bindings."""

from __future__ import annotations

import hashlib
import json
import re
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
RUNTIME_CONTRACT_VERSION = 1
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


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return (normalized or "capability")[:48]


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
            "adapter": "builtin",
            "input_schema": model.model_json_schema(),
            "output_schema": ToolResult.model_json_schema(),
            "read_only": True,
            "external": False,
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
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
                "files": files,
                "external": False,
                "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            }
        )
    return skills


def _mcp_servers():
    return [
        {
            "id": "project-tables",
            "version": 3,
            "transport": "stdio",
            "external": False,
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            "checksum": hashlib.sha256(Path(__file__).with_name("mcp_server.py").read_bytes()).hexdigest(),
        }
    ]


def _external_items(definition, version):
    spec = version["spec"]
    kind = definition["kind"]
    common = {
        "version": version["version"],
        "checksum": version["checksum"],
        "runtime_contract_version": version.get("runtime_contract_version", RUNTIME_CONTRACT_VERSION),
        "external": True,
        "definition_id": definition["id"],
        "native_driver": "agno",
    }
    if kind == "skill":
        return {
            "skills": [
                {
                    **common,
                    "id": definition["id"],
                    "name": definition["name"],
                    "description": definition.get("description", ""),
                    "references": spec["references"],
                    "required_tools": spec["required_tools"],
                    "tool_bindings": spec.get("tool_bindings", {}),
                    "files": spec["files"],
                }
            ]
        }
    if kind == "http_tool":
        tool_id = definition["id"]
        name = f"ext__{safe_name(definition['name'])}__{version['checksum'][:8]}"
        return {
            "tools": [
                {
                    **common,
                    "id": tool_id,
                    "name": name,
                    "label": definition["name"],
                    "description": definition.get("description", ""),
                    "kind": "tool",
                    "adapter": "http_json",
                    "input_schema": spec["input_schema"],
                    "output_schema": spec["output_schema"],
                    "read_only": True,
                    "handler": {
                        key: spec[key] for key in ("url", "secret_ref", "timeout_seconds", "max_result_bytes")
                    },
                }
            ]
        }
    if kind == "agno_toolkit":
        tools = []
        for remote in spec.get("tools", []):
            remote_name = remote["name"]
            tools.append(
                {
                    **common,
                    "id": f"toolkit.{definition['id']}.{remote_name}",
                    "name": remote_name,
                    "label": f"{definition['name']} / {remote_name}",
                    "description": remote.get("description", ""),
                    "kind": "tool",
                    "adapter": "agno_toolkit",
                    "native_driver": spec["native_driver"],
                    "input_schema": remote.get("inputSchema") or {"type": "object"},
                    "read_only": spec["read_only"],
                    "handler": {
                        "provider": spec["provider"],
                        "include_tools": spec["include_tools"],
                    },
                }
            )
        return {"tools": tools}
    if kind == "mcp_server":
        server = {
            **common,
            "id": definition["id"],
            "name": definition["name"],
            "transport": spec["transport"],
            "handler": {
                key: spec.get(key)
                for key in ("transport", "url", "launcher_id", "secret_ref", "tools")
                if spec.get(key) is not None
            },
        }
        tools = []
        for remote in spec.get("tools", []):
            remote_name = remote["name"]
            tools.append(
                {
                    **common,
                    "id": f"mcp.{definition['id']}.{remote_name}",
                    "name": f"mcp__{safe_name(definition['name'])}__{safe_name(remote_name)}__{version['checksum'][:8]}",
                    "label": f"{definition['name']} / {remote_name}",
                    "description": remote.get("description", ""),
                    "kind": "mcp",
                    "adapter": "mcp",
                    "server_id": definition["id"],
                    "remote_name": remote_name,
                    "transport": spec["transport"],
                    "input_schema": remote.get("inputSchema") or {"type": "object"},
                    "output_schema": remote.get("outputSchema"),
                    "read_only": True,
                    "handler": server["handler"],
                }
            )
        return {"tools": tools, "mcp_servers": [server]}
    raise ValueError("capability_kind_unsupported")


def catalog(project_id=None, repository=None):
    result = {
        "tools": _tool_specs(),
        "skills": _skill_specs(),
        "mcp_servers": _mcp_servers(),
        "definitions": [],
    }
    if project_id is None:
        return result
    repository = repository or _repository()
    for definition in repository.list("capability_definitions", project_id):
        result["definitions"].append(
            {key: value for key, value in definition.items() if key not in {"spec", "secret"}}
        )
        if definition.get("status") != "published" or not definition.get("current_version"):
            continue
        version = repository.get("capability_versions", f"{definition['id']}:{definition['current_version']}")
        if not version:
            raise ValueError("capability_version_unavailable")
        for key, items in _external_items(definition, version).items():
            result.setdefault(key, []).extend(items)
    return result


def _repository():
    from .db import repo

    return repo


def freeze_bindings(agent, repository=None):
    available = catalog(agent.get("project_id"), repository) if repository is not None else catalog()
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
    allowed = {item["name"] for item in tools} | {item["id"] for item in tools}
    for skill in skills:
        mapped = [skill.get("tool_bindings", {}).get(name, name) for name in skill["required_tools"]]
        if set(mapped) - allowed:
            raise ValueError("skill_tools_not_authorized")
        skill["required_tools"] = mapped
    server_ids = {item.get("server_id") for item in tools if item["kind"] == "mcp"}
    return {
        "tools": tools,
        "skills": skills,
        "mcp_servers": [
            item
            for item in available["mcp_servers"]
            if (item["id"] in server_ids or (not item["external"] and any(t["kind"] == "mcp" for t in tools)))
        ],
    }


def permissions(agent):
    return (
        set(agent.get("tool_ids", []))
        | {f"tool:{item['tool_id']}@{item['version']}" for item in agent.get("tool_refs", [])}
        | {f"skill:{item['skill_id']}@{item['version']}" for item in agent.get("skill_refs", [])}
    )


def validate_frozen(agent, repository=None):
    if agent.get("executor_bindings"):
        from ..casework.executors.registry import validate_binding

        validate_binding(agent)
    if agent.get("execution_strategy") != "tool_agent" and not agent.get("runtime_bindings"):
        return
    bindings = agent.get("runtime_bindings") or {}
    tools = bindings.get("tools", [])
    skills = bindings.get("skills", [])
    for item in [*tools, *skills]:
        if item.get("runtime_contract_version", RUNTIME_CONTRACT_VERSION) != RUNTIME_CONTRACT_VERSION:
            raise ValueError("capability_version_changed")
        if item.get("external") and not item.get("checksum"):
            raise ValueError("capability_version_changed")

    for items, lookup in (
        ([item for item in tools if not item.get("external")], _tool_specs),
        ([item for item in skills if not item.get("external")], _skill_specs),
    ):
        current = {item["id"]: item for item in lookup({item["id"] for item in items})}
        for item in items:
            candidate = current.get(item["id"])
            if candidate != item or str(item["version"]) != str(candidate["version"]):
                raise ValueError("capability_version_changed")
    current_servers = {item["id"]: item for item in _mcp_servers()}
    for item in bindings.get("mcp_servers", []):
        current = current_servers.get(item["id"]) if not item.get("external") else item
        if current != item or str(item["version"]) != str(current["version"]):
            raise ValueError("capability_version_changed")

    if repository is not None:
        for kind, items in (("tools", tools), ("skills", skills)):
            for item in items:
                if not item.get("external"):
                    continue
                definition = repository.get("capability_definitions", item.get("definition_id"))
                if not definition or definition.get("status") in {"revoked", "destroyed"}:
                    raise ValueError("capability_revoked")
                version = repository.get("capability_versions", f"{definition['id']}:{item['version']}")
                if not version or version["checksum"] != item["checksum"]:
                    raise ValueError("capability_version_changed")
                expected = next(
                    (
                        candidate
                        for candidate in _external_items(definition, version)[kind]
                        if candidate["id"] == item["id"]
                    ),
                    None,
                )
                if expected is not None and kind == "skills":
                    bindings_map = expected.get("tool_bindings", {})
                    expected["required_tools"] = [
                        bindings_map.get(name, name) for name in expected["required_tools"]
                    ]
                if expected != item:
                    raise ValueError("capability_version_changed")

    allowed = {item["name"] for item in tools} | {item["id"] for item in tools}
    if any(set(item["required_tools"]) - allowed for item in skills):
        raise ValueError("capability_version_changed")
