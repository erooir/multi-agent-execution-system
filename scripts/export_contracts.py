"""Export strict public integration contracts; contains no runtime or private input."""

import json
from pathlib import Path

from backend.app.platform.contracts import (
    AgentInput,
    AgentTextResult,
    AssetInput,
    AutonomousTextResult,
    CapabilityCatalog,
    HttpToolInput,
    ImageUnderstandingResult,
    McpServerInput,
    ModelQualityReview,
    NativeToolkitInput,
    OcrResult,
    ResultBundle,
    TaskInput,
    TaskPreflight,
    ToolCallRecord,
    ToolResult,
    WorkflowInput,
)
from backend.app.platform.task_case_contracts import (
    CandidateEvaluationInput,
    DataSubmissionInput,
    ExecutionPlan,
    InlineSubmissionInput,
    RequirementAnalysis,
    TaskCaseInput,
)


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "contracts"
    output.mkdir(parents=True, exist_ok=True)
    for name, model in {
        "agent_spec": AgentInput,
        "data_asset": AssetInput,
        "task_request": TaskInput,
        "workflow_spec": WorkflowInput,
        "result_bundle": ResultBundle,
        "tool_call": ToolCallRecord,
        "tool_result": ToolResult,
        "capability_catalog": CapabilityCatalog,
        "task_preflight": TaskPreflight,
        "agent_text_result": AgentTextResult,
        "autonomous_text_result": AutonomousTextResult,
        "image_understanding_result": ImageUnderstandingResult,
        "ocr_result": OcrResult,
        "http_tool_input": HttpToolInput,
        "mcp_server_input": McpServerInput,
        "native_toolkit_input": NativeToolkitInput,
        "model_quality_review": ModelQualityReview,
        "task_case": TaskCaseInput,
        "data_submission": DataSubmissionInput,
        "customer_data_submission": InlineSubmissionInput,
        "execution_plan": ExecutionPlan,
        "requirement_analysis": RequirementAnalysis,
        "candidate_evaluation": CandidateEvaluationInput,
    }.items():
        (output / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    from backend.app.main import app

    (output / "openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
