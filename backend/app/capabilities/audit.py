"""能力调用审计：内存环形缓冲 + 可选落库回调。

脱敏规则：不记录原文与凭据，命中敏感键的参数值替换为 <redacted>，
其余字符串截断，避免审计记录变成资料外泄通道。
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = {
    "text",
    "prompt",
    "content",
    "system",
    "api_key",
    "apikey",
    "key",
    "token",
    "secret",
    "password",
    "authorization",
    "image",
    "images",
    "base64",
}
_MAX_STRING = 120


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key.lower() in _SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value[:20]]
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + "…(截断)"
    return value


class AuditLog:
    def __init__(self, capacity: int = 1000, persist: Callable[[dict], None] | None = None):
        self._events: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._persist = persist

    def record(
        self,
        *,
        capability_type: str,
        capability_id: str,
        status: str,
        context,
        duration_ms: int = 0,
        error_code: str | None = None,
        arguments: dict | None = None,
    ) -> dict:
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "capability_type": capability_type,
            "capability_id": capability_id,
            "status": status,
            "error_code": error_code,
            "duration_ms": duration_ms,
            "request_id": getattr(context, "request_id", None),
            "run_id": getattr(context, "run_id", None),
            "step_id": getattr(context, "step_id", None),
            "user_id": getattr(context, "user_id", None),
            "project_id": getattr(context, "project_id", None),
            "agent_id": getattr(context, "agent_id", None),
            "mode": getattr(context, "mode", None),
            "arguments": redact(arguments or {}),
        }
        with self._lock:
            self._events.append(event)
        if self._persist is not None:
            self._persist(event)
        return event

    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)


audit_log = AuditLog()
