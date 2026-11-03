"""Govern native Agno Functions, Toolkits, MCPTools and Skills per task."""

from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import time
import uuid

from agno.tools.function import Function
from agno.tools.function import ToolResult as AgnoToolResult
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate as validate_json
from pydantic import ValidationError

from .contracts import ToolCallRecord, ToolResult
from .db import LeaseLost, now
from .mcp_runtime import connect_mcp, create_builtin_mcp, create_mcp_tools
from .native_capabilities import create_native_toolkit, load_native_skills
from .quality import text_of
from .tool_registry import digest, validate_frozen


class ToolSession:
    def __init__(self, execution, agent, revision, permissions, *, instance_id=None):
        self.execution, self.agent, self.revision = execution, agent, revision
        validate_frozen(agent, execution.repository)
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
        self.derived = []
        self.external = []
        self.native_toolkits = []
        self.native_functions = {}
        self.standalone_functions = {}
        self.skill_functions = {}
        self.skills_native = None
        self.temporary = None
        self.closed = False
        self.instance_id = instance_id or next(
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

    @staticmethod
    def _normalize_result(value):
        if isinstance(value, AgnoToolResult):
            if value.images or value.audios or value.videos or value.files:
                raise ValueError("mcp_media_result_unsupported")
            structured = (value.metadata or {}).get("structured_content")
            value = structured if structured is not None else value.content
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {"text": value}
        if isinstance(value, dict):
            return value
        return {"value": value}

    async def invoke(self, name, schema, arguments, handler, *, skill_id=None, project=None):
        from .runtime import TaskFailure

        execution = self.execution
        execution.check()
        validate_frozen(self.agent, execution.repository)
        spec = self.skills[skill_id] if skill_id else self.tools[name]
        current = execution.current()
        count = sum(
            1
            for call in current.get("tool_calls", [])
            if call["node_id"] == execution.active_node_id and call["revision"] == self.revision
        )
        if count >= 12:
            raise TaskFailure("tool_call_limit", "节点本轮工具调用达到12次上限")
        external = bool(spec.get("external")) and not skill_id
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
            external=external,
            transport=spec.get("transport"),
            server_id=spec.get("server_id"),
        ).model_dump()
        self.save(record, "tool.started")
        started = time.monotonic()
        sources_before = set(self.sources_read)
        data = None
        checkpoint_result = None
        raw_result = None
        try:
            if self.closed:
                raise RuntimeError("mcp_unavailable")
            if hasattr(schema, "model_validate"):
                validated = schema.model_validate(arguments).model_dump()
            else:
                validate_json(arguments, schema)
                validated = arguments
            pending_result = handler(**validated)
            raw_result = await pending_result if inspect.isawaitable(pending_result) else pending_result
            data = self._normalize_result(raw_result)
            checkpoint_result = data
            execution.check()
            record.update(
                status="completed",
                result_checksum=digest(data),
                source_refs=sorted(self.sources_read - sources_before),
            )
            if project is not None:
                data = self._normalize_result(project(data, record))
                if data.get("error_code"):
                    raise ValueError(data["error_code"])
            if len(json.dumps(data, ensure_ascii=False).encode()) > 80_000:
                raise ValueError("tool_result_too_large")
            if data.get("error_code"):
                raise ValueError(data["error_code"])
            if spec.get("kind") == "mcp" and spec.get("adapter") == "builtin":
                evidence = {
                    "kind": "derived_result",
                    "tool_call_id": record["id"],
                    "source_id": arguments["source_id"],
                    "result_checksum": record["result_checksum"],
                }
                self.derived.append(evidence)
                data = {**data, "derived_evidence": evidence}
            elif external:
                evidence = {
                    "kind": "external_result",
                    "tool_call_id": record["id"],
                    "capability_id": spec["id"],
                    "version": str(spec["version"]),
                    "result_checksum": digest(data),
                }
                self.external.append(evidence)
                data = {**data, "external_evidence": evidence}
            record["summary"] = {
                "operation": name,
                **{
                    key: data[key]
                    for key in ("total_rows", "matched_rows", "metric", "truncated")
                    if key in data
                },
                **({"external_result_unverified": True} if external else {}),
                **({"full_result_in_checkpoint": True} if project is not None else {}),
            }
            if skill_id:
                return raw_result
            return ToolResult(
                data=data,
                evidence=[{"source_id": ref, "classification": "public"} for ref in record["source_refs"]],
            ).model_dump()
        except (LeaseLost, asyncio.CancelledError):
            raise
        except TaskFailure as exc:
            record.update(status="failed", error_code=exc.code)
            execution.check()
            return ToolResult(data={"error_code": exc.code, "message": exc.message}).model_dump()
        except (
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            SchemaValidationError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            safe_codes = {
                "capability_version_changed",
                "capability_revoked",
                "external_target_not_allowed",
                "external_address_not_allowed",
                "external_tool_http_error",
                "external_tool_invalid_response",
                "secret_unavailable",
                "mcp_tool_failed",
                "mcp_unavailable",
                "mcp_media_result_unsupported",
                "tool_result_too_large",
                "case_stage_already_executed",
                "case_previous_contract",
                "case_output_contract",
                "case_output_sources",
                "case_record_contract",
                "case_product_contract",
                "case_review_contract",
            }
            code = (
                str(exc)
                if str(exc) in safe_codes
                else "mcp_unavailable"
                if isinstance(exc, (RuntimeError, TimeoutError))
                else "tool_input_invalid"
            )
            record.update(status="failed", error_code=code)
            return ToolResult(
                data={"error_code": code, "message": "工具未完成，请检查参数、授权和外部服务状态"}
            ).model_dump()
        finally:
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            if record["status"] == "running":
                record.update(
                    status="uncertain" if external else "failed",
                    error_code="external_call_uncertain" if external else "tool_interrupted",
                )
            self.save(
                record,
                "tool." + record["status"],
                arguments=arguments,
                result=checkpoint_result if project is not None else data,
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

    def _wrap(self, function, spec, transform=None, *, standalone=False):
        original = function.entrypoint

        async def governed(*args, **kwargs):
            names = list((spec.get("input_schema") or {}).get("properties", {}))
            if len(args) > len(names):
                raise ValueError("tool_input_invalid")
            arguments = {**dict(zip(names, args, strict=False)), **kwargs}
            schema = spec.get("input_schema") or {"type": "object"}

            async def call(**validated):
                native_arguments = transform(validated) if transform else validated
                result = original(**native_arguments)
                return await result if inspect.isawaitable(result) else result

            return await self.invoke(spec["name"], schema, arguments, call, project=spec.get("_project"))

        function.entrypoint = governed
        function.skip_entrypoint_processing = True
        self.native_functions[function.name] = function
        if standalone:
            self.standalone_functions[function.name] = function
        return function

    def _builtin_handler(self, name):
        def list_sources():
            return {
                "sources": [
                    {key: item.get(key) for key in ("id", "name", "modality", "media_type", "source_version")}
                    for item in self.execution.inputs
                ]
            }

        def read_source(params):
            source = self.source(params["source_id"])
            text = text_of(source)
            start, length = params["start"], params["length"]
            return {
                "source_id": params["source_id"],
                "source_version": source["source_version"],
                "start": start,
                "end": min(len(text), start + length),
                "source_text": text[start : start + length],
                "total_chars": len(text),
            }

        def search_sources(params):
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

        def locate_evidence(params):
            source = self.source(params["source_id"])
            offset = text_of(source).find(params["quote"])
            if offset < 0:
                raise ValueError("quote_not_found")
            return {
                "source_id": params["source_id"],
                "source_version": source["source_version"],
                "quote": params["quote"],
                "start": offset,
                "end": offset + len(params["quote"]),
            }

        handlers = {
            "list_sources": lambda: list_sources(),
            "read_source": read_source,
            "search_sources": search_sources,
            "locate_evidence": locate_evidence,
        }
        return handlers[name]

    def _table_arguments(self, params):
        from .runtime import structured_transform

        params = dict(params)
        source = self.source(params.pop("source_id"))
        return {"records": structured_transform([source])["content"]["records"], **params}

    async def initialize(self):
        builtin_mcp = [
            spec for spec in self.tools.values() if spec.get("adapter") == "builtin" and spec["kind"] == "mcp"
        ]
        if builtin_mcp:
            toolkit = await connect_mcp(
                create_builtin_mcp(
                    [spec["remote_name"] if "remote_name" in spec else spec["name"] for spec in builtin_mcp]
                )
            )
            self.native_toolkits.append(toolkit)
            by_name = {spec["name"]: spec for spec in builtin_mcp}
            for name, function in toolkit.functions.items():
                spec = by_name[name]
                function.parameters = spec["input_schema"]
                function.description = spec["label"]
                self._wrap(function, spec, self._table_arguments)
        for spec in self.tools.values():
            if spec.get("adapter") == "builtin" and spec["kind"] == "tool":
                handler = self._builtin_handler(spec["name"])
                function = Function(
                    name=spec["name"],
                    description=spec["label"],
                    parameters=spec["input_schema"],
                    entrypoint=lambda _handler=handler, **kwargs: _handler(kwargs),
                )
                self._wrap(function, spec, standalone=True)
        groups = {}
        for spec in self.tools.values():
            if spec.get("adapter") in {"mcp", "agno_toolkit"}:
                groups.setdefault((spec["adapter"], spec["definition_id"]), []).append(spec)
        for (adapter, definition_id), specs in groups.items():
            if adapter == "mcp":
                first = specs[0]
                prefix = f"mcp_{definition_id.replace('-', '_')}"
                toolkit = await connect_mcp(
                    create_mcp_tools(
                        first["handler"],
                        include_tools=[spec["remote_name"] for spec in specs],
                        tool_name_prefix=prefix,
                    )
                )
                by_name = {f"{prefix}_{spec['remote_name']}": spec for spec in specs}
            else:
                first = specs[0]
                toolkit = create_native_toolkit(
                    first["handler"]["provider"],
                    [spec["name"].split(".")[-1] for spec in specs],
                )
                by_name = {spec["name"].split(".")[-1]: spec for spec in specs}
            self.native_toolkits.append(toolkit)
            for name, function in toolkit.functions.items():
                spec = by_name.get(name)
                if spec is None or function.parameters != spec["input_schema"]:
                    raise ValueError("capability_version_changed")
                self._wrap(function, spec)
        for spec in self.tools.values():
            if spec.get("adapter") != "http_json":
                continue
            from .declarative_tools import call_http

            function = Function(
                name=spec["name"],
                description=spec["description"],
                parameters=spec["input_schema"],
                entrypoint=lambda _spec=spec, **kwargs: call_http(_spec, kwargs),
                skip_entrypoint_processing=True,
            )
            self._wrap(function, spec, standalone=True)
        if self.skills:
            self.temporary = tempfile.TemporaryDirectory()
            self.skills_native = load_native_skills(self.skills.values(), self.temporary.name)
            if len(self.skills_native.get_all_skills()) != len(self.skills):
                raise ValueError("skill_name_conflict")
            by_native_name = {}
            for spec in self.skills.values():
                by_native_name[spec["id"]] = spec
                by_native_name[spec["name"]] = spec
            for function in self.skills_native.get_tools():
                original = function.entrypoint

                async def skill_call(*args, _original=original, _schema=function.parameters, **kwargs):
                    names = ["skill_name", "reference_path"]
                    arguments = {**dict(zip(names, args, strict=False)), **kwargs}
                    native_name = arguments.get("skill_name")
                    spec = by_native_name.get(native_name)
                    if spec is None:
                        return json.dumps({"error": "skill_not_authorized"})

                    async def call(**validated):
                        result = _original(**validated)
                        return await result if inspect.isawaitable(result) else result

                    return await self.invoke(
                        spec["id"],
                        _schema,
                        arguments,
                        call,
                        skill_id=spec["id"],
                    )

                function.entrypoint = skill_call
                function.skip_entrypoint_processing = True
                self.skill_functions[function.name] = function
        return self

    def agent_tools(self):
        return [*self.native_toolkits, *self.standalone_functions.values()]

    def bindings(self):
        return {
            **{name: function.entrypoint for name, function in self.native_functions.items()},
            **{name: function.entrypoint for name, function in self.skill_functions.items()},
        }

    async def close(self):
        self.closed = True
        for toolkit in self.native_toolkits:
            result = toolkit.close()
            if inspect.isawaitable(result):
                await result
        if self.temporary:
            self.temporary.cleanup()
