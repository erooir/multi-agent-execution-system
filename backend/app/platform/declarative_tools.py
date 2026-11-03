"""Schema-bound, read-only HTTP JSON execution for native Agno Functions."""

from __future__ import annotations

from jsonschema import validate

from .network_policy import post_json


async def call_http(spec, arguments):
    validate(arguments, spec["input_schema"])
    handler = spec["handler"]
    result = await post_json(
        handler["url"],
        arguments,
        secret_ref=handler.get("secret_ref"),
        timeout_seconds=handler.get("timeout_seconds", 15),
        max_result_bytes=handler.get("max_result_bytes", 80000),
    )
    validate(result, spec["output_schema"])
    return result
