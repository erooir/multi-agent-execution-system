"""Project-owned MCP service. Pure table operations; no file, shell or model access."""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

server = FastMCP("hangzhi-project-tables", instructions="只处理调用方明确传入的授权记录。")


def selected_rows(records, where):
    if len(records) > 10000 or len(json.dumps(records, ensure_ascii=False).encode()) > 2_000_000:
        raise ValueError("table_input_too_large")
    return [
        (index + 1, row)
        for index, row in enumerate(records)
        if all(row.get(key) == value for key, value in (where or {}).items())
    ]


@server.tool()
def table_transform(
    records: list[dict[str, Any]],
    operation: Literal["filter", "sort", "deduplicate"] = "filter",
    where: dict[str, Any] | None = None,
    sort_by: str | None = None,
    descending: bool = False,
    sort_type: Literal["auto", "numeric", "text"] = "auto",
    limit: int = 100,
) -> dict:
    """真实筛选、排序或去重。返回原始行号，最多200条预览；total_rows为完整结果数。"""
    rows = selected_rows(records, where)
    if operation == "sort" and not sort_by:
        raise ValueError("sort_by_required")
    if sort_by:
        present = [item for item in rows if item[1].get(sort_by) is not None]
        missing = [item for item in rows if item[1].get(sort_by) is None]
        if rows and not present:
            raise ValueError("sort_field_unavailable")

        def numeric(value):
            if type(value) not in (str, int, float):
                raise ValueError("numeric_sort_required")
            value = Decimal(str(value).strip())
            if not value.is_finite():
                raise ValueError("numeric_sort_required")
            return value

        try:
            numeric_values = {number: numeric(row[sort_by]) for number, row in present}
            use_numeric = sort_type != "text"
        except (ValueError, InvalidOperation):
            if sort_type == "numeric":
                raise ValueError("numeric_sort_required") from None
            numeric_values, use_numeric = {}, False
        present.sort(
            key=lambda item: (
                numeric_values[item[0]]
                if use_numeric
                else json.dumps(item[1][sort_by], ensure_ascii=False, sort_keys=True)
            ),
            reverse=descending,
        )
        rows = [*present, *missing]
    if operation == "deduplicate":
        seen, unique = set(), []
        for item in rows:
            key = json.dumps(item[1], sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        rows = unique
    limit = min(200, max(1, limit))
    return {
        "records": [row for _, row in rows[:limit]],
        "row_numbers": [i for i, _ in rows[:limit]],
        "total_rows": len(rows),
        "truncated": len(rows) > limit,
    }


@server.tool()
def table_aggregate(
    records: list[dict[str, Any]],
    metric: Literal["count", "sum", "average", "min", "max"] = "count",
    value_field: str | None = None,
    group_by: str | None = None,
    where: dict[str, Any] | None = None,
) -> dict:
    """完整记录上的真实计数、求和、平均值、最小/最大值，可按字段分组。非数值不会隐式转为0。"""
    selected = selected_rows(records, where)
    groups = {}
    for number, row in selected:
        key = json.dumps(row.get(group_by), ensure_ascii=False, sort_keys=True) if group_by else "全部"
        groups.setdefault(key, []).append((number, row))
    if not selected and not group_by:
        groups["全部"] = []
    if len(groups) > 200:
        raise ValueError("too_many_groups")
    results = []
    for key, rows in sorted(groups.items()):
        values = [row.get(value_field) for _, row in rows]
        if metric != "count":
            try:
                if not value_field or any(type(v) not in (str, int, float) for v in values):
                    raise ValueError("numeric_field_required")
                decimals = [Decimal(str(value).strip()) for value in values]
                if any(not value.is_finite() for value in decimals):
                    raise ValueError("numeric_field_required")
                values = [float(value) for value in decimals]
                if any(not math.isfinite(value) for value in values):
                    raise ValueError("numeric_field_required")
            except (InvalidOperation, OverflowError):
                raise ValueError("numeric_field_required") from None
        if metric == "count":
            value = len(rows)
        elif metric == "sum":
            value = math.fsum(values)
        elif metric == "average":
            value = math.fsum(values) / len(values) if values else None
        elif metric == "min":
            value = min(values) if values else None
        else:
            value = max(values) if values else None
        results.append(
            {
                "group": json.loads(key) if group_by else key,
                "value": value,
                "row_count": len(rows),
                "row_numbers": [i for i, _ in rows],
            }
        )
    return {
        "metric": metric,
        "value_field": value_field,
        "group_by": group_by,
        "groups": results,
        "input_rows": len(records),
        "matched_rows": len(selected),
    }


if __name__ == "__main__":
    server.run(transport="stdio")
