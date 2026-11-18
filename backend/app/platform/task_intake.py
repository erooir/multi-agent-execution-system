"""Durable project-scoped retrieval for upstream applications.

Only new tasks opt in. A short claim fences concurrent workers while network I/O
runs outside a transaction. Source checks and the execution enqueue share one commit.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from .. import auth
from ..casework.intake_plan import _normal, _terms
from . import knowledge
from . import service as s
from . import task_cases as cases
from .db import now
from .task_case_contracts import InlineSubmissionInput

MAX_CONNECTORS = 3
MAX_ITEMS = 8


def _matches(record, asset):
    """An exact upstream key is authoritative. Otherwise use distinctive topic metadata."""
    if record.get("source_ids"):
        return asset["source_id"] in record["source_ids"]
    goal = _normal(record["goal"] + " " + " ".join(record.get("execution_missing", [])))
    name = _normal(asset["name"])
    source = _normal(asset["source_id"])
    if any(len(key) >= 3 and key in goal for key in (source, name)):
        return True
    code = (record.get("matched_case_id") or "").lower()
    if code and re.search(rf"(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])", name + " " + source):
        return True
    stop = {
        "资料",
        "参考资料",
        "数据",
        "报告",
        "分析",
        "公开",
        "任务",
        "统计",
        "csv",
        "pdf",
        "xlsx",
        "json",
        "source",
        "data",
        "report",
    }
    shared = (_terms(goal) & _terms(name + " " + source)) - stop
    return any(len(term) >= 4 for term in shared)


def _catalog_refs(assets, row, needed_keys=()):
    """Version labels are opaque. Choose the latest eligible local receipt per logical source."""
    available = {a["id"]: a for a in assets if row["mode"] != "cloud" or a["classification"] == "public"}
    pinned = {available[key]["source_id"] for key in row["input_refs"] if key in available}
    pinned.difference_update(needed_keys)
    latest, versions = {}, {}
    for asset in available.values():
        source_id = asset["source_id"]
        if (source_id in pinned and not row.get("execution_missing")) or not _matches(row, asset):
            continue
        versions.setdefault((source_id, asset["source_version"]), set()).add(asset["checksum"])
        order = (asset.get("received_at") or asset["created_at"], asset["id"])
        if source_id not in latest or latest[source_id][0] < order:
            latest[source_id] = (order, asset)
    refs, errors = [], []
    for _, asset in latest.values():
        if len(versions[(asset["source_id"], asset["source_version"])]) != 1:
            errors.append("source_version_conflict")
            continue
        if asset["id"] in row["input_refs"]:
            continue
        try:
            profile = cases.profile_asset(asset)
        except s.DomainError:
            errors.append("source_unreadable")
            continue
        if profile["readable"] and profile["nonempty"]:
            refs.append(asset["id"])
        else:
            errors.append("source_unreadable")
    return refs, errors


def management_inputs(body, user, repository):
    """Ad hoc manager requests can omit input_refs. Replay requests keep their frozen choice."""
    data = body.model_dump()
    if "input_refs" in body.model_fields_set:
        return data
    s.project_access(body.project_id, user, True, repository)
    record = {**data, "matched_case_id": data.get("case_id"), "input_refs": []}
    available = []
    for asset in repository.list("data_assets", body.project_id):
        if asset.get("task_case_id"):
            continue
        try:
            s.usable_asset(asset)
        except s.DomainError:
            continue
        available.append(asset)
    refs, _ = _catalog_refs(available, record)
    data["input_refs"] = refs[:50]
    return data


def _due(row, current):
    intake = row.get("intake") or {}
    return (
        bool(intake)
        and not row.get("deleted_at")
        and row.get("status") in {"awaiting_data", "ready"}
        and (row["mode"] != "cloud" or row.get("public_egress_authorized"))
        and (not intake.get("next_at") or s.timestamp(intake["next_at"]) <= current)
    )


def _eligible(asset, row):
    return (row["mode"] != "cloud" or asset["classification"] == "public") and asset["id"] not in row[
        "input_refs"
    ]


async def retrieve_one(repository):
    """Process at most one due case; waiting never occupies a task execution slot."""
    current = datetime.now(UTC)
    candidates = [
        r
        for state in ("ready", "awaiting_data")
        for r in repository.list("task_cases", status=state)
        if _due(r, current)
    ]
    for candidate in sorted(candidates, key=lambda r: (r["intake"].get("next_at") or "", r["created_at"])):
        with repository.transaction() as tx:
            row = tx.get("task_cases", candidate["id"], for_update=True)
            if not row or not _due(row, current):
                continue
            user = auth.store.get("users", row["intake_owner"])
            nonce = uuid.uuid4().hex
            state = {
                **row["intake"],
                "status": "searching",
                "claim": nonce,
                "attempts": row["intake"]["attempts"] + 1,
                "next_at": (current + timedelta(minutes=4)).isoformat(),
            }
            row = tx.put("task_cases", {**row, "intake": state})
            if not _authorized(tx, row, user):
                tx.put(
                    "task_cases",
                    {
                        **row,
                        "status": "failed",
                        "reason_code": "intake_access_revoked",
                        "missing": ["资料接入身份或项目授权已失效"],
                        "intake": {**state, "status": "blocked", "claim": None},
                    },
                )
                return True
            requirements = [
                r
                for r in row["requirements"]
                if r["required"] and r["status"] not in {"provided", "satisfied"}
            ]
            needed_keys = cases.missing_source_ids(tx, row)
            requirements.extend({"title": key} for key in needed_keys)
            if row.get("execution_missing"):
                requirements.extend({"title": gap} for gap in row["execution_missing"])
            connectors = [
                c
                for c in tx.list("connectors", row["project_id"])
                if c.get("automatic_intake") and c["kind"] == "http_knowledge"
            ]
            # Local sources with clear task metadata have priority over upstream requests.
            refs, catalog_errors = _catalog_refs(cases._available(tx, row), row, needed_keys)
        hits, errors, searched = await _fetch_upstream(
            row, requirements, refs, connectors, repository, needed_keys
        )
        errors.extend(catalog_errors)
        with repository.transaction() as tx:
            active = tx.get("task_cases", row["id"], for_update=True)
            if (
                not active
                or active.get("deleted_at")
                or active.get("intake", {}).get("claim") != nonce
                or active.get("status") not in {"awaiting_data", "ready"}
            ):
                return True
            user = auth.store.get("users", active["intake_owner"])
            if not _authorized(tx, active, user):
                return True
            if active.get("revision") != row["revision"]:
                # Push arrived while fetching. It owns the current input set.
                refs, hits = [], []
            available = {a["id"]: a for a in cases._available(tx, active)}
            refs = [key for key in refs[:32] if key in available and _eligible(available[key], active)]
            if refs and requirements:
                cases.submit(
                    row["id"],
                    {"input_refs": refs, "idempotency_key": "catalog-" + s.digest(refs)},
                    user,
                    tx,
                    replace_sources=True,
                )
            imported = _import_upstream(tx, active, user, hits, errors)
            active = tx.get("task_cases", row["id"])
            state = {
                **active["intake"],
                "claim": None,
                "checked_at": now(),
                "next_at": (
                    datetime.now(UTC) + timedelta(seconds=min(600, 15 * state["attempts"]))
                ).isoformat(),
                "status": "awaiting_upstream" if active["missing"] else "ready",
                "connector_ids": searched,
                "bound_asset_count": len(active["input_refs"]),
                "errors": sorted(set(errors)),
            }
            tx.put("task_cases", {**active, "intake": state})
            if refs or imported or state["attempts"] == 1:
                s.audit(
                    tx,
                    row["project_id"],
                    "task_case.sources_retrieved",
                    row["id"],
                    user,
                    {"connector_ids": searched, "catalog_count": len(refs), "received_count": len(imported)},
                )
            if not active["missing"]:
                try:
                    cases.start(row["id"], user, tx)
                except s.DomainError as exc:
                    tx.put(
                        "task_cases",
                        {
                            **tx.get("task_cases", row["id"]),
                            "status": "failed",
                            "reason_code": exc.code,
                            "missing": [exc.message],
                        },
                    )
            return True
    return False


async def _fetch_upstream(row, requirements, refs, connectors, repository, needed_keys):
    hits, errors, searched = [], [], []
    if requirements and not refs:
        query = " ".join([*needed_keys, row["goal"], *(r["title"] for r in requirements)])[:500]
        offset = ((row["intake"]["attempts"] - 1) * MAX_CONNECTORS) % max(1, len(connectors))
        ordered = connectors[offset:] + connectors[:offset]
        for connector in ordered[:MAX_CONNECTORS]:
            searched.append(connector["id"])
            found = await knowledge.connector_results(
                connector,
                {
                    "query": query,
                    "limit": MAX_ITEMS,
                    "project_id": row["project_id"],
                    "source_ids": needed_keys,
                },
                repository,
                task_intake=True,
            )
            for item in found:
                if item.get("error_code"):
                    errors.append(item["error_code"])
                elif row["mode"] == "cloud" and item["classification"] != "public":
                    errors.append("source_local_only")
                elif needed_keys and item["source_id"] not in needed_keys:
                    continue
                elif not row.get("source_ids") or item["source_id"] in row["source_ids"]:
                    hits.append((connector, item))
    return hits, errors, searched


def _import_upstream(tx, row, user, hits, errors):
    imported = []
    observed, signatures = set(), {}
    for _, item in hits:
        key = (item["source_id"], item["source_version"])
        signatures.setdefault(key, set()).add(s.digest([item.get("media_type", "text/plain"), item["text"]]))
    for connector, item in hits:
        if len(signatures[(item["source_id"], item["source_version"])]) != 1:
            errors.append("source_version_conflict")
            continue
        if item["source_id"] in observed:
            continue
        if len(imported) >= MAX_ITEMS:
            break
        config = tx.get("connectors", connector["id"], for_update=True)
        if not config or any(
            config.get(k) != connector.get(k)
            for k in ("project_id", "base_url", "automatic_intake", "allow_public_results")
        ):
            continue
        if not item["text"].strip():
            continue
        try:
            media = item.get("media_type", "text/plain")
            content = json.loads(item["text"]) if media == "application/json" else item["text"]
            suffix = {"application/json": ".json", "text/csv": ".csv", "text/plain": ".txt"}[media]
            body = InlineSubmissionInput(
                name=item["source_id"][:140] + suffix,
                content=content,
                media_type=media,
                source_id=item["source_id"],
                source_version=item["source_version"],
                provenance=item["provenance"],
                classification=item["classification"],
                idempotency_key="retrieved-"
                + s.digest([connector["id"], item["id"], item["source_id"], item["source_version"]]),
            )
        except (ValidationError, ValueError, KeyError):
            errors.append("invalid_upstream_item")
            continue
        observed.add(item["source_id"])
        try:
            cases.ingest_inline(row["id"], body, user, tx)
        except s.DomainError as exc:
            if exc.code == "duplicate_source_version":
                continue
            if exc.code not in {"source_version_conflict", "idempotency_conflict"}:
                raise
            errors.append("source_version_conflict")
            continue
        imported.append(item["source_id"])
    return imported


def _authorized(tx, row, user):
    if not user or not user.get("enabled", True) or user["id"] != row["owner_id"]:
        return False
    try:
        cases.required(tx, row["id"], user, write=True)
    except s.DomainError:
        return False
    return True
