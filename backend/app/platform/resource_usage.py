"""Project-scoped resource observations derived from task leases and the gateway."""

import sqlite3
import time
from contextlib import closing

from .. import resource_limits as limits
from ..config import MAX_OUTPUT_TOKENS, MODEL_ID, intranet_provider
from ..model_gateway import model_gateway
from . import service
from .db import TERMINAL


def project_resource_usage(project_id, user, repository):
    service.project_access(project_id, user, repository=repository)
    tasks = repository.operational_tasks([project_id])
    current = time.time()
    local_ms = []
    local_count = 0
    for row in tasks:
        if row.get("mode") != "local":
            continue
        local_count += 1
        if row["status"] in TERMINAL and row.get("finished_at") and row.get("first_claimed_at"):
            try:
                elapsed = service.timestamp(row["finished_at"]) - service.timestamp(row["first_claimed_at"])
            except service.DomainError:
                continue  # Legacy rows may have missing or invalid timing evidence.
            if elapsed.total_seconds() >= 0:
                local_ms.append(elapsed.total_seconds() * 1000)
    upstream = intranet_provider()
    result = {
        "project_id": project_id,
        "policy": {
            "task_slots": limits.task_slots(),
            "model_slots": limits.model_slots(),
            "context_tokens": limits.context_window(),
            "output_tokens": MAX_OUTPUT_TOKENS,
            "model": upstream.model if upstream else MODEL_ID,
            "provider": "intranet" if upstream else "deepseek",
        },
        "local": {
            "tasks": local_count,
            "elapsed": limits.latency_summary(local_ms),
        },
        "active_tasks": sum(
            1
            for row in tasks
            if row["status"] not in TERMINAL and row["lease_owner"] and (row["lease_until"] or 0) >= current
        ),
        "host": limits.host_capacity() if user["role"] == "admin" else None,
        "gateway": None,
    }
    try:
        result["gateway"] = gateway_measurements(model_gateway.ledger_path, [r["id"] for r in tasks])
    except (OSError, sqlite3.DatabaseError):
        result["gateway_error"] = "当前无法只读查询历史模型账本"
    return result


def gateway_measurements(path, task_ids):
    requests = settled = input_tokens = output_tokens = 0
    upper_bounds, actual_context, occupancy, durations = [], [], [], []
    spent = reserved = 0.0
    internal = 0
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        # A consistent read across all batches. Do not attribute other workspaces'
        # charges or unscoped maintenance calls to whoever happens to be signed in.
        db.execute("BEGIN")
        # Old, read-only financial ledgers predate model resource metering.
        # Query available observations without adding a table or modifying a row.
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        from_name = "model_request_usage" if "model_request_usage" in names else "charges"
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({from_name})")}
        if not {"id", "run_id", "state", "cost", "reserved", "input_tokens", "output_tokens"} <= columns:
            raise sqlite3.DatabaseError("invalid charge schema")
        provider = "c.provider" if from_name == "model_request_usage" else "'deepseek' AS provider"
        has_resources = "resource_requests" in names
        bounds = (
            "r.input_upper,r.output_limit,r.context_limit,r.duration_ms"
            if has_resources
            else ("NULL AS input_upper,NULL AS output_limit,NULL AS context_limit,NULL AS duration_ms")
        )
        join = "LEFT JOIN resource_requests r ON r.charge_id=c.id" if has_resources else ""
        for start in range(0, len(task_ids), 400):
            batch = task_ids[start : start + 400]
            rows = db.execute(
                f"SELECT {provider},c.state,c.cost,c.reserved,c.input_tokens,c.output_tokens,{bounds} "
                f"FROM {from_name} c {join} WHERE c.run_id IN (" + ",".join("?" for _ in batch) + ")",
                batch,
            )
            for row in rows:
                requests += 1
                internal += row["provider"] == "intranet"
                if row["input_upper"] is not None:
                    upper_bounds.append(row["input_upper"] + row["output_limit"])
                if row["duration_ms"] is not None:
                    durations.append(row["duration_ms"])
                if row["state"] == "settled":
                    settled += 1
                    input_tokens += row["input_tokens"]
                    output_tokens += row["output_tokens"]
                    spent += row["cost"]
                    used = row["input_tokens"] + row["output_tokens"]
                    actual_context.append(used)
                    if row["context_limit"]:
                        occupancy.append(used / row["context_limit"])
                else:
                    reserved += row["reserved"]
    return {
        "requests": requests,
        "resource_schema_available": has_resources,
        "intranet_requests": internal,
        "reported": settled,
        "unconfirmed": requests - settled,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "peak_context_tokens": max(actual_context, default=None),
        "peak_context_ratio": max(occupancy, default=None),
        "estimated_peak_upper": max(upper_bounds, default=None),
        "estimated_samples": len(upper_bounds),
        "latency": limits.latency_summary(durations),
        "settled_cny": round(spent, 6),
        "reserved_cny": round(reserved, 6),
    }
