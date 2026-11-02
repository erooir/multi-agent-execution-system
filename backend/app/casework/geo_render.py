"""Small self-contained overview of the actual GeoJSON layers, with no online tile service."""

from html import escape
from itertools import chain
from math import cos, radians

from .geo_common import lnglat

COLORS = ("#1976a8", "#b25815", "#358260", "#874c9c", "#a84141", "#687688")


def _lines(g):
    typ = g.get("type")
    c = g.get("coordinates", [])
    if typ == "Point":
        return [("point", [c])]
    if typ == "LineString":
        return [("line", c)]
    if typ == "MultiLineString":
        return [("line", line) for line in c]
    if typ == "Polygon":
        return [("polygon", c)]
    if typ == "MultiPolygon":
        return [("polygon", rings) for rings in c]
    return []


def map_view(products):
    layers = []

    def visit(obj):
        if isinstance(obj, dict):
            if obj.get("type") == "FeatureCollection":
                layers.extend(obj["features"])
                return
            for child in obj.values():
                visit(child)
        elif isinstance(obj, list):
            for child in obj:
                visit(child)

    visit(products)
    primitives = []
    points = []
    legend = {}
    vertex_count = 0
    shown = 0
    for f in layers:
        before = len(primitives)
        props = f.get("properties") or {}
        tag = str(
            next(
                (
                    props[k]
                    for k in (
                        "class",
                        "status",
                        "match",
                        "candidate",
                        "crop",
                        "island",
                        "district",
                        "site_id",
                    )
                    if props.get(k) is not None
                ),
                "geometry",
            )
        )
        for typ, line in _lines(f.get("geometry") or {}):
            coords = list(chain.from_iterable(line)) if typ == "polygon" else line
            if not coords or any(len(xy) < 2 or not lnglat(*xy[:2]) for xy in coords):
                continue
            # Full precision geometries remain in the machine-readable layers.
            if vertex_count + len(coords) > 24000 or len(primitives) >= 2400:
                continue
            vertex_count += len(coords)
            points.extend(coords)
            legend.setdefault(tag, COLORS[len(legend) % len(COLORS)])
            primitives.append((typ, line, tag))
        if len(primitives) > before:
            shown += 1
    if not points:
        return {"features": len(layers), "svg": None, "note": "无可定位几何，未生成底图或虚构位置。"}
    xs, ys = zip(*[pt[:2] for pt in points], strict=True)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    aspect = max(0.02, cos(radians((y0 + y1) / 2)))
    scale = min(680 / (max(x1 - x0, 0.001) * aspect), 360 / max(y1 - y0, 0.001))

    def xy(pt):
        return (360 + (pt[0] - (x0 + x1) / 2) * aspect * scale, 190 - (pt[1] - (y0 + y1) / 2) * scale)

    def path(pts):
        return "M" + " L".join("{:.2f},{:.2f}".format(*xy(pt)) for pt in pts)

    marks = []
    for typ, line, tag in primitives:
        color = legend[tag]
        if typ == "point":
            x, y = xy(line[0])
            marks.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{color}"/>')
        else:
            d = " ".join(path(ring) + " Z" for ring in line) if typ == "polygon" else path(line)
            marks.append(
                f'<path d="{d}" fill="{color if typ == "polygon" else "none"}" fill-opacity=".35" fill-rule="evenodd" stroke="{color}" stroke-width="1.2"/>'
            )
    labels = []
    for i, (label, color) in enumerate(list(legend.items())[:12]):
        y = 398 + (i // 4) * 19
        x = 15 + (i % 4) * 175
        labels.append(
            f'<circle cx="{x}" cy="{y - 4}" r="4" fill="{color}"/><text x="{x + 9}" y="{y}" font-size="11">{escape(label[:22])}</text>'
        )
    return {
        "features": len(layers),
        "shown_features": shown,
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 445"><title>观测图层示意</title>'
        + "".join(marks + labels)
        + "</svg>",
        "note": "示意图无在线底图；距离与面积采用原分析投影，而非示意图读数。",
    }
