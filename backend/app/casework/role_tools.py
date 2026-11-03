"""Execute the tools granted to the frozen role, both on a live node and during candidate replay."""

import time
import uuid

from ..platform.contracts import ToolCallRecord
from ..platform.db import now
from ..platform.domain_tool import FrozenSourceReader, dispatch, operation_catalog
from ..platform.tool_registry import digest, validate_frozen
from ..platform.tool_registry import permissions as granted_permissions
from . import role_analysis, role_evidence
from .roles import PDF


class CaseReader(FrozenSourceReader):
    def __init__(self, inputs, checkpoint, records):
        super().__init__(inputs, checkpoint)
        self.records = records

    def __call__(self, source_id):
        source = super().__call__(source_id)
        source["prepared_records"] = [
            r
            for r in self.records
            if r["source_id"] == source_id and r["source_version"] == source["source_version"]
        ]
        return source


def _invoke(operation, arguments, reader, agent, grants, trace):
    from ..platform.runtime import TaskFailure

    pinned = next((t for t in agent["runtime_bindings"]["tools"] if t["id"] == operation.id), None)
    if not pinned or f"tool:{operation.id}@{pinned['version']}" not in grants:
        raise TaskFailure("tool_denied", "岗位或复合边界未授权此工具")
    validate_frozen(agent)
    call = None
    started = time.monotonic()
    if trace:
        execution = trace.execution
        call = ToolCallRecord(
            id=uuid.uuid4().hex,
            task_id=execution.task["id"],
            node_id=execution.active_node_id,
            instance_id=trace.instance_id,
            revision=trace.revision,
            kind="tool",
            capability_id=operation.id,
            version=str(pinned["version"]),
            status="running",
            started_at=now(),
            argument_names=sorted(arguments),
            arguments_checksum=digest(arguments),
        ).model_dump()
        trace.save(call, "tool.started")
    data, error = None, None
    try:
        try:
            data = dispatch(operation.id, arguments, reader)
        except ValueError:
            error = "unsupported_tool_input"
        reader.checkpoint()
        validate_frozen(agent)
    except BaseException:
        # Preserve the failure without publishing partial data. Lease loss is handled at takeover.
        data, error = None, "tool_interrupted"
        raise
    finally:
        if call:
            # A lost lease or withdrawn source must not publish late observations.
            call.update(
                status="completed" if data is not None and error is None else "failed",
                duration_ms=round((time.monotonic() - started) * 1000),
                source_refs=sorted(reader.source_refs),
                result_checksum=digest(data) if data is not None else None,
                error_code=error,
                summary={"operation": operation.name},
            )
            trace.save(call, "tool." + call["status"], arguments if not error else None, data)
    return {
        "tool_id": operation.id,
        "tool_call_id": call["id"] if call else None,
        "result_checksum": digest(data) if data is not None and not error else None,
        "version": pinned["version"],
        "arguments": arguments,
        "source_refs": sorted(reader.source_refs),
        "status": "unavailable" if error else "completed",
        "result": data if not error else None,
        "error_code": error,
    }


def run_stage(
    execute,
    stage,
    profile,
    inputs,
    previous,
    read_bytes,
    feedback,
    revision,
    checkpoint=None,
    *,
    agent,
    permissions=None,
    trace=None,
    repair_plan=None,
):
    kwargs = {"repair_plan": repair_plan} if repair_plan else {}
    output = execute(stage, profile, inputs, previous, read_bytes, feedback, revision, checkpoint, **kwargs)
    role = agent.get("role_contract")
    if not role:
        return output
    from ..platform.runtime import TaskFailure

    if role["stage"] != stage or (stage == "analyze" and role["case_id"] != profile["case_id"]):
        raise TaskFailure("case_team_invalid", "岗位与执行阶段不符")
    validate_frozen(agent)
    code = profile["case_id"]
    content = output["content"]
    prepared = (
        content if stage == "prepare" else next(o["content"] for o in previous if o["type"] == "case_records")
    )
    candidate = (
        content
        if stage == "analyze"
        else next((o["content"] for o in previous if o["type"] == "case_candidate"), {})
    )
    expect_grants = set(role["grants"])
    requests = role_evidence.requests(stage, expect_grants, inputs, prepared["records"], candidate)
    if stage == "analyze":
        requests += role_analysis.requests(code, inputs, prepared["records"], candidate)
    findings = []
    requested = [(t, a) for t, a in requests if t in expect_grants]
    if len(requested) > 8:
        raise TaskFailure("tool_call_limit", "本阶段的工具计划超过冻结执行上限")
    for tool_id, args in requested:
        reader = CaseReader(inputs, checkpoint or (lambda: None), prepared["records"])
        item = _invoke(
            operation_catalog()[tool_id],
            args,
            reader,
            agent,
            granted_permissions(agent) if permissions is None else permissions,
            trace,
        )
        findings.append(item)
    return apply_tool_findings(stage, output, previous, findings, code)


def apply_tool_findings(stage, output, previous, findings, code):
    content = output["content"]
    prepared = (
        content if stage == "prepare" else next(o["content"] for o in previous if o["type"] == "case_records")
    )
    candidate = (
        content
        if stage == "analyze"
        else next((o["content"] for o in previous if o["type"] == "case_candidate"), {})
    )
    if stage == "review":
        checks = []
        for item in [*prepared.get("tool_findings", []), *candidate.get("tool_findings", []), *findings]:
            if item["status"] != "completed":
                checks.append(
                    {
                        "name": "registered_tool_unavailable",
                        "passed": False,
                        "hard": False,
                        "message": f"已选择的 {item['tool_id']} 未能完成来源核验，参见工具记录。",
                    }
                )
        for item in candidate.get("tool_findings", []):
            if item["status"] == "completed":
                checks.extend(role_analysis.compare(code, item["tool_id"], item["result"], candidate))
        for item in findings:
            if item["tool_id"] == PDF and item["status"] == "completed":
                checks.extend(
                    role_evidence.check_page(item["arguments"], item["result"], prepared["records"])
                )
        content["review"].extend(checks)
        content["gaps"] = list(
            dict.fromkeys([*content["gaps"], *(c["message"] for c in checks if not c["passed"])])
        )
        output["metrics"]["gap_count"] = len(content["gaps"])
        content["tool_findings"] = [
            *prepared.get("tool_findings", []),
            *candidate.get("tool_findings", []),
            *[dict(f, stage=stage) for f in findings],
        ]
    else:
        content["tool_findings"] = [dict(f, stage=stage) for f in findings]
    return output
