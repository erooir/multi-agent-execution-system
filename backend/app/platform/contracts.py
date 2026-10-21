"""Strict v2 request contracts, shared by OpenAPI and runtime validation."""

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

TaskType = Literal[
    "structured_transform",
    "source_summary",
    "text_analysis",
    "image_inspect",
    "audio_transcribe",
    "video_analyze",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


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


class ToolReference(Contract):
    tool_id: str = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1)


class SkillReference(Contract):
    skill_id: str = Field(min_length=1, max_length=100)
    version: str = "1.0.0"


class ToolCallRecord(Contract):
    id: str
    task_id: str
    instance_id: str | None = None
    node_id: str | None = None
    revision: int = 0
    kind: Literal["tool", "mcp", "skill"]
    capability_id: str
    version: str
    status: Literal["running", "completed", "failed"]
    started_at: str
    duration_ms: int = 0
    argument_names: list[str] = Field(default_factory=list)
    arguments_checksum: str
    source_refs: list[str] = Field(default_factory=list)
    result_checksum: str | None = None
    error_code: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class ToolResult(Contract):
    data: dict[str, Any]
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SourceCitation(Contract):
    # Explanatory provider fields are discarded, never published as verified claims.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)
    kind: Literal["source_span", "source_quote"] = Field(
        default="source_span", validation_alias=AliasChoices("kind", "type")
    )
    source_id: str
    source_version: str
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)
    quote: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="before")
    @classmethod
    def normalize_source_label(cls, value):
        # Providers commonly use "source" for a span. Only normalize the label;
        # source version, exact offsets and verbatim quote remain mandatory.
        if isinstance(value, dict) and value.get("kind", value.get("type")) == "source":
            return {**value, "kind": "source_span"}
        return value


class DerivedCitation(Contract):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["derived_result"] = Field(validation_alias=AliasChoices("kind", "type"))
    tool_call_id: str
    source_id: str
    result_checksum: str


class AgentTextResult(Contract):
    text: str = Field(min_length=1, max_length=50000)
    citations: list[SourceCitation | DerivedCitation] = Field(min_length=1, max_length=100)
    missing_information: list[str] = Field(default_factory=list, max_length=100)


class ToolCallDetail(Contract):
    call: ToolCallRecord
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class SourceRead(Contract):
    source_id: str
    start: int = Field(default=0, ge=0)
    length: int = Field(default=2000, ge=1, le=12000)


class SourceSearch(Contract):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class EvidenceLocate(Contract):
    source_id: str
    quote: str = Field(min_length=1, max_length=4000)


class SkillRead(Contract):
    skill_id: str = Field(min_length=1, max_length=100)


class SkillReferenceRead(SkillRead):
    reference: str = Field(min_length=1, max_length=200)


class TableOperation(Contract):
    source_id: str
    operation: Literal["filter", "sort", "deduplicate"] = "filter"
    where: dict[str, str | int | float | bool | None] | None = None
    sort_by: str | None = None
    descending: bool = False
    sort_type: Literal["auto", "numeric", "text"] = "auto"
    limit: int = Field(default=100, ge=1, le=200)


class TableAggregate(Contract):
    source_id: str
    metric: Literal["count", "sum", "average", "min", "max"] = "count"
    value_field: str | None = None
    group_by: str | None = None
    where: dict[str, str | int | float | bool | None] | None = None


class CapabilityCatalog(Contract):
    capabilities: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    mcp_servers: list[dict[str, Any]]


class TaskPreflight(Contract):
    ready: bool
    existing_task_id: str | None = None
    selected_agents: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    execution_strategy: Literal["fixed", "tool_agent"] = "fixed"
    tool_refs: list[ToolReference] = Field(default_factory=list, max_length=10)
    skill_refs: list[SkillReference] = Field(default_factory=list, max_length=5)


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


class ModelQualityReview(Contract):
    verdict: Literal["passed", "failed"]
    issues: list[str]
    evidence_refs: list[str] = Field(
        min_length=1, description="只填写 allowed_evidence_refs 中的资料ID，不填写工具调用ID或说明文字"
    )
    suggested_changes: list[str]


class TaskInput(Contract):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=8000)
    task_type: TaskType
    input_refs: list[str] = Field(default_factory=list, max_length=50)
    workflow_id: str | None = None
    mode: Literal["local", "cloud", "rehearsal"] = "local"
    service_class: Literal["batch", "interactive"] = Field(
        default="batch", description="交互任务优先；批量任务等待60秒后提升，不抢占运行任务，不承诺硬实时SLA"
    )
    idempotency_key: str = Field(min_length=1, max_length=160)
    deadline_seconds: int = Field(default=300, ge=1, le=86400)
    max_cost_cny: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    max_revisions: int = Field(default=1, ge=0, le=2)
    allow_partial: bool = False
    use_project_knowledge: bool = False
    quality: Quality = Field(default_factory=Quality)


class MetricsWindow(Contract):
    start: str
    end: str
    hours: int


class TaskCounts(Contract):
    total: int
    queued: int
    active: int
    terminal: int


class QueueMetrics(Contract):
    waiting_count: int
    oldest_wait_seconds: float | None
    by_service_class: dict[str, int]


class LatencyMetrics(Contract):
    sample_count: int
    p50_seconds: float | None
    p95_seconds: float | None
    excluded_count: int = Field(description="缺少完成时间的全部可见终态记录，以及窗口内开始时间无效的记录数")
    definition: str = "受理至终态的实际耗时，包含排队；按 finished_at 落入窗口取样"


class FailureCount(Contract):
    reason_code: str
    count: int


class WorkerMetrics(Contract):
    scope: Literal["global", "project_associated"]
    total: int
    online: int
    stale: int
    stopped: int
    stale_after_seconds: int = 45


class SchedulingPolicy(Contract):
    policy_version: str
    aging_seconds: int
    preemptive: bool = False
    order: list[str]


class OperationalMetrics(Contract):
    generated_at: str
    window: MetricsWindow
    project_ids: list[str]
    include_rehearsal: bool
    excluded_rehearsal_count: int
    counts_scope: str = "可见项目全部当前记录；排队/运行是当前状态，终态耗时和失败原因按窗口取样"
    counts: TaskCounts
    status_counts: dict[str, int]
    queue: QueueMetrics
    latency: LatencyMetrics
    failure_reasons: list[FailureCount]
    workers: WorkerMetrics
    scheduling: SchedulingPolicy


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
