"""Evidence-first specialist catalogue. Profiles express tool contracts, never stored case answers."""

import re
from functools import partial

from . import document_compare as compare
from . import document_media as media
from . import document_numbers as numeric
from . import document_science as science
from .document_audit import audit_products
from .document_common import Run, string, value

__all__ = ["CATALOG", "analyze", "audit_products"]


def _profile(title, inputs, outputs, experts, columns):
    return {
        "title": title,
        "inputs": inputs.split("|"),
        "outputs": outputs.split(),
        "specialists": experts.split("|"),
        "record_fields": columns.split(),
        "mode": "local",
    }


CATALOG = {
    "B01": _profile(
        "区域观察统计快报",
        "带页码的日报正文或区域数值记录|统计窗口及民用事项",
        "observations report_window context",
        "报告检索|统计复核|来源审查",
        "region current previous report_number published_date observed_until timezone",
    ),
    "B02": _profile(
        "公告承诺与执行条件核读",
        "公告和声明正文或定位OCR|各文件角色及发布时间",
        "commitments documents missing_details",
        "文件归档|承诺抽取|来源审查",
        "kind parties published_date topic commitment effective_date",
    ),
    "B03": _profile(
        "历史影像与馆藏记录编目",
        "具备图片编号的已知观察/标注|同编号档案记录及出处",
        "image_catalog overlay_layers",
        "图像编目|馆藏匹配|来源审查",
        "kind image_id visible_features candidates bbox coordinate_space width height model_name url",
    ),
    "B04": _profile(
        "会议问答与时间码对照",
        "带真实时间码的发言/字幕|独立逐字稿及说话人信息",
        "timed_index topics differences transcript_only glossary",
        "媒体对齐|发言索引|证据审查",
        "kind media_id start_ms end_ms speaker turn_type topic utterance translation_zh hearing",
    ),
    "B05": _profile(
        "月度统计复算与覆盖核查",
        "月度记录和起止日期|同口径报告总数及统计限制",
        "monthly reconciliation trend limitations",
        "统计抽取|序列复算|来源审查",
        "month start_date end_date deaths injured basis kind scope",
    ),
    "B06": _profile(
        "历史声明条文版本对照",
        "标注旧版与新版的可读条款|术语解释或条款映射",
        "clause_map unmatched commentary",
        "条款编目|版本对齐|证据审查",
        "side clause_id topic match_id clause_text translation_zh ocr_uncertainty",
    ),
    "B07": _profile(
        "双期民生统计口径比较",
        "两期分地区指标|定义、范围、方法、单位和截至日",
        "comparison coverage_notes",
        "统计抽取|口径比较|来源审查",
        "indicator region value cutoff published_date definition coverage method unit",
    ),
    "B08": _profile(
        "跨机构事实与认定层次核查",
        "问题列表|调查与司法文件及其角色",
        "matrix version_timeline unverified",
        "问题检索|证据矩阵|来源审查",
        "kind question_id question role statement scope published_date evidence_type value",
    ),
    "B09": _profile(
        "历史档案多时间线复盘",
        "可定位的档案/讲话/录音记录|事件、形成、公开和解密时间",
        "timeline unknown_time variants",
        "档案编目|时间对齐|来源审查",
        "event_id event event_date formed_at published_date declassified_at timezone time_precision channel recipient",
    ),
    "B10": _profile(
        "事件报道版本与来源追踪",
        "核查问题|简报与调查文件的分版本记述",
        "matrix version_timeline unverified",
        "版本归档|证据矩阵|来源审查",
        "kind question_id question role statement scope published_date evidence_type value",
    ),
    "T01": _profile(
        "试验研究证据阅读卡",
        "论文正文及文献信息|试验与模型结果和局限",
        "literature_card bibliography interpretation",
        "文献抽取|方法辨析|来源审查",
        "title authors institution doi published_date research_question comparison measurement model_result limitation",
    ),
    "T02": _profile(
        "项目里程碑去重与更新",
        "历史里程碑|新发布记录及事件日期",
        "milestones unmatched brief",
        "事件提取|里程碑归并|来源审查",
        "project event_type event_date published_date role outcome",
    ),
    "T03": _profile(
        "研发路线快照对比",
        "同项目的历史与新路线|阶段、日期及技术主题",
        "route_history route_changes unverified_stages",
        "项目匹配|版本对齐|来源审查",
        "project feature date value stage",
    ),
    "T04": _profile(
        "研究项目组合与机构关系",
        "带稳定项目编号的事实表|机构/主题词表和资金口径",
        "portfolio organization_graph theme_summary",
        "项目归一|关系构图|统计复核",
        "project_id objective start end coordinator participants theme currency total_cost contribution",
    ),
    "T05": _profile(
        "图表情景与标注值核读",
        "带图号的正文、图注或已知标注|单位、情景与数值来源",
        "figure_cards comparison",
        "图表索引|标注值核查|来源审查",
        "figure title unit year scenario value value_source value_kind",
    ),
    "T06": _profile(
        "技术标准草案与定稿差异",
        "含条款编号的草案与定稿|关注项与解释/评论角色",
        "clause_map unmatched commentary",
        "条款编目|版本对齐|来源审查",
        "side clause_id topic match_id clause_text published_date effective_date kind",
    ),
    "T07": _profile(
        "访谈专题与回听索引",
        "带真实时间码的转录/字幕|术语表与可选独立逐字稿",
        "timed_index topics differences transcript_only glossary",
        "媒体对齐|专题索引|证据审查",
        "kind media_id start_ms end_ms speaker topic utterance translation_zh hearing term meaning",
    ),
    "T08": _profile(
        "研发项目收尾证据复盘",
        "项目方、审计、研究三类来源|问题清单与各来源公开时间",
        "matrix version_timeline unverified",
        "问题检索|多方归因|来源审查",
        "kind question_id question role statement basis published_date scope",
    ),
    "T09": _profile(
        "研究样本趋势与适用边界",
        "多循环样本与字段单位|论文及测试条件",
        "field_coverage trend_series applicability",
        "样本核读|趋势复算|适用性审查",
        "battery_id cycle capacity capacity_unit resistance resistance_unit load temperature kind conditions population",
    ),
    "T10": _profile(
        "科研状态变化与引用依赖核查",
        "明确成果标识和状态记录|简报逐条引用及独立证据",
        "status_timeline citation_impact independent_research",
        "状态追踪|依赖核查|来源审查",
        "kind doi date status claim_id statement cites supports_claims",
    ),
}


def commitments(run):
    subjects = {
        "cessation": r"ceas(?:e|ing|ation)|停止敌对|停火",
        "humanitarian": r"humanitarian|人道",
        "services": r"(?:public|basic|essential)\s+service|restor(?:e|ation)|民生|服务恢复",
    }
    out = []
    docs = []
    unspecified = []
    for r in run.records:
        if string(r, "kind") == "document" or value(r, "parties", "published_date"):
            docs.append(
                {
                    "role": value(r, "kind"),
                    "name": value(r, "document_name"),
                    "parties": value(r, "parties"),
                    "date": value(r, "published_date"),
                    "evidence_ids": [run.cite(r)],
                }
            )
    for topic, pattern in subjects.items():
        rows = [
            run.excerpt(
                r,
                commitment=string(r, "commitment"),
                effective_date=value(r, "effective_date"),
                basis="source_record",
            )
            for r in run.records
            if string(r, "topic") == topic and string(r, "commitment")
        ]
        if not rows:
            rows = run.find([pattern], 6 if run.retry else 3)
            for item in rows:
                mt = re.search(
                    r"(?:effective|starting|from|自)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})",
                    item["text"],
                    re.IGNORECASE,
                )
                item["effective_date"] = mt[1] if mt else None
                item["basis"] = "relevant_statement"
        if not rows:
            run.gap(f"未找到{topic}主题的可定位表述")
        if not rows or any(not x.get("effective_date") for x in rows):
            unspecified.append(
                {"topic": topic, "field": "effective_date", "status": "未明确，不能据公告日期推算执行日期"}
            )
        out.append({"topic": topic, "items": rows})
    if len({(r["source_id"], r["source_version"]) for r in run.records}) < 2:
        run.gap("公告和声明两类原件未齐备")
    if not docs:
        run.gap("文件当事方或发布日期未标明")
    return {"commitments": out, "documents": docs, "missing_details": unspecified}


_HANDLERS = {
    "B01": numeric.observation,
    "B02": commitments,
    "B03": media.archive_images,
    "B04": media.speech,
    "B05": numeric.month_series,
    "B06": compare.clauses,
    "B07": numeric.compare_periods,
    "B08": partial(compare.evidence_matrix, expected_roles=("investigation", "judicial")),
    "B09": compare.chronology,
    "B10": partial(compare.evidence_matrix, expected_roles=("brief", "investigation")),
    "T01": science.research,
    "T02": compare.milestones,
    "T03": compare.roadmap,
    "T04": science.projects,
    "T05": science.chart_data,
    "T06": compare.clauses,
    "T07": partial(media.speech, interview=True),
    "T08": partial(compare.evidence_matrix, expected_roles=("project", "audit", "study")),
    "T09": science.battery,
    "T10": science.citation_impact,
}


def analyze(
    case_id: str,
    records: list[dict],
    *,
    feedback: list[str] | None = None,
    repair_action: str | None = None,
    repair_targets=(),
) -> dict:
    if case_id not in _HANDLERS:
        raise ValueError("未知文档分析任务")
    if not isinstance(records, list):
        raise TypeError("输入必须为记录列表")
    run = Run(
        records,
        retry=bool(feedback) and repair_action is None,
        repair_action=repair_action,
        repair_targets=tuple(repair_targets),
    )
    products = _HANDLERS[case_id](run)
    if set(products) != set(CATALOG[case_id]["outputs"]):
        raise RuntimeError("分析工具产物与登记契约不一致")
    return {"products": products, "evidence": run.evidence, "gaps": run.gaps}
