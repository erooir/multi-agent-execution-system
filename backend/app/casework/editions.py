"""Project-published case editions. Old task snapshots continue to use their original contracts."""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from ..platform import service as s
from ..platform.tool_registry import digest

EDITION = "public-casework-v1"
STAGES = ("prepare", "analyze", "review")
NAMES = {"prepare": "原始资料核对", "analyze": "专题分析", "review": "独立证据审核"}


@lru_cache(maxsize=32)
def implementation(code):
    h = sha256()
    family = {
        "G": ["edition_geo_checks.py"],
        "B": ["edition_archive_checks.py"],
        "T": ["edition_tech_checks.py", "edition_battery.py"],
    }[code[0]]
    modules = ["edition_runtime.py", "edition_verify.py", "edition_files.py", *family]
    for name in modules:
        # Keep the original algorithm's logical filename after separating its audit adapter.
        path = Path(__file__).with_name("edition_checks.py" if name == "edition_verify.py" else name)
        h.update(name.encode())
        h.update(path.read_text(encoding="utf8").encode())
    return h.hexdigest()


def entry(project, code):
    edition = (project or {}).get("scenario_edition")
    if not edition:
        return None
    if code not in edition["cases"]:
        s.fail("case_archived", "该用例已从当前项目目录移出，请选择新版用例。", 422)
    return edition["cases"][code]


def workflow_id(project_id, code):
    return f"{project_id}-edition-{code}"


def role_id(project_id, code, stage):
    return f"{project_id}-edition-{code if stage == 'analyze' else 'shared'}-{stage}"


def _check_published(tx, table, record):
    if not record:
        return
    pin = tx.get(table, f"{record['id']}:{record['current_version']}")
    if not pin or any(
        pin.get(k) != value for k, value in record.items() if k not in {"id", "updated_at", "created_at"}
    ):
        s.fail("asset_conflict", "已有项目定义和已发布版本不一致，请先核对修改", 409)


def provision(tx, project_id, case, *, catalogue=None):
    from ..platform.contracts import AgentInput
    from ..platform.tool_registry import catalog
    from .edition_guidance import role_bindings
    from .edition_method import guidance_signature

    if catalogue is None:
        catalogue = catalog(include_readiness=False)
    changed = {"created": [], "updated": []}
    nodes = []
    code = case["id"]
    for stage in STAGES:
        aid = role_id(project_id, code, stage)
        old = tx.get("agents", aid)
        label = case["title"] + "专员" if stage == "analyze" else NAMES[stage]
        agent = AgentInput(
            project_id=project_id,
            name=label,
            description="公开原件专题分析岗位",
            purpose="specialized",
            capabilities=["case_analysis"],
            modalities=["structured"],
            tool_ids=["case_analysis"],
            model_policy="cloud",
            parameters={"case_stage": stage},
            instructions=NAMES[stage] + "须以冻结的项目公开资料及可定位的证据为依据。",
        ).model_dump()
        if stage != "prepare":
            agent.update(role_bindings(stage, code, catalogue))
        if stage == "analyze":
            analysis_grants = {
                "tools": [*agent["tool_ids"], *(ref["tool_id"] for ref in agent["tool_refs"])],
                "skills": [ref["skill_id"] for ref in agent["skill_refs"]],
            }
        agent.update(
            id=aid,
            built_in=True,
            case_role=stage,
            edition_case_id=code if stage == "analyze" else None,
            status="published",
            explicit_workflow_only=True,
            edition_executor=EDITION,
        )
        if old and (old.get("edition_executor") != EDITION or old["status"] != "published"):
            s.fail("asset_conflict", "新版协作岗位标识已占用或被停用", 409)
        if not old or any(old.get(k) != v for k, v in agent.items()):
            _check_published(tx, "agent_versions", old)
            changed["updated" if old else "created"].append(aid)
            version = (old or {}).get("current_version", 0) + 1
            agent["current_version"] = version
            tx.put("agents", agent)
            tx.put(
                "agent_versions",
                {
                    **agent,
                    "id": f"{aid}:{version}",
                    "agent_id": aid,
                    "definition_id": aid,
                    "version": version,
                    "checksum": digest(agent),
                },
            )
        else:
            version = old["current_version"]
        nodes.append(
            {
                "id": stage,
                "label": label,
                "agent_id": aid,
                "agent_version": version,
                "depends_on": list(STAGES[: STAGES.index(stage)]),
                "config": {},
            }
        )
    wid = workflow_id(project_id, code)
    flow = {
        "id": wid,
        "name": case["title"],
        "project_id": project_id,
        "status": "published",
        "task_type": "case_analysis",
        "description": "核对公开原件、专题分析和独立审核",
        "edition_executor": EDITION,
        "edition_guidance": guidance_signature(),
        "edition_analysis_grants": analysis_grants,
        "nodes": nodes,
    }
    old = tx.get("workflows", wid)
    if old and (old.get("edition_executor") != EDITION or old["status"] != "published"):
        s.fail("asset_conflict", "新版协作流程标识已占用或被停用", 409)
    if not old or any(old.get(k) != v for k, v in flow.items()):
        _check_published(tx, "workflow_versions", old)
        changed["updated" if old else "created"].append(wid)
        version = (old or {}).get("current_version", 0) + 1
        flow["current_version"] = version
        tx.put("workflows", flow)
        tx.put(
            "workflow_versions", {**flow, "id": f"{wid}:{version}", "definition_id": wid, "version": version}
        )
    return changed


def verify_team(workflow, agents, project_id, code):
    from .edition_guidance import skill_for
    from .edition_method import supported_signature

    if (
        workflow.get("definition_id") != workflow_id(project_id, code)
        or workflow.get("edition_executor") != EDITION
    ):
        s.fail("case_team_invalid", "用例流程与新版冻结合同不符", 422)
    by_id = {agent["agent_id"]: agent for agent in agents}
    signed = workflow.get("edition_guidance")
    if signed and not supported_signature(signed):
        s.fail("case_team_invalid", "请先升级本用例的证据工具与技能版本", 422)
    if [n["id"] for n in workflow["nodes"]] != list(STAGES):
        s.fail("case_team_invalid", "用例需要解析、分析、审核三个节点", 422)
    for i, node in enumerate(workflow["nodes"]):
        aid = role_id(project_id, code, node["id"])
        agent = by_id.get(aid, {})
        stage = node["id"]
        if signed and stage != "prepare":
            skills = agent.get("skill_refs", [])
            if len(skills) != 1 or skills[0]["skill_id"] != skill_for(stage, code):
                s.fail("case_team_invalid", "公开原件岗位未冻结对应的方法技能", 422)
        if (
            node["agent_id"] != aid
            or node.get("depends_on") != list(STAGES[:i])
            or node.get("config")
            or agent.get("edition_executor") != EDITION
            or agent.get("case_role") != node["id"]
            or agent.get("parameters") != {"case_stage": node["id"]}
            or agent.get("project_id") != project_id
            or agent.get("model_policy") != "cloud"
            or agent.get("tool_ids") != ["case_analysis"]
            or not agent.get("built_in")
            or node["agent_version"] != agent.get("version")
        ):
            s.fail("case_team_invalid", "新版用例的岗位、权限或依赖关系已变化", 422)


def freeze(case, inputs, body):
    if body["mode"] != "cloud" or body["case_scope"] != "project" or body.get("use_project_knowledge"):
        s.fail("case_source_scope", "此版用例采用模型调用，使用本项目随包公开原件。", 422)
    if not case.get("input_refs") or {a["id"] for a in inputs} != set(case["input_refs"]):
        s.fail("case_source_scope", "请使用新版案例所列的全部公开原件。", 422)
    for asset in inputs:
        if asset.get("classification") != "public" or asset["checksum"] != case["checksums"].get(asset["id"]):
            s.fail("input_changed", "案例资料的授权或版本已发生变化", 409)
    return {
        "case_id": case["id"],
        "edition": EDITION,
        "scope": "project",
        "profile": case,
        "signature": implementation(case["id"]),
    }


def upgrade(project_id, case_id, user, repository):
    from ..platform.tool_registry import catalog as tool_catalog

    with repository.transaction() as tx:
        project = s.project_access(project_id, user, True, tx)
        cases = (project.get("scenario_edition") or {}).get("cases", {})
        if case_id is not None and case_id not in cases:
            s.fail("case_not_found", "未找到项目内的公开用例", 404)
        selected = [cases[case_id]] if case_id is not None else cases.values()
        catalogue = tool_catalog(include_readiness=False)
        changes = {"created": [], "updated": []}
        for case in selected:
            if case.get("input_refs"):
                delta = provision(tx, project_id, case, catalogue=catalogue)
                for key, ids in delta.items():
                    changes[key].extend(ids)
        if any(changes.values()):
            s.audit(tx, project_id, "casework.edition_roles_upgraded", project_id, user, changes)
        return changes


def directory(project, repository=None):
    edition = project.get("scenario_edition")
    if not edition:
        return None
    from .edition_method import guidance_signature

    pid = project["id"]
    # Fetch only published workflow metadata. Original files and full skill packages
    # are unnecessary for browsing the case list.
    pins = {}
    if repository is not None:
        flows = repository.project_records_by_id(
            "workflows", pid, (workflow_id(pid, code) for code in edition["cases"])
        )
        versions = repository.project_records_by_id(
            "workflow_versions",
            pid,
            (
                f"{wid}:{flow['current_version']}"
                for wid, flow in flows.items()
                if flow.get("status") == "published"
            ),
        )
        pins = {version["definition_id"]: version for version in versions.values()}
    signature = guidance_signature()
    entries = []
    for code, case in sorted(edition["cases"].items()):
        ready = bool(case.get("input_refs"))
        wid = workflow_id(pid, code)
        pin = pins.get(wid, {})
        grants = pin.get("edition_analysis_grants") or {}
        role = {
            "stage": "analyze",
            "case_id": code,
            "operation": "基于冻结原件的专题分析",
            "duty": case["goal"],
            "inputs": case["files"],
            "outputs": [case["deliverables"]],
            "acceptance": [case.get("completion") or case["scope"]],
            "grants": grants.get("tools", ["case_analysis"] if pin else []),
            "skills": grants.get("skills", []),
            "shared_cases": [code],
        }
        entries.append(
            {
                "id": code,
                "title": case["title"],
                "inputs": case["files"],
                "outputs": [case["deliverables"]],
                "edition": EDITION,
                "scope_note": case["scope"],
                "role_contract": role,
                "installed": ready,
                "project_tools_current": ready and pin.get("edition_guidance") == signature,
                "workflow_id": wid,
                "case_request": {
                    "name": case["title"],
                    "goal": case["goal"],
                    "input_refs": case.get("input_refs", []),
                },
                "specialists": list(NAMES.values()),
            }
        )
    return entries
