"""Real TaskCase intake, authorization and worker behavior on synthetic data."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import runtime, service, task_case_api, task_cases
from backend.app.platform.contracts import AutonomousTextResult
from backend.app.platform.task_case_contracts import InlineSubmissionInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project

db = fixtures.db
REQUESTER = {"id": "task_customer", "username": "task_customer", "role": "requester", "enabled": True}


def setup(db, monkeypatch):
    p = project(db)
    db.put("projects", {**p, "members": [ADMIN["id"], REQUESTER["id"]]})
    with auth.store.transaction() as tx:
        auth._insert_record(tx, "users", dict(REQUESTER))
    monkeypatch.setattr(task_case_api, "repo", db)
    monkeypatch.setattr(
        importlib.import_module("backend.app.model_gateway"), "model_gateway", runtime.model_gateway
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY_ZX", "synthetic-never-dispatched")
    monkeypatch.delenv("HANGZHI_OFFLINE", raising=False)
    return p


def create(db, p, **changes):
    return task_cases.create(
        {
            "project_id": p["id"],
            "goal": "请分析参考资料并总结关键事实",
            "idempotency_key": "task-one",
            "mode": "cloud",
            "max_cost_cny": 2,
            **changes,
        },
        REQUESTER,
        db,
    )


def no_data_analysis():
    return {
        "summary": "这是概念解释或写作任务，无需提供外部参考资料",
        "needs_data": False,
        "requirements": [],
        "assumptions": ["结果不声称核验外部实时事实"],
    }


async def drain(db, worker):
    result = None
    for _ in range(20):
        claim = db.claim(worker)
        if not claim:
            break
        result = await runtime.process_claim(db, claim, worker)
    return result


def test_missing_data_resume_idempotency_and_task_private_assets(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(db, p)
    assert first["status"] == "awaiting_data" and not db.list("tasks", p["id"])
    assert create(db, p)["id"] == first["id"]
    body = InlineSubmissionInput(
        name="Records",
        content=[{"region": "A", "count": 3}],
        source_id="public-synthetic",
        provenance="unit test",
        idempotency_key="batch1",
        classification="public",
    )
    accepted = task_cases.ingest_inline(first["id"], body, REQUESTER, db)
    assert accepted["status"] == "ready"
    assert accepted["requirements"][0]["status"] == "provided"
    assert accepted["requirements"][0]["profiles"][0]["records"] == 1
    assert task_cases.ingest_inline(first["id"], body, REQUESTER, db)["input_refs"] == accepted["input_refs"]
    with pytest.raises(service.DomainError, match="授权"):
        task_cases.start(first["id"], REQUESTER, db)
    second = create(db, p, idempotency_key="task-two")
    assert not second["available_assets"]
    with pytest.raises(service.DomainError, match="范围"):
        task_cases.submit(
            second["id"], {"input_refs": accepted["input_refs"], "idempotency_key": "foreign"}, REQUESTER, db
        )
    outsider = {"id": "other", "role": "requester"}
    with pytest.raises(service.DomainError, match="未找到"):
        task_cases.get(first["id"], outsider, db)


def test_scoped_api_token_revoked_user_and_project_access(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(db, p)
    token = task_cases.create_ingest_token(first["id"], 1, REQUESTER, db)
    assert task_cases.ingest_identity(first["id"], token["token"], db)["id"] == REQUESTER["id"]
    second = create(db, p, idempotency_key="two")
    with pytest.raises(service.DomainError):
        task_cases.ingest_identity(second["id"], token["token"], db)
    db.put("projects", {**db.get("projects", p["id"]), "members": [ADMIN["id"]]})
    with pytest.raises(service.DomainError):
        task_cases.ingest_identity(first["id"], token["token"], db)


def test_empty_and_unrelated_data_does_not_satisfy_monthly_requirement(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(db, p, goal="核对每月死亡和受伤统计，复算月度总数")
    source = asset(db, p, [{"unrelated": 1}])
    result = task_cases.submit(
        first["id"], {"input_refs": [source["id"]], "idempotency_key": "unrelated"}, REQUESTER, db
    )
    assert result["status"] == "awaiting_data" and result["missing"]


@pytest.mark.parametrize("list_sources", [False, True])
async def test_no_data_task_real_worker_plans_and_reviews_with_bounded_gateway_mock(
    db, monkeypatch, list_sources
):
    p = setup(db, monkeypatch)
    first = create(db, p, goal="用一个生活例子解释递归与迭代的区别", public_egress_authorized=True)
    assert first["status"] == "analyzing_requirements" and first["requirements"] == []
    calls = []

    async def complete(prompt, *, purpose, **kwargs):
        calls.append(purpose)
        if purpose == "task_case_requirements":
            result = no_data_analysis()
        elif purpose == "task_case_planning":
            result = {
                "rationale": "不需外部资料，解释概念并给出例子",
                "missing_data": [],
                "steps": [
                    {
                        "label": "概念解释",
                        "instructions": "解释递归和迭代，明确比较调用栈与循环。",
                        "tool_ids": ["data.list_sources"] if list_sources else [],
                        "skill_ids": [],
                        "success_criteria": ["包含例子与区别"],
                    }
                ],
            }
        else:
            result = {"verdict": "passed", "issues": [], "evidence_refs": [], "suggested_changes": []}
        return {"text": json.dumps(result, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(agent, prompt, bindings, **kwargs):
        calls.append("execution")
        assert kwargs["output_schema"] is AutonomousTextResult
        if list_sources:
            native = next(
                tool for tool in kwargs["native_tools"] if getattr(tool, "name", None) == "list_sources"
            )
            listed = await native.entrypoint()
            assert listed["data"] == {"sources": []}
        return {
            "text": json.dumps(
                {
                    "text": "递归像打开套盒时对每一层重复同样步骤，迭代像按顺序逐个打开盒子；递归使用调用栈，迭代使用循环。",
                    "citations": [],
                    "missing_information": [],
                },
                ensure_ascii=False,
            ),
            "model": "test-model",
            "request_id": "run",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    started = task_cases.start(first["id"], REQUESTER, db)
    assert started["status"] == "analyzing_requirements"
    run = await drain(db, "autonomy-test")
    assert run["status"] == "succeeded", run.get("result")
    detail = task_cases.get(first["id"], REQUESTER, db)
    assert detail["result"]["answer"] and detail["review"]["passed"]
    assert calls == ["task_case_requirements", "task_case_planning", "execution", "v2_independent_review"]
    assert len(detail["plans"]) == 3 and len(detail["runs"]) == 2
    assert detail["trace"]["agents"][0]["scope"] == "task"
    assert db.get("tasks", run["id"])["submitted_snapshot"]
    if list_sources:
        assert len(run["tool_calls"]) == 1
        assert run["tool_calls"][0]["status"] == "completed"
        assert run["tool_calls"][0]["argument_names"] == []
    with pytest.raises(service.DomainError):
        service.create_task({**run["request"], "idempotency_key": "reuse-private-agent"}, ADMIN, db)


async def test_generic_failed_review_changes_plan_and_records_before_after(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(
        db, p, goal="写出三条清晰的沟通建议", data_requirement="none", public_egress_authorized=True
    )
    review_count = 0

    async def complete(prompt, *, purpose, **kwargs):
        nonlocal review_count
        if purpose == "task_case_requirements":
            result = no_data_analysis()
        elif purpose in {"task_case_planning", "task_case_replanning"}:
            repair = purpose == "task_case_replanning"
            parsed = json.loads(prompt)
            if repair:
                assert parsed["failed_checks"] and parsed["previous_output"]
            result = {
                "rationale": "补足第三条建议并逐项检查" if repair else "按目标撰写建议",
                "missing_data": [],
                "steps": [
                    {
                        "label": "补足遗漏建议" if repair else "撰写建议",
                        "instructions": "明确分为三条并检查计数" if repair else "提供沟通建议",
                        "tool_ids": [],
                        "skill_ids": [],
                        "success_criteria": ["三条清晰建议"],
                    }
                ],
            }
        else:
            review_count += 1
            result = {
                "verdict": "failed" if review_count == 1 else "passed",
                "issues": ["只给出两条建议，缺第三条"] if review_count == 1 else [],
                "evidence_refs": [],
                "suggested_changes": ["补写一条并检查编号"] if review_count == 1 else [],
            }
        return {"text": json.dumps(result, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(agent, prompt, bindings, **kwargs):
        text = "1.说清目标。2.确认理解。3.约定下一步。" if review_count else "1.说清目标。2.确认理解。"
        return {
            "text": json.dumps(
                {"text": text, "citations": [], "missing_information": []}, ensure_ascii=False
            ),
            "model": "test-model",
            "request_id": "run",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    task_cases.start(first["id"], REQUESTER, db)
    run = await drain(db, "repair-test")
    assert run["status"] == "succeeded", run.get("result")
    repair = run["repair_history"][0]
    assert repair["action"] == "targeted_replanning" and repair["outcome"]["passed"]
    assert repair["plan_diff"]["before"] != repair["plan_diff"]["after"]
    assert len(db.list("plan_versions", p["id"])) == 4


async def test_planner_missing_data_releases_worker_and_task_can_resume(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(db, p, goal="核验当日营业额", data_requirement="none", public_egress_authorized=True)

    async def complete(prompt, **kwargs):
        if kwargs.get("purpose") == "task_case_requirements":
            return {"text": json.dumps(no_data_analysis()), "model": "test", "request_id": "requirements"}
        return {
            "text": json.dumps(
                {
                    "rationale": "缺少事实依据",
                    "missing_data": ["当日营业流水及退款明细"],
                    "steps": [
                        {
                            "label": "核验",
                            "instructions": "读取流水",
                            "tool_ids": [],
                            "skill_ids": [],
                            "success_criteria": ["金额可复算"],
                        }
                    ],
                }
            ),
            "model": "test",
            "request_id": "missing",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    task_cases.start(first["id"], REQUESTER, db)
    run = await drain(db, "missing-test")
    assert run["status"] == "failed" and run["lease_owner"] is None
    detail = task_cases.get(first["id"], REQUESTER, db)
    assert detail["status"] == "awaiting_data" and detail["missing"] == ["当日营业流水及退款明细"]
    source = asset(db, p, [{"revenue": 10}])
    resumed = task_cases.submit(
        first["id"], {"input_refs": [source["id"]], "idempotency_key": "new-data"}, REQUESTER, db
    )
    assert resumed["status"] == "ready" and resumed["task_id"] is None and len(resumed["runs"]) == 2


def test_web_upload_and_customer_api_are_same_task_scoped_intake(db, monkeypatch):
    p = setup(db, monkeypatch)
    monkeypatch.setattr(
        service, "ASSET_DIR", __import__("pathlib").Path(db.engine.url.database).parent / "assets"
    )
    monkeypatch.setattr(runtime, "ASSET_ROOT", service.ASSET_DIR)
    first = create(db, p)
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/task-cases/{first['id']}/upload",
            data={"idempotency_key": "file", "classification": "local"},
            files={"file": ("rows.csv", b"name,value\nalpha,3\n", "text/csv")},
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["status"] == "ready"
        assert len(result["submissions"]) == 1
        a = db.get("data_assets", result["input_refs"][0])
        assert a["task_case_id"] == first["id"] and a["classification"] == "local"
        token = client.post(f"/api/v2/task-cases/{first['id']}/ingest-tokens", json={}).json()
        response = client.post(
            f"/api/v2/task-cases/{first['id']}/ingest",
            headers={"Authorization": "Bearer " + token["token"]},
            json={
                "name": "API rows",
                "content": [{"name": "beta", "value": 4}],
                "source_id": "api:data",
                "provenance": "synthetic",
                "idempotency_key": "api-1",
            },
        )
        assert response.status_code == 201, response.text
        assert "result" not in response.json()
        client.delete(f"/api/v2/task-cases/{first['id']}/ingest-tokens/{token['id']}")
        assert (
            client.post(
                f"/api/v2/task-cases/{first['id']}/ingest",
                headers={"Authorization": "Bearer " + token["token"]},
                json={
                    "name": "API rows",
                    "content": [],
                    "source_id": "api:data",
                    "provenance": "synthetic",
                    "idempotency_key": "api-2",
                },
            ).status_code
            == 403
        )


def test_running_null_node_output_and_source_download_enforce_owner_and_revocation(db, monkeypatch):
    p = setup(db, monkeypatch)
    first = create(db, p)
    source = asset(db, p, [{"value": 3}])
    task_cases.submit(
        first["id"],
        {"input_refs": [source["id"]], "idempotency_key": "data", "public_egress_authorized": True},
        REQUESTER,
        db,
    )
    detail = task_cases.start(first["id"], REQUESTER, db)
    task = db.get("tasks", detail["task_id"])
    a = task["snapshot"]["agents"][0]
    db.put(
        "nodes",
        {
            "id": task["id"] + ":running",
            "project_id": p["id"],
            "task_id": task["id"],
            "node_id": "process",
            "agent_id": a["id"],
            "agent_version": a["version"],
            "status": "running",
            "output": None,
        },
    )
    assert task_cases.get(first["id"], REQUESTER, db)["trace"]["models"] == []
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        url = f"/api/v2/task-cases/{first['id']}/sources/{source['id']}/download"
        response = client.get(url)
        assert response.status_code == 200 and "3" in response.text
        other = asset(db, p, [{"private": 9}])
        assert (
            client.get(f"/api/v2/task-cases/{first['id']}/sources/{other['id']}/download").status_code == 404
        )
        service.revoke_asset(source["id"], ADMIN, db)
        assert client.get(url).status_code == 409
        assert task_cases.get(first["id"], REQUESTER, db)["content_withheld"]


def test_generic_monthly_work_never_enters_casualty_specialist():
    assert task_cases._case_match("统计每月的收入、成本、利润并复算合计") is None
    assert task_cases._case_match("请根据仓库记录核对每月库存合计，列出异常仓库") is None
    assert task_cases._case_match("根据OHCHR报告复算每月平民死亡受伤人数并核对总数") == "B05"


@pytest.mark.parametrize("media_type", ["text/html", "image/svg+xml", "application/xml"])
def test_registered_active_content_cannot_execute_on_platform_origin(db, monkeypatch, media_type):
    p = setup(db, monkeypatch)
    case = create(db, p)
    source = asset(db, p, "<script>alert(document.cookie)</script>", modality="text", media_type=media_type)
    task_cases.submit(case["id"], {"input_refs": [source["id"]], "idempotency_key": "active"}, REQUESTER, db)
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        response = client.get(f"/api/v2/task-cases/{case['id']}/sources/{source['id']}/download")
        assert response.status_code == 200
        assert response.headers["content-disposition"] == "attachment"
        assert response.headers["content-type"].startswith("application/octet-stream")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]


@pytest.mark.parametrize("filename", ["broken.pdf", "broken.docx", "broken.xlsx"])
def test_corrupt_office_or_pdf_upload_is_a_data_gap_not_internal_error(db, monkeypatch, filename):
    p = setup(db, monkeypatch)
    monkeypatch.setattr(
        service, "ASSET_DIR", __import__("pathlib").Path(db.engine.url.database).parent / "assets"
    )
    monkeypatch.setattr(runtime, "ASSET_ROOT", service.ASSET_DIR)
    case = create(db, p)
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/task-cases/{case['id']}/upload",
            data={"idempotency_key": "corrupt", "classification": "local"},
            files={"file": (filename, b"not a valid document or zip", "application/octet-stream")},
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "awaiting_data"
        assert not data["profiles"][0]["readable"] and "无法解析" in data["profiles"][0]["validation"]


async def test_script_review_is_untrusted_and_contains_actual_code_and_sources(db, monkeypatch):
    from types import SimpleNamespace

    p = setup(db, monkeypatch)
    source = asset(db, p, [{"value": 2}, {"value": 3}])
    cite = {
        "kind": "derived_result",
        "tool_call_id": "script-call",
        "source_id": source["id"],
        "result_checksum": "output-checksum",
    }
    checkpoint = {
        "arguments": {"code": "answer = sum(row['value'] for row in rows)", "source_ids": [source["id"]]},
        "result": {"status": "succeeded", "quality_status": "unreviewed", "outputs": [{"preview": "5"}]},
    }
    execution = SimpleNamespace(
        request={"task_type": "autonomous_analysis", "mode": "cloud", "goal": "复算数据之和"},
        inputs=[source],
        task={},
        check=lambda: None,
        current=lambda: {
            "tool_calls": [{"id": "script-call", "capability_id": "script.execute"}],
            "tool_checkpoints": {"script-call": checkpoint},
        },
    )
    seen = {}

    async def model(execution, prompt, **kwargs):
        seen.update(body=json.loads(prompt), system=kwargs["system"])
        return {
            "text": json.dumps(
                {
                    "verdict": "failed",
                    "issues": ["缺少独立原始输入覆盖核对"],
                    "evidence_refs": [source["id"]],
                    "suggested_changes": ["核对实际rows来自全部输入"],
                }
            )
        }

    monkeypatch.setattr(runtime, "_model", model)
    result = await runtime._review_output(
        execution,
        {
            "type": "text_analysis",
            "method": "tool_agent",
            "content": {"text": "合计5", "citations": [cite]},
            "source_refs": [source["id"]],
            "derived_evidence": [cite],
        },
    )
    entry = seen["body"]["verified_calculations"][0]
    assert entry["trust"] == "untrusted_generated_script" and entry["capability_id"] == "script.execute"
    assert entry["arguments"]["code"] == checkpoint["arguments"]["code"]
    assert entry["arguments"]["source_ids"] == [source["id"]]
    assert "exit0" in seen["system"] and "不能当成审核通过" in seen["system"]
    assert not result["passed"]


def test_plan_normalizes_exact_aliases_and_closes_only_available_skill_dependencies():
    from backend.app.platform.task_case_runtime import normalize_plan

    plan = {"steps": [{"tool_ids": ["read_source"], "skill_ids": ["evidence"], "reuse_agent_id": None}]}
    tools = [
        {"id": "data.read_source", "name": "read_source"},
        {"id": "data.locate_evidence", "name": "locate_evidence"},
    ]
    skills = [{"id": "evidence", "required_tools": ["read_source", "data.locate_evidence"]}]
    result, changes, errors = normalize_plan(plan, tools, skills, [])
    assert result["steps"][0]["tool_ids"] == ["data.read_source", "data.locate_evidence"]
    assert [change["action"] for change in changes] == ["canonical_tool_id", "skill_dependency_added"]
    assert not errors and plan["steps"][0]["tool_ids"] == ["read_source"]
    _, _, errors = normalize_plan(plan, tools[:1], skills, [])
    assert errors == [
        {
            "step": 1,
            "code": "skill_dependency_unavailable",
            "skill_id": "evidence",
            "id": "data.locate_evidence",
        }
    ]
    many = [{"id": f"test.{i}", "name": f"test_{i}"} for i in range(17)]
    _, _, errors = normalize_plan(
        {"steps": [{"tool_ids": [], "skill_ids": ["too-many"]}]},
        many,
        [{"id": "too-many", "required_tools": [t["id"] for t in many]}],
        [],
    )
    assert errors == [{"step": 1, "code": "tool_limit_exceeded", "count": 17, "limit": 16}]


def changed_tool_publication(monkeypatch):
    from backend.app.platform import tool_registry

    original = tool_registry._tool_specs

    def published(requested=None):
        return [
            {**tool, "checksum": service.digest([tool["checksum"], "new-test-publication"])}
            for tool in original(requested)
        ]

    monkeypatch.setattr(tool_registry, "_tool_specs", published)


def test_scoped_publication_retains_agent_and_workflow_versions(db, monkeypatch):
    p = setup(db, monkeypatch)
    case = create(db, p)
    row = db.get("task_cases", case["id"])
    with db.transaction() as tx:
        agent = task_cases._provision_generic(tx, row)
        workflow_id = task_cases._workflow(tx, row, agent)
    old_agent = db.get("agent_versions", agent["id"] + ":1")
    old_flow = db.get("workflow_versions", workflow_id + ":1")
    changed_tool_publication(monkeypatch)
    with db.transaction() as tx:
        updated = task_cases._provision_generic(tx, row)
        assert updated["id"] == agent["id"] and updated["current_version"] == 2
        assert task_cases._workflow(tx, row, updated) == workflow_id
        assert task_cases._provision_generic(tx, row)["current_version"] == 2
    assert db.get("agent_versions", agent["id"] + ":1") == old_agent
    assert db.get("workflow_versions", workflow_id + ":1") == old_flow
    flow = db.get("workflows", workflow_id)
    assert flow["current_version"] == 2 and flow["nodes"][0]["agent_version"] == 2


def test_same_case_can_resume_after_data_revision_and_new_tool_publication(db, monkeypatch):
    p = setup(db, monkeypatch)
    case = create(db, p)
    row = db.get("task_cases", case["id"])
    db.put(
        "task_cases",
        {
            **row,
            "requirements_analysis": no_data_analysis(),
            "requirements": [],
            "missing": [],
            "status": "ready",
            "public_egress_authorized": True,
        },
    )
    before = task_cases.start(case["id"], REQUESTER, db)
    old_run = db.get("tasks", before["task_id"])
    old_snapshot = old_run["snapshot"]
    db.put("tasks", {**old_run, "status": "failed", "reason_code": "synthetic_failure"})
    changed_tool_publication(monkeypatch)
    resumed = task_cases.submit(
        case["id"], {"input_refs": [], "idempotency_key": "supplement-after-release"}, REQUESTER, db
    )
    assert resumed["status"] == "ready" and resumed["task_id"] is None
    after = task_cases.start(case["id"], REQUESTER, db)
    assert after["status"] == "accepted" and after["task_id"] != old_run["id"]
    new_run = db.get("tasks", after["task_id"])
    assert new_run["snapshot"]["agents"][0]["id"] != old_snapshot["agents"][0]["id"]
    assert db.get("tasks", old_run["id"])["snapshot"] == old_snapshot
    assert len(after["runs"]) == 2
    task_cases.validate_frozen(new_run["snapshot"]["agents"][0], db)


@pytest.mark.parametrize("corrected", [True, False])
async def test_invalid_plan_is_audited_and_gets_only_one_specific_correction(db, monkeypatch, corrected):
    p = setup(db, monkeypatch)
    case = create(db, p, goal="解释清晰沟通的三个原则", public_egress_authorized=True)
    calls = []

    async def complete(prompt, *, purpose, **kwargs):
        calls.append(purpose)
        if purpose == "task_case_requirements":
            result = no_data_analysis()
        elif purpose in {"task_case_planning", "task_case_plan_correction"}:
            if purpose == "task_case_plan_correction":
                assert '"planning_validation_errors"' in prompt and '"unknown_tool"' in prompt
                assert '"id":"made.up"' in prompt
            result = {
                "rationale": "根据明确原则撰写",
                "missing_data": [],
                "steps": [
                    {
                        "label": "原则说明",
                        "instructions": "列三条原则并解释",
                        "tool_ids": []
                        if corrected and purpose == "task_case_plan_correction"
                        else ["made.up"],
                        "skill_ids": [],
                        "success_criteria": ["三条清晰原则"],
                    }
                ],
            }
        else:
            result = {"verdict": "passed", "issues": [], "evidence_refs": [], "suggested_changes": []}
        return {"text": json.dumps(result, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(*args, **kwargs):
        calls.append("execution")
        return {
            "text": json.dumps(
                {"text": "1.明确目的。2.具体表达。3.核对理解。", "citations": [], "missing_information": []},
                ensure_ascii=False,
            ),
            "model": "test-model",
            "request_id": "execution",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    run = await drain(db, "plan-correction")
    assert calls.count("task_case_plan_correction") == 1
    assert run["status"] == ("succeeded" if corrected else "failed"), run.get("result")
    assert ("execution" in calls) is corrected
    detail = task_cases.get(case["id"], REQUESTER, db)
    rejected = [plan for plan in detail["plans"] if plan["status"] == "rejected"]
    assert len(rejected) == (1 if corrected else 2)
    for rejected_plan in rejected:
        assert rejected_plan["validation_errors"] == [{"step": 1, "code": "unknown_tool", "id": "made.up"}]
        assert "made.up" in rejected_plan["raw_response"] and rejected_plan["checksum"]
    if corrected:
        final_plan = next(plan for plan in detail["plans"] if plan.get("rejected_attempts"))
        assert final_plan["rejected_attempts"] == [rejected[0]["id"]]
        assert final_plan["proposed_plan"]["steps"][0]["tool_ids"] == []


async def test_two_nodes_reuse_actual_calculation_without_repeating_tool_call(db, monkeypatch):
    p = setup(db, monkeypatch)
    case = create(db, p, goal="计算value合计并用中文说明结果")
    source = asset(db, p, [{"value": 2}, {"value": 3}])
    task_cases.submit(
        case["id"],
        {"input_refs": [source["id"]], "public_egress_authorized": True, "idempotency_key": "records"},
        REQUESTER,
        db,
    )
    observed = []

    async def complete(prompt, *, purpose, **kwargs):
        if purpose == "task_case_requirements":
            data = {
                "summary": "需要包含value的数值记录",
                "needs_data": True,
                "requirements": [
                    {
                        "id": "records",
                        "title": "数值记录",
                        "description": "提供待统计记录",
                        "required": True,
                        "formats": ["JSON"],
                        "fields": ["value"],
                    }
                ],
                "assumptions": [],
            }
        elif purpose == "task_case_planning":
            data = {
                "rationale": "先确定性计算，再根据该证据撰写结论",
                "missing_data": [],
                "steps": [
                    {
                        "label": "计算",
                        "instructions": "用table.describe计算value合计",
                        "tool_ids": ["table.describe"],
                        "skill_ids": [],
                        "success_criteria": ["实际计算合计"],
                    },
                    {
                        "label": "汇总",
                        "instructions": "消费上游结果，不重复调用工具，保留原始引用",
                        "tool_ids": [],
                        "skill_ids": [],
                        "success_criteria": ["中文说明数值结论"],
                    },
                ],
            }
        else:
            body = json.loads(prompt)
            assert body["verified_calculations"][0]["result"]["fields"]["value"]["sum"] == "5"
            data = {
                "verdict": "passed",
                "issues": [],
                "evidence_refs": [source["id"]],
                "suggested_changes": [],
            }
        return {"text": json.dumps(data, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(agent, prompt, bindings, **kwargs):
        body = json.loads(prompt)
        if not body["previous_outputs"]:
            result = await bindings["table_describe"](source_id=source["id"], fields=["value"])
            assert result["data"]["fields"]["value"]["sum"] == "5"
            citation = result["data"]["derived_evidence"]
            observed.append("actual_calculation")
        else:
            assert not bindings
            citation = body["previous_outputs"][0]["derived_evidence"][0]
            observed.append("summary_without_tools")
        return {
            "text": json.dumps(
                {"text": "两条记录的value合计为5。", "citations": [citation], "missing_information": []},
                ensure_ascii=False,
            ),
            "model": "test-model",
            "request_id": "node",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    task_cases.start(case["id"], REQUESTER, db)
    run = await drain(db, "multi-node")
    assert run["status"] == "succeeded", run.get("result")
    assert observed == ["actual_calculation", "summary_without_tools"]
    assert len(run["tool_calls"]) == 1 and run["tool_calls"][0]["status"] == "completed"
    nodes = db.list("nodes", task_id=run["id"])
    assert len(nodes) == 2 and all(node["status"] == "succeeded" for node in nodes)
    assert nodes[0]["output"]["derived_evidence"] == nodes[1]["output"]["derived_evidence"]
    assert nodes[0]["output"]["source_refs"] == nodes[1]["output"]["source_refs"] == [source["id"]]


@pytest.mark.parametrize("fault", ["unpersisted", "checksum", "failed", "foreign_source"])
def test_inherited_evidence_requires_persisted_output_and_valid_current_checkpoint(db, monkeypatch, fault):
    """Synthetic records exercise the trust boundary; this is not execution evidence."""
    from types import SimpleNamespace

    p = setup(db, monkeypatch)
    source = asset(db, p, [{"value": 5}])
    result = {"sum": 5}
    checksum = service.digest(result)
    evidence = {
        "kind": "derived_result",
        "tool_call_id": "calculation",
        "source_id": source["id"],
        "result_checksum": checksum,
    }
    output = {
        "type": "text_analysis",
        "method": "tool_agent",
        "content": {"text": "合计5", "citations": [evidence]},
        "source_refs": [source["id"]],
        "derived_evidence": [evidence],
    }
    call = {
        "id": "calculation",
        "task_id": "upstream-fixture",
        "status": "completed",
        "source_refs": [source["id"]],
        "result_checksum": checksum,
    }
    checkpoint = {"arguments": {"source_id": source["id"]}, "result": result, "sources": [source["id"]]}
    if fault == "failed":
        call["status"] = "failed"
    elif fault == "checksum":
        checkpoint["result"] = {"sum": 500}
    elif fault == "foreign_source":
        checkpoint["arguments"]["source_id"] = "foreign"
    current = {
        "id": "upstream-fixture",
        "tool_calls": [call],
        "tool_checkpoints": {"calculation": checkpoint},
    }
    if fault != "unpersisted":
        db.put(
            "nodes",
            {"project_id": p["id"], "task_id": current["id"], "status": "succeeded", "output": output},
        )
    execution = SimpleNamespace(
        request={"task_type": "autonomous_analysis"}, check=lambda: current, repository=db, inputs=[source]
    )
    with pytest.raises(runtime.TaskFailure) as error:
        runtime._verified_upstream_evidence(execution, [output])
    assert error.value.code == "upstream_evidence_invalid"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "whitespace",
        "uncited",
        "failed_task",
        "checkpoint",
        "output_bytes",
        "script_code",
        "foreign_source",
    ],
)
def test_delivery_files_require_final_citation_and_checked_script_output(db, monkeypatch, fault):
    """Synthetic durable script records test publication, not real sandbox execution."""
    import base64
    import hashlib

    p = setup(db, monkeypatch)
    case = create(db, p)
    source = asset(db, p, [{"value": 5}])
    payload = b'{"sum":5}'
    code = "print('synthetic sandbox fixture')"
    output = {
        "name": "result.json",
        "media_type": "application/json",
        "bytes": len(payload),
        "checksum": hashlib.sha256(payload).hexdigest(),
    }
    script = {
        "id": "script-delivery:1",
        "project_id": p["id"],
        "task_id": "script-delivery",
        "code": code,
        "checksum": hashlib.sha256(code.encode()).hexdigest(),
    }
    db.put("script_versions", script)
    result = {
        "status": "succeeded",
        "script_checksum": script["checksum"],
        "script_version_id": script["id"],
        "sandbox_run_id": "sandbox-delivery",
        "source_refs": [source["id"]],
        "outputs": [{**output, "id": "sandbox-delivery:0"}],
    }
    checksum = service.digest(result)
    content = {
        "text": "合计5",
        "citations": [
            {
                "kind": "derived_result",
                "tool_call_id": "script-call",
                "source_id": source["id"],
                "result_checksum": checksum,
            }
        ],
    }
    task = {
        "id": "script-delivery",
        "status": "succeeded",
        "snapshot": {"inputs": [source]},
        "quality_verdict": {"passed": True},
        "tool_calls": [
            {
                "id": "script-call",
                "task_id": "script-delivery",
                "capability_id": "script.execute",
                "status": "completed",
                "result_checksum": checksum,
            }
        ],
        "tool_checkpoints": {
            "script-call": {
                "arguments": {
                    "code": "\n" + code + "\n\n" if fault == "whitespace" else code,
                    "source_ids": [source["id"]],
                    "purpose": "synthetic output fixture",
                },
                "result": result,
            }
        },
    }
    task["tool_calls"][0]["arguments_checksum"] = service.digest(
        task["tool_checkpoints"]["script-call"]["arguments"]
    )
    run = {
        "id": "sandbox-delivery",
        "project_id": p["id"],
        "task_id": task["id"],
        "status": "succeeded",
        "script_version_id": script["id"],
        "source_refs": [source["id"]],
        "outputs": [{**output, "base64": base64.b64encode(payload).decode()}],
    }
    if fault == "uncited":
        content["citations"] = []
    elif fault == "failed_task":
        task["status"] = "failed"
    elif fault == "checkpoint":
        result["outputs"][0]["checksum"] = "modified"
    elif fault == "output_bytes":
        run["outputs"][0]["base64"] = base64.b64encode(b"wrong bytes").decode()
    elif fault == "script_code":
        task["tool_checkpoints"]["script-call"]["arguments"]["code"] = "modified"
    elif fault == "foreign_source":
        run["source_refs"] = ["foreign"]
    files = task_cases._generated_files(db, case, task, content, [run, {**run, "id": "uncited-old-run"}])
    if fault not in {None, "whitespace"}:
        assert files == []
    else:
        assert len(files) == 1 and files[0]["quality_status"] == "referenced_output"
        assert files[0]["checksum"] == output["checksum"] and files[0]["task_review_passed"]
        assert files[0]["download_url"].endswith("/sandbox-runs/sandbox-delivery/outputs/0")


@pytest.mark.parametrize("scenario", ["unique", "fabricated", "ambiguous", "persistent"])
async def test_upstream_citation_validation_repairs_only_exact_unique_quote_and_replans(
    db, monkeypatch, scenario
):
    p = setup(db, monkeypatch)
    case = create(db, p, goal="根据原文说明合计，并给出可定位的原文引用")
    text = "合计5。再次写合计5。" if scenario == "ambiguous" else "唯一事实是合计5。"
    source = asset(db, p, text, modality="text", media_type="text/plain")
    task_cases.submit(
        case["id"],
        {"input_refs": [source["id"]], "public_egress_authorized": True, "idempotency_key": "quote-source"},
        REQUESTER,
        db,
    )
    replans, summaries = [], []

    async def complete(prompt, *, purpose, **kwargs):
        if purpose == "task_case_requirements":
            data = {
                "summary": "需要待引用原文",
                "needs_data": True,
                "requirements": [
                    {
                        "id": "original",
                        "title": "原文",
                        "description": "提供包含事实的原文",
                        "formats": ["TXT"],
                        "fields": [],
                    }
                ],
                "assumptions": [],
            }
        elif purpose in {"task_case_planning", "task_case_replanning"}:
            revised = purpose == "task_case_replanning"
            if revised:
                context = json.loads(prompt)
                replans.append(context)
                assert any(issue["code"] == "source_span_boundaries" for issue in context["failed_checks"])
                assert context["previous_output"] and "data.locate_evidence" in json.dumps(
                    context["failed_checks"]
                )
            data = {
                "rationale": "针对引用边界错误增加精确原文定位" if revised else "先提取再汇总",
                "missing_data": [],
                "steps": [
                    {
                        "label": "原文定位" if revised else "原文提取",
                        "instructions": f"第{len(replans)}次使用精确定位工具，不猜引用偏移"
                        if revised
                        else "读取原文后提取引用",
                        "tool_ids": ["data.read_source", *(["data.locate_evidence"] if revised else [])],
                        "skill_ids": [],
                        "success_criteria": ["逐字引用与边界对应"],
                    },
                    {
                        "label": "汇总",
                        "instructions": "使用上游已验证引用说明合计",
                        "tool_ids": [],
                        "skill_ids": [],
                        "success_criteria": ["结论可定位"],
                    },
                ],
            }
        else:
            data = {
                "verdict": "passed",
                "issues": [],
                "evidence_refs": [source["id"]],
                "suggested_changes": [],
            }
        return {"text": json.dumps(data, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(agent, prompt, bindings, **kwargs):
        context = json.loads(prompt)
        revision = runtime.context().task.get("revision", 0)
        if context["previous_outputs"]:
            summaries.append(revision)
            citation = context["previous_outputs"][0]["content"]["citations"][0]
        else:
            native = {tool.name: tool for tool in kwargs["native_tools"] if hasattr(tool, "name")}
            await native["read_source"].entrypoint(source_id=source["id"], start=0, length=1000)
            if revision and scenario != "persistent":
                located = await native["locate_evidence"].entrypoint(
                    source_id=source["id"], quote="合计5" if scenario == "ambiguous" else text
                )
                citation = {"kind": "source_span", **located["data"]}
            else:
                quote = (
                    text if scenario == "unique" else "合计5" if scenario == "ambiguous" else "虚构的合计500"
                )
                citation = {
                    "kind": "source_span",
                    "source_id": source["id"],
                    "source_version": source["source_version"],
                    "start": 1,
                    "end": 1 + len(quote),
                    "quote": quote,
                }
        return {
            "text": json.dumps(
                {"text": "原文说明合计为5。", "citations": [citation], "missing_information": []},
                ensure_ascii=False,
            ),
            "model": "test-model",
            "request_id": "node",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    task_cases.start(case["id"], REQUESTER, db)
    run = await drain(db, "upstream-quote")
    nodes = db.list("nodes", task_id=run["id"])
    history = run.get("repair_history", [])
    if scenario == "unique":
        assert run["status"] == "succeeded" and not replans
        adjustment = next(entry for entry in history if entry["action"] == "citation_span_realign")
        assert adjustment["repairs"][0]["original"]["start"] == 1
        assert adjustment["repairs"][0]["corrected"]["start"] == 0
        assert adjustment["repairs"][0]["original"]["quote"] == adjustment["repairs"][0]["corrected"]["quote"]
        assert summaries == [0]
    else:
        assert not any(entry["action"] == "citation_span_realign" for entry in history)
        assert run.get("upstream_quality_failures") and nodes[0]["status"] == "failed"
        assert nodes[0]["output"]["content"]["citations"][0]["start"] == 1
        targeted = [entry for entry in history if entry["action"] == "targeted_replanning"]
        assert targeted and targeted[0]["plan_diff"]["before"] != targeted[0]["plan_diff"]["after"]
        if scenario == "persistent":
            assert run["status"] == "failed" and run["revision"] == 2 and len(replans) == 2
            assert len(run["upstream_quality_failures"]) == 3 and not summaries
            assert not db.list("artifacts", task_id=run["id"])
        else:
            assert run["status"] == "succeeded" and run["revision"] == 1 and len(replans) == 1
            assert summaries == [1]
            assert any(
                call["capability_id"] == "data.locate_evidence" and call["status"] == "completed"
                for call in run["tool_calls"]
            )


@pytest.mark.parametrize("genuine_gap", [False, True])
async def test_missing_data_countercheck_uses_profiles_and_preserves_real_blocker(
    db, monkeypatch, genuine_gap
):
    p = setup(db, monkeypatch)
    goal = (
        "请按实际到账日期分组说明记录"
        if genuine_gap
        else "请说明提供资料中revenue和cost对应的利润口径，金额为人民币元，统计当前记录即可"
    )
    case = create(db, p, goal=goal)
    source = asset(db, p, [{"revenue": 10, "cost": 4}])
    task_cases.submit(
        case["id"],
        {
            "input_refs": [source["id"]],
            "public_egress_authorized": True,
            "idempotency_key": "countercheck-data",
        },
        REQUESTER,
        db,
    )
    calls, executions = [], []
    claimed = ["缺少实际到账日期"] if genuine_gap else ["缺少计算好的利润列", "缺少币种", "缺少报告期间"]

    async def complete(prompt, *, purpose, **kwargs):
        calls.append((purpose, kwargs["run_id"]))
        if purpose == "task_case_requirements":
            data = {
                "summary": "需要参考记录",
                "needs_data": True,
                "requirements": [
                    {
                        "id": "rows",
                        "title": "业务记录",
                        "description": "提供原始记录",
                        "formats": ["JSON"],
                        "fields": ["revenue", "cost"],
                    }
                ],
                "assumptions": [],
            }
        elif purpose in {"task_case_planning", "task_case_data_gap_review"}:
            if purpose == "task_case_data_gap_review":
                assert "data_gap_countercheck" in prompt and "data_profiles" in prompt
                assert "revenue" in prompt and "cost" in prompt and goal in prompt
                assert "可推导" in prompt and "真实缺口" in prompt
            keep = genuine_gap or purpose == "task_case_planning"
            data = {
                "rationale": "用户要求到账日分组，现有字段无实际到账日期且无法推导"
                if genuine_gap
                else "利润由revenue-cost推导，币种已在目标明确，期间仅限当前记录，无需另补资料",
                "missing_data": claimed if keep else [],
                "steps": [
                    {
                        "label": "说明口径",
                        "instructions": "读取字段画像并基于用户提供的口径说明",
                        "tool_ids": ["data.profile"],
                        "skill_ids": [],
                        "success_criteria": ["口径与来源可追溯"],
                    }
                ],
            }
        else:
            data = {
                "verdict": "passed",
                "issues": [],
                "evidence_refs": [source["id"]],
                "suggested_changes": [],
            }
        return {"text": json.dumps(data, ensure_ascii=False), "model": "test-model", "request_id": purpose}

    async def run_agent(agent, prompt, bindings, **kwargs):
        executions.append("actual_profile")
        result = await bindings["data_profile"](source_id=source["id"])
        return {
            "text": json.dumps(
                {
                    "text": "资料包含收入与成本，利润采用收入减成本，单位人民币元，范围仅为已提供记录。",
                    "citations": [result["data"]["derived_evidence"]],
                    "missing_information": [],
                },
                ensure_ascii=False,
            ),
            "model": "test-model",
            "request_id": "execute",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    monkeypatch.setattr(runtime.model_gateway, "run_agent", run_agent)
    task_cases.start(case["id"], REQUESTER, db)
    run = await drain(db, "data-gap-review")
    detail = task_cases.get(case["id"], REQUESTER, db)
    gap_calls = [call for call in calls if call[0] == "task_case_data_gap_review"]
    assert gap_calls == [("task_case_data_gap_review", run["id"])]
    assert run["data_gap_review_used"]
    initial = next(plan for plan in detail["plans"] if plan["status"] == "requires_data")
    assert initial["proposed_plan"]["missing_data"] == claimed and initial["raw_response"]
    if genuine_gap:
        assert run["status"] == "failed" and detail["status"] == "awaiting_data"
        assert detail["missing"] == claimed and not executions
    else:
        assert run["status"] == "succeeded" and executions == ["actual_profile"]
        accepted = next(plan for plan in detail["plans"] if plan.get("data_gap_review_plans"))
        assert accepted["data_gap_review_plans"] == [initial["id"]]
        assert accepted["proposed_plan"]["missing_data"] == []
