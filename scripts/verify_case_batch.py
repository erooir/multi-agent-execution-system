"""Verify the ten reference batches with supplied original files via a loopback, offline DM platform.

No source URLs are downloaded. Exit 0: every selected reference task succeeded; 2: one or
more incomplete business outcomes; 1: infrastructure/verification error. All reports stay private.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
MIME = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
}
# Only map explicitly supplied originals. Context PDFs are never converted into invented raster observations.
CASES = {
    "B01": ("B01_*.pdf",),
    "B05": ("B05_*.pdf",),
    "B06": ("B06_*Protocol*.pdf", "B06_*Measures*.pdf"),
    "T01": ("T01_*.pdf",),
    "T05": ("ICAO_*.pdf",),
    "G03": ("G03_*.xlsx",),
    "G04": ("EFAS_*.pdf",),
    "G05": ("G05_*.pdf",),
    "G06": ("G06_*.pdf",),
    "G07": ("G07_*.csv",),
}


def now():
    return datetime.now(UTC).isoformat()


def local_url(value):
    p = urlsplit(value)
    if (
        p.scheme not in {"http", "https"}
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in ("", "/")
        or not p.hostname
        or not ip_address(p.hostname).is_loopback
        or p.port in (None, 8000)
    ):
        raise ValueError("Only a non-production loopback IP/port is accepted")
    return value.rstrip("/")


def sources(directory):
    directory = directory.resolve(strict=True)
    source_manifest = directory / "download_manifest.json"
    if source_manifest.is_symlink() or source_manifest.resolve().parent != directory:
        raise ValueError("Manifest escaped the supplied material directory")
    metadata = json.loads(source_manifest.read_text(encoding="utf-8-sig"))
    rows = {}
    for item in metadata:
        name = item["name"]
        if Path(name).name != name or name in rows or Path(name).suffix.lower() not in MIME:
            raise ValueError("Duplicate or unsafe source manifest entry")
        path = (directory / name).resolve(strict=True)
        if path.parent != directory or (directory / name).is_symlink() or not path.is_file():
            raise ValueError("Source escaped the prescribed local directory")
        raw = path.read_bytes()
        if (
            len(raw) != item["bytes"]
            or len(raw) > 10_000_000
            or hashlib.sha256(raw).hexdigest() != item["sha256"]
        ):
            raise ValueError(f"Original byte count or SHA disagrees with supplied manifest: {name}")
        # Retain the source attribution as metadata; never request it over the network.
        origin = str(item.get("url", "") or "")
        if len(origin) > 850 or any(c < " " for c in origin):
            raise ValueError("Invalid provenance in manifest")
        rows[name] = (path, item["sha256"], len(raw), origin)
    mapping = {}
    for case, patterns in CASES.items():
        mapped = []
        for pattern in patterns:
            files = [name for name, (path, _, _, _) in rows.items() if path.match(pattern)]
            if len(files) != 1:
                raise ValueError(f"{case} does not have exactly one matching original for {pattern}")
            mapped.append(files[0])
        mapping[case] = mapped
    flat = [name for files in mapping.values() for name in files]
    if len(flat) != 11 or set(flat) != set(rows) or len(set(flat)) != len(flat):
        raise ValueError("Expected 11 distinct manifest-backed originals across 10 tasks")
    return rows, mapping


def work_dir(directory):
    root = (ROOT / ".local").resolve()
    directory = directory.resolve()
    if directory == root or not directory.is_relative_to(root):
        raise ValueError("Report target must be inside ignored .local")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_report(where, state):
    temp = where / "batch.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(where / "batch.json")


def observation(case, report):
    products = report.get("products", {})
    facts = {
        "product_names": sorted(products),
        "evidence_count": len(report.get("evidence", [])),
        "gaps": report.get("gaps", []),
        "failed_checks": [
            {"name": v["name"], "message": v["message"], "hard": v["hard"]}
            for v in report.get("review", [])
            if not v.get("passed")
        ],
        "reading_aids": len(report.get("reading_aids", [])),
        "reading_notes": report.get("reading_notes", []),
    }
    if case == "B01":
        facts.update(observations=products.get("observations"), window=products.get("report_window"))
    elif case == "B05":
        facts.update(
            months=len(products.get("monthly", [])),
            partial_months=[
                m["month"] for m in products.get("monthly", []) if m.get("complete_month") is False
            ],
            totals=[
                {"computed": t["computed"], "reported": t["reported"], "agrees": t["agrees"]}
                for t in products.get("reconciliation", [])
            ],
        )
    elif case == "B06":
        facts.update(
            paired_clauses=len(products.get("clause_map", [])), unmatched=len(products.get("unmatched", []))
        )
    elif case == "T01":
        facts.update(
            bibliographic_fields=sorted(products.get("bibliography", {})),
            card_evidence={k: len(v) for k, v in products.get("literature_card", {}).items()},
        )
    elif case == "T05":
        facts.update(
            figures=[
                {
                    "figure": f["figure"],
                    "captions": len(f["captions"]),
                    "labelled_values": len(f["labelled_values"]),
                }
                for f in products.get("figure_cards", [])
            ]
        )
    elif case == "G03":
        facts.update(years=len(products.get("annual_series", [])), anomaly=products.get("anomaly"))
    elif case == "G07":
        q = products.get("quality", {})
        facts.update(
            records=q.get("record_count"),
            samples=q.get("unique_samples"),
            sites=q.get("unique_sites"),
            mapped_points=len(products.get("sample_points", {}).get("features", [])),
        )
    else:
        facts.update(context_pages=[p.get("page") for p in products.get("source_notes", [])])
    return facts


def api(client, method, path, **kwargs):
    reply = client.request(method, "/api" + path, **kwargs)
    reply.raise_for_status()
    return reply


def budget(client):
    b = api(client, "GET", "/v2/budget").json()
    return {k: b.get(k) for k in ("available", "limit_cny", "spent_cny", "reserved_cny", "request_count")}


def record_cost_scope(state):
    """Project-wide counters may legitimately change while other isolated jobs run."""
    before, after = state["budget_before"], state["budget_after"]
    tasks = list(state["tasks"].values())
    usage = [r.get("usage", {}) for r in tasks]
    attributable = bool(usage) and all(
        u.get("accounting_available") and u.get("accounting_complete") for u in usage
    )
    batch = {
        k: round(sum(u.get(k, 0) for u in usage), 6) if attributable else None
        for k in ("request_count", "cost_cny", "reserved_cny", "total_cny")
    }
    shared = bool(before["available"] and after["available"])
    delta = {
        k: round(after[k] - before[k], 6) if shared else None
        for k in ("request_count", "spent_cny", "reserved_cny")
    }
    state["accounting"] = {
        "per_batch_tasks": batch,
        "verified_task_count": len(usage) if attributable else 0,
        "global_delta": delta,
        "scope_note": "全项目计费变动包含并行工作；本批费用仅按本批实际task ID的网关结果归属。",
    }
    return (
        attributable
        and not any(batch.values())
        and shared
        and before["limit_cny"] == after["limit_cny"] == 300
        and delta["request_count"] >= 0
        and delta["spent_cny"] >= 0
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--cases", nargs="+", choices=sorted(CASES), default=list(CASES))
    parser.add_argument("--deadline", type=int, default=600)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    rows, mapping = sources(args.materials)
    cases = list(dict.fromkeys(args.cases))
    endpoint = local_url(args.url)
    if not 60 <= args.deadline <= 3600:
        raise ValueError("deadline must be 60..3600 seconds")
    out = work_dir(args.output or ROOT / ".local" / "acceptance" / ("case-batch-" + uuid.uuid4().hex[:12]))
    report_path = out / "batch.json"
    fingerprints = {name: sha for name, (_, sha, _, _) in rows.items()}
    if args.resume and report_path.exists():
        state = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            state.get("source_sha256") != fingerprints
            or state.get("endpoint") != endpoint
            or state.get("case_ids") != cases
        ):
            raise ValueError("Resume parameters or original sources differ from recorded inputs")
    elif args.resume or report_path.exists():
        raise ValueError("Unexpected existing or absent report; select a new private output directory")
    else:
        state = {
            "started_at": now(),
            "run_id": uuid.uuid4().hex,
            "endpoint": endpoint,
            "source_sha256": fingerprints,
            "case_ids": cases,
            "assets": {},
            "tasks": {},
        }
    pw = os.environ.get("HANGZHI_SMOKE_PASSWORD")
    if not pw:
        raise ValueError("Set HANGZHI_SMOKE_PASSWORD for the named isolated instance")
    with httpx.Client(base_url=endpoint, trust_env=False, follow_redirects=False, timeout=90) as client:
        api(
            client,
            "POST",
            "/auth/login",
            json={"username": os.environ.get("HANGZHI_SMOKE_USERNAME", "admin"), "password": pw},
        )
        system = api(client, "GET", "/v2/bootstrap").json()["system"]
        if (
            not system.get("offline")
            or not system.get("worker_online")
            or str(system["database"]).lower() not in {"dm", "dm8", "dameng"}
        ):
            raise ValueError("Require offline mode, a live Worker and an actual DM business repository")
        state["system"] = {
            k: system[k] for k in ("database", "offline", "worker_online", "agno_version", "version")
        }
        state.setdefault("budget_before", budget(client))
        if not state.get("project_id"):
            state["project_id"] = api(
                client,
                "POST",
                "/v2/projects",
                json={
                    "name": "本机参考批次 " + state["run_id"][:8],
                    "description": "已核验的本地来源；禁止外发",
                },
            ).json()["id"]
        pid = state["project_id"]
        write_report(out, state)
        api(client, "POST", "/v2/casework/install", params={"project_id": pid})
        for case in cases:
            rec = state["tasks"].setdefault(case, {})
            if rec.get("complete"):
                continue
            print(f"{case}: submitting reference batch", flush=True)
            try:
                ids = []
                for name in mapping[case]:
                    if name not in state["assets"]:
                        src, sha, size, origin = rows[name]
                        raw = src.read_bytes()
                        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
                            raise ValueError("Source changed before upload")
                        form = {
                            "project_id": pid,
                            "source_id": "reference-" + hashlib.sha256(name.encode()).hexdigest()[:20],
                            "source_version": "sha256:" + sha,
                            "provenance": f"{origin}；本地材料清单SHA-256:{sha}",
                            "classification": "local",
                            "retention_days": "30",
                        }
                        if src.suffix.lower() == ".csv":
                            form["csv_encoding"] = "windows-1252"
                        a = api(
                            client,
                            "POST",
                            "/v2/data-assets/upload",
                            data=form,
                            files={"file": (name, raw, MIME[src.suffix.lower()])},
                        ).json()
                        if a.get("checksum") != sha or a.get("classification") != "local":
                            raise ValueError("Stored original hash or classification mismatch")
                        state["assets"][name] = a["id"]
                        write_report(out, state)
                    ids.append(state["assets"][name])
                if case == "B06":
                    if "binding_id" not in rec:
                        bindings = [
                            {"kind": "document_binding", "target_source_id": ids[0], "side": "before"},
                            {"kind": "document_binding", "target_source_id": ids[1], "side": "after"},
                        ]
                        a = api(
                            client,
                            "POST",
                            "/v2/data-assets",
                            json={
                                "project_id": pid,
                                "name": "文档比较版本绑定",
                                "modality": "structured",
                                "media_type": "application/json",
                                "content": bindings,
                                "source_id": "version-roles",
                                "source_version": "1",
                                "provenance": "已知两版原件的比较顺序；不提供条款答案",
                                "classification": "local",
                            },
                        ).json()
                        rec["binding_id"] = a["id"]
                        write_report(out, state)
                    ids.append(rec["binding_id"])
                body = {
                    "project_id": pid,
                    "name": "参考批次 " + case,
                    "goal": "按所选用例合同从提供的原件完成分析、核查证据与缺口。",
                    "task_type": "case_analysis",
                    "case_id": case,
                    "case_scope": "reference",
                    "mode": "local",
                    "input_refs": ids,
                    "idempotency_key": "ref-" + state["run_id"] + "-" + case,
                    "max_cost_cny": 0,
                    "max_revisions": 1,
                    "allow_partial": True,
                    "deadline_seconds": args.deadline,
                }
                pre = api(client, "POST", "/v2/tasks/preflight", json=body).json()
                if not pre.get("ready"):
                    raise ValueError("Preflight did not authorize this reference batch")
                job = api(client, "POST", "/v2/tasks", json=body).json()
                tid = rec["id"] = job["id"]
                rec["input_ids"] = ids
                write_report(out, state)
                limit = time.monotonic() + args.deadline + 90
                while time.monotonic() < limit:
                    reply = api(client, "GET", f"/v2/tasks/{tid}/result")
                    if reply.status_code == 200:
                        break
                    if reply.status_code != 202:
                        raise ValueError("Unexpected result response")
                    time.sleep(1.5)
                else:
                    raise TimeoutError(
                        f"{case} did not return its terminal result before the observation deadline"
                    )
                bundle = reply.json()
                detail = api(client, "GET", f"/v2/tasks/{tid}").json()
                if not bundle or detail["status"] != bundle["status"] or bundle["mode"] != "local":
                    raise ValueError("Result/terminal state mismatch")
                node_fields = (
                    "node_id",
                    "agent_id",
                    "agent_version",
                    "attempt",
                    "status",
                    "started_at",
                    "finished_at",
                    "error",
                    "review_passed",
                )
                rec.update(
                    status=detail["status"],
                    reason=detail.get("reason_code"),
                    revision=detail["revision"],
                    accepted_at=detail["created_at"],
                    finished_at=detail.get("finished_at"),
                    end_to_end_seconds=(
                        datetime.fromisoformat(detail["finished_at"])
                        - datetime.fromisoformat(detail["created_at"])
                    ).total_seconds()
                    if detail.get("finished_at")
                    else None,
                    usage=bundle["usage"],
                    nodes=[{k: n.get(k) for k in node_fields} for n in detail["nodes"]],
                    released_instances=sum(i.get("status") == "released" for i in detail["instances"]),
                    total_instances=len(detail["instances"]),
                    event_count=len(detail["events"]),
                )
                report = next((a for a in detail["artifacts"] if a.get("type") == "case_report"), None)
                rec["report_published"] = bool(report)
                if report:
                    if (
                        report["content"].get("scope") != "reference"
                        or report["content"].get("case_id") != case
                    ):
                        raise ValueError("Unexpected report scope")
                    rec["result"] = observation(case, report["content"])
                    payload = api(client, "GET", f"/v2/artifacts/{report['id']}/case-package").content
                    z = zipfile.ZipFile(io.BytesIO(payload))
                    files = z.namelist()
                    if z.testzip() or not {"brief.docx", "report.json", "evidence.csv"} <= set(files):
                        raise ValueError("Case package is incomplete")
                    expected_card = {"B01": "bulletin.docx", "T01": "reading-card.docx"}.get(case)
                    if expected_card and expected_card not in files:
                        raise ValueError("Required single-page deliverable missing")
                    if case in {"B05", "G03"} and not (
                        any(f.startswith("tables/") and f.endswith(".csv") for f in files)
                        and any(f.startswith("figures/") and f.endswith(".svg") for f in files)
                    ):
                        raise ValueError("Required data table or trend figure missing")
                    manifest = json.loads(z.read("manifest.json"))
                    for item in manifest["files"]:
                        exported = z.read(item["name"])
                        if (
                            len(exported) != item["bytes"]
                            or hashlib.sha256(exported).hexdigest() != item["sha256"]
                        ):
                            raise ValueError("Delivery attachment integrity check failed")
                    exported_report = json.loads(z.read("report.json"))
                    if (
                        exported_report.get("scope") != "reference"
                        or not (exported_report.get("reference_batch") or {}).get("registered")
                        or exported_report.get("evidence") != report["content"]["evidence"]
                        or exported_report.get("products") != report["content"]["products"]
                    ):
                        raise ValueError("Exported evidence differs from the registered reference run")
                    (out / (case + ".zip")).write_bytes(payload)
                    rec["package"] = {
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "files": files,
                    }
                elif detail["status"] == "succeeded":
                    raise ValueError("Succeeded without a case report")
                else:
                    for node in reversed(detail["nodes"]):
                        node_output = node.get("output") or {}
                        if node_output.get("type") == "case_report":
                            rec["result"] = observation(case, node_output.get("content") or {})
                            break
                    rec["quality_issues"] = [
                        {k: issue.get(k) for k in ("code", "message", "hard")}
                        for issue in (detail.get("quality_verdict") or {}).get("issues", [])
                        if isinstance(issue, dict)
                    ]
                rec.pop("error", None)
                rec["complete"] = True
                print(
                    f"{case}: {rec['status']}, revision={rec['revision']}, nodes={len(rec['nodes'])}, gaps={len(rec.get('result', {}).get('gaps', []))}",
                    flush=True,
                )
            except (
                httpx.HTTPError,
                OSError,
                KeyError,
                ValueError,
                TypeError,
                TimeoutError,
                zipfile.BadZipFile,
            ) as exc:
                # Never print credentials, requests, source payloads or private exception bodies.
                rec["error"] = {
                    "type": type(exc).__name__,
                    "status": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
                }
                print(f"{case}: observation error {rec['error']}", flush=True)
            write_report(out, state)
        state["agent_activity"] = api(
            client, "GET", "/v2/agents/activity/summary", params={"project_id": pid}
        ).json()
        state["budget_after"] = budget(client)
        state.setdefault("finished_at", now())
        accounted = record_cost_scope(state)
        write_report(out, state)
        if not accounted:
            raise ValueError(
                "Local task accounting could not confirm a free batch with preserved lifetime budget"
            )
    print(f"Private report: {report_path}", flush=True)
    if any(not r.get("complete") for r in state["tasks"].values()):
        return 1
    return 0 if all(r["status"] == "succeeded" for r in state["tasks"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
