"""Authenticated v2 product API. No native model execution routes are mounted."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import mimetypes
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select

from .. import auth
from ..model_gateway import HARD_CAP, model_gateway
from . import contracts as c
from . import service as s
from .db import TERMINAL, now, repo, tables
from .public_evidence import PublicEvidenceRequest

router = APIRouter(prefix="/api/v2")
identity = APIRouter(prefix="/api")
signed_in = Depends(auth.get_current_user)
read = Depends(auth.require_roles("admin", "operator", "reviewer"))
edit = Depends(auth.require_roles("admin", "operator"))
question_write = Depends(auth.require_roles("requester", "operator", "admin"))
admin = Depends(auth.require_roles("admin"))


@identity.post("/auth/login")
async def login(body: dict, request: Request, response: Response):
    return await asyncio.to_thread(
        auth.login, body.get("username", ""), body.get("password", ""), response, request
    )


@identity.post("/auth/register", status_code=201)
async def register(body: dict, request: Request, response: Response):
    return await asyncio.to_thread(auth.register, body, response, request)


@identity.get("/auth/me")
def me(user: dict = signed_in):
    return user


@identity.post("/auth/logout")
def logout(request: Request, response: Response):
    return auth.logout(request, response)


@identity.get("/users")
def users(user: dict = admin):
    return auth.list_users()


@identity.post("/users")
def create_user(body: dict, user: dict = admin):
    return auth.create_user(body, user)


@identity.put("/users/{user_id}")
def update_user(user_id: str, body: dict, user: dict = admin):
    return auth.update_user(user_id, body, user)


@router.get("/health")
@identity.get("/health")
def health():
    return {"status": "ok", "service": "hangzhi-agent-platform", "version": "2.0.0"}


@router.get("/question-projects")
def question_projects(user: dict = signed_in):
    """The question portal needs project names, never asset definitions or service configuration."""
    return [
        {"id": p["id"], "name": p["name"]}
        for p in repo.list("projects")
        if user["role"] == "admin" or user["id"] in p["members"]
    ]


def workers_view(user=None, task_ids=None):
    result = []
    if task_ids is None and user and user["role"] != "admin":
        task_ids = {t["id"] for t in s.visible("tasks", user)}
    for worker in repo.list("workers"):
        try:
            elapsed = (
                datetime.now(UTC) - s.timestamp(worker.get("heartbeat_at", worker["updated_at"]))
            ).total_seconds()
        except s.DomainError:
            elapsed = 999999
        item = {
            **worker,
            "health": "online" if elapsed < 45 and worker.get("status") != "stopped" else "offline",
        }
        if task_ids is not None:
            item["active_task_ids"] = [
                task_id for task_id in item.get("active_task_ids", []) if task_id in task_ids
            ]
        if user and user.get("role") != "admin":
            item.pop("hostname", None)
            item.pop("pid", None)
        result.append(item)
    return result


def budget_view():
    try:
        return {**model_gateway.budget(), "available": True}
    except RuntimeError as exc:
        if "ledger_missing" not in str(exc) and "ledger_invalid" not in str(exc):
            raise
        return {
            "available": False,
            "error": "ledger_missing_or_invalid",
            "project_cap_cny": HARD_CAP,
            "limit_cny": None,
            "spent_cny": None,
            "reserved_cny": None,
            "remaining_cny": None,
            "request_count": None,
            "pricing_note": "历史账本缺失；付费调用关闭，请从完整备份恢复，禁止新建账本替代历史。",
        }


def _task_source_ids(task):
    return [item["id"] for item in task.get("snapshot", {}).get("inputs", [])] or task.get("request", {}).get(
        "input_refs", []
    )


def task_view(record):
    output = s.safe_task(record)
    blocked = s.unavailable_sources(_task_source_ids(record), record["project_id"], repo)
    if blocked:
        output = s.redact_derived(output, blocked)
        if output.get("result"):
            output["result"] = s.redact_derived(output["result"], blocked)
    return output


def derived_view(kind, record):
    """Every collection uses the same withdrawal rules as the detail/download APIs."""
    if kind == "tasks":
        return task_view(record)
    source_ids = []
    if kind == "memories":
        source_ids = record.get("source_refs", [])
    elif kind == "improvements":
        source_ids = [
            asset["id"]
            for sample in record.get("samples", [])
            for asset in sample.get("snapshot", {}).get("inputs", [])
        ]
    elif kind == "evaluations":
        candidate = repo.get("improvements", record.get("candidate_id"))
        if candidate and candidate["project_id"] == record["project_id"]:
            source_ids = [
                asset["id"]
                for sample in candidate.get("samples", [])
                for asset in sample.get("snapshot", {}).get("inputs", [])
            ]
        else:
            return s.redact_derived(record, ["evaluation_source_unavailable"])
    else:
        return record
    blocked = s.unavailable_sources(source_ids, record["project_id"], repo)
    if kind == "memories" and (
        record.get("status") != "active" or s.timestamp(record["expires_at"]) <= datetime.now(UTC)
    ):
        blocked = blocked or source_ids or ["memory_unavailable"]
    output = s.redact_derived(record, blocked)
    if kind == "improvements" and not blocked:
        output = deepcopy(output)
        for sample in output.get("samples", []):
            sample["snapshot"]["inputs"] = [
                s.safe_asset(asset) for asset in sample["snapshot"].get("inputs", [])
            ]
    return output


def visible_records(kind, user):
    return [derived_view(kind, record) for record in s.visible(kind, user)]


@router.get("/bootstrap")
def bootstrap(user: dict = read, scope: Literal["all", "current"] = "all", project_id: str | None = None):
    """The workbench asks for one project's collections; existing unscoped callers retain the all-project view."""
    scoped = scope == "current" or project_id is not None
    if project_id:
        s.project_access(project_id, user, repository=repo)
    projects = [p for p in repo.list("projects") if user["role"] == "admin" or user["id"] in p["members"]]
    if not projects and user["role"] in ("admin", "operator"):
        # Initialization creates executable templates, never synthetic runs or data.
        with repo.transaction() as tx:
            projects = [
                p for p in tx.list("projects") if user["role"] == "admin" or user["id"] in p["members"]
            ]
            if not projects:
                projects = [
                    s.create_project(
                        {"name": "我的工作空间", "description": "独立项目权限和任务数据"}, user, tx
                    )
                ]
    result = {"user": user, "projects": projects}
    selected = project_id or (projects[0]["id"] if projects else None)
    if scoped:
        result["current_project_id"] = selected
    for kind in (
        "agents",
        "workflows",
        "tasks",
        "data_assets",
        "schedules",
        "improvements",
        "connectors",
        "memories",
        "evaluations",
    ):
        if scoped:
            result[kind] = [derived_view(kind, row) for row in repo.list(kind, selected)] if selected else []
        else:
            result[kind] = visible_records(kind, user)
    result["data_assets"] = [s.safe_asset(a) for a in result["data_assets"]]
    tasks = result["tasks"]
    workers = workers_view(user, {t["id"] for t in tasks} if scoped or user["role"] != "admin" else None)
    terminal = [t for t in tasks if t["status"] in TERMINAL]
    succeeded = sum(t["status"] == "succeeded" for t in terminal)
    result.update(
        capabilities=s.CAPABILITIES,
        budget=budget_view(),
        workers=workers,
        stats={
            "task_count": len(tasks),
            "active_tasks": sum(t["status"] not in TERMINAL for t in tasks),
            "succeeded_tasks": succeeded,
            "failed_tasks": sum(t["status"] == "failed" for t in terminal),
            "sample_count": len(terminal),
            "success_rate": succeeded / len(terminal) if terminal else None,
            "window": "当前项目全部记录（含明确标记的演练）"
            if scoped
            else "全部本地记录（含明确标记的演练）",
        },
        system={
            "version": "2.0.0",
            "database": repo.engine.dialect.name,
            "runtime": "Agno AgentOS",
            "agno_version": importlib.metadata.version("agno"),
            "worker_online": any(w["health"] == "online" for w in workers),
            "offline": os.environ.get("HANGZHI_OFFLINE", "false").lower() == "true",
            **model_gateway.config_status(),
        },
    )
    if user["role"] == "admin":
        result["users"] = auth.list_users()
    return result


@router.get("/projects")
def projects(user: dict = read):
    return [p for p in repo.list("projects") if user["role"] == "admin" or user["id"] in p["members"]]


@router.get("/casework/catalog")
def case_catalog(project_id: str, user: dict = read):
    from ..casework.roles import contract
    from ..casework.team import STAGES, assigned_workflow, catalog, role_id
    from .tool_registry import catalog as tool_catalog

    s.project_access(project_id, user)
    flows = {w["id"]: w for w in repo.list("workflows", project_id)}
    versions = {v["id"]: v for v in repo.list("agent_versions", project_id)}
    tool_specs = {v["id"]: v for v in tool_catalog()["tools"]}
    items = []
    for code, profile in sorted(catalog().items()):
        wid = assigned_workflow(project_id, code)
        flow = flows.get(wid, {})
        roles = {stage: contract(stage, code) for stage in ("prepare", "analyze", "review")}
        pinned = (
            repo.get("workflow_versions", f"{wid}:{flow['current_version']}")
            if flow.get("status") == "published"
            else None
        )
        current = bool(pinned) and [n["id"] for n in pinned["nodes"]] == list(STAGES)
        if current:
            for n in pinned["nodes"]:
                role = roles[n["id"]]
                asset = versions.get(f"{n['agent_id']}:{n['agent_version']}", {})
                specs = [tool_specs.get(key) for key in role["grants"]]
                if (
                    n["agent_id"] != role_id(project_id, n["id"], code)
                    or asset.get("role_contract") != role
                    or not all(specs)
                    or asset.get("runtime_bindings", {}).get("tools") != specs
                    or asset.get("tool_refs")
                    != [{"tool_id": s["id"], "version": s["version"]} for s in specs]
                ):
                    current = False
                    break
        items.append(
            {
                "id": code,
                **profile,
                "workflow_id": wid,
                "installed": flow.get("status") == "published",
                "roles": roles,
                "role_contract": roles["analyze"],
                "project_tools_current": current,
            }
        )
    return items


@router.post("/casework/install")
def install_cases(project_id: str, user: dict = edit):
    from ..casework.team import install

    return install(project_id, user, repo)


@router.post("/casework/upgrade")
def upgrade_case_tools(project_id: str, user: dict = edit):
    from ..casework.team import install

    return install(project_id, user, repo, upgrade=True)


@router.post("/casework/semantic-evidence", status_code=202)
def create_public_evidence(body: PublicEvidenceRequest, user: dict = edit):
    from .public_evidence import submit

    return submit(body, user, repo)


@router.get("/tasks/{task_id}/semantic-evidence/report")
def public_evidence_report(task_id: str, user: dict = read):
    from .public_evidence import report

    content = report(task_id, user, repo)
    return Response(
        content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="public-evidence-{quote(task_id, safe="")}.txt"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/agents/activity/summary")
def agent_activity(project_id: str, window_hours: int = Query(168, ge=1, le=720), user: dict = read):
    from ..casework.monitor import activity

    return activity(project_id, user, repo, window_hours)


@router.post("/model/connectivity")
async def model_connectivity(user: dict = edit):
    return await model_gateway.connectivity()


@router.post("/tasks/preflight", response_model=c.TaskPreflight)
def task_preflight(body: c.TaskInput, user: dict = edit):
    return s.create_task(body.model_dump(), user, preflight=True)


@router.get("/capabilities", response_model=c.CapabilityCatalog | list[dict])
def capability_catalog(project_id: str | None = None, user: dict = read):
    if project_id is None:
        return s.CAPABILITIES
    return s.capability_catalog(project_id, user, repo)


@router.get("/capability-definitions")
def capability_definitions(project_id: str, user: dict = read):
    return s.capability_definitions(project_id, user, repo)


@router.post("/capability-definitions/skills/import", status_code=201)
async def import_skill(
    file: UploadFile = File(...),  # noqa: B008
    project_id: str = Form(...),
    user: dict = edit,
):
    package = await file.read(5_000_001)
    if len(package) > 5_000_000:
        s.fail("skill_package_too_large", "Skill ZIP 不能超过5MB", 413)
    return s.capability_view(s.import_skill(package, project_id, user, repo))


@router.post("/capability-definitions/tools", status_code=201)
def create_http_tool(body: c.HttpToolInput, user: dict = admin):
    return s.capability_view(s.create_http_tool(body.model_dump(), user, repo))


@router.post("/capability-definitions/toolkits", status_code=201)
def create_native_toolkit(body: c.NativeToolkitInput, user: dict = admin):
    return s.capability_view(s.create_native_toolkit(body.model_dump(), user, repo))


@router.post("/capability-definitions/mcp", status_code=201)
def create_mcp_server(body: c.McpServerInput, user: dict = admin):
    return s.capability_view(s.create_mcp_server(body.model_dump(), user, repo))


@router.get("/capability-definitions/{definition_id}/versions")
def capability_versions(definition_id: str, user: dict = read):
    return s.capability_versions(definition_id, user, repo)


@router.post("/capability-definitions/{definition_id}/discover")
async def discover_mcp(definition_id: str, user: dict = admin):
    return s.capability_view(await s.discover_mcp(definition_id, user, repo))


@router.post("/capability-definitions/{definition_id}/publish")
def publish_capability(definition_id: str, body: c.SkillPublishInput | None = None, user: dict = edit):
    definition = s.required("capability_definitions", definition_id, user)
    if definition["kind"] != "skill" and user["role"] != "admin":
        s.fail("permission_denied", "外部网络能力发布要求管理员角色", 403)
    return s.capability_view(
        s.publish_capability(definition_id, user, repo, body.tool_bindings if body else None)
    )


@router.post("/capability-definitions/{definition_id}/{action}")
def capability_action(definition_id: str, action: str, user: dict = edit):
    if action not in {"disable", "enable", "revoke", "destroy"}:
        s.fail("invalid_action", "未知能力生命周期操作", 422)
    definition = s.required("capability_definitions", definition_id, user)
    if definition["kind"] != "skill" and user["role"] != "admin":
        s.fail("permission_denied", "外部网络能力管理要求管理员角色", 403)
    return s.capability_view(s.capability_action(definition_id, action, user, repo))


@router.post("/agents/{agent_id}/tools/{tool_id}/preview")
def preview_tool(agent_id: str, tool_id: str, body: c.ToolPreviewInput, user: dict = edit):
    from .tool_preview import preview

    return preview(repo, user, agent_id, tool_id, body)


@router.get("/tasks/{task_id}/tool-calls", response_model=list[c.ToolCallRecord])
def task_tool_calls(task_id: str, user: dict = read):
    task = s.required("tasks", task_id, user)
    if s.unavailable_sources(_task_source_ids(task), task["project_id"], repo):
        return []
    return task.get("tool_calls", [])


@router.get("/tasks/{task_id}/tool-calls/{call_id}", response_model=c.ToolCallDetail)
def task_tool_call_detail(task_id: str, call_id: str, user: dict = read):
    task = s.required("tasks", task_id, user)
    if s.unavailable_sources(_task_source_ids(task), task["project_id"], repo):
        s.fail("input_unavailable", "资料已撤回或过期，调用的输入和输出不再提供", 403)
    call = next((item for item in task.get("tool_calls", []) if item["id"] == call_id), None)
    if call is None:
        s.fail("not_found", "调用记录不存在", 404)
    checkpoint = task.get("tool_checkpoints", {}).get(call_id, {})
    return {"call": call, "arguments": checkpoint.get("arguments"), "result": checkpoint.get("result")}


@router.post("/projects", status_code=201)
def create_project(body: c.ProjectInput, user: dict = edit):
    return s.create_project(body.model_dump(), user)


@router.put("/projects/{project_id}/members")
def members(project_id: str, body: c.MemberInput, user: dict = admin):
    with repo.transaction() as tx:
        project = s.project_access(project_id, user, True, tx)
        known = {u["id"] for u in auth.list_users()}
        if set(body.members) - known:
            s.fail("unknown_user", "成员列表包含不存在的用户")
        project["members"] = sorted(set(body.members) | {project["owner_id"]})
        s.audit(tx, project_id, "project.members_changed", project_id, user)
        return tx.put("projects", project)


@router.get("/agents")
def agents(user: dict = read):
    return s.visible("agents", user)


@router.post("/agents", status_code=201)
def create_agent(body: c.AgentInput, user: dict = edit):
    return s.save_agent(body.model_dump(), user)


@router.get("/agents/{agent_id}")
def agent(agent_id: str, user: dict = read):
    return s.required("agents", agent_id, user)


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, body: c.AgentInput, user: dict = edit):
    return s.save_agent(body.model_dump(), user, agent_id)


@router.get("/agents/{agent_id}/versions")
def agent_versions(agent_id: str, user: dict = read):
    s.required("agents", agent_id, user)
    return repo.list("agent_versions", definition_id=agent_id)


@router.get("/agents/{agent_id}/instances")
def instances(agent_id: str, user: dict = read):
    s.required("agents", agent_id, user)
    return repo.list("instances", agent_id=agent_id)


@router.post("/agents/{agent_id}/{action}")
def agent_action(agent_id: str, action: str, user: dict = edit):
    return s.agent_action(agent_id, action, user)


@router.delete("/agents/{agent_id}")
def destroy_agent(agent_id: str, user: dict = edit):
    return s.agent_action(agent_id, "destroy", user)


@router.get("/workflows")
def workflows(user: dict = read):
    return s.visible("workflows", user)


@router.post("/workflows", status_code=201)
def create_workflow(body: c.WorkflowInput, user: dict = edit):
    return s.save_workflow(body.model_dump(), user)


@router.get("/workflows/{workflow_id}")
def workflow(workflow_id: str, user: dict = read):
    return s.required("workflows", workflow_id, user)


@router.put("/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: c.WorkflowInput, user: dict = edit):
    return s.save_workflow(body.model_dump(), user, workflow_id)


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(workflow_id: str, user: dict = edit):
    return s.publish_workflow(workflow_id, user)


@router.get("/workflows/{workflow_id}/versions")
def workflow_versions(workflow_id: str, user: dict = read):
    s.required("workflows", workflow_id, user)
    return repo.list("workflow_versions", definition_id=workflow_id)


@router.get("/data-assets")
def assets(user: dict = read):
    return [s.safe_asset(a) for a in s.visible("data_assets", user)]


@router.post("/data-assets", status_code=201)
def create_asset(body: c.AssetInput, user: dict = edit):
    return s.safe_asset(s.create_asset(body.model_dump(), user))


@router.post("/data-assets/upload", status_code=201)
async def upload(
    file: UploadFile = File(...),  # noqa: B008 -- FastAPI dependency marker
    project_id: str = Form(...),
    name: str = Form(""),
    source_id: str = Form(...),
    source_version: str = Form("1"),
    provenance: str = Form(...),
    classification: str = Form("local"),
    retention_days: int = Form(30),
    modality: str = Form(""),
    csv_encoding: str = Form("utf-8"),
    user: dict = edit,
):
    s.project_access(project_id, user, True)
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    max_bytes = (
        128_000_000
        if suffix in {".mp3", ".mp4", ".wav", ".flac", ".mov", ".webm", ".m4a", ".ogg"}
        else 10_000_000
    )
    content = await file.read(max_bytes + 1)
    if not content or len(content) > max_bytes:
        s.fail("input_too_large", f"文件不能为空或超过{max_bytes // 1_000_000}MB", 413)
    allowed_suffixes = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".jsonl",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".wav",
        ".mp3",
        ".flac",
        ".ogg",
        ".m4a",
        ".mp4",
        ".webm",
        ".mov",
        ".pdf",
        ".docx",
        ".xlsx",
        ".geojson",
        ".srt",
        ".vtt",
    }
    if suffix not in allowed_suffixes:
        s.fail(
            "unsupported_format",
            "文件格式未接入",
            422,
        )
    media_type = mimetypes.guess_type(filename)[0] or (
        "application/json" if suffix == ".jsonl" else "application/octet-stream"
    )
    media_type = {
        ".csv": "text/csv",
        ".jsonl": "application/x-ndjson",
        ".geojson": "application/geo+json",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(suffix, media_type)
    inferred = (
        "image"
        if media_type.startswith("image/")
        else "audio"
        if media_type.startswith("audio/")
        else "video"
        if media_type.startswith("video/")
        else "structured"
        if suffix in {".csv", ".json", ".jsonl", ".xlsx", ".geojson"}
        else "text"
    )
    if modality and modality != inferred:
        s.fail("modality_mismatch", "文件类型与声明模态不一致", 422)
    value = None
    if inferred in ("text", "structured") and suffix not in {".pdf", ".docx", ".xlsx"}:
        try:
            if suffix == ".csv" and csv_encoding not in {"utf-8", "windows-1252"}:
                s.fail("unsupported_format", "CSV编码仅支持UTF-8或显式声明的Windows-1252", 422)
            encoding = "cp1252" if suffix == ".csv" and csv_encoding == "windows-1252" else "utf-8-sig"
            value = content.decode(encoding)
            if encoding == "cp1252":
                media_type += ";charset=windows-1252"
        except UnicodeDecodeError:
            s.fail("unsupported_format", "文本文件目前仅接受 UTF-8 txt / md / JSON / CSV", 422)
        if suffix in {".json", ".geojson"}:
            try:
                value = json.loads(value)
            except ValueError:
                s.fail("invalid_json", "JSON文件无法解析", 422)
        if suffix == ".csv" and (len(content) > 1_900_000 or csv_encoding == "windows-1252"):
            value = None  # Retain the hashed file, do not duplicate its entire text into every DB row.
    if inferred == "image":
        from io import BytesIO

        from PIL import Image

        try:
            with Image.open(BytesIO(content)) as img:
                if img.width * img.height > 25_000_000:
                    s.fail("image_too_large", "图片像素总数不能超过2500万", 413)
                img.verify()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            s.fail("invalid_media", "图片无法解码", 422)
    asset = s.create_asset(
        {
            "project_id": project_id,
            "name": name or filename,
            "modality": inferred,
            "media_type": media_type,
            "content": value,
            "source_id": source_id,
            "source_version": source_version,
            "provenance": provenance,
            "classification": classification,
            "retention_days": retention_days,
        },
        user,
        file_data=content,
    )
    return s.safe_asset(asset)


@router.post("/data-assets/{asset_id}/revoke")
def revoke(asset_id: str, user: dict = edit):
    return s.safe_asset(s.revoke_asset(asset_id, user))


@router.get("/data-assets/{asset_id}/content")
def asset_content(asset_id: str, user: dict = read):
    asset = s.required("data_assets", asset_id, user)
    s.usable_asset(asset)
    s.verify_frozen_asset(asset, asset)
    if asset.get("content_ref"):
        path = Path(asset["content_ref"]).resolve()
        if not path.is_relative_to(s.ASSET_DIR.resolve()):
            s.fail("invalid_storage_ref", "非法存储引用", 403)
        return FileResponse(path, media_type=asset["media_type"], filename=asset["name"])
    return {"content": asset["content"], "checksum": asset["checksum"]}


@router.get("/tasks")
def tasks(user: dict = read):
    return visible_records("tasks", user)


@router.post("/questions", response_model=c.QuestionAnswer, status_code=202)
def ask_question(body: c.QuestionInput, user: dict = question_write):
    from . import questions

    return questions.submit(body, user, repo)


@router.get("/questions", response_model=list[c.QuestionAnswer])
def question_history(project_id: str, user: dict = signed_in):
    from . import questions

    return questions.history(project_id, user, repo)


@router.get("/questions/{question_id}", response_model=c.QuestionAnswer)
def question_answer(question_id: str, user: dict = signed_in):
    from . import questions

    return questions.get(question_id, user, repo)


@router.post("/tasks", status_code=202)
def create_task(body: c.TaskInput, user: dict = edit):
    return task_view(s.create_task(body.model_dump(), user))


@router.get("/tasks/{task_id}")
def task(task_id: str, user: dict = read):
    return s.task_detail(task_id, user)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, user: dict = edit):
    return task_view(s.cancel_task(task_id, user))


@router.get("/tasks/{task_id}/result")
def result(task_id: str, user: dict = read):
    task = s.required("tasks", task_id, user)
    if task["status"] not in TERMINAL:
        return JSONResponse(status_code=202, content={"task_id": task_id, "status": task["status"]})
    return task_view(task)["result"]


def _event_page(task_id, after, limit=500):
    table = tables["events"]
    query = (
        select(table)
        .where(table.c.task_id == task_id, table.c.sequence > after)
        .order_by(table.c.sequence)
        .limit(limit)
    )
    with repo.engine.connect() as connection:
        return [repo._record(row) for row in connection.execute(query)]


def _cursor(after, last_id):
    try:
        value = int(last_id)
        if value < 0:
            raise ValueError()
        return max(after, value)
    except ValueError:
        s.fail("invalid_cursor", "事件游标必须为非负整数", 422)


@router.get("/tasks/{task_id}/events")
def events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    user: dict = read,
):
    task = s.required("tasks", task_id, user)
    blocked = s.unavailable_sources(_task_source_ids(task), task["project_id"], repo)
    return [s.redact_derived(item, blocked) for item in _event_page(task_id, after, limit)]


@router.get("/tasks/{task_id}/stream")
async def stream(task_id: str, request: Request, after: int = Query(default=0, ge=0), user: dict = read):
    s.required("tasks", task_id, user)
    cursor = _cursor(after, request.headers.get("last-event-id", "0"))

    async def generate():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                current_user = await auth.get_current_user(request)
                task = s.required("tasks", task_id, current_user)
            except (HTTPException, s.DomainError):
                yield 'event: access_revoked\ndata: {"code":"access_revoked"}\n\n'
                return
            page = _event_page(task_id, cursor)
            blocked = s.unavailable_sources(_task_source_ids(task), task["project_id"], repo)
            for item in page:
                item = s.redact_derived(item, blocked)
                cursor = item["sequence"]
                yield f"id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            if len(page) == 500:
                continue
            if task["status"] in TERMINAL:
                break
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


def artifact_record(artifact_id, user):
    artifact = s.required("artifacts", artifact_id, user)
    for source in artifact.get("source_refs", []):
        source_id = source if isinstance(source, str) else source.get("asset_id", source.get("id"))
        if source_id:
            s.usable_asset(s.required("data_assets", source_id, user))
    return artifact


@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str, user: dict = read):
    return artifact_record(artifact_id, user)


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, user: dict = read):
    record = artifact_record(artifact_id, user)
    value = record["content"]
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return Response(
        data,
        media_type=record.get("media_type", "application/json"),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote('artifact-' + artifact_id + '.txt', safe='')}"
        },
    )


@router.get("/artifacts/{artifact_id}/case-package")
def case_package(artifact_id: str, user: dict = read):
    from ..casework.delivery import bundle

    record = artifact_record(artifact_id, user)
    if record["type"] != "case_report" or not record.get("content"):
        s.fail("unsupported_output", "当前产物不是可交付的用例报告", 422)
    task = s.required("tasks", record["task_id"], user)
    payload = bundle(
        record["content"],
        {"task_id": task["id"], "name": task["name"], "status": task["status"], "revision": task["revision"]},
    )
    artifact_record(artifact_id, user)
    return Response(
        payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="case-{record["content"]["case_id"]}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/budget")
def budget(user: dict = read):
    return budget_view()


@router.get("/workers")
def workers(user: dict = read):
    return workers_view(user)


@router.get("/metrics", response_model=c.OperationalMetrics)
def metrics(
    project_id: str | None = None,
    window_hours: int = Query(default=24, ge=1, le=720),
    include_rehearsal: bool = False,
    user: dict = read,
):
    return s.operational_metrics(
        user, repo, project_id=project_id, window_hours=window_hours, include_rehearsal=include_rehearsal
    )


@router.get("/audits")
def audits(user: dict = read):
    return s.visible("audits", user)[-200:]


@router.get("/schedules")
def schedules(user: dict = read):
    return s.visible("schedules", user)


@router.post("/schedules", status_code=201)
def create_schedule(body: c.ScheduleInput, user: dict = edit):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    s.project_access(body.project_id, user, True)
    if body.task_template.project_id != body.project_id:
        s.fail("permission_denied", "计划模板必须属于同项目", 403)
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError:
        s.fail("invalid_timezone", "未知时区", 422)
    data = body.model_dump()
    data.update(
        status="enabled",
        created_by=user["id"],
        next_run_at=s.timestamp(body.next_run_at).isoformat() if body.next_run_at else now(),
        missed_policy="skip",
        last_task_id=None,
    )
    return repo.put("schedules", data)


@router.post("/schedules/{schedule_id}/{action}")
def schedule_action(schedule_id: str, action: str, user: dict = edit):
    if action not in ("enable", "disable"):
        s.fail("invalid_action", "未知计划操作")
    record = s.required("schedules", schedule_id, user, True)
    record["status"] = "enabled" if action == "enable" else "disabled"
    return repo.put("schedules", record)


@router.get("/connectors")
def connectors(user: dict = read):
    return s.visible("connectors", user)


@router.post("/connectors", status_code=201)
def create_connector(body: c.ConnectorInput, user: dict = edit):
    from .knowledge import create_connector

    return create_connector(body.model_dump(), user)


@router.post("/knowledge/search")
async def knowledge_search(body: c.KnowledgeQuery, user: dict = read):
    from .knowledge import search

    return await search(body.model_dump(), user)


@router.get("/memories")
def memories(user: dict = read):
    return visible_records("memories", user)


@router.post("/memories", status_code=201)
def create_memory(body: c.MemoryInput, user: dict = edit):
    from .knowledge import create_memory

    return create_memory(body.model_dump(), user)


@router.get("/improvements")
def improvements(user: dict = read):
    return visible_records("improvements", user)


@router.post("/improvements", status_code=201)
def create_improvement(body: c.ImprovementInput, user: dict = edit):
    from .improvement import create

    return derived_view("improvements", create(body.model_dump(), user))


@router.post("/improvements/{candidate_id}/{action}")
async def improvement_action(candidate_id: str, action: str, user: dict = edit):
    from .improvement import evaluate, promote, rollback

    if action == "evaluate":
        return derived_view("improvements", await evaluate(candidate_id, user))
    if action not in ("promote", "rollback"):
        s.fail("invalid_action", "未知改进操作")
    if user["role"] != "admin":
        s.fail("permission_denied", "版本晋升和回滚要求管理员角色", 403)
    return derived_view(
        "improvements", promote(candidate_id, user) if action == "promote" else rollback(candidate_id, user)
    )


@router.get("/evaluations")
def evaluations(user: dict = read):
    return visible_records("evaluations", user)
