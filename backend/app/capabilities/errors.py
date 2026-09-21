"""稳定错误码与可公开的能力错误。

错误码是契约字符串，调用方按 code 分支，不解析中文 message。
"""

from __future__ import annotations

CAPABILITY_NOT_FOUND = "capability_not_found"
CAPABILITY_DISABLED = "capability_disabled"
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
PERMISSION_DENIED = "permission_denied"
DATA_EGRESS_BLOCKED = "data_egress_blocked"
CONFIRMATION_REQUIRED = "confirmation_required"
PROVIDER_UNAVAILABLE = "provider_unavailable"
MCP_CONNECTION_FAILED = "mcp_connection_failed"
TOOL_TIMEOUT = "tool_timeout"
TOOL_RESULT_INVALID = "tool_result_invalid"
BUDGET_EXCEEDED = "budget_exceeded"
# 未归类的 Provider 业务失败（如资料不存在、格式不支持）。
TOOL_FAILED = "tool_failed"

STABLE_CODES = {
    CAPABILITY_NOT_FOUND,
    CAPABILITY_DISABLED,
    SCHEMA_VALIDATION_FAILED,
    PERMISSION_DENIED,
    DATA_EGRESS_BLOCKED,
    CONFIRMATION_REQUIRED,
    PROVIDER_UNAVAILABLE,
    MCP_CONNECTION_FAILED,
    TOOL_TIMEOUT,
    TOOL_RESULT_INVALID,
    BUDGET_EXCEEDED,
    TOOL_FAILED,
}


class CapabilityError(Exception):
    """携带稳定错误码的能力调用错误。"""

    def __init__(self, code: str, message: str):
        if code not in STABLE_CODES:
            raise ValueError(f"未登记的错误码: {code}")
        self.code = code
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}
