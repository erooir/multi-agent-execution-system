"""Task-scoped generated scripts: immutable code, container execution and durable audit."""

import base64
import uuid

from pydantic import Field

from .contracts import Contract
from .db import now
from .domain_tool import DomainOperation
from .script_sandbox import run_script, validate_code


class ScriptExecute(Contract):
    code: str = Field(
        min_length=1,
        max_length=20000,
        description="Python 3.12 standard-library code; read /inputs/manifest.json and write results to /outputs",
    )
    source_ids: list[str] = Field(
        default_factory=list, max_length=8, description="Only frozen authorized source IDs; no host paths"
    )
    purpose: str = Field(min_length=1, max_length=500)
    timeout_seconds: int = Field(default=30, ge=1, le=60)


def _excerpt(value, byte_limit):
    raw = value.encode("utf-8")
    return raw[:byte_limit].decode("utf-8", errors="ignore"), len(raw) > byte_limit


def compact_result(result, script_version_id, run_id, source_ids):
    """Keep tool context below the 80KB boundary; full output remains in the durable run."""
    compact = {k: v for k, v in result.items() if k not in {"outputs", "stdout", "stderr"}}
    for name in ("stdout", "stderr"):
        compact[name], truncated = _excerpt(str(result.get(name, "")), 4000)
        compact[name + "_truncated"] = truncated or bool(result.get(name + "_truncated"))
    compact["outputs"] = []
    remaining = 24000
    for index, row in enumerate(result["outputs"]):
        output = {k: v for k, v in row.items() if k != "data"}
        output["id"] = f"{run_id}:{index}"
        if row.get("preview") is not None:
            allowance = min(3000, remaining // (len(result["outputs"]) - index))
            output["preview"], truncated = _excerpt(row["preview"], allowance)
            remaining -= len(output["preview"].encode("utf-8"))
            output["preview_truncated"] = truncated or bool(row.get("preview_truncated"))
        compact["outputs"].append(output)
    compact.update(
        script_version_id=script_version_id,
        sandbox_run_id=run_id,
        source_refs=source_ids,
        quality_status="unreviewed",
        review_required="Script exit zero is execution success only; independently verify requirements, formulas and evidence before accepting outputs. Truncated previews do not prove full-file coverage; complete files remain downloadable.",
    )
    return compact


def execute(read_source, args):
    execution = getattr(read_source, "execution", None)
    if execution is None:
        raise ValueError("sandbox_task_context_required")
    checksum = validate_code(args.code)
    if len(set(args.source_ids)) != len(args.source_ids):
        raise ValueError("sandbox_duplicate_source")
    sources = []
    for source_id in args.source_ids:
        source = read_source(source_id)
        sources.append({**source, "data": read_source.read_bytes(source_id)})
    script_id, run_id = uuid.uuid4().hex, uuid.uuid4().hex
    with execution.repository.transaction() as tx:
        task = execution.check(tx)
        tx.put(
            "script_versions",
            {
                "id": script_id + ":1",
                "definition_id": script_id,
                "version": 1,
                "project_id": task["project_id"],
                "task_id": task["id"],
                "task_case_id": task.get("task_case_id"),
                "status": "task_scoped",
                "code": args.code,
                "checksum": checksum,
                "purpose": args.purpose,
                "language": "python",
                "source_refs": args.source_ids,
                "scope": "task",
                "revision": task.get("revision", 0),
            },
        )
        tx.put(
            "sandbox_runs",
            {
                "id": run_id,
                "project_id": task["project_id"],
                "task_id": task["id"],
                "script_version_id": script_id + ":1",
                "status": "running",
                "source_refs": args.source_ids,
                "node_id": execution.active_node_id,
                "revision": task.get("revision", 0),
                "started_at": now(),
            },
        )
        tx.event(
            task["id"],
            "script.started",
            {"sandbox_run_id": run_id, "script_version_id": script_id + ":1", "checksum": checksum},
        )
    try:
        result = run_script(
            args.code, sources, timeout_seconds=args.timeout_seconds, checkpoint=execution.check
        )
        durable = {
            **result,
            "outputs": [
                {
                    **{k: v for k, v in row.items() if k != "data"},
                    "base64": base64.b64encode(row["data"]).decode("ascii"),
                }
                for row in result["outputs"]
            ],
        }
        with execution.repository.transaction() as tx:
            execution.check(tx)
            record = tx.get("sandbox_runs", run_id)
            tx.put("sandbox_runs", {**record, **durable, "finished_at": now()})
            tx.event(
                execution.task["id"],
                "script." + result["status"],
                {
                    "sandbox_run_id": run_id,
                    "checksum": checksum,
                    "output_count": len(result["outputs"]),
                    "returncode": result["returncode"],
                },
            )
        return compact_result(result, script_id + ":1", run_id, args.source_ids)
    except BaseException as exc:
        # Lease loss must not let a stale worker publish or rewrite task state.
        with execution.repository.transaction() as tx:
            tx.fenced(execution.task["id"], execution.worker_id, execution.token)
            record = tx.get("sandbox_runs", run_id)
            tx.put(
                "sandbox_runs",
                {**record, "status": "failed", "finished_at": now(), "error_code": str(exc)[:120]},
            )
            tx.event(
                execution.task["id"],
                "script.failed",
                {"sandbox_run_id": run_id, "error_type": type(exc).__name__},
            )
        raise


TOOLS = [
    DomainOperation(
        "script.execute",
        "script_execute",
        "隔离运行任务数据处理脚本",
        ScriptExecute,
        execute,
        dependencies=("backend/app/platform/script_sandbox.py", "backend/app/platform/sandbox_harness.py"),
        read_only=False,
        local_preview=False,
    )
]
