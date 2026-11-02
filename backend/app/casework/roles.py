"""The same handler definitions drive execution and the visible specialist comparison."""

from functools import partial

from . import document_cases, geo_cases

PDF = "evidence.pdf_page"
NUMBERS = "evidence.extract_quantities"
DATES = "evidence.timeline_dates"
TERMS = "evidence.list_definitions"
DIFF = "evidence.compare_passages"
CUES = "evidence.select_cues"
KEYS = "table.audit_keys"
TOTAL = "stats.reconcile_total"
BASELINE = "series.baseline_anomaly"
AREA = "geo.overlay_area"
ROUTE = "geo.route_detour"
TRANSITION = "raster.class_transition"

# Grants permit both a governed manual tool run and an automatic use when its input contract matches.
GRANTS = {
    "B01": (NUMBERS, DATES),
    "B02": (DATES, TERMS),
    "B03": (KEYS,),
    "B04": (CUES, DIFF),
    "B05": (NUMBERS, KEYS, TOTAL),
    "B06": (DIFF, TERMS),
    "B07": (NUMBERS, KEYS),
    "B08": (DIFF, DATES),
    "B09": (DATES, CUES),
    "B10": (DATES, DIFF),
    "T01": (TERMS, NUMBERS),
    "T02": (DATES, KEYS),
    "T03": (DATES, DIFF),
    "T04": (KEYS, TOTAL),
    "T05": (NUMBERS, PDF),
    "T06": (DIFF, TERMS),
    "T07": (CUES, TERMS),
    "T08": (DIFF, NUMBERS),
    "T09": (NUMBERS, KEYS),
    "T10": (DATES, KEYS),
    "G01": (KEYS,),
    "G02": (KEYS,),
    "G03": (BASELINE,),
    "G04": (AREA,),
    "G05": (KEYS, AREA),
    "G06": (TRANSITION,),
    "G07": (KEYS,),
    "G08": (DATES,),
    "G09": (ROUTE,),
    "G10": (AREA,),
}


def _handler(code):
    if code.startswith("G"):
        return geo_cases.ANALYZERS[code]
    handler = document_cases._HANDLERS[code]
    return handler.func if isinstance(handler, partial) else handler


def contract(stage, code):
    if stage == "prepare":
        return {
            "stage": stage,
            "operation": "records.extract",
            "duty": "解析授权的原件，给每条输入记录标注来源版本与页、行、像素或时间位置。",
            "inputs": ["任务冻结的原件与资料版本"],
            "outputs": ["定位记录", "解析缺口"],
            "grants": [PDF, CUES],
            "shared_cases": sorted(GRANTS),
            "acceptance": ["每条记录须有来源版本和位置；无法解析的材料保留缺口。"],
        }
    if stage == "review":
        return {
            "stage": stage,
            "operation": "engine.audit",
            "duty": "用冻结原件核查专业分析结果、引用、计算口径和批次覆盖，指出未通过的检查。",
            "inputs": ["定位记录", "候选产品与证据"],
            "outputs": ["独立检查", "证据报告", "未解决问题"],
            "grants": [PDF],
            "shared_cases": sorted(GRANTS),
            "acceptance": ["核对引用、资料版本、产品字段和用例专属检查；硬性不一致阻止通过。"],
        }
    profile = (geo_cases.CATALOG if code.startswith("G") else document_cases.CATALOG)[code]
    handler = _handler(code)
    from .coverage import COUNTS

    checks = ["交付以下全部产品，引用能回到冻结原件。"]
    if code in COUNTS:
        product, count = COUNTS[code]
        checks.append(f"参考批次的 {product} 核对 {count} 项覆盖要求。")
    if code in {"B06", "T06"}:
        checks.append("对应条款须同时有双方证据；模糊或不唯一的匹配须留下未确认项。")
    if code in {"B04", "B06", "T01", "T07"}:
        checks.append("参考批次另核查阅读覆盖，未核实的译注保留原文和位置。")
    handler_spec = document_cases._HANDLERS.get(code)
    if isinstance(handler_spec, partial) and "expected_roles" in handler_spec.keywords:
        checks.append("证据矩阵区分来源角色：" + "、".join(handler_spec.keywords["expected_roles"]) + "。")
    return {
        "stage": stage,
        "case_id": code,
        "operation": f"{handler.__module__.rsplit('.', 1)[-1]}.{handler.__name__}",
        "duty": f"根据{'、'.join(profile['inputs'])}完成{profile['title']}，按原始位置交付证据。",
        "inputs": profile["inputs"],
        "outputs": profile["outputs"],
        "grants": list(GRANTS[code]),
        "shared_cases": [k for k in sorted(GRANTS) if _handler(k) is handler],
        "acceptance": checks,
    }
