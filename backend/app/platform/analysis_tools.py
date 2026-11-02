"""Bounded, read-only numerical operations over located records from an authorized source."""

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal
from statistics import fmean
from types import SimpleNamespace
from typing import Annotated

from pydantic import Field
from shapely.errors import ShapelyError
from shapely.geometry import box, shape

from ..casework.geo_common import Run, day, number, stamp
from ..casework.geo_overlay import metric_crs, project, union
from ..casework.geo_routes import routes
from ..casework.records import extract
from .contracts import Contract
from .domain_tool import DomainOperation

Key = Annotated[str, Field(min_length=1, max_length=100)]
EPSG = Annotated[str, Field(pattern=r"^EPSG:[0-9]{3,6}$")]
Year = Annotated[int, Field(ge=1, le=9999)]


class OneSource(Contract):
    source_id: Key


class AuditKeys(OneSource):
    key_fields: list[Key] = Field(min_length=1, max_length=4)
    where: dict[Key, str | int | float | bool | None] = Field(default_factory=dict, max_length=8)
    exclude_kinds: list[Key] = Field(default_factory=list, max_length=8)


class ReconcileTotal(OneSource):
    scope: Key
    unit: Key
    value_field: Key
    detail_id_field: Key
    scope_field: Key = "scope"
    unit_field: Key = "unit"
    role_field: Key = "kind"
    detail_role: Key = "detail"
    total_role: Key = "total"


class BaselineAnomaly(OneSource):
    value_field: Key
    unit: Key
    baseline_start: Year
    baseline_end: Year
    target_year: Year
    rank_start: Year | None = None
    year_field: Key = "year"
    unit_field: Key = "unit"
    policy_unit_field: Key = "extent_unit"
    sheet: Key | None = None
    unit_evidence_row: int | None = Field(default=None, ge=1)
    unit_evidence_field: Key | None = None
    min_valid_value: float | None = Field(default=None, allow_inf_nan=False)
    where: dict[Key, str | int | float | bool | None] = Field(default_factory=dict, max_length=8)


class OverlayArea(OneSource):
    other_source_id: Key
    metric_crs: EPSG


class RouteDetour(OneSource):
    od_id: Key


class ClassTransition(OneSource):
    other_source_id: Key
    before_date: Key | None = None
    after_date: Key | None = None
    aoi_source_id: Key | None = None


def _records(read_source, source_id, *, sheet=None):
    source = read_source(source_id)
    if source.get("id") != source_id or not source.get("source_version"):
        raise ValueError("Missing frozen source identity")
    rows = source.get("prepared_records")
    if rows is None:
        mime = str(source.get("media_type", "")).partition(";")[0]
        if (
            mime
            not in {
                "application/json",
                "application/geo+json",
                "application/jsonl",
                "application/x-ndjson",
                "text/csv",
            }
            or source.get("content") is None
        ):
            raise ValueError("This operation needs frozen structured records")
        if len(json.dumps(source["content"], ensure_ascii=False, allow_nan=False).encode()) > 512_000:
            raise ValueError("Structured input exceeds 512 KB; select a smaller batch")
        # Only parsed, inline content reaches extract. Never follow an asset file path.
        inline = {k: source[k] for k in ("id", "source_version", "media_type", "content")}
        inline["modality"] = "structured"
        rows, gaps = extract([inline], lambda _: None)
        if gaps:
            raise ValueError("Source could not be parsed completely")
    if sheet is not None:
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Too many worksheet records to select")
        rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("location"), dict)
            and row["location"].get("sheet") == sheet
        ]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Each input must contain 1–1000 records")
    if len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode()) > 1_000_000:
        raise ValueError("Parsed records exceed the tool limit")
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("source_id") != source_id
            or row.get("source_version") != source["source_version"]
            or not isinstance(row.get("fields"), dict)
            or not isinstance(row.get("location"), dict)
            or not row["location"]
        ):
            raise ValueError("Parsed row does not belong to this frozen asset/version")
    return rows


def _locations(rows):
    return {
        "source_id": rows[0]["source_id"],
        "source_version": rows[0]["source_version"],
        "locations": [row["location"] for row in rows],
    }


def _scalar(v):
    if isinstance(v, (dict, list)) or (isinstance(v, str) and len(v) > 200):
        raise ValueError("Expected a bounded scalar identifier")
    return v


def audit_keys(read_source, args):
    rows = [
        r
        for r in _records(read_source, args.source_id)
        if r["fields"].get("kind") != "policy"
        and r["fields"].get("kind") not in args.exclude_kinds
        and all(k in r["fields"] and r["fields"][k] == v for k, v in args.where.items())
    ]
    if not rows or len(set(args.key_fields)) != len(args.key_fields):
        raise ValueError("Specify distinct key fields on a nonempty table")
    if any(all(k not in r["fields"] for r in rows) for k in args.key_fields):
        raise ValueError("A requested key field does not exist in this table")
    groups, missing = defaultdict(list), []
    for row in rows:
        key = [_scalar(row["fields"].get(f)) for f in args.key_fields]
        if any(v is None or isinstance(v, str) and not v.strip() for v in key):
            missing.append(row["location"])
        else:
            groups[json.dumps(key, ensure_ascii=False, allow_nan=False)].append(row)
    issues = []
    identical = conflicts = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        different = (
            len(
                {
                    json.dumps(r["fields"], ensure_ascii=False, sort_keys=True, allow_nan=False)
                    for r in members
                }
            )
            > 1
        )
        conflicts += int(different)
        identical += int(not different)
        if len(issues) < 20:
            issues.append(
                {
                    "key": json.loads(key),
                    "kind": "conflicting_rows" if different else "duplicate_rows",
                    "count": len(members),
                    "locations": [r["location"] for r in members[:20]],
                }
            )
    return {
        "total_rows": len(rows),
        "key_fields": args.key_fields,
        "missing_key_count": len(missing),
        "missing_locations": missing[:20],
        "duplicate_groups": identical,
        "conflict_groups": conflicts,
        "examples": issues,
        "source_rows": _locations(rows),
    }


def _decimal(v):
    if isinstance(v, bool) or not re.fullmatch(r"[+-]?\d{1,16}(?:\.\d{1,9})?", str(v).strip()):
        raise ValueError("Expected an explicit bounded decimal value")
    return Decimal(str(v).strip())


def reconcile_total(read_source, args):
    rows = [
        r for r in _records(read_source, args.source_id) if r["fields"].get(args.scope_field) == args.scope
    ]
    if not rows or args.detail_role == args.total_role:
        raise ValueError("Scope not found or roles are ambiguous")
    details, totals = {}, []
    for row in rows:
        fields = row["fields"]
        if fields.get(args.unit_field) != args.unit:
            raise ValueError("All selected records must explicitly match the stated unit")
        amount = _decimal(fields.get(args.value_field))
        if fields.get(args.role_field) == args.total_role:
            totals.append(amount)
        elif fields.get(args.role_field) == args.detail_role:
            key = _scalar(fields.get(args.detail_id_field))
            if key is None or not str(key).strip() or key in details:
                raise ValueError("Missing or repeated detail identifier")
            details[key] = amount
        else:
            raise ValueError("Unrecognized role in the selected scope")
    if len(totals) != 1 or not details:
        raise ValueError("Require at least one detail and exactly one reported total")
    computed = sum(details.values(), Decimal(0))
    return {
        "scope": args.scope,
        "unit": args.unit,
        "value_field": args.value_field,
        "detail_count": len(details),
        "computed_total": str(computed),
        "reported_total": str(totals[0]),
        "difference": str(computed - totals[0]),
        "agrees": computed == totals[0],
        "source_rows": _locations(rows),
    }


def baseline_anomaly(read_source, args):
    start = args.rank_start if args.rank_start is not None else args.baseline_start
    if (
        not start <= args.baseline_start <= args.baseline_end <= args.target_year
        or args.target_year - start > 500
    ):
        raise ValueError("Invalid continuous annual baseline or ranking window")
    rows = _records(read_source, args.source_id, sheet=args.sheet)
    policies = [r for r in rows if r["fields"].get("kind") == "policy"]
    if len(policies) > 1:
        raise ValueError("Conflicting policies in the supplied series")
    policy = policies[0]["fields"] if policies else {}
    used = policies.copy()
    default_unit = policy.get(args.policy_unit_field)
    if args.unit_evidence_row is not None or args.unit_evidence_field is not None:
        if not (args.sheet and args.unit_evidence_row and args.unit_evidence_field):
            raise ValueError("A unit declaration requires a worksheet, row and field")
        located = [r for r in rows if r["location"].get("row") == args.unit_evidence_row]
        if len(located) != 1:
            raise ValueError("Unit declaration row not uniquely located")
        phrase = located[0]["fields"].get(args.unit_evidence_field)
        match = (
            re.search(
                r"(?<!\w)" + re.escape(args.value_field) + r"\s*[:：]\s*[+-]?\d+(?:\.\d+)?\s+(\S+)\s*$",
                phrase,
                re.IGNORECASE,
            )
            if isinstance(phrase, str)
            else None
        )
        if (
            not match
            or _unit_label(match[1]) != _unit_label(args.unit)
            or default_unit not in {None, args.unit}
        ):
            raise ValueError("Source declaration does not bind this metric to the requested unit")
        default_unit = args.unit
        used.append(located[0])
    obs = defaultdict(list)
    for row in rows:
        f = row["fields"]
        if f.get("kind") == "policy" or not all(k in f and f[k] == v for k, v in args.where.items()):
            continue
        year = number(f.get(args.year_field))
        if year is None or not year.is_integer():
            raise ValueError("Selected observation has no integer year")
        if start <= year <= args.target_year:
            if f.get(args.unit_field, default_unit) != args.unit:
                raise ValueError("Observation units are missing or inconsistent")
            val = number(f.get(args.value_field))
            obs[int(year)].append(
                None
                if val is not None and args.min_valid_value is not None and val < args.min_valid_value
                else val
            )
            used.append(row)
    if not obs:
        raise ValueError("No observations matched the series selection")
    vals = {}
    issues = []
    for year in range(start, args.target_year + 1):
        readings = set(obs.get(year, []))
        if len(readings) == 1 and None not in readings:
            vals[year] = next(iter(readings))
        else:
            issues.append({"year": year, "reason": "conflict" if len(readings) > 1 else "missing"})
    years = range(args.baseline_start, args.baseline_end + 1)
    try:
        mean = fmean(vals[y] for y in years) if all(y in vals for y in years) else None
    except OverflowError as error:
        raise ValueError("Baseline exceeds finite numerical range") from error
    target = vals.get(args.target_year)
    diff = target - mean if target is not None and mean is not None else None
    pct = (diff / mean) * 100 if diff is not None and mean else None
    if any(v is not None and not math.isfinite(v) for v in (mean, diff, pct)):
        raise ValueError("Result exceeds finite numerical range")
    rank = 1 + sum(v < target for v in vals.values()) if not issues and target is not None else None
    return {
        "target_year": args.target_year,
        "target": target,
        "unit": args.unit,
        "baseline_mean": mean,
        "difference": diff,
        "percent": pct,
        "ascending_rank": rank,
        "baseline_years": [args.baseline_start, args.baseline_end],
        "rank_start": start,
        "gaps": issues,
        "source_rows": _locations(used),
    }


def _unit_label(unit):
    return unicodedata.normalize("NFKC", unit).replace("^2", "2")


def _metric(code):
    return metric_crs(SimpleNamespace(policy={"metric_crs": code}))


def _polygons(rows, dst):
    shapes = []
    for row in rows:
        f = row["fields"]
        raw = f.get("geometry")
        if not isinstance(raw, dict) or raw.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Only located polygon features may be measured")
        src = f.get("crs")
        if not isinstance(src, str) or not re.fullmatch(r"EPSG:[0-9]{3,6}", src):
            raise ValueError("Input features require an explicit EPSG source CRS")
        try:
            geom = shape(raw)
        except (ValueError, TypeError, KeyError, IndexError, ShapelyError) as error:
            raise ValueError("Invalid polygon") from error
        if geom.is_empty or not geom.is_valid or geom.has_z:
            raise ValueError("Invalid or non-planar polygon")
        geom = project(geom, src, dst)
        if geom.is_empty or not geom.is_valid or not math.isfinite(geom.area) or geom.area <= 0:
            raise ValueError("Projection failed")
        shapes.append(geom)
    return union(shapes)


def overlay_area(read_source, args):
    if args.source_id == args.other_source_id:
        raise ValueError("Select two independent polygon layers")
    left, right = (_records(read_source, sid) for sid in (args.source_id, args.other_source_id))
    left, right = ([r for r in rows if r["fields"].get("kind") != "policy"] for rows in (left, right))
    if not left or not right:
        raise ValueError("Both layers need at least one polygon")
    dst = _metric(args.metric_crs)
    a, b = _polygons(left, dst), _polygons(right, dst)
    intersection = a.intersection(b).area
    return {
        "metric_crs": dst,
        "unit": "m²",
        "left_area": a.area,
        "right_area": b.area,
        "intersection": intersection,
        "union": a.union(b).area,
        "left_exposed_percent": 100 * intersection / a.area,
        "sources": [_locations(left), _locations(right)],
    }


def route_detour(read_source, args):
    rows = _records(read_source, args.source_id)
    policies = [r for r in rows if r["fields"].get("kind") == "policy"]
    orders = [r for r in rows if r["fields"].get("kind") == "od" and r["fields"].get("od_id") == args.od_id]
    if len(orders) != 1 or len(policies) != 1 or not stamp(policies[0]["fields"].get("snapshot")):
        raise ValueError("Need exactly one located OD record and one timezone-qualified snapshot policy")
    network = [r for r in rows if r["fields"].get("kind") in {"edge", "notice"}]
    if len(network) > 180:
        raise ValueError("Select at most 180 road edges and notices")
    if not any(r["fields"]["kind"] == "edge" for r in network):
        raise ValueError("No frozen road edges for this OD")
    for row in network:
        f = row["fields"]
        if f["kind"] == "edge" and not isinstance(f.get("oneway", True), bool):
            raise ValueError("Road direction must be a boolean")
    selected = [*policies, *network, *orders]
    ctx = Run(selected)
    ctx.policy["expected_od"] = 1  # This operation selects one OD even when the source describes a batch.
    result = routes(ctx)
    return {
        "od": result["od_comparison"][0],
        "snapshot": result["notice_audit"]["snapshot"],
        "notice_conflicts": result["notice_audit"]["conflicts"],
        "exclusions": result["exclusions"],
        "gaps": ctx.gaps,
        "evidence": list(ctx.evidence.values()),
    }


def _grid(rows, requested_date):
    candidates = [
        r
        for r in rows
        if r["fields"].get("kind") == "cover_grid"
        and (requested_date is None or r["fields"].get("date") == requested_date)
    ]
    if len(candidates) != 1:
        raise ValueError("Each source must contain exactly one classified grid")
    row = candidates[0]
    f = row["fields"]
    values = f.get("values")
    if (
        not isinstance(values, list)
        or not values
        or not isinstance(values[0], list)
        or not values[0]
        or any(not isinstance(v, list) or len(v) != len(values[0]) for v in values)
        or len(values) * len(values[0]) > 4096
    ):
        raise ValueError("Expected a registered rectangular grid of at most 4096 cells")
    mask = f.get("valid_mask")
    if mask is not None and (
        not isinstance(mask, list)
        or len(mask) != len(values)
        or any(
            not isinstance(r, list) or len(r) != len(values[0]) or any(not isinstance(b, bool) for b in r)
            for r in mask
        )
    ):
        raise ValueError("Invalid grid validity mask")
    ref = [number(f.get(k)) for k in ("origin_x", "origin_y", "pixel_width", "pixel_height")]
    if (
        None in ref
        or not ref[2]
        or not ref[3]
        or not day(f.get("date"))
        or any(number(f.get(k, 0)) != 0 for k in ("rotation_x", "rotation_y"))
    ):
        raise ValueError("Missing grid registration or date")
    labels = []
    for i, items in enumerate(values):
        for j, v in enumerate(items):
            if mask is not None and not mask[i][j] or v is None or v == "unknown":
                labels.append(None)
            elif isinstance(v, str) and 0 < len(v.strip()) <= 80:
                labels.append(v.strip())
            else:
                raise ValueError("Supply explicit class labels or unknown/masked pixels")
    return row, (len(values), len(values[0]), _metric(f.get("crs")), *ref), labels


def class_transition(read_source, args):
    if args.source_id == args.other_source_id and not (args.before_date and args.after_date):
        raise ValueError("Select independent before/after grids or explicitly select their dates")
    before, grid, old = _grid(_records(read_source, args.source_id), args.before_date)
    after, other, new = _grid(_records(read_source, args.other_source_id), args.after_date)
    if grid != other or day(before["fields"]["date"]) >= day(after["fields"]["date"]):
        raise ValueError("Grids must be co-registered and ordered in time")
    sources = [_locations([before]), _locations([after])]
    aoi = None
    if args.aoi_source_id:
        aoi_rows = [r for r in _records(read_source, args.aoi_source_id) if r["fields"].get("kind") == "aoi"]
        if not aoi_rows:
            raise ValueError("The requested source has no AOI polygon")
        aoi = _polygons(aoi_rows, grid[2])
        sources.append(_locations(aoi_rows))
    pixel_area = abs(grid[-1] * grid[-2])
    if not math.isfinite(pixel_area * len(old)):
        raise ValueError("Pixel area exceeds finite range")
    counts = Counter()
    areas = Counter()
    unknown = outside = 0
    for index, (a, b) in enumerate(zip(old, new, strict=True)):
        area = pixel_area
        if aoi is not None:
            i, j = divmod(index, grid[1])
            x, y = grid[3] + j * grid[5], grid[4] + i * grid[6]
            area = box(x, y, x + grid[5], y + grid[6]).intersection(aoi).area
        if area <= 0:
            outside += 1
        elif a is None or b is None:
            unknown += 1
        else:
            counts[a, b] += 1
            areas[a, b] += area
    if len(counts) > 100:
        raise ValueError("Class transition matrix exceeds 100 entries")
    return {
        "before": before["fields"]["date"],
        "after": after["fields"]["date"],
        "metric_crs": grid[2],
        "unit": "m²",
        "valid_pixels": sum(counts.values()),
        "unknown_pixels": unknown,
        "outside_aoi_pixels": outside,
        "transitions": [
            {"before": a, "after": b, "pixels": n, "area": areas[a, b]}
            for (a, b), n in sorted(counts.items())
        ],
        "sources": sources,
    }


_RECORDS = ("backend/app/casework/records.py", "backend/app/casework/control.py")
_COMMON = (*_RECORDS, "backend/app/casework/geo_common.py")
_OVERLAY = (*_COMMON, "backend/app/casework/geo_overlay.py")
TOOLS = (
    DomainOperation(
        "table.audit_keys",
        "audit_keys",
        "主键缺失与重复冲突核查",
        AuditKeys,
        audit_keys,
        dependencies=_RECORDS,
    ),
    DomainOperation(
        "stats.reconcile_total",
        "reconcile_total",
        "明细与报告总量核算",
        ReconcileTotal,
        reconcile_total,
        dependencies=_RECORDS,
    ),
    DomainOperation(
        "series.baseline_anomaly",
        "baseline_anomaly",
        "年度基线距平与排名",
        BaselineAnomaly,
        baseline_anomaly,
        dependencies=_COMMON,
    ),
    DomainOperation(
        "geo.overlay_area",
        "overlay_area",
        "双图层相交与覆盖面积",
        OverlayArea,
        overlay_area,
        dependencies=_OVERLAY,
        packages=("shapely", "pyproj"),
    ),
    DomainOperation(
        "geo.route_detour",
        "route_detour",
        "时点路网绕行与可达性",
        RouteDetour,
        route_detour,
        dependencies=(*_COMMON, "backend/app/casework/geo_routes.py"),
    ),
    DomainOperation(
        "raster.class_transition",
        "class_transition",
        "两期配准栅格分类转移",
        ClassTransition,
        class_transition,
        dependencies=_OVERLAY,
        packages=("shapely", "pyproj"),
    ),
)
