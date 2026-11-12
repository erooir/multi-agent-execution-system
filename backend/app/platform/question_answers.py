"""Project question answers contain business findings and locatable evidence only."""

import math
import re

from ..casework.delivery_brief import HEAVY, LABELS, findings
from ..casework.document_common import bounded_excerpt
from ..casework.narrative_checks import monthly_delivery_summary
from . import service as s
from .question_events import project_events
from .question_geo import project_geo
from .question_science import project_science

FIELDS = {
    **LABELS,
    "target_year": "目标年",
    "target_extent": "目标年范围",
    "difference": "与基线的差值",
    "baseline_n": "基线样本数",
    "baseline_mean": "基线均值",
    "percent": "差值百分比",
    "ascending_rank": "升序排名",
    "unit": "单位",
    "distance_km": "距离（公里）",
    "scope": "统计范围",
    "agrees": "与报告合计一致",
    "change": "变化",
}


def _text(value, sources):
    text = re.sub(r"〔[^〔〕]*〕", "", str(value))
    for key, source in sources.items():
        if key in text:
            text = text.replace(key, source["name"])
    return text.strip()


def _location(position):
    if not isinstance(position, dict):
        return ""
    labels = {
        "page": "页",
        "row": "行",
        "sheet": "工作表",
        "paragraph": "段落",
        "table": "表格",
        "cue": "字幕段",
        "image_index": "页内图像",
        "start_ms": "起始毫秒",
        "end_ms": "结束毫秒",
        "start": "起始字符",
        "end": "结束字符",
        "line": "行",
        "start_seconds": "开始秒",
        "end_seconds": "结束秒",
        "column": "列",
    }
    parts = [f"{label} {position[k]}" for k, label in labels.items() if k in position]
    bbox = position.get("bbox_xywh") or position.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(n, (int, float)) for n in bbox):
        parts.append("图像区域 " + ", ".join(str(n) for n in bbox))
    elif position.get("image"):
        parts.append("整幅图像")
    return "、".join(parts)


def _table(key, value, sources):
    if not isinstance(value, (list, dict)) or key in HEAVY or not value:
        return None
    all_rows = value if isinstance(value, list) else [value]
    rows = []
    for row in all_rows[:100]:
        if not isinstance(row, dict):
            continue
        clean = {}
        for k, v in row.items():
            if (
                k in HEAVY
                or k.endswith(("_id", "_refs", "_ids", "_sha256"))
                or k in {"formula", "method", "checksum", "signature", "versions"}
            ):
                continue
            if k not in FIELDS and k.isascii():
                continue
            if isinstance(v, str) and len(v) <= 2400 and not v.lstrip().startswith(("<", "data:")):
                clean[FIELDS.get(k, k)] = _text(v, sources)
            elif isinstance(v, (int, float)) and math.isfinite(v):
                clean[FIELDS.get(k, k)] = v
        if clean:
            rows.append(clean)
    if not rows:
        return None
    columns = list(dict.fromkeys(k for row in rows for k in row))[:12]
    return {
        "title": LABELS.get(key, key),
        "columns": columns,
        "rows": [[r.get(k) for k in columns] for r in rows],
        "total_rows": len(all_rows),
    }


def _map(key, value):
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        return None
    # Return only GeoJSON coordinates from the delivered artifact. Drop arbitrary feature properties.
    geometries = []
    for feature in value.get("features", [])[:250]:
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if geometry.get("type") not in {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }:
            continue

        def valid(v, depth=0):
            if isinstance(v, list):
                return depth < 5 and bool(v) and all(valid(c, depth + 1) for c in v)
            return type(v) in (int, float) and math.isfinite(v)

        if valid(coordinates):
            geometries.append(
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": geometry["type"], "coordinates": coordinates},
                }
            )
    return (
        {"title": LABELS.get(key, "地图"), "geojson": {"type": "FeatureCollection", "features": geometries}}
        if geometries
        else None
    )


def render_case(artifact, sources, question, *, summary=False):
    report = artifact["content"]
    case, products = report["case_id"], report["products"]
    presenter = {"B": project_events, "G": project_geo, "T": project_science}.get(case[0])
    business = presenter(case, products, question) if presenter else None
    if business:
        answer = _text(business.get("answer") or "", sources)
        tables = business.get("tables", [])
        unanswered = business.get("missing", [])
        if case == "B05":
            answer = monthly_delivery_summary(products) + "\n" + answer
            limitations = products.get("limitations", [])
            if limitations:
                tables.append(
                    {
                        "title": "统计范围与核验限制（原文）",
                        "columns": ["限制原文", "证据编号"],
                        "rows": [
                            [item.get("text", ""), "、".join(item.get("evidence_ids", []))]
                            for item in limitations
                        ],
                        "total_rows": len(limitations),
                    }
                )
    elif case == "G03":
        answer = "\n".join(_text(note, sources) for note in findings(report))
        tables = [] if summary else [t for k, v in products.items() if (t := _table(k, v, sources))]
        unanswered = []
    else:
        answer, tables, unanswered = "", [], ["当前没有可核验的业务结论。"]
    if not answer and not unanswered:
        unanswered = ["现有材料没有形成可核验的业务结论。"]
    if summary:
        return {"incomplete": bool(unanswered)}
    if report.get("scope") == "sample":
        answer += "\n本次结论仅覆盖缩小样本。"
    for t in tables:
        t["rows"] = [
            [_text(cell, sources) if isinstance(cell, str) else cell for cell in row] for row in t["rows"]
        ]
    maps = [m for k, v in products.items() if (m := _map(k, v))]
    refs = []
    for e in report.get("evidence", []):
        source = sources.get(e.get("source_id"))
        # This is a local result shown to the very requester who ran the question. Membership and
        # frozen inputs are rechecked on every read. Public classification is only required for egress.
        if source and e.get("source_version") == source["source_version"]:
            refs.append(
                {
                    "source": source["name"],
                    "source_id": e.get("source_id"),
                    "page": (e.get("location") or {}).get("page"),
                    "location": _location(e.get("location")),
                    "excerpt": bounded_excerpt(str(e.get("quote", "")), 450),
                    "quote_truncated": bool(e.get("quote_truncated")) or len(str(e.get("quote", ""))) > 450,
                }
            )
        if len(refs) >= 36:
            break
    missing = [_text(g, sources) for g in [*unanswered, *report.get("gaps", [])]]
    missing.extend(
        _text(c["message"], sources) for c in report.get("review", []) if not c["passed"] and c.get("message")
    )
    return {
        "answer": answer or None,
        "incomplete": bool(unanswered),
        "citations": refs,
        "missing": list(dict.fromkeys(missing))[:30],
        "tables": tables,
        "maps": maps,
    }


def render_semantic(artifact, sources):
    value = artifact.get("content") or {}
    if artifact.get("quality_status") != "passed" or not value.get("text") or not value.get("citations"):
        return {"missing": ["资料解读未通过自动审核，当前没有可交付的结论。"]}
    citations = []
    for cite in value["citations"]:
        source = sources.get(cite.get("source_id"))
        if source and source["classification"] == "public" and isinstance(cite.get("quote"), str):
            citations.append(
                {
                    "source": source["name"],
                    "source_id": cite.get("source_id"),
                    "location": _location(cite),
                    "excerpt": bounded_excerpt(cite["quote"], 450),
                    "quote_truncated": len(cite["quote"]) > 450,
                }
            )
    return {
        "answer": _text(value["text"], sources),
        "missing": [_text(x, sources) for x in value.get("missing_information", [])],
        "citations": citations,
    }


def deliver(tx, task, sources, question, *, summary=False):
    partial = task["status"] == "partial"
    for artifact in tx.list("artifacts", task["project_id"], task_id=task["id"]):
        content = artifact.get("content")
        if not isinstance(content, dict) or artifact.get("checksum") != s.digest(content):
            continue
        if artifact["type"] == "case_report" and (partial or artifact.get("quality_status") == "passed"):
            return render_case(artifact, sources, question, summary=summary)
        if artifact["type"] == "text_analysis" and task.get("semantic_evidence"):
            return render_semantic(artifact, sources)
    return {"missing": ["当前没有可核验的业务答案。"], "incomplete": True}
