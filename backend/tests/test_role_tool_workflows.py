"""Role identity, immutable migration and execution permissions at the real case boundary."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.casework import document_cases, geo_cases, roles, team
from backend.app.casework.role_tools import CaseReader, _invoke
from backend.app.platform import runtime, service
from backend.app.platform.contracts import AgentInput
from backend.app.platform.domain_tool import operation_catalog
from backend.app.platform.tool_registry import catalog, digest, freeze_bindings, permissions
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_geo_samples import samples
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = fixtures.db


def legacy_workflow(db, p, code="G03"):
    """Seed the prior release's published definitions and fully pinned DAG (no new role grants)."""
    pid = p["id"]
    title = team.catalog()[code]["title"]
    nodes = []
    for stage, name, deps in [
        ("prepare", "资料与坐标解析员", []),
        ("analyze", title + "专员", ["prepare"]),
        ("review", "独立证据核查员", ["prepare", "analyze"]),
    ]:
        aid = team.role_id(pid, stage, code)
        agent = AgentInput(
            project_id=pid,
            name=name,
            description="用例协作组 · " + name,
            purpose="specialized",
            capabilities=["case_analysis"],
            modalities=["text", "structured", "image", "audio", "video"],
            parameters={"case_stage": stage, **({"case_scope": code} if stage == "analyze" else {})},
            tool_ids=["case_analysis"],
        ).model_dump()
        agent.update(id=aid, status="published", current_version=1, built_in=True, case_role=stage)
        db.put("agents", agent)
        db.put(
            "agent_versions",
            {
                **agent,
                "id": aid + ":1",
                "agent_id": aid,
                "definition_id": aid,
                "version": 1,
                "checksum": service.digest(agent),
            },
        )
        nodes.append(
            {
                "id": stage,
                "label": name,
                "agent_id": aid,
                "agent_version": 1,
                "depends_on": deps,
                "config": {},
            }
        )
    wid = team.assigned_workflow(pid, code)
    wf = {
        "id": wid,
        "project_id": pid,
        "name": title + "协作流程",
        "description": "解析 → 专业分析 → 独立审核",
        "task_type": "case_analysis",
        "nodes": nodes,
        "status": "published",
        "current_version": 1,
    }
    db.put("workflows", wf)
    db.put("workflow_versions", {**wf, "id": wid + ":1", "definition_id": wid, "version": 1})
    return wid, [n["agent_id"] for n in nodes]


def request(p, src, key="one"):
    return task_body(
        p,
        src,
        task_type="case_analysis",
        case_id="G03",
        case_scope="sample",
        max_revisions=0,
        idempotency_key=key,
        allow_partial=False,
    )


def test_role_contract_describes_actual_shared_and_distinct_handlers():
    a, b = [roles.contract("analyze", k) for k in ("B06", "T06")]
    assert document_cases._HANDLERS["B06"] is document_cases._HANDLERS["T06"]
    assert a["operation"] == b["operation"] == "document_compare.clauses"
    assert set(a["shared_cases"]) == {"B06", "T06"} and a["shared_cases"] == b["shared_cases"]
    assert a["inputs"] != b["inputs"] and a["acceptance"] != b["acceptance"]
    assert set(a["grants"]) == set(b["grants"]) == {roles.DIFF, roles.TERMS}
    ice, route = [roles.contract("analyze", k) for k in ("G03", "G09")]
    assert geo_cases.ANALYZERS["G03"] is not geo_cases.ANALYZERS["G09"]
    assert ice["operation"] == "geo_series.ice" and route["operation"] == "geo_routes.routes"
    assert ice["grants"] == [roles.BASELINE] and route["grants"] == [roles.ROUTE]
    assert ice["inputs"] != route["inputs"] and ice["outputs"] != route["outputs"]


async def test_upgrade_appends_versions_and_preserves_old_task_snapshot_and_idempotency(db):
    p = project(db)
    wid, aids = legacy_workflow(db, p)
    before = {a: db.get("agent_versions", a + ":1") for a in aids}
    original_flow = db.get("workflow_versions", wid + ":1")
    src = asset(db, p, {"records": samples("G03")}, classification="local")
    old = service.create_task(request(p, src), ADMIN, db)
    frozen = deepcopy(old["snapshot"])
    assert not team.install(p["id"], ADMIN, db)[
        "updated"
    ]  # Ordinary installation cannot opt in existing roles.
    result = team.install(p["id"], ADMIN, db, upgrade=True)
    assert set(aids + [wid]) <= set(result["updated"])
    assert all(db.get("agent_versions", a + ":1") == before[a] for a in aids)
    assert db.get("workflow_versions", wid + ":1") == original_flow
    assert db.get("tasks", old["id"])["snapshot"] == frozen
    assert all(x["agent_version"] == 1 for x in frozen["workflow"]["nodes"])
    assert not any(x.get("role_contract") for x in frozen["agents"])
    new = service.create_task(request(p, src, "two"), ADMIN, db)
    assert new["snapshot"]["workflow"]["version"] == 2
    versions = {a["parameters"]["case_stage"]: a for a in new["snapshot"]["agents"]}
    assert versions["analyze"]["role_contract"] == roles.contract("analyze", "G03")
    assert versions["analyze"]["version"] == 2
    assert team.install(p["id"], ADMIN, db, upgrade=True) == {"created": [], "updated": []}
    assert all(len(db.list("agent_versions", definition_id=a)) == 2 for a in aids)
    claim = db.claim("legacy-pinned-worker")
    assert claim["id"] == old["id"]
    finished = await runtime.process_claim(db, claim, "legacy-pinned-worker")
    assert finished["status"] == "succeeded" and not finished.get("tool_calls")
    assert all(n["agent_version"] == 1 for n in db.list("nodes", p["id"], task_id=old["id"]))


@pytest.mark.parametrize("changed", ["custom", "disabled", "draft", "modified_flow", "old_binding"])
def test_upgrade_refuses_overwriting_existing_work_and_rolls_back_the_entire_project(db, changed):
    p = project(db)
    wid, aids = legacy_workflow(db, p)
    aid = aids[1]
    agent = db.get("agents", aid)
    flow = db.get("workflows", wid)
    if changed == "custom":
        db.put("agents", {**agent, "instructions": "Retain operator-specific instructions."})
    elif changed == "disabled":
        service.agent_action(aid, "disable", ADMIN, db)
    elif changed == "draft":
        db.put("workflows", {**flow, "status": "draft"})
    elif changed == "modified_flow":
        flow["nodes"][0]["label"] = "operator chosen step"
        db.put("workflows", flow)
    else:
        prior = db.get("agent_versions", aid + ":1")
        db.put("agent_versions", {**prior, "version": 2, "id": aid + ":2"})
        db.put("agents", {**agent, "current_version": 2})  # The flow is intentionally still bound to :1.
    tables = ("agents", "agent_versions", "workflows", "workflow_versions")
    previous = {kind: sorted(db.list(kind, p["id"]), key=lambda r: r["id"]) for kind in tables}
    with pytest.raises(service.DomainError) as caught:
        team.install(p["id"], ADMIN, db, upgrade=True)
    assert caught.value.code == "asset_conflict"
    assert previous == {kind: sorted(db.list(kind, p["id"]), key=lambda r: r["id"]) for kind in tables}


async def test_g03_worker_runs_granted_operation_then_source_withdrawal_closes_all_derived_access(db):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    src = asset(db, p, {"records": samples("G03")}, classification="local")
    t = await run(db, request(p, src))
    assert t["status"] == "succeeded" and t["result"]["usage"]["request_count"] == 0
    detail = service.task_detail(t["id"], ADMIN, db)
    (call,) = [c for c in detail["tool_calls"] if c["capability_id"] == roles.BASELINE]
    assert call["status"] == "completed" and call["source_refs"] == [src["id"]]
    assert call["node_id"] == "analyze" and call["instance_id"] in {i["id"] for i in detail["instances"]}
    (report,) = detail["artifacts"]
    result = next(x for x in report["content"]["tool_findings"] if x["tool_id"] == roles.BASELINE)
    assert (
        result["stage"] == "analyze"
        and result["result"]["difference"] == -3
        and result["result"]["ascending_rank"] == 1
    )
    assert call["result_checksum"] == digest(result["result"]) == result["result_checksum"]
    assert result["tool_call_id"] == call["id"]
    assert any(c["name"] == "independent_annual_anomaly" and c["passed"] for c in report["content"]["review"])
    with TestClient(application()) as client:
        route = f"/api/v2/tasks/{t['id']}/tool-calls"
        public = client.get(route + "/" + call["id"]).raise_for_status().json()
        assert public["result"]["source_rows"]["source_version"] == src["source_version"]
        assert digest(public["result"]) == call["result_checksum"]
        service.revoke_asset(src["id"], ADMIN, db)
        assert client.get(route).raise_for_status().json() == []
        assert client.get(route + "/" + call["id"]).status_code == 403
        hidden = client.get("/api/v2/tasks/" + t["id"]).raise_for_status().json()
        assert hidden["content_withheld"] and all(a["content"] is None for a in hidden["artifacts"])
        assert all(n["output"] is None for n in hidden["nodes"])


@pytest.mark.parametrize("forgery", ["contract", "grant"])
def test_preflight_rejects_forged_role_or_extra_grant_on_a_bound_version(db, forgery):
    p = project(db)
    pid = p["id"]
    team.install(pid, ADMIN, db)
    aid = team.role_id(pid, "analyze", "G03")
    version = db.get("agent_versions", aid + ":1")
    if forgery == "contract":
        version["role_contract"] = {**version["role_contract"], "duty": "Pretend this is authorized."}
    else:
        tool = next(t for t in catalog()["tools"] if t["id"] == roles.ROUTE)
        version["tool_refs"].append({"tool_id": roles.ROUTE, "version": tool["version"]})
        # The extra tool is genuinely installed and hash-pinned, but not assigned
        # to this scientific role. It must fail the role gate at intake.
        version["runtime_bindings"] = freeze_bindings(version)
    # Existing :1 is immutable. Exercise intake of an otherwise well-formed newer
    # binding whose professed domain role has been altered.
    db.put("agent_versions", {**version, "id": aid + ":2", "version": 2, "current_version": 2})
    db.put("agents", {**db.get("agents", aid), "current_version": 2})
    wid = team.assigned_workflow(pid, "G03")
    flow = db.get("workflows", wid)
    flow.update(
        current_version=2,
        nodes=[
            {**n, "agent_version": 2 if n["agent_id"] == aid else n["agent_version"]} for n in flow["nodes"]
        ],
    )
    db.put("workflows", flow)
    db.put("workflow_versions", {**flow, "id": wid + ":2", "definition_id": wid, "version": 2})
    src = asset(db, p, {"records": samples("G03")}, classification="local")
    with pytest.raises(service.DomainError) as caught:
        service.create_task(request(p, src), ADMIN, db, preflight=True)
    assert caught.value.code == "case_team_invalid"
    assert not db.list("tasks", pid)


def test_runtime_role_grant_cannot_cross_a_narrower_parent_permission_boundary(db):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    agent = db.get("agent_versions", team.role_id(p["id"], "analyze", "G03") + ":1")
    reader = CaseReader([], lambda: None, [])
    assert any(key.startswith("tool:series.baseline_anomaly@") for key in permissions(agent))
    with pytest.raises(runtime.TaskFailure) as caught:
        _invoke(
            operation_catalog()[roles.BASELINE],
            {"source_id": "unread"},
            reader,
            agent,
            {"case_analysis"},
            None,
        )
    assert caught.value.code == "tool_denied" and reader.source_refs == set()
