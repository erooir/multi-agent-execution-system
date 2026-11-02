"""Utilities for bounded, source-located geographical observations."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from html import escape


def number(value):
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if not isinstance(value, bool) and math.isfinite(n) else None


def day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        try:
            month, day_num, year = map(int, str(value).split("/"))
            return date(year, month, day_num)
        except ValueError:
            return None


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except ValueError:
        return None


def lnglat(lon, lat):
    lon, lat = number(lon), number(lat)
    return [lon, lat] if lon is not None and lat is not None and abs(lon) <= 180 and abs(lat) <= 90 else None


class Run:
    """Evidence ids are local to the result; each points back into a frozen asset."""

    def __init__(self, records):
        self.rows = []
        self.evidence = {}
        self.gaps = []
        self._indexes = {}
        self.policy = {}
        for idx, row in enumerate(records):
            if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
                self.gap("输入记录没有字段对象")
                continue
            if (
                not row.get("source_id")
                or not row.get("source_version")
                or not isinstance(row.get("location"), dict)
                or not row["location"]
            ):
                self.gap("输入缺少可核对的来源、版本或原件定位")
                continue
            self._indexes[id(row)] = idx + 1
            if row["fields"].get("kind") == "policy":
                self.policy.update(row["fields"])
                self.ref(row)
            else:
                self.rows.append(row)

    def ref(self, row):
        rid = "g" + str(self._indexes[id(row)])
        if rid not in self.evidence:
            self.evidence[rid] = {
                "id": rid,
                **{k: row[k] for k in ("source_id", "source_version", "location")},
            }
        return rid

    def gap(self, msg):
        if msg not in self.gaps:
            self.gaps.append(msg)

    def select(self, kind):
        return [r for r in self.rows if r["fields"].get("kind") == kind]

    def add_derived(self, source, row):
        self._indexes[id(row)] = self._indexes[id(source)]
        self.rows.append(row)

    def done(self, products):
        if not self.evidence:
            self.gap("没有与本项分析匹配的可定位记录")

        def linked(obj):
            if isinstance(obj, dict):
                return {("evidence_ids" if k == "refs" else k): linked(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [linked(i) for i in obj]
            return obj

        return {"products": linked(products), "evidence": list(self.evidence.values()), "gaps": self.gaps}


def feature(geometry, properties):
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def line_chart(points, unit, baseline=None):
    """Render actual annual values and break the line at missing years."""
    values = [y for _, y in points if y is not None]
    if not values or not points:
        return None
    left, right = 55, 580
    top, bottom = 26, 185
    vmin, vmax = min(values), max(values)
    if baseline is not None:
        vmin, vmax = min(vmin, baseline), max(vmax, baseline)

    def xx(x):
        return left + (x - points[0][0]) * (right - left) / max(1, points[-1][0] - points[0][0])

    def yy(y):
        return bottom - (y - vmin) * (bottom - top) / (vmax - vmin or 1)

    line = []
    down = False
    for x, y in points:
        if y is None:
            down = False
            continue
        line.append(f"{'L' if down else 'M'}{xx(x):.2f},{yy(y):.2f}")
        down = True
    base = (
        ""
        if baseline is None
        else f'<path d="M{left},{yy(baseline):.2f} H{right}" stroke="#8c6200" stroke-dasharray="4 2"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 215"><title>年度趋势</title>'
        f'<path d="M{left},{top} V{bottom} H{right}" fill="none" stroke="#444"/>{base}'
        f'<path d="{" ".join(line)}" fill="none" stroke="#276ca5" stroke-width="2"/>'
        f'<g font-size="12"><text x="5" y="18">{escape(unit)}</text><text x="4" y="{top + 6}">{vmax:.2f}</text>'
        f'<text x="4" y="{bottom}">{vmin:.2f}</text><text x="{left}" y="204">{points[0][0]}</text>'
        f'<text x="{right - 28}" y="204">{points[-1][0]}</text></g></svg>'
    )
