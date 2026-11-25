import json

import pytest

from backend.app.casework import edition_guidance, editions
from backend.app.platform import runtime, service
from backend.app.platform.quality import text_of
from backend.app.platform.tool_registry import permissions
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import setup
from backend.tests.test_v2_platform import ADMIN, asset, task_body

db = fixtures.db


async def test_governed_stage_reads_drive_model_inputs_and_reuse_interrupted_node(db, monkeypatch):
    project = setup(db, monkeypatch)
    inputs = []
    for index in range(11):
        records = [{"id": f"E{index}", "text": '合成 "共同观察"', "locator": f"第{index}行"}]
        if index == 0:
            records.append({**records[0], "id": "E0b", "locator": "同一文件另一位置"})
        name = f"公开档案{index}.csv"
        source = asset(db, project, {"case_id": "B03", "records": records}, name=name, source_id=name)
        inputs.append(db.put("data_assets", {**source, "original_file": {"filename": name}}))
    refs = [item["id"] for item in inputs]
    case = {
        "id": "B03",
        "title": "民用馆藏观察",
        "goal": "对照照片索引",
        "deliverables": "研究简报",
        "completion": "逐项核对",
        "scope": "资料范围",
        "files": [item["name"] for item in inputs],
        "input_refs": refs,
        "checksums": {item["id"]: item["checksum"] for item in inputs},
    }
    db.put("projects", {**db.get("projects", project["id"]), "scenario_edition": {"cases": {"B03": case}}})
    with db.transaction() as tx:
        editions.provision(tx, project["id"], case)
    service.create_task(
        task_body(
            project,
            inputs[0],
            task_type="case_analysis",
            case_scope="project",
            input_refs=refs,
            case_id="B03",
            mode="cloud",
            max_cost_cny=1,
        ),
        ADMIN,
        db,
    )
    task = db.claim("edition")
    execution = runtime.Execution(db, task, "edition", task["lease_token"])
    prepared = {
        "type": "case_records",
        "content": {"approach": "对照", "source_checks": [], "file_context": {}},
    }
    outputs = [prepared]
    execution.output_origins[id(prepared)] = {"node_id": "prepare", "type": "case_records"}
    findings = [
        {"heading": f"合成发现{i}", "text": f"{i}条发现的合成说明" * 10, "evidence_ids": [eid]}
        for i, eid in enumerate(["E0", "E0b", "E2", "E2"])
    ]

    async def complete(prompt, *, system, purpose, **kwargs):
        payload = json.loads(prompt)
        if purpose.endswith("analyze"):
            assert "# 文档对比" in system or "compare_passages" in system
            rows = payload["证据"]
            assert len(rows) == 12 and {row["原件位置"] for row in rows} == {
                row["locator"] for item in inputs for row in item["content"]["records"]
            }
            reply = {"findings": findings}
        else:
            assert "# 结果报告与交付" in system
            checks = payload["各项发现首条引用的冻结索引抽核"]
            assert [check["证据编号"] for check in checks] == ["E0", "E0b", "E2"]
            for item in checks:
                source = next(source for source in inputs if source["id"] == item["来源"])
                start, end = item["索引字符区间"]
                matched = json.loads(text_of(source)[start:end])
                assert matched["id"] == item["证据编号"] and matched["locator"] == item["原件位置"]
            reply = {"passed": True, "reason": "已核对合成引用位置"}
        return {"text": json.dumps(reply, ensure_ascii=False), "request_id": "model-fixture"}

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    for node in task["snapshot"]["workflow"]["nodes"][1:]:
        agent = next(a for a in task["snapshot"]["agents"] if a["agent_id"] == node["agent_id"])
        output = await runtime._run_atomic(execution, node, agent, outputs, 0)
        calls = [c for c in execution.current()["tool_calls"] if c["node_id"] == node["id"]]
        assert all(
            c["status"] == "completed" and c["summary"]["invocation_mode"] == "fixed_workflow" for c in calls
        )
        if node["id"] == "analyze":
            assert len(calls) == 12 and calls[0]["capability_id"] == "document-comparison"
            assert all(c["capability_id"] == "document.extract" for c in calls[1:])
            # A recovered process uses the fenced checkpoints of already-read sources even at the node cap.
            await edition_guidance.stage_guidance(
                execution, agent, permissions(agent), 0, "replacement", outputs
            )
            assert len(execution.current()["tool_calls"]) == 12
        else:
            assert [c["capability_id"] for c in calls] == ["report-delivery", *(["data.locate_evidence"] * 3)]
            assert all(c["source_refs"] for c in calls[1:])
            assert output["content"]["review"][-1]["passed"]
        outputs.append(output)

    service.revoke_asset(refs[0], ADMIN, db)
    with pytest.raises(runtime.TaskFailure, match="撤回"):
        await edition_guidance.stage_guidance(execution, agent, permissions(agent), 0, "replacement", outputs)
