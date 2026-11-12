"""Durable user tasks, resumable data intake and immutable execution plans.

TaskCase is an input/conversation boundary. TaskRun remains the sole execution,
lease, billing and review authority. Waiting for data never retains a worker.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import mimetypes
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError
from openpyxl.utils.exceptions import InvalidFileException
from pypdf.errors import PdfReadError

from ..casework import intake_plan, team
from . import service as s
from .contracts import AgentInput
from .db import TERMINAL
from .task_case_contracts import DataSubmissionInput, TaskCaseInput
from .tool_registry import catalog, freeze_bindings, validate_frozen

EDITABLE = {"awaiting_data", "ready", "failed", "partial", "cancelled"}
FORMATS = ["PDF", "DOCX", "TXT", "JSON", "CSV", "XLSX", "PNG", "JPEG", "MP3", "WAV", "MP4"]


def _principal(user):
    if user.get("role") not in {"admin", "operator", "requester"}:
        s.fail("permission_denied", "当前身份不能创建或修改任务", 403)
    return {**user, "role": "operator"} if user["role"] == "requester" else user


def required(tx, case_id, user, *, write=False, include_deleted=False):
    record = tx.get("task_cases", case_id, for_update=write)
    if (
        not record
        or (record.get("deleted_at") and not include_deleted)
        or (record["owner_id"] != user["id"] and user.get("role") != "admin")
    ):
        s.fail("not_found", "未找到此任务", 404)
    s.project_access(record["project_id"], user, repository=tx)
    if write:
        _principal(user)
    return record


def _available(tx, record):
    result = []
    for asset in tx.list("data_assets", record["project_id"]):
        if asset.get("task_case_id") and asset["task_case_id"] != record["id"]:
            continue
        try:
            s.usable_asset(asset)
        except s.DomainError:
            continue
        result.append(asset)
    return result


def _case_match(goal):
    normalized = intake_plan._normal(goal)
    casualty_domain = bool(
        re.search(r"伤亡|平民|死亡|受伤|ohchr|casualt|civilian|killed|injured", normalized)
    )
    ranked = sorted(
        ((intake_plan._intent(code, normalized, profile), code) for code, profile in team.catalog().items()),
        reverse=True,
    )
    score, code = ranked[0]
    if score >= 6 and (score > ranked[1][0] or score >= 10) and (code != "B05" or casualty_domain):
        return code
    # A monthly reconciliation request can mention a chart/report without a case label.
    if (
        casualty_domain
        and re.search(r"月度|每月|monthly", normalized)
        and re.search(r"复算|核对|核查|合计|总数|reconcil", normalized)
    ):
        return "B05"
    return None


def _requirements(record, assets):
    code = _case_match(record["goal"])
    forced = record["data_requirement"]
    data_needed = forced == "required" or (
        forced == "auto"
        and (
            code
            or re.search(
                r"资料|数据|文件|报告|表格|图片|图像|音频|视频|统计|提取|转写|比较|对比|复算|核查|核对|上传|csv|pdf|dataset",
                record["goal"],
                re.IGNORECASE,
            )
        )
    )
    if forced == "none":
        data_needed = False
        code = None
    if not data_needed:
        return [], code
    profile = team.catalog().get(code, {})
    titles = profile.get("inputs", ["支持任务目标的参考资料"])
    refs = set(record.get("input_refs", []))
    submitted = [a for a in assets if a["id"] in refs]
    profiles = [profile_asset(a) for a in submitted]
    valid = any(p["readable"] and p["nonempty"] for p in profiles)
    if code == "B05" and submitted:
        valid = any(
            p["readable"]
            and p["nonempty"]
            and (
                p.get("pages")
                or bool({"month", "deaths", "injured", "月份", "死亡", "受伤"} & set(p.get("fields", [])))
            )
            for p in profiles
        )
    matches = []
    terms = intake_plan._terms(intake_plan._normal(record["goal"]))
    for asset in assets:
        name = intake_plan._normal(asset["name"] + " " + asset.get("source_id", ""))
        if (code and code.lower() in name) or any(term in name for term in terms):
            matches.append(asset["id"])
    # Requirements are a single evidence bundle; its fields can live in one PDF.
    return [
        {
            "id": "reference-data",
            "title": "任务参考资料",
            "required": True,
            "description": "；".join(titles) + "。平台将检查内容、覆盖范围与来源，文件存在不代表数据完整。",
            "formats": (["PDF", "JSON", "CSV", "XLSX"] if code == "B05" else FORMATS),
            "fields": profile.get("record_fields", []),
            "status": "provided" if valid else "available" if matches else "missing",
            "matched_asset_ids": matches,
            "profiles": [
                {
                    **p,
                    "record_count": p.get("records"),
                    "page_count": p.get("pages"),
                    "status": "readable" if p["readable"] else "unreadable",
                }
                for p in profiles
            ],
            "validation": "执行时核验实际内容、时间范围、口径与交付字段",
        }
    ], code


def profile_asset(asset):
    """Inspect actual file structure locally; never infer completeness from a filename."""
    from .runtime import _asset_bytes

    result = {
        "asset_id": asset["id"],
        "name": asset["name"],
        "readable": False,
        "nonempty": False,
        "fields": [],
        "records": None,
        "pages": None,
        "validation": "structure_only",
    }
    try:
        s.verify_frozen_asset(asset, asset)
        media = str(asset.get("media_type", "")).split(";")[0]
        value = asset.get("content")
        if media == "application/pdf":
            from pypdf import PdfReader

            pdf = PdfReader(io.BytesIO(_asset_bytes(asset)))
            if pdf.is_encrypted:
                raise ValueError("encrypted_pdf")
            result.update(
                readable=True,
                nonempty=bool(pdf.pages),
                pages=len(pdf.pages),
                validation="页结构可读；正文和图表完整性在执行时核验",
            )
            return result
        if media.endswith("spreadsheetml.sheet"):
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(_asset_bytes(asset)), read_only=True, data_only=True)
            try:
                sheet = workbook.active
                fields = [str(v) for v in next(sheet.iter_rows(values_only=True), ()) if v is not None]
                result.update(
                    readable=True,
                    nonempty=bool(fields),
                    fields=fields[:100],
                    records=max(0, sheet.max_row - 1),
                )
            finally:
                workbook.close()
            return result
        if media.endswith("wordprocessingml.document"):
            from docx import Document

            document = Document(io.BytesIO(_asset_bytes(asset)))
            result.update(readable=True, nonempty=bool(document.paragraphs or document.tables))
            return result
        if asset["modality"] in {"image", "audio", "video"}:
            result.update(
                readable=asset.get("bytes", 0) > 0,
                nonempty=asset.get("bytes", 0) > 0,
                validation="已校验文件完整性；解码与内容在执行时核验",
            )
            return result
        if value is None:
            value = _asset_bytes(asset).decode("utf-8-sig")
        if media == "text/csv":
            reader = csv.DictReader(io.StringIO(value))
            fields = reader.fieldnames or []
            count = sum(1 for _ in reader)
            result.update(
                readable=True, nonempty=bool(fields) and count > 0, fields=fields[:100], records=count
            )
            return result
        if isinstance(value, str) and media in {"application/json", "application/geo+json"}:
            value = json.loads(value)
        if isinstance(value, (dict, list)):
            rows = value.get("records", [value]) if isinstance(value, dict) else value
            if not isinstance(rows, list):
                rows = [rows]
            fields = list(dict.fromkeys(k for row in rows[:100] if isinstance(row, dict) for k in row))
            result.update(readable=True, nonempty=bool(rows), fields=fields[:100], records=len(rows))
        else:
            result.update(
                readable=isinstance(value, str), nonempty=bool(str(value).strip()), characters=len(str(value))
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        UnicodeDecodeError,
        PdfReadError,
        BadZipFile,
        PackageNotFoundError,
        InvalidFileException,
    ):
        result["validation"] = "无法解析资料结构，请检查文件或更换格式"
    return result


def create(body, user, repository):
    body = TaskCaseInput.model_validate(body).model_dump()
    _principal(user)
    with repository.transaction() as tx:
        s.project_access(body["project_id"], user, repository=tx)
        tx.get("projects", body["project_id"], for_update=True)
        cid = s.digest([body["project_id"], user["id"], body["idempotency_key"]])[:48]
        old = tx.get("task_cases", cid)
        if old:
            if old.get("deleted_at"):
                s.fail("task_case_deleted", "此提交对应的任务已删除，请使用新的提交标识创建任务", 409)
            if old["request_hash"] != s.digest(body):
                s.fail("idempotency_conflict", "同一提交标识已用于不同任务", 409)
            return view(tx, old, user)
        row = {
            **body,
            "id": cid,
            "owner_id": user["id"],
            "request_hash": s.digest(body),
            "title": body["title"] or body["goal"][:80],
            "input_refs": [],
            "task_ids": [],
            "task_id": None,
            "status": "ready",
            "revision": 0,
            "missing": [],
            "ingest_tokens": [],
        }
        row["requirements"], row["matched_case_id"] = _requirements(row, _available(tx, row))
        if row["requirements"]:
            row["status"] = "awaiting_data"
            row["missing"] = [r["description"] for r in row["requirements"] if r["required"]]
        row = tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.created", cid, user)
        if row["mode"] == "cloud" and row["public_egress_authorized"]:
            from .task_requirements import begin

            row = begin(tx, row, user)
        return view(tx, row, user)


def _status(tx, row):
    task = tx.get("tasks", row["task_id"]) if row.get("task_id") else None
    if task and task.get("task_case_id") == row["id"]:
        if task.get("task_case_phase") == "requirements" and task["status"] not in TERMINAL:
            return "analyzing_requirements"
        if (
            task["status"] in TERMINAL
            and task.get("reason_code") == "insufficient_data"
            and row.get("execution_missing")
        ):
            return "awaiting_data"
        return task["status"]
    return row["status"]


def _editable(tx, row):
    if _status(tx, row) not in EDITABLE:
        s.fail("task_case_locked", "任务执行中或已成功交付，不能更改冻结输入", 409)


def submit(case_id, body, user, repository):
    body = DataSubmissionInput.model_validate(body).model_dump()
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        sid = s.digest([case_id, "submission", body["idempotency_key"]])[:48]
        old = tx.get("data_submissions", sid)
        if old:
            if old["request_hash"] != s.digest(body):
                s.fail("idempotency_conflict", "资料批次标识已用于不同内容", 409)
            return view(tx, row, user)
        prior_receipt = row.get("submission_receipts", {}).get(sid)
        if prior_receipt:
            if prior_receipt != s.digest(body):
                s.fail("idempotency_conflict", "资料确认标识已用于不同内容", 409)
            return view(tx, row, user)
        _editable(tx, row)
        if set(body["requirement_ids"]) - {r["id"] for r in row["requirements"]}:
            s.fail("unknown_requirement", "资料引用了不存在的需求", 422)
        for asset_id in body["input_refs"]:
            asset = tx.get("data_assets", asset_id)
            if (
                not asset
                or asset["project_id"] != row["project_id"]
                or asset.get("task_case_id") not in {None, case_id}
            ):
                s.fail("permission_denied", "资料不属于当前项目或此任务的授权范围", 403)
            s.usable_asset(asset)
            s.verify_frozen_asset(asset, asset)
        added = [ref for ref in body["input_refs"] if ref not in row["input_refs"]]
        row["input_refs"] = list(dict.fromkeys([*row["input_refs"], *body["input_refs"]]))
        if body["public_egress_authorized"] is not None:
            row["public_egress_authorized"] = body["public_egress_authorized"]
        if row.get("requirements_analysis"):
            from .task_requirements import refresh

            for key in body["requirement_ids"]:
                row.setdefault("requirement_bindings", {}).setdefault(key, []).extend(body["input_refs"])
            row["requirements"] = refresh(row, _available(tx, row))
        else:
            row["requirements"], row["matched_case_id"] = _requirements(row, _available(tx, row))
        row["missing"] = [
            r["description"]
            for r in row["requirements"]
            if r["required"] and r["status"] not in {"provided", "satisfied"}
        ]
        row.pop("execution_missing", None)
        row["status"] = "awaiting_data" if row["missing"] else "ready"
        # A new data revision detaches a terminal run, without losing its trace or costs.
        row["task_id"] = None
        row["revision"] += 1
        if added:
            tx.put(
                "data_submissions",
                {
                    "id": sid,
                    "project_id": row["project_id"],
                    "task_case_id": case_id,
                    "owner_id": row["owner_id"],
                    "input_refs": added,
                    "requirement_ids": body["requirement_ids"],
                    "status": "accepted",
                    "request_hash": s.digest(body),
                    "submission_revision": row["revision"],
                },
            )
        else:
            row.setdefault("submission_receipts", {})[sid] = s.digest(body)
        row = tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.data_submitted", sid, user)
        return view(tx, row, user)


def _provision_generic(tx, row):
    available = catalog()
    # This initial immutable agent only bounds the planner. Its per-step successors
    # are independently frozen before any user processing node starts.
    readiness = available.get("readiness", {}).get("tools", {})
    tools = [
        t
        for t in available["tools"]
        if not t.get("external") and readiness.get(t["id"], {}).get("available", True)
    ]
    tools = tools[:48]
    ids = {t["id"] for t in tools} | {t["name"] for t in tools}
    skills = [k for k in available["skills"] if not set(k["required_tools"]) - ids][:12]
    return _save_scoped_agent(
        tx,
        row,
        "bootstrap",
        {
            "label": "任务分析智能体",
            "instructions": "依据当前任务规划，使用真实工具与可定位证据完成目标。",
            "tool_ids": [t["id"] for t in tools],
            "skill_ids": [k["id"] for k in skills],
            "success_criteria": ["满足用户目标", "引用可核验", "明确缺口"],
        },
        available,
    )


def _save_scoped_agent(tx, row, suffix, step, available):
    if step.get("reuse_agent_id"):
        reuse = tx.get("agents", step["reuse_agent_id"])
        if (
            not reuse
            or reuse["project_id"] != row["project_id"]
            or reuse.get("scope") != "reusable"
            or reuse.get("status") != "published"
            or reuse.get("candidate_status") != "promoted"
        ):
            s.fail("agent_reuse_denied", "规划引用的智能体未通过复用评测或不属于本项目", 403)
        version = tx.get("agent_versions", f"{reuse['id']}:{reuse['current_version']}")
        validate_frozen(version, tx)
        if set(step["tool_ids"]) != {t["tool_id"] for t in version.get("tool_refs", [])}:
            s.fail("agent_reuse_contract", "复用智能体的工具集合与规划不一致", 422)
        if set(step["skill_ids"]) != {t["skill_id"] for t in version.get("skill_refs", [])}:
            s.fail("agent_reuse_contract", "复用智能体的技能集合与规划不一致", 422)
        return reuse
    aid = f"tc-{row['id']}-data{row['revision']}-{suffix}"
    tools = {t["id"]: t for t in available["tools"] if not t.get("external")}
    skills = {k["id"]: k for k in available["skills"] if not k.get("external")}
    if set(step["tool_ids"]) - tools.keys() or set(step["skill_ids"]) - skills.keys():
        s.fail("plan_capability_unavailable", "规划请求了未授权或不存在的工具技能", 422)
    refs = [{"tool_id": key, "version": tools[key]["version"]} for key in step["tool_ids"]]
    spec = AgentInput(
        project_id=row["project_id"],
        name=step["label"],
        description="任务级智能体；独立评测通过前不参与跨任务自动复用。",
        purpose="specialized",
        capabilities=["autonomous_analysis"],
        modalities=["text", "structured", "image", "audio", "video"],
        tool_ids=["autonomous_analysis"],
        model_policy="cloud",
        execution_strategy="tool_agent",
        tool_refs=refs,
        skill_refs=[{"skill_id": key, "version": skills[key]["version"]} for key in step["skill_ids"]],
        instructions=step["instructions"],
    ).model_dump()
    spec.update(
        id=aid,
        status="published",
        current_version=1,
        explicit_workflow_only=True,
        scope="task",
        task_case_id=row["id"],
        created_by=row["owner_id"],
    )
    spec["runtime_bindings"] = freeze_bindings(spec, tx)
    existing = tx.get("agents", aid)
    if existing:
        if (
            existing.get("scope") != "task"
            or existing.get("task_case_id") != row["id"]
            or existing["project_id"] != row["project_id"]
        ):
            s.fail("task_agent_scope_changed", "任务智能体归属已变化，拒绝覆盖", 409)
        if all(existing.get(key) == value for key, value in spec.items() if key != "current_version"):
            prior = tx.get("agent_versions", f"{aid}:{existing['current_version']}")
            if not prior:
                s.fail("task_agent_version_missing", "任务智能体版本不存在", 409)
            validate_frozen(prior, tx)
            return existing
        # A new publication may change code/skill bindings while intake remains ready.
        # Publish a new immutable version; never refresh the old task snapshot in place.
        spec["current_version"] = (
            max(
                (v["version"] for v in tx.list("agent_versions", row["project_id"], agent_id=aid)),
                default=existing["current_version"],
            )
            + 1
        )
    tx.put("agents", spec)
    tx.put(
        "agent_versions",
        {
            **spec,
            "id": f"{aid}:{spec['current_version']}",
            "definition_id": aid,
            "agent_id": aid,
            "version": spec["current_version"],
            "checksum": s.digest(spec),
        },
    )
    return spec


def _workflow(tx, row, agent, phase="execution"):
    wid = f"tc-{row['id']}-{phase}-flow-{row['revision']}"
    flow = {
        "id": wid,
        "project_id": row["project_id"],
        "name": row["title"],
        "description": "任务级顺序流程",
        "task_type": "autonomous_analysis",
        "status": "published",
        "current_version": 1,
        "scope": "task",
        "task_case_id": row["id"],
        "nodes": [
            {
                "id": "process",
                "label": agent["name"],
                "agent_id": agent["id"],
                "agent_version": agent["current_version"],
                "depends_on": [],
                "config": {},
            }
        ],
    }
    existing = tx.get("workflows", wid)
    if existing:
        if all(existing.get(key) == value for key, value in flow.items() if key != "current_version"):
            return wid
        flow["current_version"] = (
            max(
                (v["version"] for v in tx.list("workflow_versions", row["project_id"], workflow_id=wid)),
                default=existing["current_version"],
            )
            + 1
        )
    tx.put("workflows", flow)
    tx.put(
        "workflow_versions",
        {
            **flow,
            "id": f"{wid}:{flow['current_version']}",
            "definition_id": wid,
            "workflow_id": wid,
            "version": flow["current_version"],
            "checksum": s.digest(flow),
        },
    )
    return wid


def start(case_id, user, repository):
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        if row.get("task_id"):
            return view(tx, row, user)
        if row["mode"] == "cloud" and not row["public_egress_authorized"]:
            s.fail("data_egress_blocked", "请明确授权将任务说明与所选公开资料交给云模型", 403)
        if row["mode"] == "cloud" and not row.get("requirements_analysis"):
            from .task_requirements import begin

            return view(tx, begin(tx, row, user), user)
        if row["missing"]:
            s.fail("insufficient_data", "请提供资料需求中缺少的参考数据", 422)
        worker = _principal(user)
        code = row.get("matched_case_id")
        flow_id = None
        if code:
            team.install(row["project_id"], worker, tx, case_id=code, upgrade=True)
        else:
            if row["mode"] != "cloud":
                s.fail(
                    "local_model_unavailable",
                    "当前任意任务规划需要已配置模型；本地模式仅运行已接入的确定性分析能力",
                    422,
                )
            flow_id = _workflow(tx, row, _provision_generic(tx, row))
        from .task_requirements import remaining_budget

        remaining = remaining_budget(row)
        body = {
            "project_id": row["project_id"],
            "name": row["title"],
            "goal": row["goal"]
            + ("\n交付要求：" + row["output_requirements"] if row["output_requirements"] else ""),
            "task_type": "case_analysis" if code else "autonomous_analysis",
            "case_id": code,
            "case_scope": "project" if code else "reference",
            "input_refs": row["input_refs"],
            "workflow_id": flow_id,
            "mode": row["mode"],
            "max_cost_cny": remaining,
            "max_revisions": 2,
            "deadline_seconds": 1800,
            "allow_partial": True,
            "service_class": "interactive",
            "idempotency_key": f"taskcase:{case_id}:{row['revision']}",
        }
        task = s.create_task(body, worker, tx, task_case_id=case_id)
        task.update(
            task_case_id=case_id, authorization={k: user[k] for k in ("id", "role", "username") if k in user}
        )
        task = tx.put("tasks", task)
        row["task_id"] = task["id"]
        row["task_ids"].append(task["id"])
        row["status"] = task["status"]
        row = tx.put("task_cases", row)
        nodes = task["snapshot"]["workflow"]["nodes"]
        agents = {a["id"]: a for a in task["snapshot"]["agents"]}
        plan = {
            "id": task["id"] + ":intake",
            "project_id": row["project_id"],
            "task_case_id": case_id,
            "task_id": task["id"],
            "version": len(tx.list("plan_versions", row["project_id"], task_case_id=case_id)) + 1,
            "status": "frozen",
            "reason": "匹配已验证分析能力" if code else "能力边界已冻结，执行前由模型制定具体步骤",
            "steps": [
                {
                    **node,
                    "tool_ids": [r["tool_id"] for r in agents[node["agent_id"]].get("tool_refs", [])],
                    "skill_ids": [r["skill_id"] for r in agents[node["agent_id"]].get("skill_refs", [])],
                    "success_criteria": ["来源可定位", "满足用户目标与确定性检查"],
                }
                for node in nodes
            ],
            "input_checksums": {a["id"]: a["checksum"] for a in task["snapshot"]["inputs"]},
        }
        plan["checksum"] = s.digest(plan)
        tx.put("plan_versions", plan)
        s.audit(tx, row["project_id"], "task_case.started", case_id, user)
        return view(tx, row, user)


def cancel(case_id, user, repository):
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        if row.get("task_id"):
            s.cancel_task(row["task_id"], _principal(user), tx)
        else:
            row = tx.put("task_cases", {**row, "status": "cancelled"})
        return view(tx, row, user)


def delete(case_id, user, repository):
    """Hide a user task while retaining its execution, reference and billing history."""
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True, include_deleted=True)
        if row.get("deleted_at"):
            return
        # Evaluation replays also reference this case but are not in task_ids.
        runs = tx.list("tasks", row["project_id"], task_case_id=case_id)
        if _status(tx, row) not in EDITABLE | {"succeeded"} or any(
            task["status"] not in TERMINAL for task in runs
        ):
            s.fail("task_case_not_terminal", "任务或关联评测仍在执行，请先取消并等待执行结束", 409)
        if any(
            task.get("lease_owner")
            or any(
                item["status"] == "running"
                for item in tx.list("instances", row["project_id"], task_id=task["id"])
            )
            for task in runs
        ):
            s.fail("task_case_cleanup_pending", "执行器尚未释放任务，请等待清理完成后再删除", 409)
        deleted_at = s.now()
        row.update(deleted_at=deleted_at, deleted_by=user["id"])
        row["ingest_tokens"] = [
            {**entry, "revoked": True, "revoked_at": deleted_at} for entry in row.get("ingest_tokens", [])
        ]
        tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.deleted", case_id, user)


def get(case_id, user, repository):
    with repository.transaction() as tx:
        return view(tx, required(tx, case_id, user), user)


def history(project_id, user, repository):
    with repository.transaction() as tx:
        s.project_access(project_id, user, repository=tx)
        return [
            view(tx, row, user, summary=True)
            for row in reversed(tx.list("task_cases", project_id))
            if not row.get("deleted_at") and (row["owner_id"] == user["id"] or user.get("role") == "admin")
        ]


def _case_accounting(row):
    from .runtime import _usage, model_gateway

    usages = [_usage(tid, row["mode"]) for tid in row["task_ids"]]
    totals = {
        key: (
            sum(float(u.get(key) or 0) for u in usages)
            if all(u.get(key) is not None for u in usages)
            else None
        )
        for key in (
            "request_count",
            "cost_cny",
            "reserved_cny",
            "total_cny",
            "input_tokens",
            "output_tokens",
            "uncertain_count",
        )
    }
    totals.update(
        accounting_available=all(u.get("accounting_available", True) for u in usages),
        accounting_complete=all(u.get("accounting_complete", True) for u in usages),
        scope="task_case_total",
    )
    models = []
    if row["task_ids"] and totals["accounting_available"]:
        with model_gateway.ledger.connect() as ledger:
            slots = ",".join("?" for _ in row["task_ids"])
            values = ledger.execute(
                "SELECT model,COUNT(*) requests, SUM(CASE WHEN state='settled' THEN cost ELSE 0 END) cost_cny,"
                "SUM(CASE WHEN state!='settled' THEN reserved ELSE 0 END) reserved_cny,"
                "SUM(CASE WHEN state='uncertain' THEN 1 ELSE 0 END) uncertain_count FROM charges WHERE run_id IN ("
                + slots
                + ") GROUP BY model",
                row["task_ids"],
            ).fetchall()
            models = [
                {**dict(v), "model": v["model"] or "费用尚未确认的模型", "scope": "task_case_total"}
                for v in values
            ]
    return totals, models


def _aggregate_trace(tx, row, out):
    """All phases and data revisions remain observable, including charged failed runs."""
    usage, models = _case_accounting(row)
    out["usage"] = usage
    out["trace"]["models"] = models
    runs = [tx.get("tasks", tid) for tid in row["task_ids"]]
    runs = [r for r in runs if r and r.get("task_case_id") == row["id"]]
    out["runs"] = [
        {
            **{
                k: run.get(k)
                for k in ("id", "status", "reason_code", "revision", "created_at", "finished_at")
            },
            "phase": run.get("task_case_phase", "execution"),
            "detail_url": f"/api/v2/task-cases/{row['id']}/runs/{run['id']}",
        }
        for run in runs
    ]
    try:
        for run in runs:
            for frozen in run["snapshot"]["inputs"]:
                s.verify_frozen_asset(frozen, tx.get("data_assets", frozen["id"]))
    except s.DomainError:
        out.update(content_withheld=True, plans=[], requirements_analysis=None, review=None, result=None)
        out["trace"] = {
            "models": models,
            "agents": [],
            "tools": [],
            "revisions": [],
            "assessment_reviews": [],
            "scripts": [],
            "sandbox_runs": [],
        }
        return
    agents, tools, revisions, assessments = [], [], [], []
    for run in runs:
        nodes = tx.list("nodes", row["project_id"], task_id=run["id"])
        instances = tx.list("instances", row["project_id"], task_id=run["id"])
        definitions = {(a["id"], a.get("version", 1)): a for a in run["snapshot"]["agents"]}
        for node in nodes:
            key = (node["agent_id"], node["agent_version"])
            saved = tx.get("agent_versions", f"{key[0]}:{key[1]}")
            if key not in definitions and saved:
                definitions[key] = {**saved, "id": key[0]}
        names = {a["id"]: a["name"] for a in definitions.values()}
        for a in definitions.values():
            own = [
                i
                for i in instances
                if i["agent_id"] == a["id"] and i.get("agent_version") == a.get("version", 1)
            ]
            agents.append(
                {
                    "id": a["id"],
                    "name": a["name"],
                    "version": a.get("version", 1),
                    "scope": a.get("scope", "reusable"),
                    "task_id": run["id"],
                    "phase": run.get("task_case_phase", "execution"),
                    "instances": len(own),
                    "status": own[-1]["status"] if own else "pending",
                }
            )
        tools.extend(
            {
                **{
                    k: call.get(k)
                    for k in (
                        "id",
                        "capability_id",
                        "status",
                        "revision",
                        "duration_ms",
                        "node_id",
                        "summary",
                        "error_code",
                    )
                },
                "task_id": run["id"],
            }
            for call in run.get("tool_calls", [])
        )
        revisions.extend({**entry, "task_id": run["id"]} for entry in run.get("repair_history", []))
        assessments.extend(
            {
                "agent_id": n["agent_id"],
                "agent_name": names.get(n["agent_id"], ""),
                "node_id": n["node_id"],
                "task_id": run["id"],
                "revision": n.get("attempt", 1) - 1,
                **n["output"]["assessment_review"],
            }
            for n in nodes
            if (n.get("output") or {}).get("assessment_review")
        )
    out["trace"].update(agents=agents, tools=tools, revisions=revisions, assessment_reviews=assessments)


def _generated_files(tx, row, task, content, sandbox_runs):
    """Publish only checked files belonging to script evidence in the final result."""
    from .script_tool import ScriptExecute

    if task["status"] not in {"succeeded", "partial"}:
        return []
    sources = {a["id"] for a in task["snapshot"]["inputs"]}
    calls = {call["id"]: call for call in task.get("tool_calls", [])}
    runs = {run["id"]: run for run in sandbox_runs}
    published, seen = [], set()
    for citation in content.get("citations", []):
        call = calls.get(citation.get("tool_call_id"), {})
        checkpoint = task.get("tool_checkpoints", {}).get(call.get("id"), {})
        result = checkpoint.get("result") or {}
        if (
            citation.get("kind") != "derived_result"
            or call.get("capability_id") != "script.execute"
            or call.get("status") != "completed"
            or call.get("task_id") != task["id"]
            or citation.get("result_checksum") != call.get("result_checksum")
            or s.digest(result) != call.get("result_checksum")
        ):
            continue
        run = runs.get(result.get("sandbox_run_id"))
        if not run or run["status"] != "succeeded" or result.get("status") != "succeeded":
            continue
        if run["task_id"] != task["id"] or run["project_id"] != row["project_id"]:
            continue
        refs = set(run.get("source_refs", []))
        arguments = checkpoint.get("arguments") or {}
        if s.digest(arguments) != call.get("arguments_checksum"):
            continue
        try:
            executed_arguments = ScriptExecute.model_validate(arguments).model_dump()
        except (ValueError, TypeError):
            continue
        if (
            refs - sources
            or citation.get("source_id") not in refs
            or refs != set(executed_arguments["source_ids"])
            or refs != set(result.get("source_refs", []))
        ):
            continue
        script = tx.get("script_versions", run.get("script_version_id"))
        if (
            not script
            or script["id"] != result.get("script_version_id")
            or script.get("task_id") != task["id"]
            # The execution contract strips surrounding string whitespace;
            # the audit checkpoint intentionally retains the original request.
            or script.get("code") != executed_arguments["code"]
            or result.get("script_checksum") != script.get("checksum")
            or hashlib.sha256(script["code"].encode()).hexdigest() != script.get("checksum")
        ):
            continue
        outputs = {output.get("id"): output for output in result.get("outputs", [])}
        for index, output in enumerate(run.get("outputs", [])):
            identity = f"{run['id']}:{index}"
            cited = outputs.get(identity, {})
            if identity in seen or any(
                cited.get(k) != output.get(k) for k in ("name", "media_type", "bytes", "checksum")
            ):
                continue
            try:
                payload = base64.b64decode(output["base64"], validate=True)
            except (KeyError, TypeError, ValueError):
                continue
            if len(payload) != output["bytes"] or hashlib.sha256(payload).hexdigest() != output["checksum"]:
                continue
            seen.add(identity)
            published.append(
                {
                    **{k: output[k] for k in ("name", "media_type", "bytes", "checksum")},
                    "id": identity,
                    "sandbox_run_id": run["id"],
                    "script_version_id": script["id"],
                    "tool_call_id": call["id"],
                    "quality_status": "referenced_output",
                    "task_review_passed": bool((task.get("quality_verdict") or {}).get("passed")),
                    "download_url": f"/api/v2/task-cases/{row['id']}/sandbox-runs/{run['id']}/outputs/{index}",
                }
            )
    return published


def view(tx, row, user, *, summary=False):
    keys = [
        "id",
        "project_id",
        "title",
        "goal",
        "output_requirements",
        "mode",
        "max_cost_cny",
        "public_egress_authorized",
        "requirements",
        "missing",
        "input_refs",
        "task_id",
        "created_at",
        "updated_at",
        "requirements_analysis",
    ]
    out = {key: row.get(key) for key in keys}
    out["status"] = _status(tx, row)
    task = tx.get("tasks", row["task_id"]) if row.get("task_id") else None
    out["task_type"] = task["request"]["task_type"] if task else None
    out["candidate_eligible"] = bool(
        task
        and not task.get("task_case_phase")
        and task["request"]["task_type"] == "autonomous_analysis"
        and task["status"] == "succeeded"
    )
    from .agent_candidates import evaluation_sample_eligible

    out["evaluation_eligible"] = evaluation_sample_eligible(task, tx)
    if summary:
        return out
    out["available_assets"] = [
        {
            k: a.get(k)
            for k in ("id", "name", "modality", "media_type", "classification", "bytes", "source_version")
        }
        for a in _available(tx, row)
    ]
    out["profiles"] = [p for requirement in row["requirements"] for p in requirement.get("profiles", [])]
    submissions = tx.list("data_submissions", row["project_id"], task_case_id=row["id"])
    transported = {tuple(x.get("input_refs", [])) for x in submissions if x.get("transport")}
    out["submissions"] = [
        x for x in submissions if x.get("transport") or tuple(x.get("input_refs", [])) not in transported
    ]
    out["plans"] = tx.list("plan_versions", row["project_id"], task_case_id=row["id"])
    out.update(
        runs=[],
        result=None,
        review=None,
        trace={
            "agents": [],
            "models": [],
            "tools": [],
            "revisions": [],
            "assessment_reviews": [],
            "scripts": [],
            "sandbox_runs": [],
        },
    )
    task = tx.get("tasks", row["task_id"]) if row.get("task_id") else None
    for tid in row["task_ids"]:
        run = tx.get("tasks", tid)
        if run:
            out["runs"].append(
                {
                    k: run.get(k)
                    for k in ("id", "status", "reason_code", "revision", "created_at", "finished_at")
                }
            )
    if task:
        blocked = s.unavailable_sources([a["id"] for a in task["snapshot"]["inputs"]], row["project_id"], tx)
        try:
            for frozen in task["snapshot"]["inputs"]:
                s.verify_frozen_asset(frozen, tx.get("data_assets", frozen["id"]))
        except s.DomainError:
            blocked = True
        if blocked:
            out.update(
                content_withheld=True,
                plans=[],
                review=None,
                missing=["输入资料已撤回、过期或变化，相关产物与运行详情已屏蔽。"],
            )
        else:
            out["review"] = task.get("quality_verdict")
            out["trace"]["scripts"] = tx.list("script_versions", row["project_id"], task_id=task["id"])
            sandbox_runs = tx.list("sandbox_runs", row["project_id"], task_id=task["id"])
            out["trace"]["sandbox_runs"] = [
                {
                    **run,
                    "outputs": [
                        {
                            **{k: v for k, v in output.items() if k != "base64"},
                            "download_url": f"/api/v2/task-cases/{row['id']}/sandbox-runs/{run['id']}/outputs/{index}",
                        }
                        for index, output in enumerate(run.get("outputs", []))
                    ],
                }
                for run in sandbox_runs
            ]
            nodes = tx.list("nodes", row["project_id"], task_id=task["id"])
            instances = tx.list("instances", row["project_id"], task_id=task["id"])
            historical_agents = {(a["id"], a.get("version", 1)): a for a in task["snapshot"]["agents"]}
            for node in nodes:
                key = (node["agent_id"], node["agent_version"])
                if key not in historical_agents:
                    saved = tx.get("agent_versions", f"{key[0]}:{key[1]}")
                    if saved:
                        historical_agents[key] = {**saved, "id": key[0]}
            for a in historical_agents.values():
                own = [i for i in instances if i["agent_id"] == a["id"]]
                out["trace"]["agents"].append(
                    {
                        "id": a["id"],
                        "name": a["name"],
                        "version": a.get("version", 1),
                        "scope": a.get("scope", "reusable"),
                        "instances": len(own),
                        "status": own[-1]["status"] if own else "pending",
                    }
                )
            out["trace"]["tools"] = [
                {
                    k: call.get(k)
                    for k in (
                        "id",
                        "capability_id",
                        "status",
                        "revision",
                        "duration_ms",
                        "node_id",
                        "summary",
                        "error_code",
                    )
                }
                for call in task.get("tool_calls", [])
            ]
            out["trace"]["revisions"] = task.get("repair_history", [])
            names = {a["id"]: a["name"] for a in historical_agents.values()}
            out["trace"]["assessment_reviews"] = [
                {
                    "agent_id": node["agent_id"],
                    "agent_name": names.get(node["agent_id"], ""),
                    "node_id": node["node_id"],
                    "revision": node.get("attempt", 1) - 1,
                    **node["output"]["assessment_review"],
                }
                for node in nodes
                if (node.get("output") or {}).get("assessment_review")
            ]
            model_ids = sorted(
                {(n.get("output") or {}).get("model") for n in nodes if (n.get("output") or {}).get("model")}
            )
            from .runtime import _usage

            usage = _usage(task["id"], task["request"]["mode"])
            out["usage"] = usage
            out["trace"]["models"] = (
                [
                    {
                        "model": "、".join(model_ids) or "网关模型",
                        "requests": usage.get("request_count"),
                        "cost_cny": usage.get("cost_cny"),
                        "scope": "task_total",
                    }
                ]
                if usage.get("request_count")
                else []
            )
            if task["status"] in TERMINAL:
                from .question_answers import deliver

                artifacts = tx.list("artifacts", row["project_id"], task_id=task["id"])
                if artifacts:
                    sources = {a["id"]: a for a in task["snapshot"]["inputs"]}
                    if task["request"]["task_type"] == "autonomous_analysis":
                        content = artifacts[-1].get("content") or {}
                        citations = []
                        for citation in content.get("citations", []):
                            asset = sources.get(citation.get("source_id"))
                            if not asset:
                                continue
                            checkpoint = task.get("tool_checkpoints", {}).get(
                                citation.get("tool_call_id"), {}
                            )
                            page = (checkpoint.get("arguments") or {}).get("page")
                            base_url = f"/api/v2/task-cases/{row['id']}/sources/{asset['id']}/download"
                            citations.append(
                                {
                                    "source_id": asset["id"],
                                    "source": asset["name"],
                                    "source_url": base_url
                                    + (f"#page={page}" if type(page) is int and page > 0 else ""),
                                    "page": page if type(page) is int and page > 0 else None,
                                    "location": f"第 {page} 页"
                                    if page
                                    else (
                                        f"字符 {citation['start']}–{citation['end']}"
                                        if "start" in citation
                                        else "可核验的工具计算或解析结果"
                                    ),
                                    "excerpt": str(citation.get("quote", "由工具执行记录及校验和定位此结果"))[
                                        :450
                                    ],
                                    "quote_truncated": len(str(citation.get("quote", ""))) > 450,
                                    "tool_call_id": citation.get("tool_call_id"),
                                    "kind": citation.get("kind"),
                                }
                            )
                        out["result"] = {
                            "answer": content.get("text", ""),
                            "citations": citations,
                            "tables": [],
                            "missing": content.get("missing_information", []),
                            "incomplete": task["status"] != "succeeded",
                        }
                    else:
                        out["result"] = deliver(tx, task, sources, row["goal"])
                    if not out["result"].get("answer"):
                        out["result"]["answer"] = "\n\n".join(
                            str((a.get("content") or {}).get("text", ""))
                            for a in artifacts
                            if isinstance(a.get("content"), dict)
                        )
                    out["result"]["artifacts"] = [
                        {
                            "id": a["id"],
                            "type": a["type"],
                            "media_type": a["media_type"],
                            "download_url": f"/api/v2/task-cases/{row['id']}/artifacts/{a['id']}/download",
                        }
                        for a in artifacts
                    ]
                    out["result"]["generated_files"] = _generated_files(
                        tx, row, task, artifacts[-1].get("content") or {}, sandbox_runs
                    )
                    out["result"]["status"] = task["status"]
                    out["result"]["products"] = next(
                        (a["content"].get("products") for a in artifacts if a["type"] == "case_report"), None
                    )
                    out["result"]["warnings"] = (task.get("result") or {}).get("warnings", [])
                    by_name = {a["name"]: a for a in sources.values()}
                    report_evidence = [
                        e
                        for a in artifacts
                        if a["type"] == "case_report"
                        for e in a["content"].get("evidence", [])
                    ]
                    for citation in out["result"].get("citations", []):
                        source = sources.get(citation.get("source_id")) or by_name.get(citation.get("source"))
                        if not source:
                            continue
                        evidence = next(
                            (
                                e
                                for e in report_evidence
                                if e.get("source_id") == source["id"]
                                and str(e.get("quote", ""))[:450] == citation.get("excerpt")
                            ),
                            None,
                        )
                        page = citation.get("page") or ((evidence or {}).get("location") or {}).get("page")
                        citation["source_id"] = source["id"]
                        citation["source_url"] = (
                            f"/api/v2/task-cases/{row['id']}/sources/{source['id']}/download"
                            + (f"#page={page}" if type(page) is int and page > 0 else "")
                        )
                        if type(page) is int and page > 0:
                            citation["page"] = page
                        if evidence:
                            citation["quote_truncated"] = bool(
                                evidence.get("quote_truncated") or len(str(evidence.get("quote", ""))) > 450
                            )
                out["reason_code"] = task.get("reason_code")
    _aggregate_trace(tx, row, out)
    stages = [
        ("created", "分析资料需求"),
        ("data", "准备资料"),
        ("plan", "规划与匹配智能体"),
        ("execute", "调用工具执行"),
        ("review", "自动审核与修订"),
        ("deliver", "交付结果"),
    ]
    active = {
        "analyzing_requirements": 0,
        "awaiting_data": 1,
        "ready": 1,
        "accepted": 2,
        "queued": 2,
        "planning": 2,
        "running": 3,
        "evaluating": 4,
        "revising": 4,
        "succeeded": 5,
        "partial": 5,
    }.get(out["status"], 1)
    if out["status"] in {"failed", "cancelled"} and task:
        active = (
            4
            if task.get("quality_verdict")
            else 3
            if tx.list("nodes", row["project_id"], task_id=task["id"])
            else 2
        )
    out["progress"] = [
        {
            "key": key,
            "label": label,
            "status": "completed"
            if i < active or out["status"] == "succeeded"
            else "failed"
            if i == active and out["status"] in {"failed", "cancelled"}
            else "active"
            if i == active
            else "pending",
        }
        for i, (key, label) in enumerate(stages)
    ]
    return out


def create_ingest_token(case_id, hours, user, repository):
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        token = secrets.token_urlsafe(32)
        token_id = secrets.token_hex(8)
        expiry = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
        row["ingest_tokens"] = [
            *row.get("ingest_tokens", []),
            {
                "id": token_id,
                "hash": hashlib.sha256(token.encode()).hexdigest(),
                "expires_at": expiry,
                "owner_id": user["id"],
                "username": user.get("username"),
                "revoked": False,
            },
        ][-10:]
        tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.ingest_token_created", token_id, user)
        return {
            "id": token_id,
            "token": token,
            "expires_at": expiry,
            "task_case_id": case_id,
            "scope": ["data:submit"],
            "endpoint": f"/api/v2/task-cases/{case_id}/ingest",
        }


def ingest_identity(case_id, token, repository):
    from .. import auth

    row = repository.get("task_cases", case_id)
    if not row or row.get("deleted_at") or not token:
        s.fail("permission_denied", "资料接口令牌无效", 403)
    digest = hashlib.sha256(token.encode()).hexdigest()
    match = next(
        (
            v
            for v in row.get("ingest_tokens", [])
            if secrets.compare_digest(v["hash"], digest)
            and not v.get("revoked")
            and s.timestamp(v["expires_at"]) > datetime.now(UTC)
        ),
        None,
    )
    if not match:
        s.fail("permission_denied", "资料接口令牌无效、已撤销或已过期", 403)
    user = auth.store.get("users", match.get("username", ""))
    if not user or user["id"] != match["owner_id"] or not user.get("enabled", True):
        s.fail("permission_denied", "资料接口的授权身份已失效", 403)
    required(repository, case_id, user, write=True)
    return user


def ingest_inline(case_id, body, user, repository):
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        inner_key = "inline-" + s.digest(body.idempotency_key)[:48]
        sid = s.digest([case_id, "submission", inner_key])[:48]
        existing = tx.get("data_submissions", sid) or tx.get(
            "data_submissions", s.digest([case_id, "inline", body.idempotency_key])[:48]
        )
        if existing:
            if existing.get("upload_hash") != s.digest(body.model_dump()):
                s.fail("idempotency_conflict", "同一数据提交标识已用于不同内容", 409)
            return view(tx, row, user)
        _editable(tx, row)
        asset = s.create_asset(
            {
                "project_id": row["project_id"],
                "name": body.name,
                "modality": "text" if body.media_type == "text/plain" else "structured",
                "media_type": body.media_type,
                "content": body.content,
                "source_id": body.source_id,
                "source_version": body.source_version,
                "provenance": body.provenance,
                "classification": body.classification,
            },
            _principal(user),
            tx,
        )
        asset = tx.put("data_assets", {**asset, "task_case_id": case_id, "owner_id": user["id"]})
        submit(
            case_id,
            {
                "input_refs": [asset["id"]],
                "requirement_ids": [body.requirement_id] if body.requirement_id else [],
                "idempotency_key": inner_key,
            },
            user,
            tx,
        )
        tx.put(
            "data_submissions",
            {
                **tx.get("data_submissions", sid),
                "upload_hash": s.digest(body.model_dump()),
                "transport": "api",
            },
        )
        return view(tx, tx.get("task_cases", case_id), user)


def upload(case_id, filename, content, classification, requirement_id, key, user, repository):
    suffix = Path(filename).suffix.lower()
    known = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".jsonl",
        ".pdf",
        ".docx",
        ".xlsx",
        ".png",
        ".jpg",
        ".jpeg",
        ".wav",
        ".mp3",
        ".flac",
        ".ogg",
        ".m4a",
        ".mp4",
        ".webm",
        ".mov",
        ".geojson",
        ".srt",
        ".vtt",
    }
    if suffix not in known:
        s.fail("unsupported_format", "尚未支持此文件格式", 422)
    media = {
        ".jsonl": "application/x-ndjson",
        ".geojson": "application/geo+json",
        ".srt": "application/x-subrip",
        ".csv": "text/csv",
        ".md": "text/markdown",
    }.get(suffix, mimetypes.guess_type(filename)[0] or "application/octet-stream")
    modality = next(
        (kind for kind in ("image", "audio", "video") if media.startswith(kind + "/")),
        "structured" if suffix in {".json", ".jsonl", ".csv", ".xlsx", ".geojson"} else "text",
    )
    limit = 128_000_000 if modality in {"audio", "video"} else 10_000_000
    if not content or len(content) > limit:
        s.fail("input_too_large", "文件为空或超出此类型的大小限制", 413)
    value = None
    if modality in {"text", "structured"} and suffix not in {".pdf", ".docx", ".xlsx"}:
        try:
            value = content.decode("utf-8-sig")
            if suffix in {".json", ".geojson"}:
                value = json.loads(value)
        except (UnicodeDecodeError, ValueError):
            s.fail("invalid_input", "文本需UTF-8编码；JSON需有效结构", 422)
    if modality == "image":
        from io import BytesIO

        from PIL import Image

        try:
            with Image.open(BytesIO(content)) as image:
                if image.width * image.height > 25_000_000:
                    s.fail("input_too_large", "图片像素超限", 413)
                image.verify()
        except (OSError, ValueError):
            s.fail("invalid_media", "图片无法解码", 422)
    if isinstance(value, str) and len(value.encode()) > 1_900_000:
        value = None
    with repository.transaction() as tx:
        row = required(tx, case_id, user, write=True)
        inner_key = "upload-" + s.digest(key)[:48]
        sid = s.digest([case_id, "submission", inner_key])[:48]
        signature = s.digest([filename, hashlib.sha256(content).hexdigest(), classification, requirement_id])
        old = tx.get("data_submissions", sid) or tx.get(
            "data_submissions", s.digest([case_id, "upload", key])[:48]
        )
        if old:
            if old["upload_hash"] != signature:
                s.fail("idempotency_conflict", "同一上传标识已用于不同文件", 409)
            return view(tx, row, user)
        _editable(tx, row)
        asset = s.create_asset(
            {
                "project_id": row["project_id"],
                "name": Path(filename).name,
                "modality": modality,
                "media_type": media,
                "content": value,
                "source_id": f"task-upload:{case_id}:{key}",
                "source_version": "sha256:" + hashlib.sha256(content).hexdigest(),
                "provenance": "用户为当前任务上传的原始资料",
                "classification": classification,
            },
            _principal(user),
            tx,
            file_data=content,
        )
        asset = tx.put("data_assets", {**asset, "task_case_id": case_id, "owner_id": user["id"]})
        submit(
            case_id,
            {
                "input_refs": [asset["id"]],
                "requirement_ids": [requirement_id] if requirement_id else [],
                "idempotency_key": inner_key,
            },
            user,
            tx,
        )
        tx.put(
            "data_submissions",
            {**tx.get("data_submissions", sid), "upload_hash": signature, "transport": "web"},
        )
        return view(tx, tx.get("task_cases", case_id), user)
