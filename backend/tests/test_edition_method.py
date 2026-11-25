import json
from types import SimpleNamespace

import pytest

from backend.app.casework import edition_guidance
from backend.app.casework import edition_method as method
from backend.app.platform.runtime import TaskFailure


def execution_and_outputs(signature):
    check = {"name": "table_totals", "passed": True, "message": "仅复核表中覆盖数量。", "hard": True}
    prepared = {
        "type": "case_records",
        "content": {"source_checks": [check], "file_context": {"核算表.csv": "摘录"}},
    }
    candidate = {
        "type": "case_candidate",
        "content": {
            "findings": [{"heading": "面积来源", "text": "面积计算基于overlay_area，输出空间覆盖数。"}]
        },
    }
    base = {"status": "completed", "result_checksum": "result", "revision": 1, "instance_id": "now"}
    task = {
        "snapshot": {
            "workflow": {"edition_guidance": signature},
            "agents": [
                {
                    "runtime_bindings": {
                        "tools": [
                            {"id": "geo.overlay_area", "name": "overlay_area"},
                            {"id": "document.extract", "name": "document_extract"},
                        ]
                    }
                }
            ],
        },
        "tool_calls": [
            {
                **base,
                "kind": "tool",
                "node_id": "prepare",
                "capability_id": "case_analysis",
                "revision": 0,
                "instance_id": "reused-preparation",
            },
            {**base, "kind": "tool", "node_id": "analyze", "capability_id": "document.extract"},
            {
                **base,
                "kind": "tool",
                "node_id": "analyze",
                "capability_id": "geo.overlay_area",
                "revision": 0,
                "instance_id": "discarded-draft",
            },
            {
                **base,
                "kind": "tool",
                "node_id": "review",
                "capability_id": "geo.overlay_area",
                "status": "failed",
            },
            {**base, "kind": "skill", "node_id": "review", "capability_id": "report-delivery"},
        ],
    }
    execution = SimpleNamespace(
        task=task,
        current=lambda: task,
        active_node_id="review",
        inputs=[{"original_file": {"filename": "测试表.csv"}}],
        output_origins={id(prepared): {"instance_id": "reused-preparation"}},
    )
    return execution, [prepared, candidate]


@pytest.mark.parametrize(
    "statement",
    [
        "本次尚未调用 overlay_area。",
        "建议调用 overlay_area。",
        "资料说明面积计算基于 overlay_area。",
        "面积计算基于 overlay_area 的说法缺乏证据。",
        "若面积计算采用 overlay_area，可再复核。",
        "后续可核查几何有效性。",
        "已使用了 document_extract。",
        "“本次运行了 overlay_area”只是待核实的引述。",
    ],
)
def test_tool_names_in_limitations_attributions_and_real_calls_are_allowed(statement):
    assert not method._unsupported_claims([{"heading": "局限", "text": statement}], {"overlay_area"})


async def test_frozen_method_routes_consistently_and_grounds_review_in_actual_tools(monkeypatch):
    async def read(*args):
        return edition_guidance.StageGuidance("只读方法说明", {"抽核结果": ["已定位"]})

    monkeypatch.setattr(edition_guidance, "stage_guidance", read)
    old = edition_guidance.guidance_signature()
    # First deployed workflow. Its checks and wording must remain runnable byte-for-byte.
    assert old == "dc261b53af9fc6cab8a83ec64200049e416bfa20799fed0d97ba5fa55e09e606"
    assert method.guidance_signature() != old
    for signature in [old, method.guidance_signature(), "unknown"]:
        execution, previous = execution_and_outputs(signature)
        args = (execution, {}, set(), 1, "now", previous)
        if signature == "unknown":
            with pytest.raises(TaskFailure, match="冻结"):
                await method.stage_guidance(*args)
            continue
        guide = await method.stage_guidance(*args)
        system, prompt = guide.apply("依据公开资料。", '{"正文":[]}')
        payload = json.loads(prompt)
        if signature == old:
            assert type(guide) is edition_guidance.StageGuidance
            assert "本次执行依据" not in payload and "成功的工具轨迹" not in system
            continue
        facts = payload["本次执行依据"]
        assert [(i["轮次"], i["类型"], i["能力ID"]) for i in facts["成功调用"]] == [
            (1, "tool", "case_analysis"),
            (2, "tool", "document.extract"),
            (2, "skill", "report-delivery"),
        ]
        assert facts["原件核查项目与实测说明"] == previous[0]["content"]["source_checks"]
        assert facts["已核验的研究附件文件名"] == ["核算表.csv"]
        assert "原件及‘本次执行依据’" in system and "独立审核" in system
        assert payload["抽核结果"] and facts["本次输入原件"] == ["测试表.csv"]
        report = {"type": "case_report", "content": {"review": [], "gaps": []}}
        guide.check_report(report)
        (failed,) = report["content"]["review"]
        assert not failed["passed"] and failed["hard"] and "overlay_area" in failed["message"]
        assert report["content"]["gaps"] == [failed["message"]]
        execution.active_node_id = "analyze"
        analyze = await method.stage_guidance(*args[:-1], previous[:1])
        analysis_system, analysis_prompt = analyze.apply("分析。", '{"资料":{}}')
        assert "独立审核请对照" not in analysis_system
        assert json.loads(analysis_prompt)["本次执行依据"]["原件核查项目与实测说明"]
