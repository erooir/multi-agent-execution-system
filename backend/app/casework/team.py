"""Project-owned, immutable specialist teams. Installation creates no tasks or observations."""

import hashlib
import json
from pathlib import Path

GROUPS = {"B": "历史与事件核查", "T": "科技证据分析", "G": "地理与时序分析"}
STAGES = ("prepare", "analyze", "review")


def catalog():
    from . import document_cases, geo_cases

    return {**document_cases.CATALOG, **geo_cases.CATALOG}


def frozen_profile(code):
    from .provider_versions import profile as provider_profile
    from .repair_plan import POLICY_VERSION

    definition = catalog()[code]
    providers = provider_profile()
    root = Path(__file__).resolve().parent
    # A deployment change must not silently execute new logic on an old task.
    h = hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True).encode())
    h.update(json.dumps(providers, sort_keys=True).encode())
    for path in sorted(root.glob("*.py")):
        active = {
            "team",
            "engine",
            "control",
            "records",
            "coverage",
            "translation",
            "media",
            "provider_versions",
            "reference_batches",
            "repair_plan",
            "roles",
            "role_tools",
            "role_evidence",
            "role_analysis",
            "document_common",
            "geo_raster",
        }
        prefix = "geo_" if code.startswith("G") else "document_"
        if not (
            path.stem in active or path.stem.startswith(prefix) or (code == "B03" and path.stem == "vision")
        ):
            continue
        h.update(path.name.encode())
        h.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return {
        "case_id": code,
        "profile": definition,
        "providers": providers,
        "repair_policy_version": POLICY_VERSION,
        "signature": h.hexdigest(),
    }


def assigned_workflow(project_id, code):
    return f"{project_id}-case-{code}"


def role_id(project_id, stage, family):
    return f"{project_id}-case-{family if stage == 'analyze' else 'shared'}-{stage}"


def install(project_id, user, repository, *, upgrade=False, case_id=None):
    from ..platform import service as s
    from ..platform.contracts import AgentInput
    from ..platform.tool_registry import catalog as tool_catalog
    from ..platform.tool_registry import freeze_bindings
    from .roles import contract

    with repository.transaction() as tx:
        s.project_access(project_id, user, True, tx)
        tx.get("projects", project_id, for_update=True)
        created, updated = [], []
        versions = {t["id"]: t["version"] for t in tool_catalog()["tools"]}
        original_versions = {}
        for group, profile in catalog().items():
            if case_id is not None and group != case_id:
                continue
            title = profile["title"]
            nodes = []
            for index, stage in enumerate(STAGES):
                name = {"prepare": "资料与坐标解析员", "analyze": title + "专员", "review": "独立证据核查员"}[
                    stage
                ]
                aid = role_id(project_id, stage, group)
                agent = tx.get("agents", aid)
                first_visit = aid not in original_versions
                if first_visit:
                    original_versions[aid] = agent["current_version"] if agent else None
                if first_visit and (not agent or upgrade):
                    role = contract(stage, group)
                    refs = [{"tool_id": key, "version": versions[key]} for key in role["grants"]]
                    if agent and (
                        not agent.get("built_in")
                        or agent.get("case_role") != stage
                        or agent["status"] != "published"
                        or agent["name"] != name
                        or agent.get("description") != "用例协作组 · " + name
                        or agent.get("purpose") != "specialized"
                        or agent.get("structure") != "atomic"
                        or set(agent.get("modalities", []))
                        != {"text", "structured", "image", "audio", "video"}
                        or agent.get("instructions") not in ("", role["duty"])
                        or agent.get("execution_strategy") != "fixed"
                        or agent.get("model_policy") != "local"
                        or agent.get("members")
                        or agent.get("skill_refs")
                        or agent.get("tool_ids") != ["case_analysis"]
                        or agent.get("capabilities") != ["case_analysis"]
                        or (not agent.get("role_contract") and agent.get("tool_refs"))
                    ):
                        s.fail("asset_conflict", "项目含自定义或停用的用例岗位，请先核对版本。", 409)
                    if (
                        agent
                        and agent.get("role_contract")
                        and (agent.get("role_contract") != role or agent.get("tool_refs") != refs)
                    ):
                        s.fail("asset_conflict", "岗位授权或职责已被编辑，不能自动覆盖。", 409)
                    if agent and agent.get("role_contract") == role and agent.get("tool_refs") == refs:
                        if agent.get("runtime_bindings") != freeze_bindings(agent):
                            s.fail("tool_version_changed", "工具实现与已发布版本不同，须显式迁移。", 409)
                    else:
                        version = (
                            max(
                                (v["version"] for v in tx.list("agent_versions", definition_id=aid)),
                                default=0,
                            )
                            + 1
                        )
                        previous = agent or {}
                        agent = AgentInput(
                            project_id=project_id,
                            name=name,
                            description="用例协作组 · " + name,
                            instructions=role["duty"],
                            purpose="specialized",
                            capabilities=["case_analysis"],
                            modalities=["text", "structured", "image", "audio", "video"],
                            parameters={
                                **previous.get("parameters", {}),
                                "case_stage": stage,
                                **({"case_scope": group} if stage == "analyze" else {}),
                            },
                            tool_ids=["case_analysis"],
                            tool_refs=refs,
                        ).model_dump()
                        agent = {
                            **previous,
                            **agent,
                            "id": aid,
                            "status": "published",
                            "current_version": version,
                            "built_in": True,
                            "case_role": stage,
                            "role_contract": role,
                        }
                        agent["runtime_bindings"] = freeze_bindings(agent)
                        tx.put("agents", agent)
                        tx.put(
                            "agent_versions",
                            {
                                **agent,
                                "id": f"{aid}:{version}",
                                "agent_id": aid,
                                "definition_id": aid,
                                "version": version,
                                "checksum": s.digest(agent),
                            },
                        )
                        (updated if previous else created).append(aid)
                if agent.get("case_role") != stage or agent.get("project_id") != project_id:
                    s.fail("asset_conflict", "专员标识冲突，原资产未覆盖", 409)
                nodes.append(
                    {
                        "id": stage,
                        "label": name,
                        "agent_id": aid,
                        "agent_version": agent["current_version"],
                        "depends_on": []
                        if index == 0
                        else ["prepare"]
                        if index == 1
                        else ["prepare", "analyze"],
                        "config": {},
                    }
                )
            wid = assigned_workflow(project_id, group)
            existing_flow = tx.get("workflows", wid)
            if not existing_flow:
                flow = {
                    "id": wid,
                    "project_id": project_id,
                    "name": title + "协作流程",
                    "description": "解析 → 专业分析 → 独立审核",
                    "task_type": "case_analysis",
                    "nodes": nodes,
                    "status": "published",
                    "current_version": 1,
                }
                tx.put("workflows", flow)
                tx.put("workflow_versions", {**flow, "id": wid + ":1", "definition_id": wid, "version": 1})
                created.append(wid)
            elif upgrade and existing_flow["nodes"] != nodes:
                published = tx.get("workflow_versions", f"{wid}:{existing_flow['current_version']}")
                prior_nodes = [
                    {**node, "agent_version": original_versions[node["agent_id"]]} for node in nodes
                ]
                if (
                    existing_flow["status"] != "published"
                    or not published
                    or published["nodes"] != existing_flow["nodes"]
                    or existing_flow["nodes"] != prior_nodes
                ):
                    s.fail("asset_conflict", "协作流程已有修改，禁止覆盖。", 409)
                version = max(v["version"] for v in tx.list("workflow_versions", definition_id=wid)) + 1
                flow = tx.put("workflows", {**existing_flow, "nodes": nodes, "current_version": version})
                tx.put(
                    "workflow_versions",
                    {**flow, "id": f"{wid}:{version}", "definition_id": wid, "version": version},
                )
                updated.append(wid)
        if created or updated:
            s.audit(
                tx,
                project_id,
                "casework.team_upgraded" if updated else "casework.team_installed",
                project_id,
                user,
                {"created": len(created), "updated": len(updated)},
            )
        return {"created": created, "updated": updated}


def verify_team(flow, agents, project_id, code):
    from ..platform.service import fail
    from .repair_plan import allowed_action
    from .roles import contract

    nodes = flow["nodes"]
    if [n["id"] for n in nodes] != list(STAGES):
        fail("case_team_invalid", "用例协作流程必须含解析、专业分析和独立核查三个节点", 422)
    for n, stage, deps in zip(nodes, STAGES, ([], ["prepare"], ["prepare", "analyze"]), strict=True):
        agent = next(
            (a for a in agents if a["agent_id"] == n["agent_id"] and a["version"] == n["agent_version"]), None
        )
        if (
            not agent
            or agent["agent_id"] != role_id(project_id, stage, code)
            or (stage == "analyze" and agent.get("parameters", {}).get("case_scope") != code)
            or agent.get("parameters", {}).get("case_stage") != stage
            or n["depends_on"] != deps
            or n.get("config")
        ):
            fail("case_team_invalid", "节点角色、工具或依赖与已验证流程不一致", 422)
        strategy = agent.get("parameters", {}).get("case_strategy", "strict")
        if agent.get("role_contract") or agent.get("tool_refs"):
            expected = contract(stage, code)
            if (
                agent.get("role_contract") != expected
                or [r["tool_id"] for r in agent.get("tool_refs", [])] != expected["grants"]
            ):
                fail("case_team_invalid", "冻结岗位的职责或工具授权与用例不匹配", 422)
        if (
            stage == "analyze"
            and strategy not in {"strict", "adaptive"}
            and not allowed_action(code, strategy)
        ):
            fail("case_team_invalid", "此分析版本的修复操作不适用于该用例", 422)
