"""Read published methodology and source records through the frozen node's actual tools."""

import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from ..platform.tool_registry import digest, validate_frozen
from ..platform.tool_trace import NodeDomainToolSession

ANALYSIS_SKILLS = {
    "pdf-chart-extraction": ("B01", "B05", "B07"),
    "statistics-visualization": ("B02", "B06", "G03", "T05", "T09"),
    "audio-analysis": ("B04", "T07"),
    "document-comparison": ("B03", "B08", "B09", "B10", "T01", "T02", "T06", "T08", "T10"),
    "data-cleaning-joining": ("G01", "G07", "T04"),
    "geospatial-analysis": ("G02", "G04", "G05", "G06", "G09", "G10"),
    "document-reading": ("G08", "T03"),
}


def skill_for(stage, code):
    if stage == "review":
        return "report-delivery"
    return next((skill for skill, cases in ANALYSIS_SKILLS.items() if code in cases), "document-reading")


@lru_cache(maxsize=1)
def guidance_signature():
    return sha256(Path(__file__).read_text(encoding="utf8").encode()).hexdigest()


def role_bindings(stage, code, catalogue):
    from ..platform.tool_registry import freeze_bindings

    name = skill_for(stage, code)
    skill = next(item for item in catalogue["skills"] if item["id"] == name and not item["external"])
    required = {*skill["required_tools"], "document_extract" if stage == "analyze" else "locate_evidence"}
    refs = {
        "tool_refs": [
            {"tool_id": item["id"], "version": item["version"]}
            for item in catalogue["tools"]
            if item["name"] in required and not item.get("external")
        ],
        "skill_refs": [{"skill_id": name, "version": skill["version"]}],
    }
    return {**refs, "runtime_bindings": freeze_bindings(refs, catalogue=catalogue)}


class EditionToolSession(NodeDomainToolSession):
    async def invoke(self, name, schema, arguments, handler, *, skill_id=None, project=None):
        # A worker can take over between a completed local read and the paid model dispatch.
        # Reuse only authenticated, completed reads from another instance of this same frozen node.
        task = self.execution.task
        spec = self.skills[skill_id] if skill_id else self.tools[name]
        for call in task.get("tool_calls", []):
            if not (
                call["node_id"] == self.execution.active_node_id
                and call["revision"] == self.revision
                and call["instance_id"] != self.instance_id
                and call["status"] == "completed"
                and call["capability_id"] == spec["id"]
                and call["version"] == str(spec["version"])
                and call["arguments_checksum"] == digest(arguments)
                and call.get("summary", {}).get("invocation_mode") == "fixed_workflow"
            ):
                continue
            checkpoint = task.get("tool_checkpoints", {}).get(call["id"], {})
            result = checkpoint.get("result")
            if (
                result is not None
                and checkpoint.get("arguments") == arguments
                and digest(result) == call["result_checksum"]
            ):
                self.execution.check()
                validate_frozen(self.agent, self.execution.repository)
                return (
                    json.dumps(result, ensure_ascii=False) if skill_id else {"data": result, "evidence": []}
                )
        return await super().invoke(name, schema, arguments, handler, skill_id=skill_id, project=project)

    def save(self, record, event_type, arguments=None, result=None):
        record["summary"]["invocation_mode"] = "fixed_workflow"
        if record["capability_id"] in {"document.extract", "data.locate_evidence"}:
            record["summary"]["input_kind"] = "edition_evidence_index"
        # The same source may be read by consecutive citation checks.
        if (
            arguments
            and record["capability_id"] == "data.locate_evidence"
            and record["status"] == "completed"
        ):
            record["source_refs"] = [arguments["source_id"]]
        super().save(record, event_type, arguments, result)


@dataclass
class StageGuidance:
    instructions: str
    data: dict

    def apply(self, system, prompt):
        from ..platform.runtime import canonical

        system += (
            "\n以下为本岗位已发布的方法说明。按当前资料、节点职责和输出结构采用适用部分，"
            "区分公开原件、已核对的资料索引和分析推断；仅将已实际执行的检验写为已执行。\n" + self.instructions
        )
        return system, canonical({**json.loads(prompt), **self.data})


def tool_data(response):
    from ..platform.runtime import TaskFailure

    data = response["data"]
    if data.get("error_code"):
        raise TaskFailure(data["error_code"], data.get("message", "用例的证据工具未完成"))
    return data


async def read_indexes(bindings, inputs):
    from ..platform.runtime import TaskFailure

    rows = []
    for asset in inputs:
        offset = 0
        while True:
            page = tool_data(
                await bindings["document_extract"](
                    source_id=asset["id"],
                    start=offset,
                    limit=30,
                    max_chars=2000,
                )
            )
            if page["gaps"]:
                raise TaskFailure("case_record_contract", "未能完整读取冻结资料的证据索引")
            for record in page["records"]:
                fields = record["fields"]
                rows.append(
                    {
                        "id": fields["id"],
                        "文件": asset["original_file"]["filename"],
                        "原件位置": fields["locator"],
                        "依据": fields["text"],
                    }
                )
            if not page["truncated"]:
                break
            offset = page["next_start"]
    return rows


async def locate_citations(bindings, inputs, previous):
    findings = next(item["content"]["findings"] for item in previous if item["type"] == "case_candidate")
    by_id = {row["id"]: (asset["id"], row) for asset in inputs for row in asset["content"]["records"]}
    # Check the lead citation for each finding independently. A shared citation needs one lookup.
    ids = dict.fromkeys(f["evidence_ids"][0] for f in findings if f.get("evidence_ids"))
    located = []
    for eid in ids:
        if eid not in by_id:
            located.append({"证据编号": eid, "结果": "未在冻结证据索引中找到"})
            continue
        source_id, record = by_id[eid]
        # Locate the record, not bare text which may repeat at a different location.
        literal = json.dumps(record, ensure_ascii=False)
        # Very long records use an exact bounded span containing the evidence ID.
        identifier = '"id": ' + json.dumps(eid, ensure_ascii=False)
        start = max(0, literal.index(identifier) + len(identifier) - 4000)
        literal = literal[start : start + 4000]
        span = tool_data(await bindings["locate_evidence"](source_id=source_id, quote=literal))
        located.append(
            {
                "证据编号": eid,
                "资料版本": span["source_version"],
                "来源": source_id,
                "原件位置": record["locator"],
                "索引字符区间": [span["start"], span["end"]],
                "索引记录摘录": span["quote"],
            }
        )
    return located


async def stage_guidance(execution, agent, permissions, revision, instance_id, previous):
    from ..platform.runtime import TaskFailure

    stage = execution.active_node_id
    skill_id = skill_for(stage, execution.request["case_id"])
    operation = "document.extract" if stage == "analyze" else "data.locate_evidence"
    # This fixed stage may open only its selected read-only function and the Skill reader.
    granted = {item for item in permissions if item.startswith((f"skill:{skill_id}@", f"tool:{operation}@"))}
    session = EditionToolSession(execution, agent, revision, granted, instance_id)
    try:
        if skill_id not in session.skills or operation not in {item["id"] for item in session.tools.values()}:
            raise TaskFailure("tool_denied", "岗位未获本阶段的方法或证据读取授权")
        await session.initialize()
        calls = session.bindings()
        response = await calls["get_skill_instructions"](skill_name=skill_id)
        if isinstance(response, dict):
            tool_data(response)
            raise TaskFailure("skill_unavailable", "岗位方法内容无法读取")
        instructions = json.loads(response)["instructions"]
        if stage == "analyze":
            data = {"证据": await read_indexes(calls, execution.inputs)}
        else:
            data = {
                "各项发现首条引用的冻结索引抽核": await locate_citations(calls, execution.inputs, previous)
            }
        return StageGuidance(instructions, data)
    finally:
        await session.close()
