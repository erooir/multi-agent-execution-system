"""Agent activity is computed from actual node attempts; never load their source/output payloads."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ..platform.dameng import json_scalar
from ..platform.db import TERMINAL, tables
from ..platform.service import project_access, timestamp


def activity(project_id, user, repository, window_hours=168):
    project_access(project_id, user, repository=repository)
    start = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    table = tables["nodes"]
    cols = [
        "agent_id",
        "agent_version",
        "task_id",
        "node_id",
        "started_at",
        "finished_at",
        "error",
        "attempt",
        "review_passed",
        "reused_from",
    ]
    q = (
        select(
            table.c.status,
            table.c.created_at,
            *[json_scalar(repository.engine, table.c.payload, k).label(k) for k in cols],
        )
        .where(
            table.c.project_id == project_id, (table.c.created_at >= start) | (table.c.status == "running")
        )
        .order_by(table.c.created_at)
    )
    with repository.engine.connect() as conn:
        rows = conn.execute(q).mappings().all()
        task_table = tables["tasks"]
        leased = set(
            conn.execute(
                select(task_table.c.id).where(
                    task_table.c.project_id == project_id,
                    task_table.c.status.not_in(sorted(TERMINAL)),
                    task_table.c.lease_owner.is_not(None),
                    task_table.c.lease_until > time.time(),
                )
            ).scalars()
        )
    agents = {
        a["id"]: {
            "agent_id": a["id"],
            "name": a["name"],
            "status": a["status"],
            "current_version": a["current_version"],
            "attempts": 0,
            "task_ids": set(),
            "active_nodes": [],
            "running": 0,
            "failed": 0,
            "revisions": 0,
            "timed": 0,
            "total_seconds": 0,
            "last": None,
            "versions": {},
            "review_rejections": 0,
        }
        for a in repository.list("agents", project_id)
    }
    for r in rows:
        a = agents.get(r["agent_id"])
        if not a:
            continue
        if r["reused_from"] or r["status"] == "reused":
            continue
        if r["created_at"] >= start:
            a["task_ids"].add(r["task_id"])
        running = r["status"] == "running" and r["task_id"] in leased
        if running:
            a["active_nodes"].append(
                {"task_id": r["task_id"], "node_id": r["node_id"], "started_at": r["started_at"]}
            )
        a["attempts"] += 1
        a["running"] += int(running)
        a["failed"] += int(r["status"] == "failed")
        a["review_rejections"] += int(
            r["review_passed"] is not None and str(r["review_passed"]).lower() in {"false", "0"}
        )
        a["revisions"] += int(int(r["attempt"] or 1) > 1)
        v = str(r["agent_version"])
        a["versions"][v] = a["versions"].get(v, 0) + 1
        if r["started_at"] and r["finished_at"]:
            elapsed = (timestamp(r["finished_at"]) - timestamp(r["started_at"])).total_seconds()
            if elapsed >= 0:
                a["timed"] += 1
                a["total_seconds"] += elapsed
        a["last"] = {k: r[k] for k in ("task_id", "node_id", "status", "error", "started_at", "finished_at")}
    for a in agents.values():
        a["distinct_tasks"] = len(a.pop("task_ids"))
        a["mean_seconds"] = round(a.pop("total_seconds") / a["timed"], 3) if a["timed"] else None
        a.pop("total_seconds", None)
    return {
        "project_id": project_id,
        "window_hours": window_hours,
        "agents": sorted(agents.values(), key=lambda a: (-a["running"], -a["attempts"], a["name"])),
    }


def capability_usage(project_id, user, repository, window_hours=168):
    """Count recorded invocation IDs by lifecycle event; never fetch tool arguments or results."""
    project_access(project_id, user, repository=repository)
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    t = tables["events"]

    def field(*keys):
        return json_scalar(repository.engine, t.c.payload, *keys)

    event, cap, version, kind, call = (
        field("type"),
        field("payload", "capability_id"),
        field("payload", "version"),
        field("payload", "kind"),
        field("payload", "id"),
    )
    q = (
        select(cap, version, kind, event, func.count(func.distinct(call)))
        .where(
            t.c.project_id == project_id,
            t.c.created_at >= since,
            event.in_(["tool.started", "tool.completed", "tool.failed", "tool.uncertain"]),
        )
        .group_by(cap, version, kind, event)
    )
    with repository.engine.connect() as conn:
        rows = conn.execute(q).all()
    usage = {}
    for cid, ver, kind_name, phase, count in rows:
        if not cid:
            continue
        entry = usage.setdefault(
            (cid, ver, kind_name),
            {
                "id": cid,
                "version": str(ver),
                "kind": kind_name,
                "started": 0,
                "completed": 0,
                "failed": 0,
                "uncertain": 0,
            },
        )
        entry[phase.split(".")[1]] = count
    return {"window_hours": window_hours, "items": list(usage.values())}
