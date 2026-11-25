"""Audit the fixed preparation tool separately from the edition's model calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from uuid import uuid4

from ..platform.contracts import ToolCallRecord
from ..platform.db import LeaseLost, now
from ..platform.tool_registry import digest
from ..platform.tool_trace import TaskToolTrace
from . import edition_checks

_active_trace: ContextVar[TaskToolTrace | None] = ContextVar("edition_source_trace", default=None)


@contextmanager
def bind_source_check(execution, agent, permissions, revision, instance_id):
    from ..platform.runtime import TaskFailure
    from .editions import EDITION

    if (
        execution.active_node_id != "prepare"
        or agent.get("edition_executor") != EDITION
        or agent.get("case_role") != "prepare"
        or "case_analysis" not in permissions
        or "case_analysis" not in agent.get("tool_ids", [])
    ):
        raise TaskFailure("tool_denied", "此节点未获本次公开原件复核的固定流程授权")
    # asyncio.to_thread propagates this context to the decoder without sharing it with other jobs.
    token = _active_trace.set(TaskToolTrace(execution, revision, instance_id))
    try:
        yield
    finally:
        _active_trace.reset(token)


def verify_sources(code, sources, products):
    from ..platform.runtime import TaskFailure

    trace = _active_trace.get()
    if trace is None:
        # Offline import validation also uses this pure check, outside any task or lease.
        return edition_checks.verify_sources(code, sources, products)
    execution = trace.execution
    execution.check()
    profile = execution.task["snapshot"]["case_profile"]
    if code != profile["case_id"] or any(
        asset.get("classification") != "public" or asset.get("project_id") != execution.task["project_id"]
        for asset in execution.inputs
    ):
        raise TaskFailure("tool_source_denied", "公开原件复核只接受本用例冻结的项目公开输入")
    refs = [asset["id"] for asset in execution.inputs]
    arguments = {"case_id": code, "input_refs": refs}
    summary = {
        "operation": "verify_public_originals",
        "label": "公开原件复核",
        "invocation_mode": "fixed_workflow",
    }
    call = ToolCallRecord(
        id=uuid4().hex,
        task_id=execution.task["id"],
        node_id=trace.node_id,
        instance_id=trace.instance_id,
        revision=trace.revision,
        kind="tool",
        capability_id="case_analysis",
        version=profile["signature"],
        status="running",
        started_at=now(),
        argument_names=sorted(arguments),
        arguments_checksum=digest(arguments),
        source_refs=refs,
        summary=summary,
    ).model_dump()
    trace.save(call, "tool.started")
    start = monotonic()
    try:
        checks = edition_checks.verify_sources(code, sources, products)
        execution.check()
    except LeaseLost:
        # Only the new lease owner can close an interrupted invocation.
        raise
    except Exception as exc:
        call.update(
            status="failed",
            error_code=getattr(exc, "code", "tool_interrupted"),
            duration_ms=int((monotonic() - start) * 1000),
        )
        trace.save(call, "tool.failed", arguments)
        raise
    result = {"checks": checks}
    call.update(
        status="completed", result_checksum=digest(result), duration_ms=int((monotonic() - start) * 1000)
    )
    call["summary"].update(
        check_count=len(checks), failed_checks=sum(not check["passed"] for check in checks)
    )
    trace.save(call, "tool.completed", arguments, result)
    return checks
