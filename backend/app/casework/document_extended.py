"""Extended document analyses for the added case catalogue.

These consume *already structured* upstream records (image要素/参数/报告节点), never raw
media. They keep evidence positions and report gaps; no invented values, no canned answers.
"""

from .document_common import Run, items, number, string, value


def _feature_values(r):
    """读取可见要素，兼容标量 feature / visible_feature / visible_features 列表 / 要素。"""
    found = []
    for key in ("feature", "visible_feature", "visible_features", "要素"):
        v = r.get(key) if isinstance(r, dict) else None
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            found.extend(str(x).strip() for x in v if str(x).strip())
        elif str(v).strip():
            found.append(str(v).strip())
    return found


def _visible_features(run):
    """Shared element-catalog builder for image-annotation cases (B12/T15/G12 style)."""
    cards = []
    for r in run.records:
        kind = string(r, "kind", "role")
        if kind in {"reference", "catalog", "条目", "元数据", "ocr"}:
            continue
        features = _feature_values(r.get("fields", {})) + _feature_values(r)
        if not features:
            continue
        box = value(r, "bbox", "location_in_image", "box")
        valid = (
            isinstance(box, list)
            and len(box) == 4
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0 for n in box)
        )
        for feature in dict.fromkeys(features):
            cards.append(
                {
                    "feature": feature,
                    "location_in_image": box if valid else None,
                    "confidence": string(r, "confidence") or "未标注",
                    "evidence_ids": [run.cite(r)],
                }
            )
            if not valid:
                run.gap(f"要素「{feature}」缺有效图像定位，仅作候选")
    return cards


def poster_catalog(run):
    """B12: OCR 转录 + 档案要素抽取（消费上游 OCR 后的要素记录）。"""
    cards = _visible_features(run)
    refs = []
    for r in run.records:
        if string(r, "kind", "role") not in {"reference", "catalog", "馆藏说明"}:
            continue
        refs.append(
            {
                "field": string(r, "field", "name") or "档案要素",
                "value": string(r, "value", "text"),
                "evidence_ids": [run.cite(r)],
            }
        )
    ocr = [run.excerpt(r) for r in run.records if string(r, "kind", "role") == "ocr"]
    if not cards and not ocr:
        run.gap("未见可编目的图像要素或 OCR 文本")
    if not refs:
        run.gap("缺馆藏条目元数据，档案编号/年代/版权不可核验")
    return {
        "image_catalog": cards,
        "overlay_layers": {"ocr_lines": ocr, "reference_fields": refs},
    }


def parameter_sheet(run):
    """T12: 公开技术参数结构化速读（消费上游抽取的参数记录）。"""
    rows = []
    for r in run.records:
        kind = string(r, "kind", "role")
        if kind and kind not in {"parameter", "param", "参数"}:
            continue  # 图表等非参数行由下方 figure_cards 处理
        name = string(r, "parameter", "param", "参数名称")
        val = value(r, "value", "数值")
        if not name or val is None:
            run.gap("参数行缺名称或数值，未纳入速读表")
            continue
        rows.append(
            {
                "parameter": name,
                "value": val,
                "unit": string(r, "unit", "单位") or None,
                "scope": string(r, "scope", "口径类别") or None,
                "evidence_ids": [run.cite(r)],
            }
        )
    cards = []
    for r in run.records:
        if string(r, "kind", "role") not in {"figure", "chart", "图表", "table"}:
            continue
        cards.append(run.excerpt(r, label=string(r, "figure_id", "图号")))
    if not rows:
        run.gap("没有可核对的数值参数")
    groups = {}
    for row in rows:
        groups.setdefault(row["scope"] or "未分类", []).append(row)
    return {
        "figure_cards": cards,
        "comparison": {"groups": groups, "count": len(rows)},
    }


def technical_photo_catalog(run):
    """T15: 技术照片可见要素标注与型号依据核验（消费上游视觉标注记录）。"""
    cards = _visible_features(run)
    basis = []
    for r in run.records:
        kind = string(r, "kind", "role")
        if kind not in {"identification", "型号依据", "reference", "catalog"}:
            continue
        basis.append(
            {
                "designation": string(r, "designation", "model", "型号"),
                "basis_from_image": string(r, "basis_from_image", "图像依据"),
                "basis_from_text": string(r, "basis_from_text", "文字依据"),
                "evidence_ids": [run.cite(r)],
            }
        )
    if not cards:
        run.gap("未见带定位的可见技术要素")
    if not basis:
        run.gap("缺型号依据记录，不能据照片自行认定型号")
    return {
        "image_catalog": cards,
        "overlay_layers": {"model_basis": basis},
    }


def cross_report_review(run):
    """T16: 跨报告进度与建议状态复盘（消费上游抽取的报告节点记录）。"""
    nodes = []
    for r in run.records:
        topic = string(r, "topic", "主题")
        report = string(r, "report_id", "source_id", "报告编号")
        status = string(r, "status", "状态")
        if not topic or not report:
            run.gap("报告节点缺主题或报告编号")
            continue
        nodes.append(
            {
                "topic": topic,
                "report": report,
                "published_date": string(r, "published_date", "发布日期") or None,
                "data_as_of": string(r, "data_as_of", "数据截至日") or None,
                "status": status or None,
                "recommendation": string(r, "recommendation", "建议编号") or None,
                "evidence_ids": [run.cite(r)],
            }
        )
    portfolio = []
    by_report = {}
    for n in nodes:
        by_report.setdefault(n["report"], []).append(n)
    for report, items_ in sorted(by_report.items()):
        portfolio.append({"report": report, "topics": items_})
    if not portfolio:
        run.gap("没有可复盘的报告节点")
    # 建议状态演进：同一建议在不同报告中的状态变化
    rec = {}
    for n in nodes:
        if n["recommendation"]:
            rec.setdefault(n["recommendation"], []).append(
                {"report": n["report"], "status": n["status"]}
            )
    return {
        "portfolio": portfolio,
        "organization_graph": {"recommendation_status": rec},
        "theme_summary": {"topics": sorted({n["topic"] for n in nodes})},
    }
