"""Strict v2 request contracts, shared by OpenAPI and runtime validation."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskType = Literal[
    "structured_transform",
    "source_summary",
    "text_analysis",
    "image_inspect",
    "audio_transcribe",
    "video_analyze",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class MemberInput(Contract):
    members: list[str] = Field(max_length=100)


class Member(Contract):
    agent_id: str
    version: int = Field(ge=1)


class ToolParameters(Contract):
    operation: Literal["normalize", "select", "deduplicate", "sort", "filter"] = "normalize"
    select_fields: list[str] | None = Field(default=None, max_length=50)
    filter: dict[str, str | int | float | bool | None] | None = None
    sort_by: str | None = None
    descending: bool = False
    deduplicate: bool = False
    max_chars: int = Field(default=2000, ge=100, le=20000)


class AgentInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    purpose: Literal["general", "specialized"] = "general"
    structure: Literal["atomic", "composite"] = "atomic"
    capabilities: list[TaskType] = Field(min_length=1, max_length=6)
    modalities: list[Literal["text", "structured", "image", "audio", "video"]] = Field(
        default_factory=lambda: ["text"]
    )
    tool_ids: list[str] = Field(default_factory=list, max_length=10)
    instructions: str = Field(default="", max_length=8000)
    model_policy: Literal["local", "cloud"] = "local"
    members: list[Member] = Field(default_factory=list, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkflowNode(Contract):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    agent_id: str
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    task_type: TaskType
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=30)


class AssetInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=150)
    modality: Literal["text", "structured", "image", "audio", "video"]
    media_type: str = Field(default="text/plain", max_length=100)
    content: Any
    source_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(default="1", max_length=100)
    provenance: str = Field(min_length=1, max_length=1000)
    classification: Literal["local", "public"] = "local"
    retention_days: int = Field(default=30, ge=1, le=3650)
    expires_at: str | None = None


class Quality(Contract):
    min_records: int = Field(default=1, ge=0, le=100000)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    min_length: int = Field(default=1, ge=0, le=100000)


class TaskInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=8000)
    task_type: TaskType
    input_refs: list[str] = Field(default_factory=list, max_length=50)
    workflow_id: str | None = None
    mode: Literal["local", "cloud", "rehearsal"] = "local"
    idempotency_key: str = Field(min_length=1, max_length=160)
    deadline_seconds: int = Field(default=300, ge=1, le=86400)
    max_cost_cny: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    max_revisions: int = Field(default=1, ge=0, le=2)
    allow_partial: bool = False
    quality: Quality = Field(default_factory=Quality)


class ScheduleInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(ge=60, le=31536000)
    timezone: str = "Asia/Shanghai"
    task_template: TaskInput
    next_run_at: str | None = None


class ConnectorInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["local_assets", "http_knowledge"] = "local_assets"
    base_url: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)


class KnowledgeQuery(Contract):
    project_id: str
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryInput(Contract):
    project_id: str
    content: str = Field(min_length=1, max_length=10000)
    source_refs: list[str] = Field(min_length=1, max_length=50)
    retention_days: int = Field(default=7, ge=1, le=365)


class ImprovementInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    agent_id: str
    instructions: str = Field(max_length=8000)
    tool_ids: list[str] = Field(default_factory=list, max_length=10)
    sample_task_ids: list[str] = Field(min_length=2, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResultBundle(Contract):
    task_id: str
    status: Literal["succeeded", "partial", "failed", "cancelled"]
    mode: Literal["local", "cloud", "rehearsal"]
    reason_code: str | None = None
    artifacts: list[Any] = Field(default_factory=list)
    quality_verdict: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    lineage: list[Any] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    version_refs: list[Any] = Field(default_factory=list)
    content_withheld: bool = False
    withheld_reason: str | None = None
    unavailable_source_refs: list[str] = Field(default_factory=list)
