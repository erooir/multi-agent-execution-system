"""HTTP Provider：allowed_hosts 白名单校验、超时、响应大小上限与结果解析。

entrypoint 约定为 "module:base"，模块需提供：
- ``base_request(arguments) -> {"method", "url", "params"?, "headers"?}``
- ``base_parse(payload, arguments) -> dict``（payload 为 JSON 解析结果或文本）

Provider 负责唯一的网络出口：主机白名单、超时与大小上限都在这里强制。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from ..contracts import ExecutionContext, ToolDefinition
from ..errors import DATA_EGRESS_BLOCKED, PROVIDER_UNAVAILABLE, TOOL_TIMEOUT, CapabilityError
from .local import resolve_callable

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_HTTP_TIMEOUT = 15.0


class HttpProvider:
    def __init__(self, max_response_bytes: int = MAX_RESPONSE_BYTES, transport=None):
        self.max_response_bytes = max_response_bytes
        self._transport = transport  # 测试注入 httpx.MockTransport；生产为 None（真实网络）

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
        builder = resolve_callable(definition.entrypoint + "_request")
        parser = resolve_callable(definition.entrypoint + "_parse")
        spec = builder(arguments)
        url = str(spec.get("url", ""))
        self.check_allowed(definition, url)
        timeout = min(float(definition.timeout_seconds), MAX_HTTP_TIMEOUT)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.request(
                    spec.get("method", "GET"),
                    url,
                    params=spec.get("params"),
                    headers=spec.get("headers"),
                )
                content = await response.aread()
        except httpx.TimeoutException as error:
            raise CapabilityError(TOOL_TIMEOUT, f"HTTP 调用超时（{timeout}s）") from error
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
        if response.status_code >= 400:
            raise CapabilityError(
                PROVIDER_UNAVAILABLE, f"HTTP {response.status_code}：上游服务不可用或拒绝请求"
            )
        content_type = response.headers.get("content-type", "")
        payload: Any
        if "json" in content_type:
            import json

            payload = json.loads(content.decode("utf-8", "replace"))
        else:
            payload = content.decode("utf-8", "replace")
        result = parser(payload, arguments)
        if not isinstance(result, dict):
            raise CapabilityError(PROVIDER_UNAVAILABLE, "HTTP 结果解析器未返回对象")
        return result
