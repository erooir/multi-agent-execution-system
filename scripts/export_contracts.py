"""Export strict public integration contracts; contains no runtime or private input."""

import json
from pathlib import Path

from backend.app.platform.contracts import AgentInput, AssetInput, ResultBundle, TaskInput, WorkflowInput


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "contracts"
    output.mkdir(parents=True, exist_ok=True)
    for name, model in {
        "agent_spec": AgentInput,
        "data_asset": AssetInput,
        "task_request": TaskInput,
        "workflow_spec": WorkflowInput,
        "result_bundle": ResultBundle,
    }.items():
        (output / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
