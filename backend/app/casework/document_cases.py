"""Evidence-first specialist catalogue. Profiles express tool contracts, never stored case answers."""

import re
from functools import partial

from . import document_compare as compare
from . import document_extended as extended
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
    "B11": _profile(
        "OPCW 化武销毁核验进度数据快报",
        "上游提供 1 份官方统计页面存档（HTML 或另存 PDF，约 1 页）与 1 份字段模板。页面正文含数据表与截至日期；原始视察报告、各国申报底稿不在本次材料中，不能视为已取得。任务运行时假定所需材料一次到位。",
        "observations report_window context",
        "报告检索|统计复核|来源审查",
        "region current previous report_number published_date observed_until timezone",
    ),
    "B12": _profile(
        "二战航空宣传海报的 OCR 转录与档案要素抽取",
        "需要一张可下载的历史航空海报高清 JPG 作为主输入（本用例提供 B12-M01，另附 B12-M02 作对照）；NARA 条目页元数据用于核对机构、NAID、年代、版权；LOC 条目页（B12-M03）提供权威档案要素字段规范参照。缺口：Commons 文件页未给出海报图像内文字的逐字 OCR 全文，需由模型从图像自行识读；精确制作年份未知，仅有年代区间。 结构化记录kind合法取值：image(可见要素)/ocr(转录文本)/reference(馆藏元数据)；字段feature或visible_features(可列表)、text或ocr_text、bbox、confidence。",
        "image_catalog overlay_layers",
        "图像编目|馆藏匹配|来源审查",
        "kind image_id feature visible_feature visible_features 要素 text ocr_text bbox coordinate_space width height confidence model_name url",
    ),  # 专属分析函数已实现
    "B13": _profile(
        "UNMAS 地雷行动年度统计跨年核查",
        "上游提供 1 份年度报告网页存档（2023 年，约 1 页正文）与 1 份年度报告 PDF（2024 年，公开入口约数十页，含照片与图表），另附输出字段模板与口径对照表。底层逐国数据表与原始作业记录不在材料中。",
        "monthly reconciliation trend limitations",
        "统计抽取|序列复算|来源审查",
        "month start_date end_date deaths injured basis kind scope",
    ),
    "B14": _profile(
        "《地雷监测 2025》伤亡与污染统计口径核查",
        "上游提供 1 份报告主页存档（HTML，含章节列表）与 1 份完整报告 PDF（公开下载入口约 9 MB），另附字段模板。报告涉及的国别底层数据集与原始调查表不在本次材料中。",
        "monthly reconciliation trend limitations",
        "统计抽取|序列复算|来源审查",
        "month start_date end_date deaths injured basis kind scope",
    ),
    "B15": _profile(
        "OPCW 视察与销毁执行报告结构化快报",
        "上游提供 1 份官方报告 PDF（含表格与附件）与 1 份当前统计页面存档，另附字段模板。报告引用的原始视察记录与环境样本数据不在材料中。",
        "observations report_window context",
        "报告检索|统计复核|来源审查",
        "region current previous report_number published_date observed_until timezone",
    ),
    "B16": _profile(
        "ICRC 历史档案开放清单与数字化时间线复盘",
        "上游提供 3 份官方材料存档（档案说明页、2015 年开放公告页、2016 年视听门户公告页）及 1 份时间线字段模板；1966—1975 年清单 PDF（约 4.8 MB）与视听门户检索结果为可选补充，需人工取得。原始档案扫描件不在本次材料中。",
        "timeline unknown_time variants",
        "档案编目|时间对齐|来源审查",
        "event_id event event_date formed_at published_date declassified_at timezone time_precision channel recipient",
    ),
    "B17": _profile(
        "爆炸物污染与伤亡数据的多源交叉核验矩阵",
        "上游提供 1 份国别项目页存档（HTML，含年度与累计数字）、1 份 UNMAS 年度报告 PDF、1 份公民社会年度报告页存档，另附不超过 15 项的待核验问题清单与字段模板。底层逐月作业数据、事件级记录和现场调查表不在材料中。",
        "matrix version_timeline unverified",
        "问题检索|证据矩阵|来源审查",
        "kind question_id question role statement scope published_date evidence_type value",
    ),
    "T11": _profile(
        "C919适航取证里程碑结构化快报",
        "上游提供 CAAC 适航审定中心型号成果页1份、新华社专访文本1份，合计约3000字，字段模板1份；不需交付监控或运营数据。型号合格证编号、生产许可证颁发日期未在所给两份材料中给出，列为缺口。",
        "milestones unmatched brief",
        "事件提取|里程碑归并|来源审查",
        "project event_type event_date published_date role outcome",
    ),
    "T12": _profile(
        "GE9X发动机公开技术参数结构化速读",
        "上游提供 GE9X 官方产品页1份、推力纪录新闻稿1份，参数字段模板1份，两页合计约4000字。厂商未公开逐台发动机性能包线与适航审定符合性报告，不作为已取得材料。",
        "figure_cards comparison",
        "图表索引|标注值核查|来源审查",
        "kind parameter param 参数名称 value 数值 unit 单位 scope 口径类别 figure figure_id",
    ),  # 专属分析函数已实现
    "T13": _profile(
        "EH216-S无人驾驶载人eVTOL取证事件卡",
        "上游提供企业中文新闻稿与英文投资者关系稿各1份（合计约5000字），术语对照表1份。两份材料为企业发布口径；CAAC正式TC证件与型号合格证数据单（TCDS）原件未在本次核验中取得，须标为缺口。",
        "milestones unmatched brief",
        "事件提取|里程碑归并|来源审查",
        "project event_type event_date published_date role outcome",
    ),
    "T14": _profile(
        "XB-1超声速试飞里程碑去重与参数表",
        "上游提供官方新闻稿1份、试飞实时博客1份（合计约6000字，含13次飞行表），字段模板1份。声爆声学原始记录、NASA纹影影像原始数据与FAA认证材料未在本次核验中取得。",
        "milestones unmatched brief",
        "事件提取|里程碑归并|来源审查",
        "project event_type event_date published_date role outcome",
    ),
    "T15": _profile(
        "NASA X-48B 翼身融合体技术照片的可见技术要素标注与型号依据核验",
        "需要一张可下载的 X-48B 技术照片原图作为主输入（T15-M01）；一份 NASA 官方图像库条目页元数据用于核对图像身份、日期、中心、摄影师与关键词（T15-M02）；一份 NASA 官方项目技术说明用于型号与参数依据（T15-M03）。缺口：条目页为前端 JS 动态渲染，WebFetch 抓取不全，身份/日期/主题改由 NASA 官方元数据 API 交叉核验；原图为纯图像，不含逐字要素标注，需模型自行识读；照片未给出内部结构与精确参数，翼展、速度、推力等无法从图像量测。 结构化记录kind合法取值：image(可见要素)/identification(型号依据)；字段feature或visible_features(可列表)、designation、basis_from_image、basis_from_text、bbox、confidence。",
        "image_catalog overlay_layers",
        "图像编目|馆藏匹配|来源审查",
        "kind image_id feature visible_feature visible_features 要素 text designation basis_from_image basis_from_text bbox coordinate_space width height confidence model_name url",
    ),  # 专属分析函数已实现
    "T16": _profile(
        "FAA NextGen空管现代化进度与建议跨报告复盘",
        "上游提供GAO-24-105254（2023-11-09发布）与GAO-25-108162（2025-03-04发布）产品页全文，含推荐事项状态说明；字段模板1份、建议编号对照表1份。FAA原始预算提交、NextGen绩效与问责报告及系统清单未在本次核验中取得。",
        "portfolio organization_graph theme_summary",
        "项目归一|关系构图|统计复核",
        "topic 主题 report_id 报告编号 published_date 发布日期 data_as_of 数据截至日 status 状态 recommendation 建议编号",
    ),  # 专属分析函数已实现
    "T17": _profile(
        "Artemis I无人绕月飞行试验跨源时间线与参数核对",
        "上游提供NASA Artemis I专题页1份、溅落新闻稿1份（合计约7000字，含系统参数表与图片说明），字段模板1份。任务逐秒遥测、轨道数据和再入热防护实测数据未在本次核验中取得。",
        "timeline unknown_time variants",
        "档案编目|时间对齐|来源审查",
        "event_id event event_date formed_at published_date declassified_at timezone time_precision channel recipient",
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
    "B11": numeric.observation,
    "B12": extended.poster_catalog,
    "B13": numeric.month_series,
    "B14": numeric.month_series,
    "B15": numeric.observation,
    "B16": compare.chronology,
    "B17": partial(compare.evidence_matrix, expected_roles=("investigation", "judicial")),
    "T11": compare.milestones,
    "T12": extended.parameter_sheet,
    "T13": compare.milestones,
    "T14": compare.milestones,
    "T15": extended.technical_photo_catalog,
    "T16": extended.cross_report_review,
    "T17": compare.chronology,
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
