"""User task and customer data intake contracts; no executor choices are accepted."""

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract


class TaskCaseInput(Contract):
    project_id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=100)
    goal: str = Field(min_length=3, max_length=7000)
    output_requirements: str = Field(default="", max_length=1000)
    data_requirement: Literal["auto", "none", "required"] = "auto"
    mode: Literal["cloud", "local"] = "cloud"
    max_cost_cny: float = Field(default=2, ge=0, le=10, allow_inf_nan=False)
    public_egress_authorized: bool = False
    idempotency_key: str = Field(min_length=1, max_length=140)


class DataSubmissionInput(Contract):
    input_refs: list[str] = Field(default_factory=list, max_length=50)
    requirement_ids: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=140)
    public_egress_authorized: bool | None = None


class InlineSubmissionInput(Contract):
    name: str = Field(min_length=1, max_length=150)
    content: str | dict | list
    media_type: Literal["text/plain", "text/csv", "application/json"] = "application/json"
    classification: Literal["local", "public"] = "local"
    source_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(default="1", min_length=1, max_length=100)
    provenance: str = Field(min_length=1, max_length=1000)
    requirement_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=140)


class PlanningStep(Contract):
    label: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=3000)
    tool_ids: list[str] = Field(default_factory=list, max_length=16)
    skill_ids: list[str] = Field(default_factory=list, max_length=5)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    reuse_agent_id: str | None = Field(default=None, max_length=100)


class ExecutionPlan(Contract):
    rationale: str = Field(min_length=1, max_length=2000)
    steps: list[PlanningStep] = Field(min_length=1, max_length=4)
    missing_data: list[str] = Field(default_factory=list, max_length=10)


class CandidateEvaluationInput(Contract):
    task_case_ids: list[str] = Field(min_length=2, max_length=10)
    max_cost_cny: float = Field(default=4, ge=0.2, le=10, allow_inf_nan=False)


class IngestTokenInput(Contract):
    expires_hours: int = Field(default=24, ge=1, le=168)


class AnalyzedDataRequirement(Contract):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    title: str = Field(min_length=1, max_length=100)
    required: bool = True
    description: str = Field(min_length=1, max_length=1500)
    formats: list[str] = Field(min_length=1, max_length=14)
    fields: list[str] = Field(default_factory=list, max_length=30)
    time_scope: str = Field(default="由任务目标或资料明确", max_length=300)
    spatial_scope: str = Field(default="不适用", max_length=300)
    unit_scope: str = Field(default="依照来源原始口径，不混用单位", max_length=300)


class RequirementAnalysis(Contract):
    summary: str = Field(min_length=1, max_length=2000)
    needs_data: bool
    requirements: list[AnalyzedDataRequirement] = Field(default_factory=list, max_length=8)
    assumptions: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def coherent_requirements(self):
        if len({r.id for r in self.requirements}) != len(self.requirements):
            raise ValueError("duplicate_requirement_id")
        if self.needs_data and not any(r.required for r in self.requirements):
            raise ValueError("required_data_missing")
        if not self.needs_data and any(r.required for r in self.requirements):
            raise ValueError("data_free_with_required_input")
        return self
