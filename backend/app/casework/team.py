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


def install(project_id, user, repository):
    from ..platform import service as s
    from ..platform.contracts import AgentInput

    with repository.transaction() as tx:
        s.project_access(project_id, user, True, tx)
        created = []
        for group, profile in catalog().items():
            title = profile["title"]
            nodes = []
            for index, stage in enumerate(STAGES):
                name = {"prepare": "资料与坐标解析员", "analyze": title + "专员", "review": "独立证据核查员"}[
                    stage
                ]
                aid = role_id(project_id, stage, group)
                agent = tx.get("agents", aid)
                if not agent:
                    agent = AgentInput(
                        project_id=project_id,
                        name=name,
                        description="用例协作组 · " + name,
                        purpose="specialized",
                        capabilities=["case_analysis"],
                        modalities=["text", "structured", "image", "audio", "video"],
                        parameters={
                            "case_stage": stage,
                            **({"case_scope": group} if stage == "analyze" else {}),
                        },
                        tool_ids=["case_analysis"],
                    ).model_dump()
                    agent.update(
                        id=aid, status="published", current_version=1, built_in=True, case_role=stage
                    )
                    tx.put("agents", agent)
                    tx.put(
                        "agent_versions",
                        {
                            **agent,
                            "id": aid + ":1",
                            "agent_id": aid,
                            "definition_id": aid,
                            "version": 1,
                            "checksum": s.digest(agent),
                        },
                    )
                    created.append(aid)
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
            if not tx.get("workflows", wid):
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
        if created:
            s.audit(tx, project_id, "casework.team_installed", project_id, user)
        return {"created": created}


def verify_team(flow, agents, project_id, code):
    from ..platform.service import fail
    from .repair_plan import allowed_action

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
        if (
            stage == "analyze"
            and strategy not in {"strict", "adaptive"}
            and not allowed_action(code, strategy)
        ):
            fail("case_team_invalid", "此分析版本的修复操作不适用于该用例", 422)
