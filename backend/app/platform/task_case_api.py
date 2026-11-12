"""Owner-scoped task portal and scoped customer ingestion API."""

import base64
import hashlib
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import FileResponse, Response

from .. import auth
from . import service as s
from . import task_cases as cases
from .db import repo
from .task_case_contracts import (
    CandidateEvaluationInput,
    DataSubmissionInput,
    IngestTokenInput,
    InlineSubmissionInput,
    TaskCaseInput,
)

router = APIRouter(prefix="/task-cases", tags=["User tasks"])
candidate_router = APIRouter(prefix="/agent-candidates", tags=["Agent evaluations"])
signed_in = Depends(auth.get_current_user)
uploaded_file = File(...)


@router.post("/{case_id}/candidates", status_code=201)
def create_candidate(case_id: str, user: dict = signed_in):
    from .agent_candidates import create

    return create(case_id, user, repo)


@candidate_router.get("")
def candidate_history(project_id: str, user: dict = signed_in):
    from .agent_candidates import history

    return history(project_id, user, repo)


@candidate_router.get("/{candidate_id}")
def candidate_detail(candidate_id: str, user: dict = signed_in):
    from .agent_candidates import get

    return get(candidate_id, user, repo)


@candidate_router.post("/{candidate_id}/evaluate", status_code=202)
def evaluate_candidate(candidate_id: str, body: CandidateEvaluationInput, user: dict = signed_in):
    from .agent_candidates import evaluate

    return evaluate(candidate_id, body.task_case_ids, body.max_cost_cny, user, repo)


@candidate_router.post("/{candidate_id}/promote")
def promote_candidate(candidate_id: str, user: dict = signed_in):
    from .agent_candidates import promote

    return promote(candidate_id, user, repo)


@router.post("", status_code=201)
def create(body: TaskCaseInput, user: dict = signed_in):
    return cases.create(body, user, repo)


@router.get("")
def history(project_id: str, user: dict = signed_in):
    return cases.history(project_id, user, repo)


@router.get("/{case_id}")
def detail(case_id: str, user: dict = signed_in):
    return cases.get(case_id, user, repo)


@router.get("/{case_id}/runs/{task_id}")
def task_run_detail(case_id: str, task_id: str, user: dict = signed_in):
    with repo.transaction() as tx:
        case = cases.required(tx, case_id, user)
        if task_id not in case["task_ids"]:
            s.fail("not_found", "执行记录不属于此任务", 404)
        task = tx.get("tasks", task_id)
        if not task or task.get("task_case_id") != case_id:
            s.fail("not_found", "执行记录不可用", 404)
        for source in task["snapshot"]["inputs"]:
            s.verify_frozen_asset(source, tx.get("data_assets", source["id"]))
        return {
            "id": task_id,
            "phase": task.get("task_case_phase", "execution"),
            "status": task["status"],
            "reason_code": task.get("reason_code"),
            "review": task.get("quality_verdict"),
            "nodes": tx.list("nodes", task["project_id"], task_id=task_id),
            "instances": tx.list("instances", task["project_id"], task_id=task_id),
            "tool_calls": task.get("tool_calls", []),
            "repair_history": task.get("repair_history", []),
            "result": task.get("result"),
            "plans": tx.list("plan_versions", task["project_id"], task_id=task_id),
        }


@router.post("/{case_id}/submissions")
def submit(case_id: str, body: DataSubmissionInput, user: dict = signed_in):
    return cases.submit(case_id, body, user, repo)


@router.post("/{case_id}/upload", status_code=201)
async def upload(
    case_id: str,
    file: UploadFile = uploaded_file,
    classification: str = Form("local"),
    requirement_id: str = Form(""),
    idempotency_key: str = Form(...),
    user: dict = signed_in,
):
    cases.required(repo, case_id, user, write=True)
    if classification not in {"local", "public"} or not 1 <= len(idempotency_key) <= 130:
        s.fail("invalid_submission", "分类或提交标识无效", 422)
    return cases.upload(
        case_id,
        file.filename or "upload",
        await file.read(128_000_001),
        classification,
        requirement_id or None,
        idempotency_key,
        user,
        repo,
    )


@router.post("/{case_id}/start", status_code=202)
def start(case_id: str, user: dict = signed_in):
    return cases.start(case_id, user, repo)


@router.post("/{case_id}/cancel")
def cancel(case_id: str, user: dict = signed_in):
    return cases.cancel(case_id, user, repo)


@router.post("/{case_id}/ingest-tokens", status_code=201)
def token(case_id: str, body: IngestTokenInput, user: dict = signed_in):
    return cases.create_ingest_token(case_id, body.expires_hours, user, repo)


@router.delete("/{case_id}/ingest-tokens/{token_id}", status_code=204)
def revoke_token(case_id: str, token_id: str, user: dict = signed_in):
    with repo.transaction() as tx:
        row = cases.required(tx, case_id, user, write=True)
        for entry in row.get("ingest_tokens", []):
            if entry["id"] == token_id:
                entry["revoked"] = True
        tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.ingest_token_revoked", token_id, user)


@router.post("/{case_id}/ingest", status_code=201)
def ingest(case_id: str, body: InlineSubmissionInput, authorization: str = Header("")):
    user = cases.ingest_identity(
        case_id, authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else "", repo
    )
    result = cases.ingest_inline(case_id, body, user, repo)
    # Customer credentials grant submission only, never a read into task answers.
    return {"task_case_id": case_id, "status": result["status"], "submission_accepted": True}


@router.post("/{case_id}/ingest/upload", status_code=201)
async def ingest_upload(
    case_id: str,
    file: UploadFile = uploaded_file,
    classification: str = Form("local"),
    requirement_id: str = Form(""),
    idempotency_key: str = Form(...),
    authorization: str = Header(""),
):
    user = cases.ingest_identity(
        case_id, authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else "", repo
    )
    if classification not in {"local", "public"} or not 1 <= len(idempotency_key) <= 130:
        s.fail("invalid_submission", "分类或提交标识无效", 422)
    result = cases.upload(
        case_id,
        file.filename or "upload",
        await file.read(128_000_001),
        classification,
        requirement_id or None,
        idempotency_key,
        user,
        repo,
    )
    return {"task_case_id": case_id, "status": result["status"], "submission_accepted": True}


@router.get("/{case_id}/result/download")
def download(case_id: str, user: dict = signed_in):
    result = cases.get(case_id, user, repo)
    if result.get("content_withheld") or not result.get("result"):
        s.fail("result_unavailable", "结果尚未形成或来源已不可用", 409)
    return Response(
        json.dumps(
            {
                k: result.get(k)
                for k in ("title", "goal", "status", "result", "review", "trace", "plans", "usage")
            },
            ensure_ascii=False,
            indent=2,
        ),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="task-{case_id[:12]}.json"'},
    )


@router.get("/{case_id}/sources/{asset_id}/download")
def source_download(case_id: str, asset_id: str, user: dict = signed_in):
    from .runtime import _asset_path

    with repo.transaction() as tx:
        case = cases.required(tx, case_id, user)
        if asset_id not in case.get("input_refs", []):
            s.fail("not_found", "资料不属于此任务的输入", 404)
        asset = tx.get("data_assets", asset_id, for_update=True)
        if (
            not asset
            or asset["project_id"] != case["project_id"]
            or asset.get("task_case_id") not in {None, case_id}
        ):
            s.fail("not_found", "来源不可用", 404)
        s.usable_asset(asset)
        frozen = None
        if case.get("task_id"):
            task = tx.get("tasks", case["task_id"])
            frozen = next((a for a in task["snapshot"]["inputs"] if a["id"] == asset_id), None)
            if not frozen:
                s.fail("not_found", "资料不属于当前执行的冻结输入", 404)
        s.verify_frozen_asset(frozen or asset, asset)
        media = str(asset["media_type"]).partition(";")[0].lower()
        inline = media in {"application/pdf", "image/png", "image/jpeg"}
        safe_media = (
            media
            if inline or media in {"text/plain", "text/csv", "application/json"}
            else "application/octet-stream"
        )
        safety_headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
        if asset.get("content_ref"):
            return FileResponse(
                _asset_path(asset),
                media_type=safe_media,
                filename=asset["name"],
                content_disposition_type="inline" if inline else "attachment",
                headers=safety_headers,
            )
        content = asset["content"]
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        return Response(
            content, media_type=safe_media, headers={**safety_headers, "Content-Disposition": "attachment"}
        )


@router.get("/{case_id}/artifacts/{artifact_id}/download")
def artifact_download(case_id: str, artifact_id: str, user: dict = signed_in):
    result = cases.get(case_id, user, repo)
    allowed = {a["id"] for a in (result.get("result") or {}).get("artifacts", [])}
    if result.get("content_withheld") or artifact_id not in allowed:
        s.fail("not_found", "产物不可用或不属于当前任务", 404)
    artifact = repo.get("artifacts", artifact_id)
    content = artifact["content"]
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    return Response(
        content,
        media_type=artifact["media_type"],
        headers={"Content-Disposition": f'attachment; filename="artifact-{artifact_id[:12]}.json"'},
    )


@router.get("/{case_id}/sandbox-runs/{run_id}/outputs/{index}")
def script_output(case_id: str, run_id: str, index: int, user: dict = signed_in):
    detail = cases.get(case_id, user, repo)
    if detail.get("content_withheld"):
        s.fail("source_unavailable", "来源不可用，派生产物已屏蔽", 409)
    run = repo.get("sandbox_runs", run_id)
    if not run or run["task_id"] != detail.get("task_id") or run["project_id"] != detail["project_id"]:
        s.fail("not_found", "未找到当前任务的脚本产物", 404)
    outputs = run.get("outputs", [])
    if run["status"] != "succeeded" or not 0 <= index < len(outputs):
        s.fail("not_found", "脚本执行未生成此产物", 404)
    output = outputs[index]
    try:
        content = base64.b64decode(output["base64"], validate=True)
    except (KeyError, ValueError):
        s.fail("artifact_corrupt", "脚本产物内容无效", 409)
    if len(content) != output["bytes"] or hashlib.sha256(content).hexdigest() != output["checksum"]:
        s.fail("artifact_corrupt", "脚本产物完整性核验失败", 409)
    return Response(
        content,
        media_type=output["media_type"],
        headers={
            "Content-Disposition": "attachment; filename*=utf-8''" + quote(output["name"]),
            "X-Content-Type-Options": "nosniff",
        },
    )
