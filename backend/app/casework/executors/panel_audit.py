"""G02 key audits over the actual row groups in frozen annotation tables."""

from dataclasses import replace
from typing import Literal

from pydantic import create_model

from ...platform.analysis_tools import _records, _scalar
from ...platform.contracts import Contract
from ...platform.domain_tool import operation_catalog
from .base import CaseDomainToolSession
from .geospatial import PanelExecutor


def panel_key_checks(inputs, prepared):
    """Offer only row sets that audit_keys can select exactly, without a fabricated kind."""
    by_source = {}
    for row in prepared:
        by_source.setdefault(row["source_id"], []).append(row)
    checks = {}
    for asset in inputs:
        sid = asset["id"]
        if (
            asset["classification"] != "public"
            or asset["modality"] != "structured"
            or (asset.get("bytes") or 0) > 10_000_000
            or sid not in by_source
        ):
            continue
        source = {sid: {**asset, "prepared_records": by_source[sid]}}
        try:
            rows = [
                r["fields"] for r in _records(source.__getitem__, sid) if r["fields"].get("kind") != "policy"
            ]
        except ValueError:
            continue
        bindings = [r for r in rows if r.get("kind") == "raster_binding" and r.get("role") == "panel"]
        observations = [
            r for r in rows if r.get("kind") != "raster_binding" and ("observation" in r or "bbox" in r)
        ]
        for group, members in (("bindings", bindings), ("observations", observations)):
            if not members or any(not any(k in r for r in members) for k in ("image_id", "date")):
                continue
            try:
                for row in members:
                    for key in ("image_id", "date"):
                        _scalar(row.get(key))
            except ValueError:
                continue
            args = {"source_id": sid, "key_fields": ["image_id", "date"]}
            if group == "bindings":
                args["where"] = {"kind": "raster_binding", "role": "panel"}
            elif len(members) != len(rows):
                kind = members[0].get("kind")
                if (
                    isinstance(kind, str)
                    and 0 < len(kind) <= 100
                    and kind == kind.strip()
                    and all(r.get("kind") == kind for r in members)
                ):
                    args["where"] = {"kind": kind}
                else:
                    member_ids = {id(r) for r in members}
                    excluded = {
                        r["kind"] for r in rows if id(r) not in member_ids and isinstance(r.get("kind"), str)
                    }
                    if (
                        not excluded
                        or len(excluded) > 8
                        or any(not 0 < len(k) <= 100 or k != k.strip() for k in excluded)
                    ):
                        continue
                    args["exclude_kinds"] = sorted(excluded)
            selected = [
                r
                for r in rows
                if r.get("kind") not in args.get("exclude_kinds", ())
                and all(k in r and r[k] == v for k, v in args.get("where", {}).items())
            ]
            if selected == members:
                checks[f"{group}:{sid}"] = args
    return checks


class PanelAuditSession(CaseDomainToolSession):
    def __init__(self, context, binding, projector, description):
        super().__init__(context, binding, projector, description)
        self.key_checks = (
            panel_key_checks(context.execution.inputs, context.prepared["records"])
            if "audit_keys" in self.tools
            else {}
        )

    def domain_bindings(self):
        bindings = super().domain_bindings()
        audit = bindings.pop("audit_keys", None)
        if audit is None or not self.key_checks:
            return bindings
        # Successful calls go through the original governed operation with its real arguments.
        # Invalid selections use the same trace and review path with the selector's own schema.
        selector = create_model(
            "PanelKeyCheck", __base__=Contract, group=(Literal[tuple(self.key_checks)], ...)
        )
        invalid_selection = self._binding(replace(operation_catalog()["table.audit_keys"], schema=selector))

        async def select_group(**arguments):
            group = arguments.get("group")
            if set(arguments) == {"group"} and isinstance(group, str) and group in self.key_checks:
                return await audit(**self.key_checks[group])
            return await invalid_selection(**arguments)

        select_group.__name__ = "audit_keys"
        select_group.__doc__ = "选择冻结资料中实际存在的注记或图像绑定分组，独立核对图像编号和日期。"
        select_group.__hangzhi_tool_schema__ = selector.model_json_schema()
        bindings["audit_keys"] = select_group
        return bindings

    def _project_main(self, output, record):
        published = super()._project_main(output, record)
        published["panel_key_checks"] = self.key_checks
        return published


class PanelObservationExecutor(PanelExecutor):
    tool_session = PanelAuditSession

    def instructions(self, context):
        return super().instructions(context) + (
            "需要独立核查时，按主工具 panel_key_checks 的真实分组调用 audit_keys，只传 group。"
            "同一图像和日期允许多条注记；重复只描述分组，不单凭重复判定错误或去重。"
        )
