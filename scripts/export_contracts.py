"""Export strict public integration contracts; contains no runtime or private input."""

import json
from pathlib import Path

from backend.app.platform.contracts import (
    AgentInput,
    AgentTextResult,
    AssetInput,
    CapabilityCatalog,
    ModelQualityReview,
    ResultBundle,
    TaskInput,
    TaskPreflight,
    ToolCallRecord,
    ToolResult,
    WorkflowInput,
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
        "model_quality_review": ModelQualityReview,
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
