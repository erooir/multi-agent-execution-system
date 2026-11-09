"""Project-owned capability import, discovery, publication and revocation."""

from __future__ import annotations

import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from agno.skills import LocalSkills
from agno.skills.errors import SkillValidationError
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from .db import TERMINAL, now
from .service import DomainError, audit, fail, project_access, required
from .tool_registry import RUNTIME_CONTRACT_VERSION, digest


def _schema(value, code):
    try:
        validator_for(value).check_schema(value)
    except SchemaError:
        fail(code, "JSON Schema 无效", 422)

    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "$ref" and isinstance(child, str) and not child.startswith("#"):
                    fail("remote_schema_ref_blocked", "不允许远程 JSON Schema 引用", 422)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)


def parse_skill_package(package: bytes):
    if not package or len(package) > 5_000_000:
        fail("skill_package_too_large", "Skill ZIP 不能为空且不能超过5MB", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile:
        fail("skill_package_invalid", "文件不是有效 ZIP", 422)
    with archive, tempfile.TemporaryDirectory() as temporary:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        if not entries or len(entries) > 50:
            fail("skill_package_invalid", "Skill 包文件数必须为1至50", 422)
        normalized = {}
        for entry in entries:
            if entry.file_size > 1_000_000 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                fail("skill_package_not_allowed", "Skill 包含超限文件或符号链接", 422)
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not name:
                fail("skill_path_blocked", "Skill 包含不安全路径", 422)
            normalized_name = "/".join(path.parts)
            key = normalized_name.casefold()
            if key in normalized:
                fail("skill_path_blocked", "Skill 包含重复路径", 422)
            normalized[key] = (normalized_name, entry)
        names = [name for name, _ in normalized.values()]
        prefix = ""
        if "SKILL.md" not in names:
            roots = {name.split("/", 1)[0] for name in names}
            if len(roots) != 1 or f"{roots.pop()}/SKILL.md" not in names:
                fail("skill_metadata_invalid", "ZIP 根目录或唯一顶层目录必须包含 SKILL.md", 422)
            prefix = names[0].split("/", 1)[0] + "/"
        target = Path(temporary) / "package"
        for name, entry in normalized.values():
            relative = name.removeprefix(prefix)
            if relative != "SKILL.md" and not (
                relative.startswith("references/") and relative.endswith((".md", ".txt"))
            ):
                fail("skill_package_not_allowed", "仅允许 SKILL.md 与 references 下的 Markdown/文本", 422)
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.write_bytes(archive.read(entry))
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                fail("skill_encoding_invalid", "Skill 文档必须使用 UTF-8", 422)
        try:
            loaded = LocalSkills(str(target), validate=False).load()
            if len(loaded) != 1:
                raise ValueError("skill_count")
            target.rename(target.with_name(loaded[0].name))
            skill = LocalSkills(temporary, validate=True).load()[0]
        except (SkillValidationError, ValueError, OSError):
            fail("skill_metadata_invalid", "SKILL.md 不符合 Agno Agent Skills 规范", 422)
        files = {
            str(path.relative_to(skill.source_path)).replace("\\", "/"): path.read_text(encoding="utf-8")
            for path in Path(skill.source_path).rglob("*")
            if path.is_file()
        }
        return {
            "name": skill.name,
            "description": skill.description,
            "spec": {
                "files": files,
                "references": skill.references,
                "required_tools": skill.allowed_tools or [],
                "tool_bindings": {},
            },
            "upstream_version": str((skill.metadata or {}).get("version", "1.0.0"))[:100],
            "source": {"license": str(skill.license or "未声明")[:200]},
        }


def _new_definition(body, user, repository, kind, spec, extra=None):
    project_access(body["project_id"], user, True, repository)
    with repository.transaction() as tx:
        record = tx.put(
            "capability_definitions",
            {
                "id": uuid.uuid4().hex,
                "project_id": body["project_id"],
                "kind": kind,
                "name": body["name"],
                "description": body.get("description", ""),
                "status": "draft",
                "current_version": 0,
                "draft_spec": spec,
                "source": (extra or {}).get("source", {}),
                "upstream_version": (extra or {}).get("upstream_version"),
                "created_by": user["id"],
            },
        )
        audit(tx, record["project_id"], f"capability.{kind}.created", record["id"], user)
        return record


def import_skill(package, project_id, user, repository):
    parsed = parse_skill_package(package)
    return _new_definition(
        {"project_id": project_id, **parsed}, user, repository, "skill", parsed["spec"], parsed
    )


def create_http_tool(body, user, repository):
    _schema(body["input_schema"], "tool_input_schema_invalid")
    _schema(body["output_schema"], "tool_output_schema_invalid")
    if body["input_schema"].get("type") != "object":
        fail("tool_input_schema_invalid", "Tool 输入 Schema 顶层必须是 object", 422)
    from .network_policy import validate_target

    validate_target(body["url"], "HANGZHI_TOOL_HOSTS")
    spec = {
        key: body[key]
        for key in (
            "url",
            "input_schema",
            "output_schema",
            "secret_ref",
            "timeout_seconds",
            "max_result_bytes",
        )
    }
    spec.update(method="POST", read_only=True, billing="unmetered")
    return _new_definition(body, user, repository, "http_tool", spec)


def create_native_toolkit(body, user, repository):
    from .native_capabilities import create_native_toolkit as build

    try:
        toolkit = build(body["provider"], body["include_tools"] or None)
    except ValueError:
        fail("native_toolkit_config_invalid", "Agno Toolkit 或所选功能不可用", 422)
    spec = {
        "provider": body["provider"],
        "include_tools": body["include_tools"],
        "native_driver": f"agno.tools.{body['provider']}",
        "tools": [
            {
                "name": function.name,
                "description": function.description or "",
                "inputSchema": function.parameters,
            }
            for function in toolkit.functions.values()
        ],
        "read_only": body["provider"] == "calculator",
        "billing": "unmetered",
    }
    return _new_definition(body, user, repository, "agno_toolkit", spec)


def create_task_plugin(body, user, repository):
    from .contracts import TaskPluginInput
    from .task_plugins import TaskPluginError, manifest

    if user.get("role") != "admin":
        fail("permission_denied", "任务插件发布配置要求管理员角色", 403)
    body = TaskPluginInput.model_validate(body).model_dump()
    try:
        spec = manifest(body)
    except TaskPluginError as exc:
        fail(exc.code, exc.message, 422)
    return _new_definition(body, user, repository, "task_plugin", spec)


def create_mcp_server(body, user, repository):
    if body["transport"] == "streamable_http":
        if not body["url"] or body["launcher_id"]:
            fail("mcp_transport_invalid", "Streamable HTTP 需要 url 且不接受 launcher_id", 422)
        from .network_policy import validate_target

        validate_target(body["url"], "HANGZHI_MCP_HOSTS")
    elif not body["launcher_id"] or body["url"]:
        fail("mcp_transport_invalid", "stdio 需要 launcher_id 且不接受 url", 422)
    spec = {key: body[key] for key in ("transport", "url", "launcher_id", "secret_ref")}
    spec.update(read_only=True, billing="unmetered", tools=[])
    return _new_definition(body, user, repository, "mcp_server", spec)


async def discover_mcp(definition_id, user, repository):
    definition = required("capability_definitions", definition_id, user, True, repository)
    if definition["kind"] != "mcp_server":
        fail("capability_kind_unsupported", "只有 MCP 服务可以发现工具", 422)
    from .mcp_runtime import discover

    try:
        discovered = await discover(definition["draft_spec"])
    except Exception as exc:
        if isinstance(exc, DomainError):
            raise
        fail("mcp_discovery_failed", f"MCP 发现失败（{type(exc).__name__}）", 422)
    with repository.transaction() as tx:
        current = required("capability_definitions", definition_id, user, True, tx)
        spec = {**current["draft_spec"], **discovered}
        current["draft_spec"] = spec
        current["discovered_at"] = now()
        current = tx.put("capability_definitions", current)
        audit(tx, current["project_id"], "capability.mcp.discovered", definition_id, user)
        return current


def publish(definition_id, user, repository, tool_bindings=None):
    with repository.transaction() as tx:
        definition = required("capability_definitions", definition_id, user, True, tx)
        if definition["status"] in {"revoked", "destroyed"}:
            fail("invalid_state", "已撤销或销毁的能力不能发布", 409)
        spec = dict(definition.get("draft_spec") or {})
        if definition["kind"] == "task_plugin":
            from .task_plugins import TaskPluginError, manifest

            if user.get("role") != "admin":
                fail("permission_denied", "任务插件发布要求管理员角色", 403)
            try:
                spec = manifest(
                    {
                        "project_id": definition["project_id"],
                        "name": definition["name"],
                        **{
                            key: spec[key]
                            for key in (
                                "provider",
                                "input_media_types",
                                "max_input_bytes",
                                "max_output_bytes",
                            )
                        },
                    }
                )
            except TaskPluginError as exc:
                fail(exc.code, exc.message, 422)
        if definition["kind"] == "skill":
            spec["tool_bindings"] = tool_bindings or {}
            unknown = set(spec["tool_bindings"]) - set(spec["required_tools"])
            if unknown:
                fail("skill_tools_invalid", "工具映射包含 Skill 未声明的名称", 422)
        if definition["kind"] == "mcp_server" and not spec.get("tools"):
            fail("mcp_discovery_required", "发布前必须成功发现至少一个只读 MCP 工具", 422)
        version = definition.get("current_version", 0) + 1
        record = {
            "id": f"{definition_id}:{version}",
            "definition_id": definition_id,
            "project_id": definition["project_id"],
            "kind": definition["kind"],
            "version": version,
            "spec": spec,
            "checksum": digest(spec),
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            "upstream_version": definition.get("upstream_version"),
            "created_by": user["id"],
            "status": "published",
        }
        tx.put("capability_versions", record)
        definition.update(
            status="published",
            current_version=version,
            draft_spec=spec,
            published_at=now(),
        )
        definition = tx.put("capability_definitions", definition)
        audit(tx, definition["project_id"], "capability.published", definition_id, user, {"version": version})
        return definition


def action(definition_id, action_name, user, repository):
    with repository.transaction() as tx:
        definition = required("capability_definitions", definition_id, user, True, tx)
        if definition["kind"] == "task_plugin" and user.get("role") != "admin":
            fail("permission_denied", "任务插件管理要求管理员角色", 403)
        status = definition["status"]
        if action_name == "disable" and status == "published":
            definition["status"] = "disabled"
        elif action_name == "enable" and status == "disabled":
            definition["status"] = "published"
        elif action_name == "revoke" and status != "destroyed":
            definition.update(status="revoked", revoked_at=now())
        elif action_name == "destroy" and status != "destroyed":
            agents = tx.list("agents", definition["project_id"])
            referenced = any(
                agent.get("status") != "destroyed"
                and (
                    any(
                        ref["tool_id"] == definition_id or ref["tool_id"].startswith(f"mcp.{definition_id}.")
                        for ref in agent.get("tool_refs", [])
                    )
                    or any(ref["skill_id"] == definition_id for ref in agent.get("skill_refs", []))
                )
                for agent in agents
            )
            active = any(
                task["status"] not in TERMINAL
                and (
                    definition_id in json.dumps(task.get("snapshot", {}).get("agents", []))
                    or (task.get("snapshot", {}).get("task_plugin") or {}).get("id") == definition_id
                )
                for task in tx.list("tasks", definition["project_id"])
            )
            if referenced or active:
                fail("reference_protected", "能力仍被 Agent 或运行任务引用", 409)
            definition["status"] = "destroyed"
        else:
            fail("invalid_action", "当前状态不支持此生命周期操作", 409)
        definition = tx.put("capability_definitions", definition)
        audit(tx, definition["project_id"], f"capability.{action_name}", definition_id, user)
        return definition
