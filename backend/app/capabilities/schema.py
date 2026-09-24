"""极简 JSON Schema 校验器（object/array/string/integer/number/boolean/null、required、properties、items、enum）。

只覆盖本平台 manifest 使用的子集，不引入额外依赖。
"""

from __future__ import annotations

from typing import Any

from .errors import SCHEMA_VALIDATION_FAILED, CapabilityError

_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def assert_valid_schema(schema: Any, *, where: str) -> None:
    """校验一段 schema 本身是合法的 JSON Schema 对象子集。"""
    if schema in ({}, None):
        return
    if not isinstance(schema, dict):
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: schema 必须是 JSON 对象")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SCHEMA_TYPES:
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: 不支持的 type {schema_type!r}")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: properties 必须是对象")
        for key, sub in properties.items():
            assert_valid_schema(sub, where=f"{where}.properties.{key}")
    required = schema.get("required")
    if required is not None and not (
        isinstance(required, list) and all(isinstance(item, str) for item in required)
    ):
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: required 必须是字符串数组")
    if "items" in schema:
        assert_valid_schema(schema["items"], where=f"{where}.items")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: enum 必须是数组")


def _type_ok(expected: str, value: Any) -> bool:
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }[expected](value)


def validate_value(schema: dict[str, Any], value: Any, *, where: str = "input") -> None:
    """按 schema 校验值；失败抛 schema_validation_failed。"""
    if not schema:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: 取值 {value!r} 不在允许范围")
    expected = schema.get("type")
    if expected is not None and value is not None and not _type_ok(expected, value):
        raise CapabilityError(
            SCHEMA_VALIDATION_FAILED, f"{where}: 期望类型 {expected}，实际 {type(value).__name__}"
        )
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value or value[key] is None:
                raise CapabilityError(SCHEMA_VALIDATION_FAILED, f"{where}: 缺少必填字段 {key}")
        properties = schema.get("properties", {})
        for key, sub in properties.items():
            if key in value and value[key] is not None:
                validate_value(sub, value[key], where=f"{where}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_value(schema["items"], item, where=f"{where}[{index}]")
