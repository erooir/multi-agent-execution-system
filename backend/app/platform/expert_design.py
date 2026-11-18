"""Build reviewable expert drafts from a constrained, offline interview."""

from typing import Literal

from pydantic import Field

from . import service
from .contracts import AgentInput, Contract, ManagementProfile
from .service import CAPABILITIES

# The operator's requested deliverable selects one existing executable, including its input contract.
OUTPUT_METHODS = {
    "records": "structured_transform",
    "excerpts": "source_summary",
    "transcription": "image_ocr",
    "image_properties": "image_inspect",
}


class ExpertInterview(Contract):
    project_id: str
    business: Literal["science", "historical", "public_sources", "general"]
    reason: str = Field(min_length=2, max_length=500)
    deliverable: Literal["records", "excerpts", "transcription", "image_properties"]
    name: str = Field(default="", max_length=100)


def draft(interview: ExpertInterview) -> AgentInput:
    method = OUTPUT_METHODS[interview.deliverable]
    capability = next(item for item in CAPABILITIES if item["id"] == method)
    return AgentInput(
        project_id=interview.project_id,
        name=interview.name or capability["name"] + "专家",
        description=interview.reason,
        capabilities=[method],
        tool_ids=[capability["tool_id"]],
        modalities=capability["modalities"],
        management_profile=ManagementProfile(
            business=interview.business, creation_method="interview", reason=interview.reason
        ),
    )


class ConfirmInterview(ExpertInterview):
    confirmation_id: str = Field(min_length=16, max_length=80, pattern=r"^[a-zA-Z0-9-]+$")


def confirm(interview, user, repository):
    design = draft(interview).model_dump()
    key = service.digest([user["id"], interview.project_id, interview.confirmation_id])
    signature = service.digest(design)
    with repository.transaction() as tx:
        service.project_access(interview.project_id, user, True, tx)
        saved = tx.get("audits", "expert-" + key)
        if saved:
            if saved["detail"]["design_hash"] != signature:
                service.fail("idempotency_conflict", "确认标识已用于另一份配置", 409)
            return service.required("agents", saved["entity_id"], user, repository=tx)
        agent = service.save_agent(design, user, repository=tx, interview=True)
        tx.put(
            "audits",
            {
                "id": "expert-" + key,
                "project_id": interview.project_id,
                "action": "expert.confirmed",
                "actor": user["id"],
                "entity_id": agent["id"],
                "detail": {"design_hash": signature},
            },
        )
        return agent
