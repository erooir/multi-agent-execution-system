"""Project-scoped views of immutable plan versions and evaluated publications."""

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .. import auth
from . import service as s
from .dameng import json_scalar
from .db import repo, tables
from .workflow_learning import frozen_components, publication, source_available, verified_repairs

router = APIRouter(prefix="/workflow-learning", tags=["Workflow learning"])
read = Depends(auth.require_roles("admin", "operator", "reviewer"))


@router.get("")
def index(project_id: str, user: dict = read):
    with repo.transaction() as tx:
        s.project_access(project_id, user, repository=tx)
        plans, cases, tasks = (tables[name] for name in ("plan_versions", "task_cases", "tasks"))
        case_id = json_scalar(tx.engine, plans.c.payload, "task_case_id")
        task_id = json_scalar(tx.engine, plans.c.payload, "task_id")
        query = (
            select(
                plans.c.id,
                plans.c.status,
                plans.c.created_at,
                case_id.label("task_case_id"),
                task_id.label("task_id"),
                json_scalar(tx.engine, plans.c.payload, "version").label("version"),
                json_scalar(tx.engine, plans.c.payload, "phase").label("phase"),
                json_scalar(tx.engine, plans.c.payload, "workflow_id").label("workflow_id"),
            )
            .select_from(plans.join(cases, cases.c.id == case_id).join(tasks, tasks.c.id == task_id))
            .where(
                plans.c.project_id == project_id,
                cases.c.project_id == project_id,
                tasks.c.project_id == project_id,
                json_scalar(tx.engine, cases.c.payload, "deleted_at").is_(None),
                json_scalar(tx.engine, tasks.c.payload, "deleted_at").is_(None),
            )
            .order_by(plans.c.created_at.desc(), plans.c.id.desc())
            .limit(50)
        )
        recent = [dict(row._mapping) for row in tx.connection.execute(query)]
        flows = [
            {
                "candidate_id": candidate["id"],
                "workflow_id": candidate["promoted_workflow_id"],
                "name": candidate["name"],
                "evaluation_id": candidate["current_evaluation_id"],
            }
            for candidate in tx.list("agent_candidates", project_id, status="promoted")
            if candidate.get("promoted_workflow_id")
        ]
        return {"plans": recent, "publications": flows}


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: str, user: dict = read):
    with repo.transaction() as tx:
        plan = s.required("plan_versions", plan_id, user, repository=tx)
        project = plan["project_id"]
        task = s.required("tasks", plan["task_id"], user, repository=tx)
        case = tx.get("task_cases", plan["task_case_id"])
        if not case or case["project_id"] != project or case.get("deleted_at"):
            s.fail("not_found", "任务不可用", 404)
        if not source_available(tx, task, project):
            s.fail("source_unavailable", "来源已撤回、过期或变化，计划详情已屏蔽", 409)
        private = user.get("role") == "admin" or case["owner_id"] == user["id"]
        steps = []
        for i, step in enumerate(plan.get("steps", [])):
            components = step.get("components")
            if components is None and step.get("agent_version") and step.get("agent_id"):
                agent = tx.get("agent_versions", f"{step['agent_id']}:{step['agent_version']}")
                if agent and agent["project_id"] == project:
                    components = frozen_components(agent)
            steps.append(
                {
                    "label": step.get("label") if private else f"步骤 {i + 1}",
                    "depends_on": step.get("depends_on") or [],
                    "success_criteria": step.get("success_criteria", []) if private else [],
                    "agent_id": step.get("agent_id"),
                    "agent_version": step.get("agent_version"),
                    "components": components,
                }
            )
        return {
            "id": plan["id"],
            "task_id": task["id"],
            "version": plan["version"],
            "title": case.get("title") if private else None,
            "phase": plan.get("phase"),
            "requirements": plan.get("requirements", []) if private else [],
            "status": plan["status"],
            "reason": plan.get("reason") if private else None,
            "checksum": plan.get("checksum"),
            "steps": steps,
            "publication": plan.get("workflow_publication"),
            "inputs": [
                {"source_version": asset["source_version"], "id": asset["id"], "checksum": asset["checksum"]}
                for asset in task["snapshot"].get("inputs", [])
            ],
            "lessons": plan.get("reviewed_guidance", []),
        }


@router.get("/publications/{candidate_id}")
def publication_detail(candidate_id: str, user: dict = read):
    with repo.transaction() as tx:
        candidate = s.required("agent_candidates", candidate_id, user, repository=tx)
        item = publication(tx, candidate)
        if not item:
            s.fail("workflow_reuse_unavailable", "流程未通过评测，或来源、依赖或发布状态已变化", 409)
        flow = item["flow"]
        return {
            "id": flow["definition_id"],
            "version": flow["version"],
            "checksum": flow["checksum"],
            "evaluation_id": item["evaluation"]["id"],
            "checks": item["evaluation"]["checks"],
            "steps": [
                {
                    "label": agent["name"],
                    "agent_id": agent["id"],
                    "agent_version": agent["version"],
                    "components": frozen_components(agent),
                    "depends_on": node["depends_on"],
                }
                for agent, node in zip(item["agents"], flow["nodes"], strict=True)
            ],
            "repairs": verified_repairs(item["origin"]),
        }
