"""Create agent assets from text through the same governed save path as manual creation."""

import json
from typing import Literal

from pydantic import AliasChoices, ConfigDict, Field, ValidationError

from ..model_gateway import BudgetExceeded, ModelCallError, ModelOutputTruncated, model_gateway, request_scope
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


class TextAgentRequest(Contract):
    project_id: str
    prompt: str = Field(min_length=5, max_length=5000)
    idempotency_key: str = Field(min_length=16, max_length=80, pattern=r"^[a-zA-Z0-9-]+$")


class TextAgentDesign(Contract):
    # The provider can return either spelling. Only these fields are used to create an asset.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(
        min_length=3, max_length=3000, validation_alias=AliasChoices("instructions", "instruction")
    )
    capabilities: list[str] = Field(min_length=1, max_length=4)
    business: Literal["general", "battlefield", "science", "geographic"]
    reason: str = Field(
        min_length=2, max_length=500, validation_alias=AliasChoices("reason", "category_reason")
    )
    model_policy: Literal["local", "cloud"] | None = None


async def create_from_text(request, user, repository):
    service.project_access(request.project_id, user, True, repository)
    run_id = "design-" + service.digest([user["id"], request.project_id, request.idempotency_key])
    signature = service.digest(request.prompt)

    def saved_agent(tx):
        receipt = tx.get("audits", run_id)
        if receipt:
            if receipt["detail"]["request_hash"] != signature:
                service.fail("idempotency_conflict", "此请求标识已用于另一段创建描述", 409)
            return service.required("agents", receipt["entity_id"], user, repository=tx)
        return None

    if saved := saved_agent(repository):
        return saved
    capabilities = [
        item
        for item in CAPABILITIES
        if item["available"]
        and item["tool_id"] in service.TOOLS
        and item["id"] not in {"case_analysis", "autonomous_analysis"}
    ]
    by_id = {item["id"]: item for item in capabilities}
    correction = ""
    for _ in range(2):
        try:
            with request_scope(run_id, 1, True):
                result = await model_gateway.complete(
                    json.dumps(
                        {
                            "request": request.prompt,
                            "available_capabilities": capabilities,
                            "previous_error": correction,
                        },
                        ensure_ascii=False,
                    ),
                    system=(
                        "把描述转换为 TextAgentDesign JSON。填写中文名称、简短说明、可执行指令以及分类理由。"
                        "capabilities 只填 available_capabilities 中的 id。business 填 general、battlefield、science 或 geographic。"
                        "model_policy 使用所选能力共有的模式。项目、输入模态和工具由系统绑定。"
                    ),
                    purpose="agent_design",
                    run_id=run_id,
                    output_schema=TextAgentDesign,
                    json_mode=True,
                )
            proposed = TextAgentDesign.model_validate_json(result["text"])
            chosen = list(dict.fromkeys(proposed.capabilities))
            matches = [by_id[key] for key in chosen]
            modes = set.intersection(*(set(item["modes"]) for item in matches)) - {"rehearsal"}
            mode = proposed.model_policy or ("local" if "local" in modes else "cloud")
            if mode not in modes:
                raise ValueError("能力与执行模式不匹配")
            design = AgentInput(
                project_id=request.project_id,
                name=proposed.name,
                description=proposed.description,
                instructions=proposed.instructions,
                model_policy=mode,
                capabilities=chosen,
                modalities=list(dict.fromkeys(value for item in matches for value in item["modalities"])),
                tool_ids=list(dict.fromkeys(item["tool_id"] for item in matches)),
                management_profile=ManagementProfile(business=proposed.business, reason=proposed.reason),
            ).model_dump()
            with repository.transaction() as tx:
                service.project_access(request.project_id, user, True, tx)
                if saved := saved_agent(tx):
                    return saved
                agent = service.save_agent(design, user, repository=tx, interview=True)
                tx.put(
                    "audits",
                    {
                        "id": run_id,
                        "project_id": request.project_id,
                        "entity_id": agent["id"],
                        "action": "agent.created_from_text",
                        "actor": user["id"],
                        "detail": {"request_hash": signature},
                    },
                )
                return agent
        except (ModelCallError, BudgetExceeded) as exc:
            service.fail(getattr(exc, "code", "budget_exhausted"), str(exc), 422)
        except (
            ValidationError,
            ModelOutputTruncated,
            KeyError,
            TypeError,
            AttributeError,
            ValueError,
        ) as exc:
            if isinstance(exc, service.DomainError) and exc.status in {403, 404, 409}:
                raise
            correction = "请检查 JSON、目录能力、执行模式、业务方向与必填字段"
            if isinstance(exc, ValidationError):
                correction += "。需修正字段：" + ", ".join(
                    str(item["loc"][0]) for item in exc.errors() if item["loc"]
                )
    service.fail("invalid_agent_design", "模型未生成可保存的智能体配置，请补充职责和预期交付", 422)


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
