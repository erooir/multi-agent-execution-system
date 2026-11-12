"""Personal question inbox. Only the service can convert an intake into an executable task."""

import os

from ..casework import intake_plan, team
from . import public_evidence, question_answers
from . import service as s
from .db import TERMINAL

FAILURES = {
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
            assets = [a for a in tx.list("data_assets", body.project_id) if not a.get("task_case_id")]
            plan = intake_plan.plan_question(body.question, body.project_id, assets)
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
                            "allow_partial": True,
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
    if scope_issue := _scope_issue(tx, task, question["question"]):
        return {**visible, "status": "partial", "missing": [scope_issue], "outcome": "scope_mismatch"}
    delivery = question_answers.deliver(tx, task, sources, question["question"], summary=summary)
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
