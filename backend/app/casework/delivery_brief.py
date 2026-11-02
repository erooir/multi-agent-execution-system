"""A readable, bounded Chinese brief over the actual audited observations."""

import io
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

LABELS = {
    "extent": "海冰范围",
    "observations": "分区观察记录",
    "report_window": "报告时段",
    "context": "背景与限制",
    "commitments": "承诺事项",
    "documents": "来源文件",
    "missing_details": "待核信息",
    "image_catalog": "图像编目",
    "overlay_layers": "标注图层",
    "timed_index": "回听索引",
    "topics": "专题",
    "differences": "逐字稿差异",
    "transcript_only": "仅见于逐字稿的内容",
    "glossary": "术语解释",
    "monthly": "逐月统计",
    "reconciliation": "总量核对",
    "trend": "月度趋势",
    "limitations": "适用范围",
    "clause_map": "条款对照",
    "unmatched": "未匹配条款",
    "commentary": "说明与评论",
    "comparison": "比较结果",
    "coverage_notes": "覆盖范围",
    "matrix": "证据矩阵",
    "version_timeline": "版本时间线",
    "unverified": "待核事实",
    "timeline": "观测时间线",
    "unknown_time": "时刻不明",
    "variants": "记录差异",
    "milestones": "项目里程碑",
    "brief": "事件摘要",
    "route_history": "技术路线历史",
    "route_changes": "路线变更",
    "unverified_stages": "未确认阶段",
    "portfolio": "项目概览",
    "organization_graph": "机构关系",
    "theme_summary": "主题汇总",
    "figure_cards": "图表",
    "field_coverage": "字段覆盖",
    "trend_series": "样本趋势",
    "applicability": "适用范围",
    "status_timeline": "状态变化",
    "citation_impact": "引用影响",
    "independent_research": "独立研究",
    "hotspots": "热点清单",
    "daily": "每日统计",
    "points": "热点图层",
    "exceptions": "异常项",
    "panels": "影像对照",
    "changes": "变化注记",
    "unreadable": "不可判区",
    "summary": "摘要",
    "annual_series": "年度范围",
    "anomaly": "基线比较",
    "trend_svg": "趋势图",
    "flood_layer": "水域图层",
    "road_exposure": "道路暴露",
    "areas": "分区面积",
    "coverage": "覆盖范围",
    "buildings": "建筑清单",
    "blocks": "街区汇总",
    "layer": "建筑图层",
    "comparison_panels": "前后影像",
    "uncertain": "不可确认项",
    "scope": "范围",
    "land_cover": "覆盖类型",
    "transitions": "转移矩阵",
    "new_built": "新增建设候选区",
    "method": "方法",
    "results": "实验记录",
    "sample_points": "样点图层",
    "quality": "数据质量",
    "retrospective": "回溯调查",
    "island_matrix": "分岛证据",
    "map": "地图",
    "uncertainty": "不确定项",
    "od_comparison": "起终点比较",
    "route_layer": "路线图层",
    "exclusions": "排除边",
    "notice_audit": "公告核查",
    "exposure": "耕地暴露",
    "grid_exposure": "网格暴露",
    "max_union": "多期空间并集",
    "repeat_coverage": "重复观测区",
    "priority": "候选分级",
    "map_view": "观测示意图",
    "source_notes": "报告背景",
    "date": "日期",
    "month": "月份",
    "year": "年份",
    "site_id": "站点",
    "annual_years": "年份数",
    "start_date": "开始日",
    "end_date": "截止日",
    "value": "数值",
    "unit": "单位",
    "region": "地区",
    "count": "本期",
    "previous": "上期",
    "change": "变化",
    "deaths": "死亡",
    "injured": "受伤",
    "total": "合计",
    "basis": "口径",
    "complete_month": "完整月份",
    "published_date": "发布日期",
    "observed_until": "观测截止",
    "event_date": "事件日期",
    "report_number": "报告编号",
    "timezone": "时区",
    "period": "时期",
    "statement": "来源陈述",
    "question": "问题",
    "conclusion": "结论",
    "status": "状态",
    "quote": "原文",
    "text": "摘录",
    "topic": "主题",
    "title": "标题",
    "project": "项目",
    "project_id": "项目编号",
    "role": "来源角色",
    "citation": "引用",
    "event": "事件",
    "parties": "参与方",
    "effective_date": "生效日期",
    "quantity": "数量",
    "impact": "影响",
    "region_id": "区域编号",
    "source_id": "资料编号",
    "source_version": "版本",
    "asset_id": "资料编号",
    "confidence": "置信信息",
    "observation": "观察",
    "interpretation": "解释",
    "translation": "译文",
    "translation_zh": "中文译文",
    "translation_basis": "译文依据",
    "translation_method": "翻译方式",
    "translation_status": "翻译状态",
    "translation_note": "翻译说明",
    "speaker": "发言人",
    "time_ms": "毫秒",
    "start_ms": "起始毫秒",
    "end_ms": "结束毫秒",
    "turn_type": "发言类型",
    "location": "位置",
    "page": "页",
    "row": "行",
    "sheet": "工作表",
    "paragraph": "段",
    "table": "表",
    "image": "图像",
    "segment_id": "片段",
    "from_date": "开始日期",
    "to_date": "结束日期",
    "area_m2": "面积（平方米）",
    "metric": "指标",
    "type": "类型",
    "outcome": "结果",
    "kind": "类别",
    "figure": "图号",
    "scenario": "情景",
    "indicator": "指标",
    "event_type": "事件类别",
    "normalized_value": "规范化数值",
    "version": "版本",
    "precision": "精度",
    "purpose": "目的",
    "objective": "目标",
    "visible_features": "可见特征",
    "candidates": "候选",
    "reason": "原因",
    "name": "名称",
    "term": "术语",
    "meaning": "释义",
    "section": "章节",
    "claim": "主张",
    "status_reason": "状态依据",
    "fields": "字段",
    "record_count": "记录数",
    "unique_samples": "样品数",
    "unique_sites": "站点数",
    "delta": "差值",
    "scope_note": "适用范围",
    "note": "说明",
    "before": "旧版",
    "after": "新版",
    "before_text": "旧版原文",
    "after_text": "新版原文",
    "evidence_type": "证据类别",
    "evidence_ids": "证据编号",
    "key": "标识",
    "reported": "转述",
    "assessed": "材料评估",
    "observed": "材料观察",
}
STATUSES = {
    "observed": "有观测",
    "missing_or_conflict": "缺测或冲突",
    "succeeded": "已完成",
    "partial": "部分完成",
    "failed": "未完成",
    "cancelled": "已取消",
    "reachable": "可达",
    "unreachable_in_snapshot": "快照内未发现可达路径",
    "unconfirmed": "待确认",
    "unknown": "未知",
    "new": "新增",
    "matched": "与前期匹配",
    "detected": "已检出",
    "nondetect": "未检出",
    "pending_qc": "待质控",
    "new_visible": "新增可见损伤",
    "preexisting": "既有损伤",
    "no_new_visible": "无新增可见损伤",
    "abstain": "证据不足",
    "supported_candidate": "有来源支持的候选",
    "source_record": "来源记录",
}
INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
HEAVY = {
    "png",
    "geometry",
    "coordinates",
    "features",
    "svg",
    "trend_svg",
    "pixel_overlay_svg",
    "map_view",
    "source_id",
    "source_version",
    "evidence_ids",
    "refs",
}


def clean(value, limit=200):
    s = INVALID_XML.sub("", str(value)).strip()
    if s.lstrip().startswith(("<svg", "<?xml", "data:image/")) or (
        len(s) > 150 and re.fullmatch(r"[A-Za-z0-9+/=]+", s)
    ):
        return "图件见附件"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def show(value, limit=160):
    if value is None:
        return "未给出"
    if type(value) is bool:
        return "是" if value else "否"
    if isinstance(value, float):
        return format(value, ".8g")
    if isinstance(value, dict):
        parts = [
            f"{LABELS.get(k, k)}：{show(v, 45)}" for k, v in value.items() if k not in HEAVY and v is not None
        ][:3]
        return clean("；".join(parts) or "见完整产品", limit)
    if isinstance(value, list):
        return (
            clean("、".join(show(x, 45) for x in value[:3]) + (" 等" if len(value) > 3 else ""), limit)
            or "无"
        )
    return clean(STATUSES.get(str(value), str(value)), limit)


def refs(item):
    ids = item.get("evidence_ids", []) if isinstance(item, dict) else []
    return list(dict.fromkeys(str(i) for i in ids)) if isinstance(ids, list) else []


def cite(ids):
    ids = list(dict.fromkeys(ids))
    return (
        "〔" + ", ".join(clean(i, 32) for i in ids[:4]) + (" 等" if len(ids) > 4 else "") + "〕"
        if ids
        else ""
    )


def row_text(row, limit=165):
    if not isinstance(row, dict):
        return show(row, limit)
    preferred = (
        "question",
        "event",
        "topic",
        "project",
        "region",
        "date",
        "published_date",
        "month",
        "figure",
        "claim",
        "speaker",
        "title",
        "scope",
        "statement",
        "observation",
        "text",
        "quote",
        "outcome",
        "status",
        "value",
        "unit",
        "reason",
        "interpretation",
        "translation",
        "translation_zh",
    )
    keys = list(dict.fromkeys([k for k in preferred if k in row] + list(row)))
    parts = []
    for k in keys:
        if k in HEAVY or row[k] in (None, "", [], {}):
            continue
        parts.append(f"{LABELS.get(k, k)}：{show(row[k], 75)}")
        if len(parts) == 5:
            break
    return clean("；".join(parts), limit) + cite(refs(row))


def findings(report):
    products = report["products"]
    code = report["case_id"]
    notes = []
    if code == "B01":
        for r in products.get("observations") or []:
            notes.append(
                f"{show(r.get('region'))}在本报告范围内，本期记录{show(r.get('count'))}起，上期{show(r.get('previous'))}起，差额为{show(r.get('change'))}起。"
                + cite(refs(r))
            )
        window = products.get("report_window") or {}
        for k in ("published_date", "observed_until", "timezone"):
            if isinstance(window.get(k), dict):
                notes.append(f"{LABELS[k]}：{show(window[k].get('value'))}。" + cite(refs(window[k])))
    elif code == "B05":
        for r in products.get("reconciliation") or []:
            calc = r.get("computed")
            reported = r.get("reported")
            s = f"{show(r.get('scope') or r.get('source_id'))}："
            if isinstance(calc, list) and len(calc) == 2:
                s += f"月度记录加总为死亡{show(calc[0])}人、受伤{show(calc[1])}人。"
            else:
                s += "现有月度记录不足以独立复算总数。"
            for pair in reported or []:
                if len(pair) == 2:
                    s += f"材料列示死亡{show(pair[0])}人、受伤{show(pair[1])}人。"
            s += "同口径总数已核对一致。" if r.get("agrees") else "月度合计与报告口径尚未核对一致。"
            notes.append(s + cite(refs(r)))
        months = [
            r.get("month") for r in products.get("monthly") or [] if r.get("complete_month") is not True
        ]
        if months:
            notes.append(f"{show(months, 100)}的覆盖未确认为完整自然月，不直接进行整月趋势比较。")
    elif code == "G03":
        r = products.get("anomaly")
        if r:
            rows = products.get("annual_series") or []
            ref_ids = [i for row in rows if row["year"] == r.get("target_year") for i in refs(row)]
            unit = show(r.get("unit"))
            if unit in {"Mkm²", "Mkm^2"}:
                unit = "百万平方公里"
            target = r.get("target_extent")
            base = r.get("baseline_mean")
            window = r.get("baseline_years") or []
            notes.append(
                f"目标年{show(r.get('target_year'))}的北半球9月平均范围为{show(target)} {unit}。"
                + cite(ref_ids)
            )
            if len(window) == 2 and base is not None:
                note = f"{window[0]}至{window[1]}年的基线由{show(r.get('baseline_n'))}个年度记录计算，均值为{show(base)} {unit}。"
                diff = r.get("difference")
                pct = r.get("percent")
                if diff is not None:
                    side = "低于" if diff < 0 else "高于" if diff > 0 else "等于"
                    note += f"目标年{side}基线" + (f"{abs(diff):.4g} {unit}" if diff else "")
                    if pct is not None and diff:
                        note += f"（相差{abs(pct):.4g}%）"
                    note += "。"
                notes.append(note + "逐年数值及来源行保留在年度数据附件中。")
            if r.get("ascending_rank") is not None:
                notes.append(
                    f"在截止目标年的同口径年度样本中，由小到大排名第{show(r['ascending_rank'])}。月均范围不能解释为单日最小值。"
                )
    elif code == "G07":
        q = products.get("quality")
        if q:
            notes.append(
                f"指定窗口内共有{show(q.get('record_count'))}条实验室结果，分属{show(q.get('unique_samples'))}个样品、{show(q.get('unique_sites'))}个站点。记录条数不能替代站点数。"
            )
            missed = q.get("missing_coordinate_refs", [])
            notes.append(
                f"{len(missed)}条结果未能定位到坐标；未检出项不作为浓度零值，未采样区域不据此判定安全。"
                + cite(missed)
            )
    elif code == "G09":
        for r in (products.get("od_comparison") or [])[:6]:
            paths = r.get("candidates") or []
            s = f"{show(r.get('from'))}至{show(r.get('to'))}在给定快照内{show(r.get('status'))}。"
            if paths:
                s += (
                    f"首选候选距离{show(paths[0].get('distance_km'))}公里，预计用时{show(paths[0].get('estimated_time_min'))}分钟。"
                    + cite(refs(paths[0]))
                )
            else:
                s += cite(refs(r))
            notes.append(s)
    if not notes:
        for key, value in products.items():
            if (
                key in HEAVY
                or not value
                or isinstance(value, dict)
                and value.get("type") == "FeatureCollection"
            ):
                continue
            examples = value[:2] if isinstance(value, list) else [value]
            text = "；".join(row_text(v) for v in examples)
            if text:
                notes.append(f"{LABELS.get(key, key)}：{text}。")
            if len(notes) >= 6:
                break
    return notes[:8] or ["当前材料尚未形成有来源支撑的结论，请参阅下列缺口与审查记录。"]


def scope_text(report):
    scope = report.get("scope")
    if scope == "project":
        return "根据此次问题和项目内实际可用的资料核查。"
    if scope == "sample":
        return "缩小样本验证，不代表完整参考用例验收。"
    if scope != "reference":
        return "历史产物未标注范围，不能作为完整参考批次证明。"
    batch = report.get("reference_batch") or {}
    if not batch.get("registered"):
        return "指定参考批次，未登记可信清单，须结合实际审核结果。"
    return f"指定参考批次，已登记清单版本 {show(batch.get('version'), 44)}，仍须结合实际审核结果。"


def document_bytes(report, metadata):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = Cm(2.1)
    sec.bottom_margin = Cm(1.9)
    for name, size in (("Normal", 11), ("Title", 21), ("Subtitle", 10), ("Heading 1", 15), ("Heading 2", 12)):
        style = doc.styles[name]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.italic = False
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        pf = style.paragraph_format
        pf.line_spacing = 1.32
        pf.space_after = Pt(6)
        for border in list(style.element.iter(qn("w:pBdr"))):
            border.getparent().remove(border)
    doc.core_properties.title = clean(report["title"], 90) + "研究简报"
    doc.core_properties.author = "航智平台"
    doc.add_paragraph(doc.core_properties.title, "Title")
    doc.add_paragraph("验收范围：" + scope_text(report))
    status = show(metadata.get("status") or "未提供任务终态")
    doc.add_paragraph(f"用例 {clean(report['case_id'], 20)}　任务状态：{status}", "Subtitle")
    if metadata.get("name"):
        doc.add_paragraph("任务：" + clean(metadata["name"], 140))
    checks = report["review"]
    gaps = report["gaps"]
    failed = [r for r in checks if not r.get("passed")]
    if any(r.get("hard") for r in failed):
        doc.add_paragraph("独立审核发现硬性不一致。本简报记录待核候选及问题，不能作为已核实结论。")
    elif failed or gaps:
        doc.add_paragraph("现有材料仅支持部分结果。下文保留来源可定位的发现，未确认事项单列。")
    else:
        doc.add_paragraph("以下结果依据此次任务冻结的资料及版本生成，已通过本次报告所列自动检查。")
    doc.add_heading("主要发现", 1)
    hard_failure = any(r.get("hard") for r in failed)
    found = (
        ["候选数据未通过强制核验，暂不能据此形成主要发现。详见审核明细。"]
        if hard_failure
        else findings(report)
    )
    for text in found:
        doc.add_paragraph(clean(text, 700))
    if not hard_failure:
        _product_preview(doc, report)
    doc.add_heading("证据与适用范围", 1)
    if gaps:
        for gap in gaps[:8]:
            doc.add_paragraph(clean(gap, 240))
        if len(gaps) > 8:
            doc.add_paragraph(f"共{len(gaps)}项缺口，完整清单见report.json。")
    else:
        doc.add_paragraph("本次分析未记录额外材料缺口。结论限于交付资料、统计时间窗及输入规则。")
    translations = _translations(report["products"])
    if translations:
        doc.add_paragraph(
            "译文仅用于辅助理解。以下保留输入或产品给出的译文说明；未标明来源的译文不能作为权威翻译。"
        )
        for note in translations[:3]:
            doc.add_paragraph(clean(note, 150))
    readings = report.get("reading_aids", [])
    notes = report.get("reading_notes", [])
    if readings or notes:
        doc.add_heading("机译阅读索引", 2)
        doc.add_paragraph("以下为辅助读法，以对应证据的原文为准。完整译文、方法和未译原因见report.json。")
        by_id = {e["id"]: e for e in report["evidence"]}
        for entry in readings[:8]:
            source = by_id.get(entry.get("evidence_id"), {})
            place = "，".join(
                f"{LABELS.get(k, k)} {show(v, 24)}" for k, v in source.get("location", {}).items()
            )
            doc.add_paragraph(
                f"〔{clean(entry.get('evidence_id', ''), 32)}〕 {clean(entry.get('text_zh', ''), 400)}"
                f" （{clean(entry.get('method', '机译'), 60)}，{place or '位置见证据索引'}）"
            )
        for reason in notes[:8]:
            doc.add_paragraph("未采用译文：" + clean(reason, 160))
    evidence = report["evidence"]
    if evidence:
        rows = []
        cited = {
            i for ids in re.findall(r"〔([^〕]+)〕", " ".join(found)) for i in re.split(r"[, ]+", ids) if i
        }
        for e in sorted(evidence, key=lambda x: x["id"] not in cited)[:6]:
            loc = e["location"]
            place = "；".join(f"{LABELS.get(k, k)} {show(v, 34)}" for k, v in loc.items())
            rows.append(
                [
                    e["id"],
                    f"{clean(e['source_id'], 30)}\n版本 {clean(e['source_version'], 22)}",
                    place + (("\n" + clean(e["quote"], 52)) if e.get("quote") else ""),
                ]
            )
        _table(doc, ["证据编号", "资料与版本", "来源位置或原文摘录"], rows, [2.6, 5.1, 8.7])
        doc.add_paragraph(
            f"正文摘列{len(rows)}项证据。全部{len(evidence)}项的完整编号、版本、位置和摘录见evidence.csv。"
        )
    elif not gaps:
        doc.add_paragraph("报告没有可核对的来源记录。")
    doc.add_heading("自动审核", 2)
    doc.add_paragraph(
        f"本次记录{len(checks)}项检查，其中{len(checks) - len(failed)}项通过。"
        + (
            "未通过事项："
            + "；".join(clean(c.get("message", c.get("name", "")), 90) for c in failed[:4])
            + "。"
            if failed
            else ""
        )
    )
    doc.add_paragraph(
        "完整产品、检查明细和附件对应关系见report.json与manifest.json。表格和空间图层可直接复算。"
    )
    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    foot.add_run(f"航智平台  {clean(report['case_id'], 20)}  |  ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    foot._p.append(field)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _translations(obj):
    out = []

    def visit(v):
        if isinstance(v, list):
            for r in v:
                visit(r)
        elif isinstance(v, dict):
            keys = [k for k in v if ("translat" in str(k) or "译文" in str(k)) and v[k]]
            if keys:
                note = "；".join(f"{LABELS.get(k, k)}：{show(v[k], 80)}" for k in keys) + cite(refs(v))
                if note not in out and len(out) < 3:
                    out.append(note)
            for r in v.values():
                if isinstance(r, (dict, list)):
                    visit(r)

    visit(obj)
    return out


def _product_preview(doc, report):
    products = report["products"]
    if report["case_id"] == "G03" and _ice_figure(doc, products):
        return
    for key, value in products.items():
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            continue
        if key in {"panels", "changes"} or key.startswith("source_notes"):
            continue
        cols = []
        for row in value[:3]:
            for k, v in row.items():
                if (
                    k not in cols
                    and k not in HEAVY
                    and v is not None
                    and not isinstance(v, (list, dict))
                    and len(str(v)) <= 250
                ):
                    cols.append(k)
        cols = cols[:4]
        if not cols:
            continue
        count = min(len(value), 5)
        doc.add_heading(LABELS.get(key, clean(key, 55)) + "摘录", 2)
        _table(
            doc,
            [LABELS.get(c, clean(c, 28)) for c in cols],
            [[show(r.get(c), 60) for c in cols] for r in value[:count]],
            [16.4 / len(cols)] * len(cols),
        )
        if len(value) > count:
            doc.add_paragraph(f"此处列出前{count}行，完整{len(value)}行见表格附件。")
        break


def _ice_figure(doc, products):
    points = products.get("annual_series") or []
    stats = products.get("anomaly") or {}
    known = [(int(r["year"]), float(r["extent"])) for r in points if r.get("extent") is not None]
    if len(known) < 2:
        return False
    x0, x1 = min(y for y, _ in known), max(y for y, _ in known)
    if x1 == x0:
        return False
    base = stats.get("baseline_mean")
    values = [v for _, v in known] + ([float(base)] if base is not None else [])
    low, high = min(values), max(values)
    image = Image.new("RGB", (940, 275), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=19)
    left, right, top, bottom = 75, 900, 19, 225

    def xy(y, v):
        return left + (y - x0) * (right - left) / (x1 - x0), bottom - (v - low) * (bottom - top) / (
            high - low or 1
        )

    draw.line([(left, top), (left, bottom), (right, bottom)], fill="#5d7583", width=2)
    for label, at in ((f"{high:.2f}", top), (f"{low:.2f}", bottom)):
        draw.text((3, at - 8), label, font=font, fill="#263f4b")
    draw.text((left, bottom + 7), str(x0), font=font, fill="#263f4b")
    draw.text((right - 42, bottom + 7), str(x1), font=font, fill="#263f4b")
    if base is not None:
        y = xy(x0, base)[1]
        for x in range(left, right, 16):
            draw.line((x, y, min(x + 8, right), y), fill="#98700c", width=2)
    prev = None
    for row in points:
        if row.get("extent") is None:
            prev = None
            continue
        x, y = xy(row["year"], row["extent"])
        if prev:
            draw.line([prev, (x, y)], fill="#17618c", width=3)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#17618c")
        prev = (x, y)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    doc.add_heading("年度变化", 2)
    doc.add_picture(buf, width=Cm(16))
    doc.add_paragraph(
        f"单位：{show(stats.get('unit'))}。每点为一个年度的9月平均范围；虚线表示有数据支持的基线。逐年数值与引用见表格附件。"
    )
    return True


def _table(doc, headers, rows, widths):
    t = doc.add_table(rows=0, cols=len(headers))
    t.autofit = False
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement("w:" + side)
        for k, v in (("val", "single"), ("sz", "5"), ("color", "D9D9D9")):
            b.set(qn("w:" + k), v)
        borders.append(b)
    t._tbl.tblPr.append(borders)
    for col, width in zip(t.columns, widths, strict=True):
        col.width = Cm(width)
    for n, values in enumerate([headers, *rows]):
        row = t.add_row()
        rp = row._tr.get_or_add_trPr()
        rp.append(OxmlElement("w:cantSplit"))
        if n == 0:
            rp.append(OxmlElement("w:tblHeader"))
        for i, value in enumerate(values):
            c = row.cells[i]
            c.width = Cm(widths[i])
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            bg = OxmlElement("w:shd")
            bg.set(qn("w:fill"), "254962" if n == 0 else "F2F5F7" if n % 2 else "FFFFFF")
            c._tc.get_or_add_tcPr().append(bg)
            p = c.paragraphs[0]
            if n == 0:
                p.paragraph_format.keep_with_next = True
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.space_before = Pt(3)
            run = p.add_run(clean(value, 160))
            run.font.size = Pt(9.5)
            run.bold = n == 0
            if n == 0:
                run.font.color.rgb = RGBColor(255, 255, 255)
