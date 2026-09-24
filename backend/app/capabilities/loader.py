"""加载 skill.yaml / SKILL.md 与 tool/mcp yaml，并做启动校验。"""

from __future__ import annotations

from pathlib import Path

import yaml

from .contracts import McpServerDefinition, SkillManifest, ToolDefinition
from .errors import SCHEMA_VALIDATION_FAILED, CapabilityError
from .registry import McpRegistry, SkillRegistry, ToolRegistry
from .schema import assert_valid_schema

APP_DIR = Path(__file__).resolve().parents[1]
SKILLS_DIR = APP_DIR / "skills"
TOOLS_DIR = APP_DIR / "capability_manifests" / "tools"
MCP_DIR = APP_DIR / "capability_manifests" / "mcp"

_TEMPLATE_PREFIXES = ("{input.", "{steps[")


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{path.name}: 顶层必须是对象")
    return data


def load_tools(tools_dir: Path = TOOLS_DIR) -> ToolRegistry:
    registry = ToolRegistry()
    for path in sorted(tools_dir.glob("*.yaml")):
        payload = _read_yaml(path)
        entries = payload.get("tools")
        if not isinstance(entries, list) or not entries:
            raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{path.name}: 缺少非空 tools 列表")
        for entry in entries:
            definition = ToolDefinition.model_validate(entry)
            assert_valid_schema(definition.input_schema, where=f"{definition.id}.input_schema")
            assert_valid_schema(definition.output_schema, where=f"{definition.id}.output_schema")
            if definition.provider == "http" and not definition.allowed_hosts:
                raise CapabilityError(
                    SCHEMA_VALIDATION_FAILED, f"{definition.id}: HTTP Tool 必须声明 allowed_hosts"
                )
            registry.register(definition)
    return registry


def load_skills(skills_dir: Path = SKILLS_DIR, tool_registry: ToolRegistry | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    for directory in sorted(path for path in skills_dir.iterdir() if path.is_dir()):
        manifest_path = directory / "skill.yaml"
        instructions_path = directory / "SKILL.md"
        if not manifest_path.is_file() or not instructions_path.is_file():
            raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{directory.name}: 缺少 skill.yaml 或 SKILL.md")
        manifest = SkillManifest.model_validate(_read_yaml(manifest_path))
        manifest.instructions_path = str(instructions_path)
        assert_valid_schema(manifest.input_schema, where=f"{manifest.id}.input_schema")
        assert_valid_schema(manifest.output_schema, where=f"{manifest.id}.output_schema")
        if tool_registry is not None:
            for tool_id in manifest.allowed_tools:
                if tool_id not in tool_registry:
                    raise CapabilityError(
                        SCHEMA_VALIDATION_FAILED, f"Skill {manifest.id} 引用了不存在的 Tool: {tool_id}"
                    )
        if manifest.execution_mode == "recipe" and not manifest.recipe:
            raise CapabilityError(
                SCHEMA_VALIDATION_FAILED, f"Skill {manifest.id}: recipe 模式必须声明 recipe 步骤"
            )
        # agent 技能也可声明 recipe，作为不调用模型的 drill 回退路径。
        # 两种模式下都校验回退配方，避免只有上线执行时才暴露坏引用。
        if manifest.recipe:
            for index, step in enumerate(manifest.recipe):
                if tool_registry is not None and step.tool not in tool_registry:
                    raise CapabilityError(
                        SCHEMA_VALIDATION_FAILED,
                        f"Skill {manifest.id} recipe[{index}] 引用了不存在的 Tool: {step.tool}",
                    )
                for key, value in step.args.items():
                    if (
                        isinstance(value, str)
                        and "{" in value
                        and not any(token in value for token in _TEMPLATE_PREFIXES)
                    ):
                        raise CapabilityError(
                            SCHEMA_VALIDATION_FAILED,
                            f"Skill {manifest.id} recipe[{index}].args.{key}: 无法识别的模板占位符",
                        )
        registry.register(manifest)
    return registry


def load_mcp_servers(mcp_dir: Path = MCP_DIR) -> McpRegistry:
    registry = McpRegistry()
    for path in sorted(mcp_dir.glob("*.yaml")):
        definition = McpServerDefinition.model_validate(_read_yaml(path))
        if definition.transport == "stdio" and not definition.command:
            raise CapabilityError(
                SCHEMA_VALIDATION_FAILED, f"MCP {definition.id}: stdio 传输必须声明 command"
            )
        if definition.transport == "streamable_http" and not definition.url:
            raise CapabilityError(
                SCHEMA_VALIDATION_FAILED, f"MCP {definition.id}: streamable_http 传输必须声明 url"
            )
        registry.register(definition)
    return registry


def load_default_registries() -> tuple[SkillRegistry, ToolRegistry, McpRegistry]:
    """按启动顺序加载并交叉校验三层注册表。"""
    tools = load_tools()
    mcp_servers = load_mcp_servers()
    skills = load_skills(tool_registry=tools)
    return skills, tools, mcp_servers
