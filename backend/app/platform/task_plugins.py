"""Versioned task plugins over deployed, governed executors.

A manifest selects an installed provider; it cannot name Python modules, execute
scripts, install packages, change permissions, or introduce another model client.
Provider contracts and implementation signatures are owned by the deployment.
"""

from __future__ import annotations

import copy
import inspect
import json
from importlib.metadata import version as package_version

from jsonschema import ValidationError, validate

from .contracts import TaskPluginInput
from .tool_registry import digest

CONTRACT_VERSION = 1
_TEXT_MEDIA = ["text/plain", "text/csv", "application/json", "application/geo+json"]
_PROVIDERS = {
    "image_metadata": {
        "task_types": ["image_inspect"],
        "input_modalities": ["image"],
        "input_media_types": ["image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"],
        "artifact_type": "image_metadata",
        "content_schema": {
            "type": "object",
            "required": ["images"],
            "properties": {
                "images": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["source_id", "width", "height", "decoded", "frames", "bytes"],
                        "properties": {
                            "source_id": {"type": "string"},
                            "width": {"type": "integer", "minimum": 1},
                            "height": {"type": "integer", "minimum": 1},
                            "decoded": {"const": True},
                            "frames": {"type": "integer", "minimum": 1},
                            "bytes": {"type": "integer", "minimum": 1},
                        },
                    },
                }
            },
        },
        "functions": ["image_inspect", "_asset_bytes", "_asset_path"],
        "packages": ["Pillow"],
    },
    "source_digest": {
        "task_types": ["source_summary", "text_analysis"],
        "input_modalities": ["text", "structured"],
        "input_media_types": _TEXT_MEDIA,
        "artifact_type": "source_digest",
        "content_schema": {
            "type": "object",
            "required": ["method", "fragments"],
            "properties": {
                "method": {"const": "extractive"},
                "fragments": {"type": "array", "minItems": 1, "items": {"type": "object"}},
            },
        },
        "functions": ["source_summary", "_read_content", "_asset_bytes", "_asset_path"],
        "packages": [],
    },
    "structured_table": {
        "task_types": ["structured_transform"],
        "input_modalities": ["text", "structured"],
        "input_media_types": _TEXT_MEDIA,
        "artifact_type": "table",
        "content_schema": {
            "type": "object",
            "required": ["records"],
            "properties": {"records": {"type": "array", "items": {"type": "object"}}},
        },
        "functions": ["structured_transform", "_read_content", "_asset_bytes", "_asset_path"],
        "packages": [],
    },
}


class TaskPluginError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def provider_contract(provider):
    """Return a detached public contract, including the installed implementation."""
    from . import runtime
    from .quality import deterministic_quality

    config = _PROVIDERS.get(provider)
    if config is None:
        raise TaskPluginError("plugin_provider_unavailable", "插件执行器未在部署中登记")
    contract = {
        key: copy.deepcopy(value) for key, value in config.items() if key not in {"functions", "packages"}
    }
    implementation = {
        "provider": provider,
        "functions": {name: inspect.getsource(getattr(runtime, name)) for name in config["functions"]},
        "dispatch": inspect.getsource(runtime.execute_local),
        "quality": inspect.getsource(deterministic_quality),
        "contract": contract,
        "packages": {name: package_version(name) for name in config["packages"]},
        "plugin_runtime": {
            function.__name__: inspect.getsource(function)
            for function in (execute, _check_inputs, _input_size)
        },
    }
    return {
        **contract,
        "provider": provider,
        "contract_version": CONTRACT_VERSION,
        "implementation_checksum": digest(implementation),
        "modes": ["local", "rehearsal"],
        "offline_ready": True,
        "network": False,
        "model_calls": False,
        "billing": "unmetered",
        "output_media_type": "application/json",
    }


def manifest(body):
    body = TaskPluginInput.model_validate(body).model_dump()
    contract = provider_contract(body["provider"])
    media_types = sorted(set(body["input_media_types"] or contract["input_media_types"]))
    if set(media_types) - set(contract["input_media_types"]):
        raise TaskPluginError("plugin_input_contract_invalid", "插件不能声明执行器未支持的媒体类型")
    return {
        **contract,
        "input_media_types": media_types,
        "max_input_bytes": body["max_input_bytes"],
        "max_output_bytes": body["max_output_bytes"],
    }


def _validate_manifest(spec):
    try:
        expected = manifest(
            {
                "project_id": "validation",
                "name": "validation",
                **{
                    key: spec[key]
                    for key in ("provider", "input_media_types", "max_input_bytes", "max_output_bytes")
                },
            }
        )
    except (KeyError, ValueError, AttributeError, OSError, TypeError, ImportError):
        raise TaskPluginError("plugin_version_changed", "冻结插件合同无效或执行器不可用") from None
    if expected != spec:
        raise TaskPluginError("plugin_version_changed", "冻结插件合同或执行器实现已变化，须重新发布")


def project_version(definition, version):
    return {
        "id": definition["id"],
        "project_id": definition["project_id"],
        "name": definition["name"],
        "version": version["version"],
        "checksum": version["checksum"],
        "spec": copy.deepcopy(version["spec"]),
    }


def validate_frozen(plugin, repository, project_id=None):
    if plugin is None:
        return
    definition = repository.get(
        "capability_definitions",
        plugin.get("id"),
        for_update=repository.connection is not None,
    )
    if not definition or definition.get("status") in {"revoked", "destroyed"}:
        raise TaskPluginError("plugin_revoked", "任务插件已撤回或不可用")
    if (
        definition.get("kind") != "task_plugin"
        or definition.get("project_id") != plugin.get("project_id")
        or project_id is not None
        and definition.get("project_id") != project_id
    ):
        raise TaskPluginError("plugin_permission_denied", "任务插件不属于当前项目")
    version = repository.get("capability_versions", f"{plugin['id']}:{plugin.get('version')}")
    if (
        not version
        or project_version(definition, version) != plugin
        or digest(version["spec"]) != version["checksum"]
    ):
        raise TaskPluginError("plugin_version_changed", "任务插件与已发布的不可变版本不一致")
    _validate_manifest(plugin["spec"])


def _input_size(asset):
    # Match _read_content: DAG projections may retain the original file reference
    # while replacing its parsed content with a larger merged in-memory value.
    content = asset.get("content")
    if content is None and asset.get("content_ref"):
        from .runtime import _asset_path

        return _asset_path(asset).stat().st_size
    return len((content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)).encode())


def _check_inputs(spec, task_type, inputs, mode="local"):
    if task_type not in spec["task_types"] or mode not in spec["modes"]:
        raise TaskPluginError("plugin_task_mismatch", "插件不支持当前任务类型或执行模式")
    if not inputs or any(
        item.get("modality") not in spec["input_modalities"]
        or item.get("media_type") not in spec["input_media_types"]
        for item in inputs
    ):
        raise TaskPluginError("plugin_input_contract_invalid", "资料模态或媒体类型不符合插件合同")
    if sum(_input_size(item) for item in inputs) > spec["max_input_bytes"]:
        raise TaskPluginError("plugin_input_too_large", "资料总大小超过冻结插件限额")


def select_plugin(body, inputs, repository):
    """Explicit selection is strict; automatic selection is stable and optional."""
    selected = body.get("plugin_id")
    candidates = sorted(
        repository.list("capability_definitions", body["project_id"]), key=lambda item: item["id"]
    )
    if selected:
        definition = repository.get("capability_definitions", selected)
        if not definition or definition.get("project_id") != body["project_id"]:
            raise TaskPluginError("plugin_permission_denied", "任务插件不存在或不属于当前项目")
        candidates = [definition]
    for definition in candidates:
        if definition.get("kind") != "task_plugin" or definition.get("status") != "published":
            if selected:
                raise TaskPluginError("plugin_unavailable", "任务插件尚未发布或已停用")
            continue
        version = repository.get("capability_versions", f"{definition['id']}:{definition['current_version']}")
        if not version:
            if selected:
                raise TaskPluginError("plugin_unavailable", "任务插件缺少已发布版本")
            continue
        plugin = project_version(definition, version)
        try:
            validate_frozen(plugin, repository, body["project_id"])
            _check_inputs(plugin["spec"], body["task_type"], inputs, body["mode"])
        except TaskPluginError:
            if selected:
                raise
            continue
        return plugin
    return None


def execute(plugin, task_type, inputs, config, execute_local):
    """Run through the existing permission-checked local executor, with hard gates."""
    if plugin is None:
        return execute_local(task_type, inputs, config)
    from .quality import deterministic_quality

    spec = plugin["spec"]
    _validate_manifest(spec)
    _check_inputs(spec, task_type, inputs)
    output = execute_local(task_type, inputs, config)
    try:
        validate(output.get("content"), spec["content_schema"])
    except ValidationError:
        raise TaskPluginError("plugin_output_contract_invalid", "插件输出未通过固定产物合同") from None
    if output.get("type") != spec["artifact_type"] or output.get("media_type") != spec["output_media_type"]:
        raise TaskPluginError("plugin_output_contract_invalid", "插件产物类型不符合固定合同")
    quality = deterministic_quality({"task_type": task_type, "quality": {}}, output, inputs)
    if quality["hard_failures"]:
        raise TaskPluginError("plugin_output_contract_invalid", "插件输出未通过独立结构与来源检查")
    output = {
        **output,
        "task_plugin": {key: plugin[key] for key in ("id", "version", "checksum")},
    }
    if len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode()) > spec["max_output_bytes"]:
        raise TaskPluginError("plugin_output_too_large", "插件产物超过冻结大小限额")
    return output
