"""Non-executable export formats for authorized case products."""

import base64
import csv
import io
import json
import re
import unicodedata
from xml.etree import ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from PIL import Image


def encoded_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    s = value if isinstance(value, str) else encoded_json(value).decode("utf-8")
    # Apply to untrusted headings too. Preserve exact typed values in report.json.
    normalized = unicodedata.normalize("NFKC", s)
    j = 0
    while j < len(normalized) and (
        normalized[j].isspace() or unicodedata.category(normalized[j]) in {"Cc", "Cf"}
    ):
        j += 1
    if s.startswith(("\t", "\r", "\n")) or normalized[j:].startswith(("=", "+", "-", "@")):
        return "'" + s
    return s


def csv_table(rows):
    cols = list(dict.fromkeys(k for row in rows for k in row))
    if len(cols) > 512:
        raise ValueError("表格字段超过单表导出上限")
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow([csv_cell(c) for c in cols])
    for row in rows:
        writer.writerow([csv_cell(row.get(k)) for k in cols])
    return text.getvalue().encode("utf-8-sig")


SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
TAGS = {
    "svg",
    "title",
    "desc",
    "g",
    "path",
    "line",
    "rect",
    "circle",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "image",
}
NUMBERS = {
    "x",
    "y",
    "width",
    "height",
    "x1",
    "x2",
    "y1",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "stroke-width",
    "font-size",
    "fill-opacity",
    "stroke-opacity",
    "opacity",
}
NUM = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
COLOR = re.compile(r"(?:#[0-9a-fA-F]{3,8}|none|black|white|red|blue|green|gray|grey)")


def _png(value):
    if not value.startswith("data:image/png;base64,") or len(value) > 2_000_000:
        raise ValueError("只接受内嵌PNG图像")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != "PNG" or im.width * im.height > 2_000_000:
                raise ValueError("无效PNG类型或尺寸")
            dest = io.BytesIO()
            im.convert("RGBA").save(dest, format="PNG")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("内嵌图像解码失败") from exc
    return "data:image/png;base64," + base64.b64encode(dest.getvalue()).decode("ascii")


def safe_svg(raw):
    """Emit only a static allowlisted tree; no CSS, DTD, scripts or remote references."""
    if len(raw.encode("utf-8")) > 3_000_000:
        raise ValueError("SVG超过独立文件上限")
    try:
        tree = fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise ValueError("SVG文档结构不安全或无效") from exc
    if tree.tag not in {"svg", f"{{{SVG_NS}}}svg"}:
        raise ValueError("SVG根元素无效")
    clean = ET.Element("svg", {"xmlns": SVG_NS})
    total = 0

    def copy(node, out, depth=0):
        nonlocal total
        total += 1
        if depth > 24 or total > 50000:
            raise ValueError("SVG结构超过限制")
        tag = node.tag.removeprefix(f"{{{SVG_NS}}}")
        if tag not in TAGS:
            raise ValueError("不支持活动或非白名单SVG元素")
        out.text = node.text
        out.tail = node.tail
        for key, val in node.attrib.items():
            if key in {"href", f"{{{XLINK}}}href"} and tag == "image":
                out.set("href", _png(val))
                continue
            if key in NUMBERS:
                ok = NUM.fullmatch(val)
            elif key in {"viewBox", "points", "stroke-dasharray"}:
                components = [v for v in re.split(r"[,\s]+", val.strip()) if v]
                ok = bool(components) and all(NUM.fullmatch(n) for n in components)
                if key == "viewBox":
                    ok = ok and len(components) == 4
            elif key in {"fill", "stroke"}:
                ok = COLOR.fullmatch(val)
            elif key == "fill-rule":
                ok = val in {"evenodd", "nonzero"}
            elif key == "text-anchor":
                ok = val in {"start", "middle", "end"}
            elif key == "d":
                ok = re.fullmatch(r"[MLHVCSQTAZmlhvcsqtaz0-9 .,+eE\s-]+", val) is not None
            else:
                ok = False
            if not ok:
                raise ValueError("SVG含未获准的属性、样式或外部引用")
            out.set(key, val)
        for child in node:
            childtag = child.tag.removeprefix(f"{{{SVG_NS}}}")
            copy(child, ET.SubElement(out, childtag), depth + 1)

    copy(tree, clean)
    return ET.tostring(clean, encoding="utf-8", xml_declaration=True)


def derived_files(products, limit=150):
    """Return (path, bytes, source JSON pointer, kind), skipped-file diagnostics.

    Paths are generated locally. Product keys are data in the manifest, never ZIP paths.
    """
    out = []
    omitted = []
    counts = {"table": 0, "layer": 0, "figure": 0}
    used_bytes = 0

    def save(kind, parts, data):
        nonlocal used_bytes
        used_bytes += len(data)
        if used_bytes > 48_000_000:
            raise ValueError("独立附件超过48MB，请按任务窗口分批导出")
        counts[kind] += 1
        n = counts[kind]
        folders = {"table": ("tables", "csv"), "layer": ("layers", "geojson"), "figure": ("figures", "svg")}
        folder, ext = folders[kind]
        out.append((f"{folder}/{kind}-{n:03}.{ext}", data, parts, kind))

    def walk(value, pointer, depth):
        if depth > 48:
            raise ValueError("产品嵌套超过导出上限")
        if len(out) >= limit:
            if not omitted or omitted[-1]["reason"] != "独立附件达到数量上限，完整产品保留在report.json":
                omitted.append(
                    {"pointer": pointer, "reason": "独立附件达到数量上限，完整产品保留在report.json"}
                )
            return
        if isinstance(value, dict):
            if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
                save("layer", pointer, encoded_json(value))
                return
            for k, v in value.items():
                walk(v, pointer + "/" + str(k).replace("~", "~0").replace("/", "~1"), depth + 1)
        elif isinstance(value, list):
            if value and all(isinstance(row, dict) for row in value):
                save("table", pointer, csv_table(value))
            for i, v in enumerate(value):
                if isinstance(v, (dict, list)):
                    walk(v, pointer + "/" + str(i), depth + 1)
        elif isinstance(value, str) and value.lstrip().startswith(("<svg", "<?xml")):
            try:
                save("figure", pointer, safe_svg(value))
            except ValueError as exc:
                omitted.append({"pointer": pointer, "reason": str(exc)})

    walk(products, "/products", 0)
    return out, omitted
