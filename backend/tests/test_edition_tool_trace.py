"""The edition preparation node must expose its real fixed tool without fabricating model calls."""

import hashlib

import pytest

from backend.app.casework import edition_checks, edition_verify, editions
from backend.app.platform import asset_originals, runtime, service
from backend.app.platform.tool_registry import digest
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import setup
from backend.tests.test_v2_platform import ADMIN, asset, task_body

db = fixtures.db


@pytest.mark.parametrize("reported_date", ["2022-04", "2022-04-01", "throw"])
async def test_edition_prepare_records_actual_verification_before_model(
    db, monkeypatch, tmp_path, reported_date
):
    project = setup(db, monkeypatch)
    monkeypatch.setattr(asset_originals, "ROOT", tmp_path)
    raw = (
        "program,milestone_date,subject,event\n演示项目,2022-04,演示生产方,Production Certificate\n".encode()
    )
    fingerprint = hashlib.sha256(raw).hexdigest()
    name = "T06-AN01.csv"
    original = {"filename": name, "bytes": len(raw), "sha256": fingerprint}
    path = asset_originals.original_path(project["id"], fingerprint)
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    row = {
        "项目": "演示项目",
        "日期": reported_date,
        "主体": "演示生产方",
        "节点": "Production Certificate",
        "泳道": "单机及生产",
        "evidence_ids": ["E1"],
    }
    source = asset(
        db,
        project,
        {
            "case_id": "T06",
            "records": [{"id": "E1", "text": "演示项目记录", "locator": "第2行"}],
            "products": {"timeline": [row]},
        },
    )
    source = db.put("data_assets", {**source, "original_file": original})
    case = {
        "id": "T06",
        "title": "民航合成资料",
        "goal": "核对取证时间",
        "deliverables": "时间线",
        "completion": "逐项核对",
        "scope": "单份公开表格",
        "files": [name],
        "input_refs": [source["id"]],
        "checksums": {source["id"]: source["checksum"]},
    }
    db.put("projects", {**db.get("projects", project["id"]), "scenario_edition": {"cases": {"T06": case}}})
    with db.transaction() as tx:
        editions.provision(tx, project["id"], case)
    service.create_task(
        task_body(
            project,
            source,
            task_type="case_analysis",
            case_scope="project",
            case_id="T06",
            mode="cloud",
            max_cost_cny=1,
        ),
        ADMIN,
        db,
    )
    claim = db.claim("edition-worker")
    execution = runtime.Execution(db, claim, "edition-worker", claim["lease_token"])
    plan = claim["snapshot"]["workflow"]["nodes"][0]
    agent = next(a for a in claim["snapshot"]["agents"] if a["agent_id"] == plan["agent_id"])
    execution.active_node_id = "prepare"
    with (
        pytest.raises(runtime.TaskFailure, match="授权"),
        edition_verify.bind_source_check(execution, agent, set(), 0, "instance"),
    ):
        pytest.fail("ungranted tool executed")
    assert not execution.current().get("tool_calls")

    async def ask(*args, **kwargs):
        assert [x["status"] for x in execution.current()["tool_calls"]] == ["completed"]
        return {"text": '{"approach":"核对时间", "evidence_ids":["E1"]}'}

    monkeypatch.setattr(runtime, "_model", ask)
    if reported_date == "throw":

        def stop(*args):
            raise runtime.TaskFailure("input_changed", "原件已变化")

        monkeypatch.setattr(edition_checks, "verify_sources", stop)
        with pytest.raises(runtime.TaskFailure, match="原件已变化"):
            await runtime._run_atomic(execution, plan, agent, [], 0)
    else:
        output = await runtime._run_atomic(execution, plan, agent, [], 0)
        assert await runtime._run_atomic(execution, plan, agent, [], 0) == output
    task = execution.current()
    (call,) = task["tool_calls"]
    (node,) = db.list("nodes", task_id=claim["id"])
    assert (call["node_id"], call["instance_id"], call["revision"], call["kind"]) == (
        "prepare",
        node["instance_id"],
        0,
        "tool",
    )
    assert call["capability_id"] == "case_analysis" and call["version"] == editions.implementation("T06")
    assert call["source_refs"] == [source["id"]] and call["summary"]["invocation_mode"] == "fixed_workflow"
    checkpoint = task["tool_checkpoints"][call["id"]]
    assert checkpoint["arguments"] == {"case_id": "T06", "input_refs": [source["id"]]}
    status = call["status"]
    if reported_date == "throw":
        assert status == "failed" and call["error_code"] == "input_changed"
        assert call["result_checksum"] is None and checkpoint["result"] is None
    else:
        assert status == "completed" and call["result_checksum"] == digest(checkpoint["result"])
        assert checkpoint["result"]["checks"] == output["content"]["source_checks"]
        assert (call["summary"]["failed_checks"] > 0) == (reported_date != "2022-04")
    assert [e["type"] for e in db.list("events", task_id=claim["id"]) if e["type"].startswith("tool.")] == [
        "tool.started",
        "tool." + status,
    ]
