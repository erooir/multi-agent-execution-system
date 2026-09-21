"""Authenticated product API. Agno's unrestricted model routes are never public."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import engine, reports, workflows
from .auth import (
    create_user,
    get_current_user,
    list_users,
    login,
    logout,
    register,
    require_roles,
    update_user,
)
from .config import get_settings, save_settings
from .knowledge import knowledge
from .model_gateway import model_gateway
from .storage import store

router = APIRouter(prefix="/api")
read = Depends(get_current_user)
edit = Depends(require_roles("admin", "operator"))
reviewer = Depends(require_roles("admin", "reviewer"))
report_editor = Depends(require_roles("admin", "operator", "reviewer"))
admin = Depends(require_roles("admin"))


def required(kind: str, item_id: str) -> dict:
    item = store.get(kind, item_id)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def safe_document(doc: dict) -> dict:
    allowed = {
        "id",
        "name",
        "project_id",
        "visibility",
        "status",
        "chunk_count",
        "size",
        "kind",
        "created_at",
        "updated_at",
        "error",
    }
    return {k: v for k, v in doc.items() if k in allowed}


def safe_run(run: dict) -> dict:
    return {k: v for k, v in run.items() if k != "workflow_snapshot"}


def audit(action: str, entity: str, user: dict, detail=None):
    store.audit(action, entity, detail or {}, user=user["username"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "research-agent-workbench"}


@router.post("/auth/login")
async def auth_login(body: dict, request: Request, response: Response):
    return await asyncio.to_thread(
        login, body.get("username", ""), body.get("password", ""), response, request
    )


@router.post("/auth/register", status_code=201)
async def auth_register(body: dict, request: Request, response: Response):
    return await asyncio.to_thread(register, body, response, request)


@router.get("/auth/me")
def auth_me(user: dict = read):
    return user


@router.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    return logout(request, response)


@router.get("/users")
def list_users_route(user: dict = admin):
    return list_users()


@router.post("/users", status_code=201)
def create_user_route(body: dict, user: dict = admin):
    return create_user(body, user)


@router.put("/users/{item_id}")
def update_user_route(item_id: str, body: dict, user: dict = admin):
    return update_user(item_id, body, user)


def system_info():
    status = model_gateway.config_status()
    status.update(engine.runtime_status())
    status["runtime_mode"] = "local-connected"
    status["default_mode"] = get_settings()["default_mode"]
    status["skills"] = knowledge.skills()
    embedding = getattr(knowledge, "embedding_status", None)
    status["embedding_status"] = embedding() if callable(embedding) else embedding or "按需加载本地嵌入模型"
    return status


@router.get("/bootstrap")
def bootstrap(user: dict = read):
    result = {
        kind: store.list(kind)
        for kind in ("projects", "agents", "workflows", "reports", "evaluations", "samples", "approvals")
    }
    result["reports"] = [{k: v for k, v in r.items() if k != "content"} for r in result["reports"]]
    result["runs"] = [
        {
            k: v
            for k, v in safe_run(r).items()
            if k
            not in {
                "steps",
                "logs",
                "evidence",
                "skill_results",
                "reviewed_content",
                "approved_intermediate_content",
            }
        }
        for r in store.list("runs")[:60]
    ]
    result["documents"] = [safe_document(d) for d in store.list("documents")]
    result["skills"] = knowledge.skills()
    result["stats"] = {
        "projects": len(result["projects"]),
        "agents": len(result["agents"]),
        "workflows": len(result["workflows"]),
        "documents": len(result["documents"]),
        "runs": len(store.list("runs")),
        "completed_runs": sum(r.get("status") == "completed" for r in store.list("runs")),
        "pending_approvals": sum(a.get("status") == "pending" for a in result["approvals"]),
        "reports": len(result["reports"]),
    }
    result.update(budget=model_gateway.budget(), system=system_info(), settings=get_settings(), user=user)
    return result


@router.get("/projects")
def list_projects(user: dict = read):
    return store.list("projects")


@router.post("/projects", status_code=201)
def create_project(body: dict, user: dict = edit):
    if not str(body.get("name", "")).strip():
        raise HTTPException(400, "项目名称不能为空")
    category = body.get("category", "technology")
    if category not in {"technology", "geography", "situational"}:
        raise HTTPException(400, "项目分类无效")
    item = store.save(
        "projects",
        {
            "name": str(body["name"])[:100],
            "description": str(body.get("description", ""))[:5000],
            "category": category,
        },
    )
    audit("project.create", item["id"], user)
    return item


@router.put("/projects/{item_id}")
def update_project(item_id: str, body: dict, user: dict = edit):
    item = required("projects", item_id)
    if "name" in body and not str(body["name"]).strip():
        raise HTTPException(400, "项目名称不能为空")
    if body.get("category", item.get("category")) not in {"technology", "geography", "situational"}:
        raise HTTPException(400, "项目分类无效")
    item.update({k: body[k] for k in ("name", "description", "category") if k in body})
    item = store.save("projects", item)
    audit("project.update", item_id, user)
    return item


@router.delete("/projects/{item_id}")
def delete_project(item_id: str, user: dict = edit):
    required("projects", item_id)
    if any(
        r.get("project_id") == item_id
        for kind in ("runs", "documents", "workflows")
        for r in store.list(kind)
    ):
        raise HTTPException(409, "项目仍有关联任务、流程或资料，暂不能删除")
    store.delete("projects", item_id)
    audit("project.delete", item_id, user)
    return {"deleted": True}


@router.get("/agents")
def list_agents(user: dict = read):
    return store.list("agents")


def agent_data(body: dict, previous=None):
    item = dict(previous or {})
    item.update(
        {
            k: body[k]
            for k in ("name", "description", "role", "instructions", "skill_ids", "enabled")
            if k in body
        }
    )
    if not str(item.get("name", "")).strip():
        raise HTTPException(400, "智能体名称不能为空")
    if item.get("role", "writer") not in {"planner", "retriever", "writer", "coordinator", "parser"}:
        raise HTTPException(400, "智能体角色无效")
    valid_skills = {s["id"] for s in knowledge.skills()}
    if not isinstance(item.get("skill_ids", []), list) or any(
        s not in valid_skills for s in item.get("skill_ids", [])
    ):
        raise HTTPException(400, "包含未注册的技能")
    item.update(model="deepseek-flash", version=int(item.get("version", 0)) + 1)
    item.setdefault("enabled", True)
    item.setdefault("role", "writer")
    item.setdefault("skill_ids", [])
    item.setdefault("instructions", "")
    return item


@router.post("/agents", status_code=201)
def create_agent(body: dict, user: dict = edit):
    item = store.save("agents", agent_data(body))
    audit("agent.create", item["id"], user)
    return item


@router.put("/agents/{item_id}")
def update_agent(item_id: str, body: dict, user: dict = edit):
    item = store.save("agents", agent_data(body, required("agents", item_id)))
    audit("agent.update", item_id, user)
    return item


@router.delete("/agents/{item_id}")
def delete_agent(item_id: str, user: dict = edit):
    required("agents", item_id)
    if any(
        n.get("data", {}).get("agent_id") == item_id
        for w in store.list("workflows")
        for n in w.get("nodes", [])
    ):
        raise HTTPException(409, "有工作流引用此智能体，请先解除绑定")
    store.delete("agents", item_id)
    audit("agent.delete", item_id, user)
    return {"deleted": True}


@router.post("/agents/{item_id}/test")
async def test_agent(item_id: str, body: dict, user: dict = edit):
    agent = required("agents", item_id)
    if not agent.get("enabled", True):
        raise HTTPException(400, "此智能体已禁用")
    message, mode = str(body.get("message", "")), body.get("mode", get_settings()["default_mode"])
    mode = "live" if mode == "real" else mode
    if not message.strip():
        raise HTTPException(400, "请输入测试问题")
    if mode == "rehearsal":
        result = {
            "text": f"【演练模式；未调用模型】\n智能体：{agent['name']}\n角色：{agent.get('role')}\n收到问题：{message}\n已验证配置和调用路径；真实能力需切换真实模型测试。",
            "mode": mode,
        }
    elif mode == "live":
        result = await model_gateway.complete(
            message, system=str(agent.get("instructions", ""))[:10000], purpose="agent_test", max_tokens=1200
        )
        result["mode"] = mode
    else:
        raise HTTPException(400, "模式无效")
    audit("agent.test", item_id, user, {"mode": mode})
    return result


@router.get("/skills")
def skills(user: dict = read):
    return knowledge.skills()


@router.post("/skills/{item_id}/test")
async def test_skill(item_id: str, body: dict, user: dict = edit):
    body.setdefault("mode", get_settings()["default_mode"])
    result = await knowledge.execute(item_id, body)
    audit("skill.test", item_id, user, {"mode": body.get("mode", "rehearsal")})
    return result


@router.get("/documents")
def documents(project_id: str | None = None, user: dict = read):
    return [
        safe_document(d)
        for d in store.list("documents")
        if not project_id or d.get("project_id") == project_id
    ]


@router.post("/documents/upload", status_code=201)
async def upload_document(
    file: Annotated[UploadFile, File()],
    project_id: Annotated[str, Form()],
    visibility: Annotated[str, Form()] = "local",
    user: dict = edit,
):
    required("projects", project_id)
    if visibility not in {"external", "local"}:
        raise HTTPException(400, "资料可见性必须为 external 或 local")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "单个文件不能超过 20 MB")
    item = await asyncio.to_thread(
        knowledge.ingest, file.filename or "upload.txt", content, project_id, visibility
    )
    audit("document.upload", item["id"], user, {"visibility": visibility, "size": len(content)})
    return safe_document(item)


@router.delete("/documents/{item_id}")
def delete_document(item_id: str, user: dict = edit):
    required("documents", item_id)
    knowledge.remove(item_id)
    audit("document.delete", item_id, user)
    return {"deleted": True}


@router.get("/documents/{item_id}/chunks")
def document_chunks(item_id: str, user: dict = read):
    required("documents", item_id)
    return knowledge.chunks(item_id)


@router.get("/documents/{item_id}/file")
def document_file(item_id: str, user: dict = read):
    item = required("documents", item_id)
    path = knowledge.document_path(item_id)
    if not path.exists():
        raise HTTPException(404, "原始文件不存在")
    return FileResponse(path, filename=item["name"], content_disposition_type="attachment")


@router.post("/knowledge/search")
def search_knowledge(body: dict, user: dict = read):
    return knowledge.search(
        str(body.get("query", "")),
        project_id=body.get("project_id"),
        document_ids=body.get("document_ids"),
        limit=min(max(int(body.get("limit", 6)), 1), 30),
        semantic=bool(body.get("semantic", False)),
    )


@router.get("/knowledge/graph")
def graph(project_id: str | None = None, user: dict = read):
    return knowledge.graph(project_id)


@router.get("/workflows")
def list_workflows(user: dict = read):
    return store.list("workflows")


@router.post("/workflows", status_code=201)
def create_workflow(body: dict, user: dict = edit):
    item = workflows.save_workflow(body)
    audit("workflow.create", item["id"], user)
    return item


@router.put("/workflows/{item_id}")
def update_workflow(item_id: str, body: dict, user: dict = edit):
    item = workflows.save_workflow(body, item_id)
    audit("workflow.update", item_id, user, {"version": item["version"]})
    return item


@router.post("/workflows/{item_id}/validate")
def validate_workflow(item_id: str, body: dict | None = None, user: dict = edit):
    item = required("workflows", item_id)
    # A canvas may validate unsaved nodes without mutating the published record.
    if body:
        item = {**item, **{k: body[k] for k in ("nodes", "edges") if k in body}}
    return workflows.validate_workflow(item)


@router.post("/workflows/{item_id}/publish")
def publish_workflow(item_id: str, user: dict = edit):
    item = workflows.publish(item_id)
    audit("workflow.publish", item_id, user, {"version": item["version"]})
    return item


@router.post("/workflows/{item_id}/clone", status_code=201)
def clone_workflow(item_id: str, user: dict = edit):
    original = deepcopy(required("workflows", item_id))
    original["name"] += " · 副本"
    item = workflows.save_workflow(original)
    audit("workflow.clone", item["id"], user, {"source": item_id})
    return item


@router.get("/workflows/{item_id}/versions")
def workflow_versions(item_id: str, user: dict = read):
    workflows.snapshot(required("workflows", item_id))
    return workflows.versions(item_id)


@router.post("/workflows/{item_id}/restore")
def restore_workflow(item_id: str, body: dict, user: dict = edit):
    item = workflows.restore(item_id, int(body.get("version", 0)))
    audit("workflow.restore", item_id, user, {"source_version": body.get("version")})
    return item


@router.post("/plan", status_code=202)
async def plan(body: dict, user: dict = edit):
    job = engine.start_plan(body, user)
    audit("workflow.plan", job["id"], user, {"mode": body.get("mode", "rehearsal")})
    return job


@router.get("/planning/{item_id}")
def planning_job(item_id: str, user: dict = read):
    return required("planning_jobs", item_id)


@router.post("/runs", status_code=201)
async def start_run(body: dict, user: dict = edit):
    return safe_run(engine.create_run(body, user))


@router.get("/runs")
def list_runs(user: dict = read):
    return [safe_run(r) for r in store.list("runs")]


@router.get("/runs/{item_id}")
def get_run(item_id: str, user: dict = read):
    return safe_run(required("runs", item_id))


@router.post("/runs/{item_id}/cancel")
async def cancel_run(item_id: str, user: dict = edit):
    return safe_run(engine.cancel_run(item_id, user))


@router.post("/runs/{item_id}/retry")
async def retry_run(item_id: str, user: dict = edit):
    return safe_run(engine.retry_run(item_id, user))


@router.post("/runs/{item_id}/review")
async def review_run(item_id: str, body: dict, user: dict = reviewer):
    return safe_run(engine.review_run(item_id, body, user))


@router.get("/runs/{item_id}/events")
async def run_events(item_id: str, request: Request, after: int = 0, user: dict = read):
    required("runs", item_id)
    try:
        after = max(after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        pass

    async def stream():
        cursor = after
        idle = 0
        while not await request.is_disconnected():
            updates = engine.events(item_id, cursor)
            for event in updates:
                cursor = event["sequence"]
                yield f"id: {cursor}\nevent: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            run = store.get("runs", item_id)
            if run["status"] in {"completed", "failed", "cancelled", "interrupted", "waiting_review"}:
                yield f"event: status\ndata: {json.dumps({'status': run['status'], 'run_id': item_id})}\n\n"
                return
            idle += 1
            if idle % 25 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/reports")
def list_reports(user: dict = read):
    return store.list("reports")


@router.get("/reports/{item_id}")
def get_report(item_id: str, user: dict = read):
    return required("reports", item_id)


@router.put("/reports/{item_id}")
def update_report(item_id: str, body: dict, user: dict = report_editor):
    previous = required("reports", item_id)
    item = reports.update_report(item_id, str(body.get("content", previous["content"])), body.get("title"))
    audit("report.update", item_id, user, {"version": item.get("version")})
    return item


@router.get("/reports/{item_id}/versions")
def report_versions(item_id: str, user: dict = read):
    required("reports", item_id)
    return reports.versions(item_id)


@router.get("/reports/{item_id}/export")
def export_report(item_id: str, format: str = "md", user: dict = read):
    required("reports", item_id)
    if format not in {"md", "docx", "html"}:
        raise HTTPException(400, "只支持 md、docx、html 导出")
    data, mime, filename = reports.export_report(item_id, format)
    from urllib.parse import quote

    audit("report.export", item_id, user, {"format": format})
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/reports/{item_id}/restore")
def restore_report(item_id: str, body: dict, user: dict = report_editor):
    required("reports", item_id)
    item = reports.restore(item_id, int(body.get("version", 0)))
    audit("report.restore", item_id, user, {"source_version": body.get("version")})
    return item


@router.get("/samples")
def samples(user: dict = read):
    return store.list("samples")


@router.post("/evaluations", status_code=201)
async def start_evaluation(body: dict, user: dict = edit):
    return engine.create_evaluation(body, user)


@router.get("/evaluations")
def list_evaluations(user: dict = read):
    return store.list("evaluations")


@router.get("/evaluations/{item_id}")
def get_evaluation(item_id: str, user: dict = read):
    return required("evaluations", item_id)


@router.get("/audits")
def audits(user: dict = read):
    return store.list("audits")[:500]


@router.get("/settings")
def settings(user: dict = read):
    return {**get_settings(), "system": system_info(), "budget": model_gateway.budget()}


@router.put("/settings")
def update_settings(body: dict, user: dict = admin):
    result = save_settings(body)
    audit(
        "settings.update",
        "settings",
        user,
        {k: v for k, v in body.items() if k in {"budget_limit_cny", "limit_cny", "default_mode"}},
    )
    return result


@router.get("/budget")
def budget(user: dict = read):
    return model_gateway.budget()
