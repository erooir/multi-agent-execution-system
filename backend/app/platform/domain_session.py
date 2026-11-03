"""Expose explicitly bound domain operations in the existing budgeted Agno tool loop."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

from .domain_tool import FrozenSourceReader, dispatch, operation_catalog
from .tool_runtime import ToolSession


@dataclass
class _Invocation:
    reader: FrozenSourceReader
    record: dict | None = None


# Agno can execute two tools concurrently. Attribute evidence to the actual call
# even when they share a task and consult different assets.
_active: ContextVar[_Invocation | None] = ContextVar("active_domain_tool", default=None)


class DomainToolSession(ToolSession):
    def save(self, record, event_type, arguments=None, result=None):
        invocation = _active.get()
        if invocation is not None:
            record["source_refs"] = sorted(invocation.reader.source_refs)
            invocation.record = record
        super().save(record, event_type, arguments=arguments, result=result)

    def on_domain_result(self, operation, arguments, result, record, reader):
        pass

    def domain_bindings(self):
        return {
            operation.name: self._binding(operation)
            for operation in operation_catalog().values()
            if operation.name in self.tools
        }

    def bindings(self):
        return {**super().bindings(), **self.domain_bindings()}

    def _binding(self, operation):
        async def bound(**arguments):
            reader = FrozenSourceReader(self.execution.inputs, self.execution.check, public_only=True)
            invocation = _Invocation(reader)
            token = _active.set(invocation)
            try:

                async def run(**params):
                    return await asyncio.to_thread(dispatch, operation.id, params, reader)

                # Reuse the legacy session's fenced transaction, input validation,
                # 12-call cap, size limit, and before/after authorization checks.
                result = await self.invoke(operation.name, operation.schema, arguments, run)
                record = invocation.record
                self.sources_read.update(reader.source_refs)
                result["evidence"] = [
                    {"source_id": ref, "classification": "public"} for ref in sorted(reader.source_refs)
                ]
                if (
                    record
                    and record["status"] == "completed"
                    and arguments["source_id"] in reader.source_refs
                ):
                    citation = {
                        "kind": "derived_result",
                        "tool_call_id": record["id"],
                        "source_id": arguments["source_id"],
                        "result_checksum": record["result_checksum"],
                    }
                    self.derived.append(citation)
                    result["data"]["derived_evidence"] = citation
                self.on_domain_result(operation, arguments, result, record, reader)
                return result
            finally:
                _active.reset(token)

        bound.__name__ = operation.name
        bound.__doc__ = operation.label
        bound.__hangzhi_tool_schema__ = operation.schema.model_json_schema()
        return bound
