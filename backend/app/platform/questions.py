"""Personal question inbox. Only the service can convert an intake into an executable task."""

import os
from datetime import UTC, datetime

from ..casework import intake_plan, team
from . import edition_intake, public_evidence, question_answers
from . import service as s
from .db import TERMINAL

FAILURES = {
    "case_source_scope": "本项目内该用例的配套公开原件不齐或版本已变化。",
    "case_archived": "所指用例已不在当前项目目录中。",
    "ambiguous_case": "问题同时指向多个用例，请选择一个编号或标题。",
    "budget_exhausted": "此次无法完成对资料的进一步解读，当前没有可核验的答案。",
    "model_not_configured": "当前无法完成所需的资料解读。",
    "offline_policy": "当前环境无法使用这项资料解读能力。",
    "asset_conflict": "与此问题匹配的分析能力暂时不可用。",
    "tool_version_changed": "与此问题对应的核查能力暂时不可用。",
    "semantic_profile_changed": "与此问题对应的资料解读能力暂时不可用。",
    "case_team_required": "当前无法运行此类分析。",
    "case_team_invalid": "当前无法运行此类分析。",
    "unpublished_dependency": "当前无法运行此类分析。",
    "workflow_unavailable": "当前无法运行此类分析。",
    "data_egress_blocked": "所需材料没有获准用于此项资料解读。",
    "ledger_unavailable": "此次资料解读暂时不可用。",
    "insufficient_data": "缺少回答问题所需的有效资料。",
    "unsupported_input": "现有资料无法满足这项分析的要求。",
    "permission_denied": "执行期间的项目或资料授权已失效。",
    "quality_not_met": "结论没有通过自动核查。",
    "deadline_exceeded": "本次分析未能在时限内完成。",
}


def _existing(tx, body, user):
    s.project_access(body.project_id, user, repository=tx)
    tx.get("projects", body.project_id, for_update=True)
    s.project_access(body.project_id, user, repository=tx)
    qid = s.digest([body.project_id, user["id"], body.idempotency_key])
    old = tx.get("questions", qid)
    if old and old["question"] != body.question:
        s.fail("idempotency_conflict", "同一提交标识已用于不同问题。", 409)
    return qid, old


def submit(body, user, repository):
    if user.get("role") not in ("requester", "operator", "admin"):
        s.fail("permission_denied", "没有提交问题的权限。", 403)
    try:
        with repository.transaction() as tx:
            qid, old = _existing(tx, body, user)
            if old:
                return _view(tx, old, user)
            project = tx.get("projects", body.project_id)
            plan = (
                _edition_plan(tx, project, body.question)
                if project.get("scenario_edition")
                else intake_plan.plan_question(
                    body.question, body.project_id, _project_assets(tx, body.project_id)
                )
            )
            record = {
                "id": qid,
                "project_id": body.project_id,
                "owner_id": user["id"],
                "question": body.question,
                "outcome": plan["status"],
                "missing": plan.get("gaps", []),
                "task_id": None,
                "status": "failed",
            }
            if plan["status"] in ("ready", "semantic"):
                # Membership is checked as the actual requester. Only this fixed platform operation can
                # provision a version and submit a job; the worker keeps checking the original identity.
                worker = {**user, "role": "operator"} if user["role"] == "requester" else user
                if plan["status"] == "semantic":
                    if os.environ.get("HANGZHI_QUESTION_PUBLIC_EGRESS", "false").lower() != "true":
                        s.fail("data_egress_blocked", "系统未开启公开资料自动云端解读。", 403)
                    task = public_evidence.submit(
                        public_evidence.PublicEvidenceRequest(
                            project_id=body.project_id,
                            input_refs=plan["input_refs"],
                            focus=plan["focus"],
                            idempotency_key="question-" + qid,
                            max_cost_cny=0.8,
                            public_egress_authorized=True,
                        ),
                        worker,
                        tx,
                        topics=plan.get("topics", []),
                        question_answer=True,
                    )
                else:
                    code = plan["case_id"]
                    edition = plan.get("edition", False)
                    if edition:
                        if os.environ.get("HANGZHI_QUESTION_PUBLIC_EGRESS", "false").lower() != "true":
                            s.fail("data_egress_blocked", "系统未开启公开资料自动云端解读。", 403)
                    else:
                        team.install(body.project_id, worker, tx, case_id=code, upgrade=True)
                    task = s.create_task(
                        {
                            "project_id": body.project_id,
                            "task_type": "case_analysis",
                            "case_id": code,
                            "case_scope": plan["case_scope"],
                            "input_refs": plan["input_refs"],
                            "name": body.question[:100],
                            "goal": body.question,
                            "idempotency_key": "question-" + qid,
                            "service_class": "interactive",
                            "deadline_seconds": 600,
                            "max_revisions": 2,
                            "allow_partial": not edition,
                            **({"mode": "cloud", "max_cost_cny": 0.8} if edition else {}),
                        },
                        worker,
                        tx,
                    )
                stored = tx.get("tasks", task["id"])
                tx.put(
                    "tasks",
                    {
                        **stored,
                        "question_id": qid,
                        "authorization": {k: user[k] for k in ("id", "role", "username") if k in user},
                    },
                )
                record.update(task_id=task["id"], status=task["status"])
            elif not record["missing"]:
                record["missing"] = [plan["reason"]]
            record = tx.put("questions", record)
            s.audit(tx, body.project_id, "question.submitted", qid, user)
            return _view(tx, record, user)
    except s.DomainError as exc:
        if exc.code not in FAILURES or exc.code == "permission_denied":
            raise
        # Roll back any partial team installation before persisting the honest terminal outcome.
        with repository.transaction() as tx:
            qid, old = _existing(tx, body, user)
            if not old:
                old = tx.put(
                    "questions",
                    {
                        "id": qid,
                        "project_id": body.project_id,
                        "owner_id": user["id"],
                        "question": body.question,
                        "outcome": "unavailable",
                        "task_id": None,
                        "status": "failed",
                        "missing": [FAILURES[exc.code]],
                    },
                )
                s.audit(tx, body.project_id, "question.unavailable", qid, user)
            return _view(tx, old, user)


def _project_assets(tx, project_id):
    return [a for a in tx.list("data_assets", project_id) if not a.get("task_case_id")]


def _edition_plan(tx, project, question):
    case = edition_intake.match(project, question)
    if case:
        return {
            "status": "ready",
            "edition": True,
            "case_id": case["id"],
            "case_scope": "project",
            "input_refs": edition_intake.sources(tx, project["id"], case),
        }
    # Open questions retain the normal public-text path. The old case ranker cannot
    # select a superseded contract in a project that published its own directory.
    from .runtime import _asset_bytes

    def result(status, reason, *, items=()):
        return {
            "status": status,
            "reason": reason,
            "input_refs": [item["id"] for item in items],
            "gaps": [] if status == "semantic" else [reason],
        }

    q = intake_plan._normal(question).strip()[:8000]
    now = datetime.now(UTC)
    eligible = [a for a in _project_assets(tx, project["id"]) if intake_plan._eligible(a, project["id"], now)]
    if len(eligible) > 500:
        eligible = [
            a for a in eligible if intake_plan._explicit_name(q, {"name": intake_plan._normal(a["name"])})
        ]
        if not eligible or len(eligible) > 500:
            return result("ambiguous", "请指明要查询的资料名称或当前用例编号。")
    remaining = [intake_plan.MAX_SCAN_BYTES]
    views = [intake_plan._source_view(a, _asset_bytes, remaining) for a in eligible]
    return intake_plan._semantic(q, views, result)


def _edition_answer(tx, task, sources, *, summary):
    from ..casework.document_common import bounded_excerpt

    for artifact in tx.list("artifacts", task["project_id"], task_id=task["id"]):
        report = artifact.get("content")
        if (
            artifact.get("type") != "case_report"
            or artifact.get("quality_status") != "passed"
            or not isinstance(report, dict)
            or not report.get("edition")
            or artifact.get("checksum") != s.digest(report)
        ):
            continue
        evidence = {
            e["id"]: e
            for e in report.get("evidence", [])
            if e.get("source_id") in sources
            and e.get("source_version") == sources[e["source_id"]]["source_version"]
        }
        findings = [
            f
            for f in report.get("products", {}).get("专题分析", [])
            if f.get("text") and f.get("evidence_ids") and set(f["evidence_ids"]) <= evidence.keys()
        ]
        if not findings:
            break
        if summary:
            return {"incomplete": False}
        citations = []
        for key in dict.fromkeys(ref for f in findings for ref in f["evidence_ids"]):
            e = evidence[key]
            quote = str(e.get("quote") or "")
            citations.append(
                {
                    "source": sources[e["source_id"]]["name"],
                    "source_id": e["source_id"],
                    "location": (e.get("location") or {}).get("原件位置", ""),
                    "excerpt": bounded_excerpt(quote, 450),
                    "quote_truncated": len(quote) > 450,
                }
            )
        paragraphs = [f"{f.get('heading', '')}　{f['text']}".strip() for f in findings]
        limits = [v for v in report["products"].get("资料适用范围", []) if isinstance(v, str) and v]
        if limits:
            paragraphs.append("资料适用范围　" + " ".join(limits))
        return {
            "answer": "\n".join(paragraphs),
            "citations": citations,
            "missing": report.get("gaps", []),
            "incomplete": False,
        }
    return {"missing": ["当前没有通过审核的新版用例结论。"], "incomplete": True}


def _view(tx, question, user, *, summary=False):
    s.project_access(question["project_id"], user, repository=tx)
    visible = {k: question[k] for k in ("id", "question", "status", "created_at", "updated_at")}
    visible.update(outcome=question["outcome"], missing=question.get("missing", []))
    if not question.get("task_id"):
        return visible
    task = tx.get("tasks", question["task_id"])
    if not task or task.get("question_id") != question["id"] or task["project_id"] != question["project_id"]:
        return {**visible, "status": "failed", "missing": ["暂时无法核验此次分析的运行记录。"]}
    visible.update(status=task["status"], updated_at=task["updated_at"], outcome=None)
    if task["status"] not in TERMINAL:
        return visible
    sources = {a["id"]: a for a in task["snapshot"]["inputs"]}
    withheld = {
        **visible,
        "content_withheld": True,
        "missing": ["原始资料已撤回、过期或发生变化，答案与引用已屏蔽。"],
    }
    checked = []
    try:
        if not summary:
            # A revoke/member change uses the same project -> source lock order.
            tx.get("projects", question["project_id"], for_update=True)
            s.project_access(question["project_id"], user, repository=tx)
        for key in sorted(sources):
            current = tx.get("data_assets", key, for_update=not summary)
            s.verify_frozen_asset(sources[key], current)
            checked.append(current)
    except s.DomainError:
        s.project_access(question["project_id"], user, repository=tx)
        return withheld
    if task["status"] not in ("succeeded", "partial"):
        return {
            **visible,
            "missing": [FAILURES.get(task.get("reason_code"), "此次分析没有形成可核验的答案。")],
        }
    edition = task["snapshot"].get("case_profile", {}).get("edition")
    if not edition and (scope_issue := _scope_issue(tx, task, question["question"])):
        return {**visible, "status": "partial", "missing": [scope_issue], "outcome": "scope_mismatch"}
    delivery = (
        _edition_answer(tx, task, sources, summary=summary)
        if edition
        else question_answers.deliver(tx, task, sources, question["question"], summary=summary)
    )
    incomplete = delivery.pop("incomplete", not bool(delivery.get("answer")))
    if incomplete and visible["status"] == "succeeded":
        visible.update(status="partial", outcome="incomplete_answer")
    if not summary:
        visible.update(delivery)
        try:
            for source in checked:
                s.usable_asset(source)
        except s.DomainError:
            return withheld
    return visible


def _scope_issue(tx, task, question):
    if task["request"].get("case_id") != "G03":
        return None
    for artifact in tx.list("artifacts", task["project_id"], task_id=task["id"]):
        products = (artifact.get("content") or {}).get("products", {})
        anomaly = products.get("anomaly") or {}
        target = anomaly.get("target_year")
        years = [v.get("year") for v in products.get("annual_series", []) if isinstance(v.get("year"), int)]
        if target and years:
            return intake_plan.case_scope_conflict(
                "G03",
                question,
                [
                    {
                        "target_year": target,
                        "start_year": min(years),
                        "baseline_years": anomaly.get("baseline_years", []),
                    }
                ],
            )
    return None


def get(qid, user, repository):
    with repository.transaction() as tx:
        row = tx.get("questions", qid)
        if not row or row["owner_id"] != user["id"]:
            s.fail("not_found", "未找到此问题。", 404)
        return _view(tx, row, user)


def history(project_id, user, repository):
    with repository.transaction() as tx:
        s.project_access(project_id, user, repository=tx)
        rows = tx.list("questions", project_id, owner_id=user["id"])
        return [_view(tx, q, user, summary=True) for q in reversed(rows)]
