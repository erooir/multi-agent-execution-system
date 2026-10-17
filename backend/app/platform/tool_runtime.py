"""Task-private tools, a real MCP session, and read-only Agno skill loading."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from typing import Literal

from pydantic import ValidationError

from .contracts import (
    Contract,
    EvidenceLocate,
    SkillRead,
    SkillReferenceRead,
    SourceRead,
    SourceSearch,
    TableAggregate,
    TableOperation,
    ToolCallRecord,
    ToolResult,
)
from .db import LeaseLost, now
from .quality import text_of
from .tool_registry import ROOT, SKILL_ROOT, digest, validate_frozen


class LocalMCP:
    """One owning coroutine keeps MCP cancel scopes and subprocess cleanup together."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.task = None
        self.ready = None
        self.active = None

    async def _serve(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE"}
        }
        env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", AGNO_TELEMETRY="false")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "backend.app.platform.mcp_server"], cwd=str(ROOT), env=env
        )
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
                async with stdio_client(params, errlog=errors) as (reader, writer):  # noqa: SIM117 - one owning MCP task
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        discovered = await session.list_tools()
                        names = {tool.name for tool in discovered.tools}
                        if names != {"table_transform", "table_aggregate"}:
                            raise ValueError("mcp_catalog_changed")
                        self.ready.set_result(True)
                        while True:
                            name, arguments, future = await self.queue.get()
                            self.active = future
                            async with asyncio.timeout(12):
                                result = await session.call_tool(name, arguments)
                            if result.isError:
                                value = {"error_code": "mcp_tool_failed"}
                            else:
                                value = result.structuredContent
                                if value is None:
                                    value = json.loads(
                                        "".join(
                                            block.text for block in result.content if block.type == "text"
                                        )
                                    )
                            if not future.done():
                                future.set_result(value)
                            self.active = None
        except BaseException as exc:
            error = RuntimeError("mcp_unavailable")
            if self.ready and not self.ready.done():
                self.ready.set_exception(error)
            if self.active and not self.active.done():
                self.active.set_exception(error)
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(error)
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def call(self, name, arguments):
        if self.task is None:
            self.ready = asyncio.get_running_loop().create_future()
            self.task = asyncio.create_task(self._serve())
        await asyncio.wait_for(asyncio.shield(self.ready), timeout=20)
        if self.task.done():
            raise RuntimeError("mcp_unavailable")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments, future))
        return await asyncio.wait_for(asyncio.shield(future), timeout=15)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class ToolSession:
    def __init__(self, execution, agent, revision, permissions):
        self.execution, self.agent, self.revision = execution, agent, revision
        validate_frozen(agent)
        self.tools = {
            item["name"]: item
            for item in agent["runtime_bindings"]["tools"]
            if f"tool:{item['id']}@{item['version']}" in permissions
        }
        self.skills = {
            item["id"]: item
            for item in agent["runtime_bindings"]["skills"]
            if f"skill:{item['id']}@{item['version']}" in permissions
        }
        self.sources_read = set()
        self.mcp = LocalMCP()
        self.derived = []
        self.instance_id = next(
            (
                node["instance_id"]
                for node in execution.repository.list("nodes", task_id=execution.task["id"])
                if node.get("node_id") == execution.active_node_id and node.get("attempt") == revision + 1
            ),
            None,
        )

    def source(self, source_id):
        from .runtime import TaskFailure, _asset_bytes

        self.execution.check()
        source = next((a for a in self.execution.inputs if a["id"] == source_id), None)
        if not source or source["classification"] != "public":
            raise TaskFailure("tool_source_denied", "工具只能读取本任务冻结的公开资料")
        _asset_bytes(source)
        self.sources_read.add(source_id)
        return source

    async def invoke(self, name, schema, arguments, handler, *, skill_id=None):
        from .runtime import TaskFailure

        execution = self.execution
        execution.check()
        validate_frozen(self.agent)
        spec = self.skills[skill_id] if skill_id else self.tools[name]
        current = execution.current()
        count = sum(
            1
            for call in current.get("tool_calls", [])
            if call["node_id"] == execution.active_node_id and call["revision"] == self.revision
        )
        if count >= 12:
            raise TaskFailure("tool_call_limit", "节点本轮工具调用达到12次上限")
        record = ToolCallRecord(
            id=uuid.uuid4().hex,
            task_id=execution.task["id"],
            instance_id=self.instance_id,
            node_id=execution.active_node_id,
            revision=self.revision,
            kind="skill" if skill_id else spec["kind"],
            capability_id=spec["id"],
            version=str(spec["version"]),
            status="running",
            started_at=now(),
            argument_names=sorted(arguments),
            arguments_checksum=digest(arguments),
        ).model_dump()
        self.save(record, "tool.started")
        started = time.monotonic()
        data = None
        try:
            validated = schema.model_validate(arguments).model_dump()
            pending_result = handler(**validated)
            data = await pending_result if hasattr(pending_result, "__await__") else pending_result
            execution.check()
            if not isinstance(data, dict) or len(json.dumps(data, ensure_ascii=False).encode()) > 80_000:
                raise ValueError("tool_result_too_large")
            if data.get("error_code"):
                raise ValueError(data["error_code"])
            record.update(
                status="completed", result_checksum=digest(data), source_refs=sorted(self.sources_read)
            )
            if spec.get("kind") == "mcp":
                evidence = {
                    "kind": "derived_result",
                    "tool_call_id": record["id"],
                    "source_id": arguments["source_id"],
                    "result_checksum": record["result_checksum"],
                }
                self.derived.append(evidence)
                data = {**data, "derived_evidence": evidence}
            record["summary"] = {
                "operation": name,
                **{
                    key: data[key]
                    for key in ("total_rows", "matched_rows", "metric", "truncated")
                    if key in data
                },
            }
            return ToolResult(
                data=data,
                evidence=[{"source_id": ref, "classification": "public"} for ref in record["source_refs"]],
            ).model_dump()
        except (LeaseLost, asyncio.CancelledError):
            raise
        except TaskFailure as exc:
            record.update(status="failed", error_code=exc.code)
            execution.check()  # Cancellation, deadline and revoked permissions stop the next model request too.
            return ToolResult(data={"error_code": exc.code, "message": exc.message}).model_dump()
        except (ValueError, TypeError, KeyError, ValidationError, RuntimeError, TimeoutError) as exc:
            code = (
                "mcp_unavailable" if isinstance(exc, (RuntimeError, TimeoutError)) else "tool_input_invalid"
            )
            record.update(status="failed", error_code=code)
            return ToolResult(
                data={"error_code": code, "message": "工具未完成，请检查参数、字段和资料范围"}
            ).model_dump()
        finally:
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            if record["status"] == "running":
                record.update(status="failed", error_code="tool_interrupted")
            self.save(
                record,
                "tool." + record["status"],
                arguments=arguments,
                result=data if isinstance(data, dict) else None,
            )

    def save(self, record, event_type, arguments=None, result=None):
        execution = self.execution
        with execution.repository.transaction() as tx:
            task = tx.fenced(execution.task["id"], execution.worker_id, execution.token)
            calls = [item for item in task.get("tool_calls", []) if item["id"] != record["id"]]
            task["tool_calls"] = [*calls, record]
            if arguments is not None:
                checkpoints = task.get("tool_checkpoints", {})
                checkpoints[record["id"]] = {
                    "arguments": arguments,
                    "result": result,
                    "sources": record["source_refs"],
                }
                task["tool_checkpoints"] = checkpoints
            execution.task = tx.put("tasks", task)
            tx.event(
                task["id"], event_type, record, node_id=record["node_id"], instance_id=record["instance_id"]
            )

    def bindings(self):
        async def list_sources() -> dict:
            """查看本任务已授权资料的ID、名称、模态和版本，不读取正文。"""
            return await self.invoke(
                "list_sources",
                Contract,
                {},
                lambda: {
                    "sources": [
                        {
                            key: item.get(key)
                            for key in ("id", "name", "modality", "media_type", "source_version")
                        }
                        for item in self.execution.inputs
                    ]
                },
            )

        async def read_source(source_id: str, start: int = 0, length: int = 2000) -> dict:
            """读取授权资料片段。start为字符偏移，最多12000字符；可分页阅读。"""

            def read(**params):
                source = self.source(params["source_id"])
                text = text_of(source)
                start, length = params["start"], params["length"]
                return {
                    "source_id": source_id,
                    "source_version": source["source_version"],
                    "start": start,
                    "end": min(len(text), start + length),
                    "source_text": text[start : start + length],
                    "total_chars": len(text),
                }

            return await self.invoke(
                "read_source", SourceRead, {"source_id": source_id, "start": start, "length": length}, read
            )

        async def search_sources(query: str, limit: int = 10) -> dict:
            """在冻结授权资料中精确查找关键词，返回原文片段和字符位置。"""

            def search(**params):
                hits = []
                for item in self.execution.inputs:
                    text = text_of(self.source(item["id"]))
                    offset = text.find(params["query"])
                    if offset >= 0:
                        start, end = max(0, offset - 100), min(len(text), offset + len(params["query"]) + 300)
                        hits.append(
                            {
                                "source_id": item["id"],
                                "source_version": item["source_version"],
                                "start": start,
                                "end": end,
                                "source_text": text[start:end],
                            }
                        )
                    if len(hits) >= params["limit"]:
                        break
                return {"hits": hits}

            return await self.invoke("search_sources", SourceSearch, {"query": query, "limit": limit}, search)

        async def locate_evidence(source_id: str, quote: str) -> dict:
            """核实逐字引文并返回实际字符位置；不存在时明确失败。"""

            def locate(**params):
                source = self.source(params["source_id"])
                offset = text_of(source).find(params["quote"])
                if offset < 0:
                    raise ValueError("quote_not_found")
                return {
                    "source_id": source_id,
                    "source_version": source["source_version"],
                    "quote": quote,
                    "start": offset,
                    "end": offset + len(quote),
                }

            return await self.invoke(
                "locate_evidence", EvidenceLocate, {"source_id": source_id, "quote": quote}, locate
            )

        async def table_transform(
            source_id: str,
            operation: Literal["filter", "sort", "deduplicate"] = "filter",
            where: dict | None = None,
            sort_by: str | None = None,
            descending: bool = False,
            sort_type: Literal["auto", "numeric", "text"] = "auto",
            limit: int = 100,
        ) -> dict:
            """真实MCP处理JSON/CSV：where先筛选，sort_by再按descending排序。sort_type=auto会识别数字字符串，numeric强制数值排序，text按文本。每次独立读取source_id，需重传where。"""
            args = {
                "source_id": source_id,
                "operation": operation,
                "where": where,
                "sort_by": sort_by,
                "descending": descending,
                "sort_type": sort_type,
                "limit": limit,
            }
            return await self.invoke(
                "table_transform", TableOperation, args, lambda **p: self.table_call("table_transform", p)
            )

        async def table_aggregate(
            source_id: str,
            metric: Literal["count", "sum", "average", "min", "max"] = "count",
            value_field: str | None = None,
            group_by: str | None = None,
            where: dict | None = None,
        ) -> dict:
            """通过真实MCP对完整JSON/CSV计数count、求和sum、平均average、min/max，可分组和筛选。"""
            args = {
                "source_id": source_id,
                "metric": metric,
                "value_field": value_field,
                "group_by": group_by,
                "where": where,
            }
            return await self.invoke(
                "table_aggregate", TableAggregate, args, lambda **p: self.table_call("table_aggregate", p)
            )

        async def get_skill_instructions(skill_id: str) -> dict:
            """按需读取已绑定技能的真实SKILL.md说明，不执行脚本。"""
            if skill_id not in self.skills:
                return {"error_code": "skill_not_authorized"}
            return await self.invoke(
                "get_skill_instructions",
                SkillRead,
                {"skill_id": skill_id},
                lambda **_: {
                    "skill_id": skill_id,
                    "instructions": (SKILL_ROOT / skill_id / "SKILL.md").read_text(encoding="utf-8"),
                    "references": self.skills[skill_id]["references"],
                },
                skill_id=skill_id,
            )

        async def get_skill_reference(skill_id: str, reference: str) -> dict:
            """读取已授权技能references中的指定文件，reference须来自技能目录。"""
            if skill_id not in self.skills or reference not in self.skills[skill_id]["references"]:
                return {"error_code": "skill_reference_not_authorized"}
            return await self.invoke(
                "get_skill_reference",
                SkillReferenceRead,
                {"skill_id": skill_id, "reference": reference},
                lambda **_: {
                    "skill_id": skill_id,
                    "reference": reference,
                    "text": (SKILL_ROOT / skill_id / "references" / reference).read_text(encoding="utf-8"),
                },
                skill_id=skill_id,
            )

        functions = {
            function.__name__: function
            for function in (
                list_sources,
                read_source,
                search_sources,
                locate_evidence,
                table_transform,
                table_aggregate,
            )
        }
        bindings = {name: function for name, function in functions.items() if name in self.tools}
        if self.skills:
            bindings.update(
                get_skill_instructions=get_skill_instructions, get_skill_reference=get_skill_reference
            )
        return bindings

    async def table_call(self, name, params):
        from .runtime import structured_transform

        params = dict(params)
        source = self.source(params.pop("source_id"))
        records = structured_transform([source])["content"]["records"]
        return await self.mcp.call(name, {"records": records, **params})

    async def close(self):
        await self.mcp.close()
