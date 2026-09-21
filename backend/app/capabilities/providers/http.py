"""HTTP Provider 基座：allowed_hosts 白名单校验、超时与响应大小上限。

本期只提供基座能力，不注册任何具体 HTTP Tool；后续 NOAA/OpenAlex 等外部
接入必须以此类为入口，禁止绕过白名单。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from ..contracts import ExecutionContext, ToolDefinition
from ..errors import DATA_EGRESS_BLOCKED, PROVIDER_UNAVAILABLE, CapabilityError

MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class HttpProvider:
    def __init__(self, max_response_bytes: int = MAX_RESPONSE_BYTES):
        self.max_response_bytes = max_response_bytes

    def check_allowed(self, definition: ToolDefinition, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        allowed = {entry.lower() for entry in definition.allowed_hosts}
        if host not in allowed:
            raise CapabilityError(
                DATA_EGRESS_BLOCKED, f"目标主机 {host or '(空)'} 不在 {definition.id} 的 allowed_hosts 中"
            )

    async def invoke(
        self, definition: ToolDefinition, arguments: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        url = str(arguments.get("url", ""))
        self.check_allowed(definition, url)
        try:
            async with httpx.AsyncClient(timeout=definition.timeout_seconds) as client:
                response = await client.get(url)
                content = await response.aread()
        except CapabilityError:
            raise
        except Exception as error:
            raise CapabilityError(
                PROVIDER_UNAVAILABLE, f"HTTP 调用失败（{type(error).__name__}）"
            ) from error
        if len(content) > self.max_response_bytes:
            raise CapabilityError(
                PROVIDER_UNAVAILABLE, f"HTTP 响应超过 {self.max_response_bytes} 字节上限"
            )
        response.raise_for_status()
        return {"status_code": response.status_code, "body": content.decode("utf-8", "replace")}
