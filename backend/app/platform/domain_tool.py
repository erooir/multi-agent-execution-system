"""Read-only operations over explicitly frozen sources. Tool arguments never select code or paths."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .contracts import Contract, ToolResult

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DomainOperation:
    id: str
    name: str
    label: str
    schema: type[Contract]
    execute: Callable
    dependencies: tuple[str, ...] = field(default=(), kw_only=True)
    packages: tuple[str, ...] = field(default=(), kw_only=True)

    def spec(self, checksums=None, installed=None):
        from .tool_registry import digest

        implementation = Path(self.execute.__code__.co_filename).resolve()
        files = (Path(__file__), Path(__file__).with_name("domain_session.py"), implementation)
        checksums = {} if checksums is None else checksums
        installed = {} if installed is None else installed
        sources = {}
        for filename in (*files, *(ROOT / relative for relative in self.dependencies)):
            path = filename.resolve()
            key = path.relative_to(ROOT).as_posix()
            if key not in checksums:
                checksums[key] = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
            sources[key] = checksums[key]
        packages = {}
        for distribution in self.packages:
            if distribution not in installed:
                try:
                    installed[distribution] = version(distribution)
                except PackageNotFoundError:
                    installed[distribution] = None
            packages[distribution] = installed[distribution]
        schema = self.schema.model_json_schema()
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "kind": "tool",
            "version": 1,
            "input_schema": schema,
            "output_schema": ToolResult.model_json_schema(),
            "read_only": True,
            "local_preview": True,
            "checksum": digest({"id": self.id, "schema": schema, "files": sources, "packages": packages}),
        }


def operation_catalog():
    from .analysis_tools import TOOLS as analysis
    from .evidence_tools import TOOLS as evidence

    operations = (*evidence, *analysis)
    if len({op.id for op in operations}) != len(operations) or len({op.name for op in operations}) != len(
        operations
    ):
        raise ValueError("duplicate_domain_tool")
    return {op.id: op for op in operations}


class FrozenSourceReader:
    """Verify the task boundary each time, including when a tool reads a second source."""

    def __init__(self, inputs, checkpoint, *, public_only=False):
        self.inputs = {asset["id"]: asset for asset in inputs}
        self.checkpoint = checkpoint
        self.public_only = public_only
        self.source_refs = set()

    def _read(self, source_id):
        from .runtime import TaskFailure, _asset_bytes

        self.checkpoint()
        source = self.inputs.get(source_id)
        if not source or (self.public_only and source.get("classification") != "public"):
            raise TaskFailure("tool_source_denied", "该资料未获本次工具调用授权")
        if source.get("bytes", 0) > 10_000_000:
            raise ValueError("工具最多处理 10MB 的单份资料")
        data = _asset_bytes(source)
        if len(data) > 10_000_000:
            raise ValueError("工具最多处理 10MB 的单份资料")
        self.source_refs.add(source_id)
        return source, data

    def read_bytes(self, source_id):
        return self._read(source_id)[1]

    def __call__(self, source_id):
        source, data = self._read(source_id)
        safe = {
            key: source.get(key)
            for key in (
                "id",
                "source_id",
                "source_version",
                "modality",
                "media_type",
                "content",
                "name",
                "bytes",
                "checksum",
            )
        }
        if safe["content"] is None and str(source.get("media_type", "")).partition(";")[0] == "text/csv":
            encoding = "cp1252" if "windows-1252" in source["media_type"].lower() else "utf-8-sig"
            safe["content"] = data.decode(encoding)
        return safe


def dispatch(tool_id, arguments, read_source):
    """Execute a specific registered tool after the caller has checked its frozen grant."""
    operation = operation_catalog().get(tool_id)
    if operation is None:
        raise ValueError("unknown_domain_tool")
    validated = operation.schema.model_validate(arguments)
    result = operation.execute(read_source, validated)
    if (
        not isinstance(result, dict)
        or len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 80_000
    ):
        raise ValueError("tool_result_too_large")
    return result
