"""Select bounded, source-supported calculations that can independently check a case product."""

import itertools
import math
import re

from .geo_common import day, number, stamp


def _annual(records):
    policies = [r for r in records if r["fields"].get("kind") == "policy"]
    if len(policies) > 1:
        return []
    policy = policies[0]["fields"] if policies else {}
    start = policy.get("start_year", 1979)
    end = policy.get("target_year", 2023)
    baseline = policy.get("baseline_years", [1981, 2010])
    if (
        not isinstance(baseline, (list, tuple))
        or len(baseline) != 2
        or any(type(n) is not int for n in [start, end, *baseline])
        or not 1 <= start <= baseline[0] <= baseline[1] <= end <= 9999
        or end - start > 500
    ):
        return []
    north = [
        r
        for r in records
        if number(r["fields"].get("month")) == 9
        and str(r["fields"].get("hemisphere", "")).upper() in {"N", "NH", "NORTH", "北极"}
    ]
    if not north or len({r["source_id"] for r in [*north, *policies]}) != 1:
        return []
    # This single-source operation checks the raw column. The primary analyst
    # can repair it with a separately scoped dictionary; comparing raw blanks
    # to its repaired values would compare different data contracts.
    in_window = [
        r for r in north if (year := number(r["fields"].get("year"))) is not None and start <= year <= end
    ]
    if not in_window or any("extent" not in r["fields"] for r in in_window):
        return []
    source_id = north[0]["source_id"]
    for r in records:
        d = r["fields"]
        if (
            d.get("kind") == "field_dictionary"
            and d.get("target_source_id") == source_id
            and isinstance(d.get("aliases"), dict)
        ) and any(
            name in {"extent", "unit", "year", "month", "hemisphere", "metric", "dataset_version"}
            for name in d["aliases"].values()
        ):
            return []
    kinds = {(r["fields"]["month"], r["fields"]["hemisphere"]) for r in north}
    if len(kinds) != 1 or any(
        f["fields"].get("metric", "monthly_mean_extent") not in {"monthly_mean_extent", "extent"}
        for f in north
    ):
        return []
    if len({r["fields"].get("dataset_version") for r in north}) > 1:
        return []
    month, hemisphere = next(iter(kinds))
    opts = {
        "source_id": north[0]["source_id"],
        "value_field": "extent",
        "rank_start": start,
        "target_year": end,
        "baseline_start": baseline[0],
        "baseline_end": baseline[1],
        "where": {"month": month, "hemisphere": hemisphere},
        "min_valid_value": 0,
    }
    sheets = {r["location"].get("sheet") for r in north}
    if len(sheets) != 1:
        return []
    sheet = sheets.pop()
    if sheet:
        opts["sheet"] = sheet
    units = {r["fields"].get("unit", policy.get("extent_unit")) for r in north}
    if units == {None} and sheet:
        # Require an actual printed declaration in this same worksheet, for the same
        # indicator. Other sheets or similarly named area/anomaly columns are not evidence.
        for r in records:
            if r["source_id"] != opts["source_id"] or r["location"].get("sheet") != sheet:
                continue
            text = r["fields"].get("col_1")
            if isinstance(text, str) and re.search(
                r"(?<!\w)extent\s*:\s*[+-]?\d+(?:\.\d+)?\s+Mkm(?:\^2|²)\s*$", text, re.IGNORECASE
            ):
                opts.update(unit="Mkm²", unit_evidence_row=r["location"]["row"], unit_evidence_field="col_1")
                break
    elif len(units) == 1:
        unit = units.pop()
        if isinstance(unit, str) and unit.strip():
            opts["unit"] = unit
    if "unit" not in opts:
        return []
    return [("series.baseline_anomaly", opts)]


def _routes(records):
    eligible = [r for r in records if r["fields"].get("kind") in {"edge", "notice", "od", "policy"}]
    if not eligible or len({r["source_id"] for r in eligible}) != 1 or len(eligible) > 180:
        return []
    policies = [r for r in eligible if r["fields"]["kind"] == "policy"]
    if (
        len(policies) != 1
        or not stamp(policies[0]["fields"].get("snapshot"))
        or not any(r["fields"]["kind"] == "edge" for r in eligible)
    ):
        return []
    ids = [r["fields"].get("od_id") for r in eligible if r["fields"]["kind"] == "od"]
    if not ids or any(not isinstance(k, str) or not k for k in ids) or len(set(ids)) != len(ids):
        return []
    return [("geo.route_detour", {"source_id": eligible[0]["source_id"], "od_id": oid}) for oid in ids[:3]]


def _grids(records):
    grids = [r for r in records if r["fields"].get("kind") == "cover_grid"]
    if len(grids) != 3 or any(not day(r["fields"].get("date")) for r in grids):
        return []
    ordered = sorted(grids, key=lambda r: day(r["fields"]["date"]))
    if len({day(r["fields"]["date"]) for r in ordered}) != len(grids):
        return []
    # Spectral or unregistered grids require their specialist classifier, which has a
    # different contract. Only directly classified, co-registered observations qualify.
    signatures = []
    for row in grids:
        f = row["fields"]
        values = f.get("values")
        if not isinstance(values, list) or not values or not isinstance(values[0], list) or not values[0]:
            return []
        if len(values) * len(values[0]) > 4096 or any(
            not isinstance(line, list)
            or len(line) != len(values[0])
            or any(v is not None and not isinstance(v, str) for v in line)
            for line in values
        ):
            return []
        signatures.append(
            (
                f.get("crs"),
                f.get("origin_x"),
                f.get("origin_y"),
                f.get("pixel_width"),
                f.get("pixel_height"),
                len(values),
                len(values[0]),
            )
        )
    if any(v is None for v in signatures[0]) or signatures.count(signatures[0]) != len(grids):
        return []
    aoi_sources = {r["source_id"] for r in records if r["fields"].get("kind") == "aoi"}
    if len(aoi_sources) > 1:
        return []
    extra = {"aoi_source_id": next(iter(aoi_sources))} if aoi_sources else {}
    return [
        (
            "raster.class_transition",
            {
                "source_id": before["source_id"],
                "other_source_id": after["source_id"],
                "before_date": before["fields"]["date"],
                "after_date": after["fields"]["date"],
                **extra,
            },
        )
        for before, after in itertools.pairwise(ordered)
    ]


def _monthly(records):
    planned = []
    for sid in dict.fromkeys(r["source_id"] for r in records):
        owned = [r["fields"] for r in records if r["source_id"] == sid]
        months = [f for f in owned if f.get("month")]
        # The same month may legitimately recur in a different population.
        if (
            months
            and len({f.get("scope", f.get("coverage")) for f in months}) == 1
            and all(f.get("kind") == "chart_label_ocr" and "deaths" in f and "injured" in f for f in months)
        ):
            planned.append(
                (
                    "table.audit_keys",
                    {"source_id": sid, "key_fields": ["month"], "where": {"kind": "chart_label_ocr"}},
                )
            )
        scopes = sorted({r["scope"] for r in owned if isinstance(r.get("scope"), str) and r["scope"].strip()})
        for scope in scopes:
            group = [f for f in owned if f.get("scope") == scope]
            totals = [f for f in group if f.get("kind") == "total"]
            detail = [f for f in group if f.get("kind") == "detail" and f.get("month")]
            units = {f.get("unit") for f in group}
            if (
                len(totals) != 1
                or not detail
                or len(detail) + 1 != len(group)
                or len(units) != 1
                or len({f["month"] for f in detail}) != len(detail)
            ):
                continue
            unit = units.pop()
            if not isinstance(unit, str) or not unit.strip():
                continue
            for field in ("deaths", "injured"):
                if all(number(f.get(field)) is not None for f in group):
                    planned.append(
                        (
                            "stats.reconcile_total",
                            {
                                "source_id": sid,
                                "scope": scope,
                                "unit": unit,
                                "detail_id_field": "month",
                                "value_field": field,
                            },
                        )
                    )
        if len(planned) >= 3:
            break
    return planned[:3]


def _projects(records):
    # One complete project table can be checked against one portfolio. When
    # several sources contribute project rows there is no per-source total.
    data = [r for r in records if r["fields"].get("kind") not in {"policy", "taxonomy", "org_alias"}]
    if not data or len({r["source_id"] for r in data}) != 1:
        return []
    key = next((k for k in ("project_id", "项目编号") if all(k in r["fields"] for r in data)), None)
    if not key:
        return []
    return [
        (
            "table.audit_keys",
            {
                "source_id": data[0]["source_id"],
                "key_fields": [key],
                "exclude_kinds": ["org_alias", "taxonomy"],
            },
        )
    ]


def requests(code, inputs, records, candidate):
    """Only material explicitly present in the frozen case can select a cross-check."""
    frozen = {(a["id"], str(a["source_version"])) for a in inputs}
    if not records or any((r["source_id"], str(r["source_version"])) not in frozen for r in records):
        return []
    planner = {"B05": _monthly, "T04": _projects, "G03": _annual, "G06": _grids, "G09": _routes}.get(code)
    try:
        return planner(records) if planner else []
    except (KeyError, TypeError, ValueError):
        # Malformed data is still assessed by the case's primary source and domain
        # checks; do not invent a callable operation over a mismatched schema.
        return []


def _equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    x, y = number(a), number(b)
    return x is not None and y is not None and math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-8)


def _check(name, ok, message, hard=True):
    return [
        {"name": name, "passed": bool(ok), "message": "独立工具复算一致" if ok else message, "hard": hard}
    ]


def compare(code, tool_id, result, candidate):
    """Compare only quantities with matching source, unit, population, epoch or OD."""
    out = result.get("data", result)
    products = candidate.get("products", {})
    if code == "G03" and tool_id == "series.baseline_anomaly":
        a = products.get("anomaly") or {}
        matches = (
            bool(a)
            and a.get("unit") == out["unit"]
            and a.get("target_year") == out["target_year"]
            and a.get("baseline_years") == out["baseline_years"]
        )
        matches = matches and all(
            _equal(a.get(k), out[alias])
            for k, alias in [
                ("baseline_mean", "baseline_mean"),
                ("target_extent", "target"),
                ("difference", "difference"),
                ("percent", "percent"),
                ("ascending_rank", "ascending_rank"),
            ]
        )
        return _check(
            "independent_annual_anomaly",
            matches,
            "独立复算的年度基线、差值、百分比或排名与产品不同",
            hard=bool(a and a.get("difference") is not None),
        )
    if code == "G09" and tool_id == "geo.route_detour":
        tested = out["od"]
        found = [v for v in (products.get("od_comparison") or []) if v["od_id"] == tested["od_id"]]
        if len(found) != 1:
            return _check(
                "independent_road_route",
                False,
                "产品缺少独立计算所用的同一起终点",
                hard=not candidate.get("gaps"),
            )
        saved = found[0]
        ok = (
            saved["status"] == tested["status"]
            and saved["from"] == tested["from"]
            and saved["to"] == tested["to"]
            and len(saved["candidates"]) == len(tested["candidates"])
            and all(
                a["edge_ids"] == b["edge_ids"]
                and all(
                    _equal(a.get(k), b.get(k))
                    for k in ("distance_km", "estimated_time_min", "extra_km", "extra_min")
                )
                for a, b in zip(saved["candidates"], tested["candidates"], strict=True)
            )
        )
        return _check("independent_road_route", ok, "冻结路网复算的可达状态、边序列或距离/时间与产品不同")
    if code == "G06" and tool_id == "raster.class_transition":
        compared = [
            r
            for r in (products.get("transitions") or [])
            if r.get("from_date") == out["before"]
            and r.get("to_date") == out["after"]
            and "unknown" not in (r.get("from_class"), r.get("to_class"))
        ]
        expected = {(r["before"], r["after"]): r["area"] for r in out["transitions"]}
        actual = {(r["from_class"], r["to_class"]): r["area_m2"] for r in compared}
        ok = (
            len(actual) == len(compared)
            and actual.keys() == expected.keys()
            and all(_equal(v, expected[k]) for k, v in actual.items())
        )
        return _check(
            "independent_class_transition",
            ok,
            "同范围、同期间、均可判读像元的转移面积与产品不同",
            hard=bool(compared or not candidate.get("gaps")),
        )
    if code == "B05" and tool_id == "stats.reconcile_total":
        sid = out["source_rows"]["source_id"]
        found = [
            r
            for r in products.get("reconciliation", [])
            if r.get("source_id") == sid
            and r.get("version") == out["source_rows"]["source_version"]
            and r.get("scope") == out["scope"]
        ]
        idx = {"deaths": 0, "injured": 1}.get(out.get("value_field"))
        if (
            idx is None
            or len(found) != 1
            or not isinstance(found[0].get("computed"), list)
            or len(found[0]["reported"]) != 1
        ):
            return _check(
                "independent_monthly_total",
                False,
                "报告缺少同范围可复算的明细或总量",
                hard=not candidate.get("gaps"),
            )
        item = found[0]
        ok = (
            len(item["computed"]) > idx
            and len(item["reported"][0]) > idx
            and _equal(item["computed"][idx], out["computed_total"])
            and _equal(item["reported"][0][idx], out["reported_total"])
        )
        return _check("independent_monthly_total", ok, "同范围、同单位的明细合计或报告总量与复算不同")
    if code == "B05" and tool_id == "table.audit_keys":
        # Duplicate keys alone cannot establish an incorrect single population count.
        if out["missing_key_count"] or out["duplicate_groups"] or out["conflict_groups"]:
            return []
        src = out["source_rows"]
        count = sum(
            r["source_id"] == src["source_id"] and r["source_version"] == src["source_version"]
            for r in products.get("monthly", [])
        )
        return _check(
            "independent_chart_month_count",
            count == out["total_rows"],
            "月度产品行数与同份原件的唯一月份标签数量不同",
            hard=not candidate.get("gaps"),
        )
    if code == "T04" and tool_id == "table.audit_keys":
        if out["missing_key_count"] or out["duplicate_groups"] or out["conflict_groups"]:
            return []
        portfolio = products.get("portfolio")
        if not isinstance(portfolio, list):
            return _check(
                "independent_project_count",
                False,
                "未找到同一完整项目台账的项目清单",
                hard=not candidate.get("gaps"),
            )
        return _check(
            "independent_project_count",
            len(portfolio) == out["total_rows"],
            "项目清单与唯一项目编号数量不同",
            hard=not candidate.get("gaps"),
        )
    return []
