"""Agent activity is computed from actual node attempts; never load their source/output payloads."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..platform.dameng import json_scalar
from ..platform.db import tables
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
    agents = {
        a["id"]: {
            "agent_id": a["id"],
            "name": a["name"],
            "status": a["status"],
            "current_version": a["current_version"],
            "attempts": 0,
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
        a["attempts"] += 1
        a["running"] += int(r["status"] == "running")
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
        a["mean_seconds"] = round(a.pop("total_seconds") / a["timed"], 3) if a["timed"] else None
        a.pop("total_seconds", None)
    return {
        "project_id": project_id,
        "window_hours": window_hours,
        "agents": sorted(agents.values(), key=lambda a: (-a["running"], -a["attempts"], a["name"])),
    }
