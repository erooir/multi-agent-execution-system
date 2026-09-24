"""Skill / Tool / MCP 三层的统一 Pydantic 契约。"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutionContext(BaseModel):
    """沿调用链显式传递的执行上下文。"""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str | None = None
    step_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    data_visibility: Literal["external", "local"] = "external"
    mode: Literal["live", "drill"] = "drill"
    allowed_skill_ids: list[str] | None = None
    allowed_tool_ids: list[str] | None = None
    file_roots: list[str] = Field(default_factory=list)
    network_policy: Literal["deny", "allow"] = "deny"
    confirmed: bool = False


class ToolCallRecord(BaseModel):
    tool_id: str
    tool_version: str = ""
    provider: str = ""
    status: str
    duration_ms: int = 0


class ToolTrace(BaseModel):
    tool_id: str = ""
    tool_version: str = ""
    provider: str = ""
    duration_ms: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


class ToolError(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    status: Literal["completed", "failed", "dry_run", "blocked"]
    data: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    error: ToolError | None = None
    trace: ToolTrace = Field(default_factory=ToolTrace)


class RecipeStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # 渲染后为真才执行该步骤（用于 prepare_only 等条件分支）。
    when: str | None = None
    unless: str | None = None


class SkillManifest(BaseModel):
    id: str
    version: str
    name: str
    description: str = ""
    execution_mode: Literal["agent", "recipe"] = "recipe"
    allowed_tools: list[str] = Field(default_factory=list)
    node_kinds: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    evidence_required: bool = False
    # 该技能只有在运行时显式选择了上传资料后才有意义。规划器和执行器都据此
    # 阻止“无附件任务误绑文档技能”，而不是等到底层 schema 校验时报错。
    requires_documents: bool = False
    side_effect: str = "none"
    recipe: list[RecipeStep] = Field(default_factory=list)
    instructions_path: str | None = None


class ToolDefinition(BaseModel):
    id: str
    version: str
    name: str
    provider: Literal["local", "http", "mcp"]
    entrypoint: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    network: Literal["none", "required"] = "none"
    allowed_hosts: list[str] = Field(default_factory=list)
    data_egress: Literal["none", "query", "derived", "raw"] = "none"
    timeout_seconds: float = 30
    requires_confirmation: bool = False
    # 该 Tool 是否消耗预算网关的模型额度（如视觉理解）。
    uses_model_budget: bool = False


class McpServerDefinition(BaseModel):
    id: str
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    enabled: bool = False
    tool_allowlist: list[str] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    startup_timeout_seconds: float = 20
    call_timeout_seconds: float = 90
