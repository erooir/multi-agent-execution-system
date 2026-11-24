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
from . import edition_intake
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
    candidates = (
        [tx.get("data_assets", ref) for ref in record["input_refs"]]
        if record.get("edition_case_id")
        else tx.list("data_assets", record["project_id"])
    )
    for asset in candidates:
        if not asset or asset.get("project_id") != record["project_id"]:
            continue
        if asset.get("task_case_id") and asset["task_case_id"] != record["id"]:
            continue
        try:
            s.usable_task_input(asset)
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
    code = None if record.get("uses_project_edition") else _case_match(record["goal"])
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
    profile = team.catalog().get(code, {}) if code else {}
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


def missing_source_ids(tx, row):
    requested = row.get("source_ids", [])
    if not requested:
        return []
    present = set()
    for ref in row.get("input_refs", []):
        asset = tx.get("data_assets", ref)
        if (
            not asset
            or asset["project_id"] != row["project_id"]
            or asset.get("task_case_id") not in {None, row["id"]}
        ):
            continue
        try:
            s.usable_task_input(asset)
        except s.DomainError:
            continue
        if row["mode"] == "cloud" and asset["classification"] != "public":
            continue
        try:
            profile = profile_asset(asset)
        except s.DomainError:
            continue
        if profile["readable"] and profile["nonempty"]:
            present.add(asset["source_id"])
    return [key for key in dict.fromkeys(requested) if key not in present]


def refresh_missing(tx, row):
    row["missing"] = [
        r["description"]
        for r in row["requirements"]
        if r["required"] and r["status"] not in {"provided", "satisfied"}
    ]
    row["missing"].extend("缺少指定来源：" + key for key in missing_source_ids(tx, row))
    row["status"] = "awaiting_data" if row["missing"] else "ready"


def create(body, user, repository):
    body = TaskCaseInput.model_validate(body).model_dump()
    _principal(user)
    with repository.transaction() as tx:
        s.project_access(body["project_id"], user, repository=tx)
        project = tx.get("projects", body["project_id"], for_update=True)
        cid = s.digest([body["project_id"], user["id"], body["idempotency_key"]])[:48]
        old = tx.get("task_cases", cid)
        if old:
            if old.get("deleted_at"):
                s.fail("task_case_deleted", "此提交对应的任务已删除，请使用新的提交标识创建任务", 409)
            legacy_body = {k: v for k, v in body.items() if k != "source_ids"}
            if old["request_hash"] != s.digest(body) and (
                body["source_ids"] or old["request_hash"] != s.digest(legacy_body)
            ):
                s.fail("idempotency_conflict", "同一提交标识已用于不同任务", 409)
            return view(tx, old, user)
        row = {
            **body,
            "id": cid,
            "owner_id": user["id"],
            "intake_owner": user.get("username", user["id"]),
            "intake": {"status": "pending", "attempts": 0},
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
        edition = project.get("scenario_edition")
        row["uses_project_edition"] = bool(edition)
        case = (
            edition_intake.match(project, row["title"] + "\n" + row["goal"])
            if edition and row["data_requirement"] != "none"
            else None
        )
        if case:
            if row["mode"] != "cloud":
                s.fail("case_model_required", "当前目录的用例需要选择模型调用。", 422)
            row["edition_case_id"] = row["matched_case_id"] = case["id"]
            row["input_refs"] = edition_intake.sources(
                tx, row["project_id"], case, declared=row["source_ids"], owner_case=cid
            )
            row["requirements"] = edition_intake.requirement(case, row["input_refs"])
        else:
            row["requirements"], row["matched_case_id"] = _requirements(row, _available(tx, row))
        refresh_missing(tx, row)
        row = tx.put("task_cases", row)
        s.audit(tx, row["project_id"], "task_case.created", cid, user)
        if row["mode"] == "cloud" and row["public_egress_authorized"]:
            if case:
                return start(cid, user, tx)
            from .task_requirements import begin

            row = begin(tx, row, user)
        return view(tx, row, user)


def _status(tx, row):
    if row["status"] == "cancelled":
        return "cancelled"
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


def submit(case_id, body, user, repository, *, replace_sources=False):
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
        if row.get("edition_case_id") and set(body["input_refs"]) - set(row["input_refs"]):
            s.fail("case_source_scope", "该用例只接受项目登记的配套原件。", 422)
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
            s.usable_task_input(asset)
            s.verify_frozen_asset(asset, asset)
        if replace_sources:
            new_ids = set(body["input_refs"])
            source_keys = {tx.get("data_assets", key)["source_id"] for key in new_ids}
            # Only the working input set changes. Historical submissions and prior runs stay frozen.
            removed = {
                key
                for key in row["input_refs"]
                if key not in new_ids and (tx.get("data_assets", key) or {}).get("source_id") in source_keys
            }
            row["input_refs"] = [key for key in row["input_refs"] if key not in removed]
            for rid, bindings in row.get("requirement_bindings", {}).items():
                row["requirement_bindings"][rid] = [key for key in bindings if key not in removed]
        added = [ref for ref in body["input_refs"] if ref not in row["input_refs"]]
        row["input_refs"] = list(dict.fromkeys([*row["input_refs"], *body["input_refs"]]))
        if body["public_egress_authorized"] is not None:
            row["public_egress_authorized"] = body["public_egress_authorized"]
        if row.get("edition_case_id"):
            case = edition_intake.current(tx, tx.get("projects", row["project_id"]), row)
            row["requirements"] = edition_intake.requirement(case, row["input_refs"])
        elif row.get("requirements_analysis"):
            from .task_requirements import refresh

            for key in body["requirement_ids"]:
                row.setdefault("requirement_bindings", {}).setdefault(key, []).extend(body["input_refs"])
            row["requirements"] = refresh(row, _available(tx, row))
        else:
            row["requirements"], row["matched_case_id"] = _requirements(row, _available(tx, row))
        row.pop("execution_missing", None)
        refresh_missing(tx, row)
        # A new data revision detaches a terminal run, without losing its trace or costs.
        row["task_id"] = None
        row["revision"] += 1
        if row.get("intake"):
            row["intake"] = {**row["intake"], "next_at": None}
        elif added:
            # An explicit new upstream submission can resume a historical task.
            row["intake"] = {"status": "pending", "attempts": 0}
            if row["owner_id"] == user["id"]:
                row["intake_owner"] = user.get("username", user["id"])
            else:
                row.pop("intake")
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
        edition_code = row.get("edition_case_id")
        project = tx.get("projects", row["project_id"])
        if project.get("scenario_edition") and row.get("matched_case_id") and not edition_code:
            s.fail("case_archived", "项目目录已更新，请用当前用例新建任务。", 409)
        if row["mode"] == "cloud" and not edition_code and not row.get("requirements_analysis"):
            from .task_requirements import begin

            return view(tx, begin(tx, row, user), user)
        if row["missing"] or missing_source_ids(tx, row):
            s.fail("insufficient_data", "必需资料尚未到齐，平台将继续检索或等待上游接口推送", 422)
        worker = _principal(user)
        code = row.get("matched_case_id")
        flow_id = None
        if edition_code:
            from ..casework import editions

            edition_intake.current(tx, project, row)
            flow_id = editions.workflow_id(row["project_id"], edition_code)
        elif code:
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
            "allow_partial": not bool(edition_code),
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
        run = tx.get("tasks", row["task_id"]) if row.get("task_id") else None
        if run and run["status"] not in TERMINAL:
            s.cancel_task(run["id"], _principal(user), tx)
        elif not run or _status(tx, row) == "awaiting_data":
            for token in row.get("ingest_tokens", []):
                token["revoked"] = True
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
                "SELECT model,provider,COUNT(*) requests, SUM(CASE WHEN state='settled' THEN cost ELSE 0 END) cost_cny,"
                "SUM(CASE WHEN state!='settled' THEN reserved ELSE 0 END) reserved_cny,"
                "SUM(CASE WHEN state='uncertain' THEN 1 ELSE 0 END) uncertain_count FROM model_request_usage WHERE run_id IN ("
                + slots
                + ") GROUP BY model,provider",
                row["task_ids"],
            ).fetchall()
            models = [
                {**dict(v), "model": v["model"] or "费用尚未确认的模型", "scope": "task_case_total"}
                for v in values
            ]
    return totals, models


def case_source_context(tx, row):
    """All deliveries and traces depend on the case's complete input history."""
    task_ids = dict.fromkeys([*row.get("task_ids", []), *([row["task_id"]] if row.get("task_id") else [])])
    runs = []
    missing_run = False
    for task_id in task_ids:
        run = tx.get("tasks", task_id)
        if run and run.get("task_case_id") == row["id"] and run["project_id"] == row["project_id"]:
            runs.append(run)
        else:
            missing_run = True
    submissions = tx.list("data_submissions", row["project_id"], task_case_id=row["id"])
    refs = set(row.get("input_refs", []))
    for receipt in submissions:
        refs.update(receipt.get("input_refs", []))
    assets = {}
    frozen_ids = set()

    def current(asset_id):
        if asset_id not in assets:
            assets[asset_id] = tx.get("data_assets", asset_id)
        asset = assets[asset_id]
        if (
            not asset
            or asset.get("project_id") != row["project_id"]
            or asset.get("task_case_id") not in (None, row["id"])
        ):
            s.fail("missing_input", "来源不存在或超出本任务授权", 409)
        return asset

    try:
        for run in runs:
            for frozen in run["snapshot"]["inputs"]:
                s.verify_frozen_asset(frozen, current(frozen["id"]))
                frozen_ids.add(frozen["id"])
        for asset_id in refs - frozen_ids:
            asset = current(asset_id)
            s.verify_frozen_asset(asset, asset)
    except (s.DomainError, OSError):
        return runs, submissions, True
    return runs, submissions, missing_run


def _aggregate_trace(tx, row, out, runs):
    """All phases and data revisions remain observable, including charged failed runs."""
    usage, models = _case_accounting(row)
    out["usage"] = usage
    out["trace"]["models"] = models
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
    if out.get("content_withheld"):
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
        "source_ids",
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
        "intake",
    ]
    out = {key: row.get(key) for key in keys}
    if out.get("intake"):
        out["intake"] = {k: v for k, v in out["intake"].items() if k != "claim"}
    out["status"] = _status(tx, row)
    task = tx.get("tasks", row["task_id"]) if row.get("task_id") else None
    out["task_type"] = task["request"]["task_type"] if task else None
    runs, submissions, withheld = case_source_context(tx, row)
    if withheld:
        out.update(
            content_withheld=True,
            title="任务资料已不可用",
            goal="",
            output_requirements="",
            source_ids=[],
            requirements=[],
            requirements_analysis=None,
            missing=["输入资料已撤回、过期或变化，相关产物与运行详情已屏蔽。"],
        )
        if out.get("intake"):
            out["intake"] = {
                k: out["intake"][k]
                for k in ("attempts", "status", "checked_at", "next_at", "bound_asset_count")
                if k in out["intake"]
            }
    out["candidate_eligible"] = bool(
        not withheld
        and task
        and not task.get("task_case_phase")
        and task["request"]["task_type"] == "autonomous_analysis"
        and task["status"] == "succeeded"
    )
    from .agent_candidates import evaluation_sample_eligible

    out["evaluation_eligible"] = not withheld and evaluation_sample_eligible(task, tx)
    if summary:
        return out
    out["available_assets"] = [
        {
            k: a.get(k)
            for k in ("id", "name", "modality", "media_type", "classification", "bytes", "source_version")
        }
        for a in ([] if withheld else _available(tx, row))
        if a["id"] in row.get("input_refs", [])
    ]
    out["profiles"] = [p for requirement in out["requirements"] for p in requirement.get("profiles", [])]
    if withheld:
        submissions = []
    transported = {tuple(x.get("input_refs", [])) for x in submissions if x.get("transport")}
    out["submissions"] = [
        x for x in submissions if x.get("transport") or tuple(x.get("input_refs", [])) not in transported
    ]
    out["plans"] = [] if withheld else tx.list("plan_versions", row["project_id"], task_case_id=row["id"])
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
    if task and not withheld:
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
                        checkpoint = task.get("tool_checkpoints", {}).get(citation.get("tool_call_id"), {})
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
    _aggregate_trace(tx, row, out, runs)
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


def _verify_upstream_scope(row, source_id, classification):
    if row.get("source_ids") and source_id not in row["source_ids"]:
        s.fail("source_not_requested", "来源标识不在本任务指定的检索范围", 403)
    if row["mode"] == "cloud" and row["public_egress_authorized"] and classification != "public":
        s.fail("data_egress_blocked", "此云端任务仅能处理已授权的公开来源", 403)


def _reject_duplicate_version(tx, row, source_id, version, checksum):
    # The original submission key handles exact replays before this check. Another
    # key may not bind a second copy or silently mutate an already seen upstream version.
    owned = {a["id"]: a for a in tx.list("data_assets", row["project_id"], task_case_id=row["id"])}
    for key in row["input_refs"]:
        if key not in owned:
            linked = tx.get("data_assets", key)
            if linked:
                owned[key] = linked
    for asset in owned.values():
        if asset["source_id"] == source_id and asset["source_version"] == version:
            if asset["checksum"] != checksum:
                s.fail("source_version_conflict", "同一来源版本的内容已变化，请提交新的来源版本", 409)
            s.fail("duplicate_source_version", "此来源版本已经接入；重试请沿用原幂等键", 409)


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
        _verify_upstream_scope(row, body.source_id, body.classification)
        _editable(tx, row)
        encoded = (
            body.content
            if isinstance(body.content, str)
            else json.dumps(
                body.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        ).encode()
        _reject_duplicate_version(
            tx, row, body.source_id, body.source_version, hashlib.sha256(encoded).hexdigest()
        )
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
            replace_sources=True,
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


def upload(
    case_id,
    filename,
    content,
    classification,
    requirement_id,
    key,
    user,
    repository,
    *,
    source_id=None,
    source_version=None,
    provenance=None,
):
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
        if source_id or source_version or provenance:
            signature = s.digest([signature, source_id, source_version, provenance])
        old = tx.get("data_submissions", sid) or tx.get(
            "data_submissions", s.digest([case_id, "upload", key])[:48]
        )
        if old:
            if old["upload_hash"] != signature:
                s.fail("idempotency_conflict", "同一上传标识已用于不同文件", 409)
            return view(tx, row, user)
        _verify_upstream_scope(row, source_id, classification)
        _editable(tx, row)
        source_id = source_id or f"task-upload:{case_id}:{key}"
        file_hash = hashlib.sha256(content).hexdigest()
        source_version = source_version or "sha256:" + file_hash
        _reject_duplicate_version(tx, row, source_id, source_version, file_hash)
        asset = s.create_asset(
            {
                "project_id": row["project_id"],
                "name": Path(filename).name,
                "modality": modality,
                "media_type": media,
                "content": value,
                "source_id": source_id,
                "source_version": source_version,
                "provenance": provenance or "上游接口提交的任务专用原始资料",
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
            replace_sources=True,
        )
        tx.put(
            "data_submissions",
            {**tx.get("data_submissions", sid), "upload_hash": signature, "transport": "api_file"},
        )
        return view(tx, tx.get("task_cases", case_id), user)
