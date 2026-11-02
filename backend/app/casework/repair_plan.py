"""Select a bounded alternative operation from a completed case review and frozen inputs."""

import re
from collections import Counter, defaultdict

from .document_common import norm, units

POLICY_VERSION = "case-repair-v1"
_ACTIONS = {
    "B05": "chart_ocr_reconcile",
    "B06": "match_unique_clauses",
    "T06": "match_unique_clauses",
    "T01": "expand_research_evidence",
    **{f"G{i:02}": "apply_field_dictionary" for i in range(1, 11)},
}
_LABELS = {
    "chart_ocr_reconcile": "读取图上印刷月度标签并重新核算",
    "match_unique_clauses": "按唯一主题核对未匹配条款",
    "expand_research_evidence": "为缺失研究字段检索可定位的替代表述",
    "apply_field_dictionary": "按已提供的字段字典补齐缺列并复算",
}
_REASONS = {
    "chart_ocr_reconcile": "正文中的月度数字不足以核对报告，但冻结资料含有可定位的 PDF 页；改读图上印刷标签。",
    "match_unique_clauses": "按编号未能对应的条款中，前后两个版本各有唯一一条相同主题；只核查这些条款。",
    "expand_research_evidence": "研究卡有缺失字段，冻结材料中找到了与该字段直接相关的另一种原文表述。",
    "apply_field_dictionary": "计算缺口对应的字段，在冻结资料的目标来源中存在唯一、无冲突的列名映射。",
}
_TRUST_CHECKS = {"trusted_reference_integrity", "trusted_reference_sources", "trusted_reference_batch"}
_MISSING_EVIDENCE = "has_evidence"


def allowed_action(case_id, action):
    return isinstance(action, str) and _ACTIONS.get(case_id) == action


def hard_blocked(quality):
    """Only absent evidence may attempt a different extraction. Publication still requires all hard gates."""
    hard = quality.get("hard_failures") if isinstance(quality, dict) else None
    return not isinstance(hard, list) or any(
        not isinstance(issue, dict) or issue.get("code") != _MISSING_EVIDENCE for issue in hard
    )


def _plan(case_id, decision, reason, *, action=None, targets=(), failed=()):
    if decision == "apply" or action == "chart_ocr_reconcile":
        rerun, reuse = ["prepare", "analyze", "review"], []
    elif action:
        rerun, reuse = ["analyze", "review"], ["prepare"]
    else:
        rerun, reuse = [], []
    return {
        "policy_version": POLICY_VERSION,
        "case_id": case_id,
        "decision": decision,
        "action": action,
        "label": _LABELS.get(action, "停止修订"),
        "reason": reason,
        "targets": sorted(set(targets)),
        "failed_checks": sorted(set(failed)),
        "rerun_stages": rerun,
        "reused_stages": reuse,
    }


def published_plan(case_id, strategy, inputs):
    """A published version can preselect only a mode this case implements."""
    if not allowed_action(case_id, strategy):
        return None
    targets = []
    if strategy == "chart_ocr_reconcile":
        targets = [
            a["id"]
            for a in inputs
            if str(a.get("media_type", "")).partition(";")[0] == "application/pdf" and a.get("content_ref")
        ]
        if not targets:
            return None
    return _plan(
        case_id,
        "apply",
        "使用已发布分析版本的受限操作，沿用冻结来源和独立审核。",
        action=strategy,
        targets=targets,
    )


# Complement first-pass research slot retrieval with narrowly attributed utterances.
# Every match is returned verbatim with its original location. Never synthesise a missing claim.
_ALTERNATIVES = {
    "question": r"(?:\b(?:our|the (?:primary |main )?study(?:'s)?) (?:objective|purpose|goal) (?:was|is) to\b|(?:本项|本次)试验(?:旨在|意在))",
    "comparison": r"(?:\b(?:against (?:the |a )?(?:matched |untreated )?control (?:group|arm)|(?:the )?two trial arms (?:received|used))\b|(?:实验组与对照组分别|以.{2,40}为参照组))",
    "measurements": r"(?:\b(?:instrument readings? (?:were|averaged)|the sensor (?:reported|recorded)|the detector recorded)\b|传感器读数为|检测仪记录为)\s*(?:a (?:value|mean) of\s+)?[+-]?\d[\d.,]*\s*(?:%|％|ppm\b|μm\b|nm\b|K\b|mg\b|kg\b|m/s\b|Hz\b)",
    "model_results": r"(?:\b(?:our numerical forecast|our computational estimate|our numerical prediction) (?:was|indicated)\b|(?:数值预测|计算预报)(?:结果为|显示))\s*[+-]?\d[\d.,]*\s*(?:%|％|ppm\b|μm\b|nm\b|K\b|mg\b|kg\b|m/s\b|Hz\b)",
    "limitations": r"(?:\b(?:these findings|our results|the observations) (?:apply only to|are restricted to|do not extend to)\b|(?:上述结果|这些结果)(?:仅适用于|不能推广至))",
}


def research_alternatives(field, records):
    """Find at most five additional source excerpts that support the requested field."""
    pattern = _ALTERNATIVES.get(field)
    if pattern is None:
        return []
    excerpts = []
    seen = set()
    for record in records:
        if not record.get("source_id") or not record.get("source_version") or not record.get("location"):
            continue
        for quote in units(record.get("text", "")):
            if len(quote) > 600 or quote in seen or not re.search(pattern, quote, re.IGNORECASE | re.DOTALL):
                continue
            excerpts.append((record, quote))
            seen.add(quote)
            if len(excerpts) == 5:
                return excerpts
    return excerpts


def _clause_targets(products):
    sides = {"before": [], "after": []}
    for row in products.get("unmatched", []):
        if row.get("side") in sides:
            sides[row["side"]].append(row["item"])
    matched = products.get("clause_map", [])
    occupied = {norm(row[side].get("topic", "")) for side in sides for row in matched}
    active = {}
    for side, rows in sides.items():
        counts = Counter(norm(row.get("topic", "")) for row in rows)
        active[side] = {
            topic
            for row in rows
            if isinstance(row.get("topic"), str)
            and (topic := norm(row["topic"]))
            and len(topic) <= 128
            and counts[topic] == 1
            and row.get("text")
            and row.get("evidence_ids")
            and not (side == "after" and row.get("match_id"))
        }
    return (active["before"] & active["after"]) - occupied


# Only grant a field remapping if the reported defect calls for that field. Other
# data, thresholds, CRS or QC meanings cannot be supplied by the repair policy.
_GEO_FIELDS = {
    "G01": [(r"时间|观测日", "observed_at acq_date acq_time"), (r"坐标|经纬度", "latitude longitude")],
    "G02": [(r"日期|时相", "date"), (r"像素|图像", "image_id width height bbox")],
    "G03": [(r"年度记录", "year month hemisphere extent"), (r"单位|版本", "unit extent")],
    "G04": [(r"坐标系|EPSG", "metric_crs crs"), (r"几何|分析范围|路径", "geometry")],
    "G05": [(r"建筑|轮廓|坐标", "building_id geometry longitude latitude")],
    "G06": [
        (r"坐标系|EPSG", "metric_crs crs"),
        (r"网格|影像", "values origin_x origin_y pixel_width pixel_height valid_mask"),
    ],
    "G07": [
        (r"时间窗内无实验室", "sample_id date analyte value"),
        (r"坐标|经纬度", "site_id longitude latitude"),
        (r"网格边界", "bbox"),
    ],
    "G08": [(r"时间|窗口|观测日", "observed_at event_at date"), (r"岛屿|地点", "island")],
    "G09": [(r"路网边|起终点|OD", "edge_id od_id from to distance_km"), (r"速度", "speed_kmh")],
    "G10": [(r"坐标系|EPSG", "crs metric_crs"), (r"几何|覆盖|图层", "geometry date")],
}


def _geo_targets(case_id, records, gaps):
    fields = {
        f
        for pattern, names in _GEO_FIELDS[case_id]
        if any(re.search(pattern, gap) for gap in gaps)
        for f in names.split()
    }
    dictionaries = defaultdict(set)
    for row in records:
        f = row.get("fields", {})
        if (
            f.get("kind") == "field_dictionary"
            and isinstance(f.get("aliases"), dict)
            and isinstance(f.get("target_source_id"), str)
        ):
            for old, new in f["aliases"].items():
                if isinstance(new, str):
                    dictionaries[(f.get("target_source_id"), old)].add(new)
    targets = set()
    conflicts = set()
    for row in records:
        values, source = row.get("fields", {}), row["source_id"]
        if values.get("kind") == "field_dictionary":
            continue
        pending = {}
        for old, value in values.items():
            names = dictionaries.get((source, old), set())
            if not names & fields:
                continue
            if len(names) != 1:
                conflicts.add(source)
                continue
            dest = next(iter(names))
            if dest in values and values[dest] != value or dest in pending and pending[dest] != value:
                conflicts.add(source)
            elif dest not in values and value is not None and value != "":
                pending[dest] = value
        if pending:
            targets.add(source)
    return targets - conflicts


def build_plan(case_id, previous_review, *, records=(), attempted_actions=()):
    """Diagnose a failed review. Unknown failures, unavailable inputs and exhausted alternatives stop."""
    review = previous_review or {}
    failed_rows = [check for check in review.get("review", []) if check.get("passed") is False]
    failed = {check["name"] for check in failed_rows}

    def stop(reason):
        return _plan(case_id, "stop", reason, failed=failed)

    if review.get("case_id", case_id) != case_id:
        return stop("上一轮审核与当前用例不一致。")
    if not failed:
        return stop("没有已定位的失败检查，不能选择修复操作。")
    if failed & _TRUST_CHECKS:
        return stop("冻结批次或来源未通过校验；需要修复资料并重新受理。")
    # Missing citations are still a hard failure of the candidate. A new extraction can
    # supply fresh, located evidence. A forged citation or failed arithmetic cannot be reused.
    if any(check.get("hard") and check["name"] != _MISSING_EVIDENCE for check in failed_rows):
        return stop("独立核验发现硬性不一致，不能沿用有问题的产品继续尝试。")
    action = _ACTIONS.get(case_id)
    if not action:
        return stop("该失败没有已验证的替代操作。")
    if action in attempted_actions:
        return stop("同一替代操作已经尝试；冻结资料没有新的修复路径。")
    products = review.get("products", {})
    gaps = review.get("gaps", [])
    targets = set()
    if action == "chart_ocr_reconcile" and failed & {
        "case_gaps",
        "reference_inventory",
        "substantive_product",
    }:
        no_months = not products.get("monthly") and any(g.startswith("未提供可读月度表") for g in gaps)
        if no_months or "reference_inventory" in failed:
            targets = {
                row["source_id"]
                for row in records
                if str(row.get("media_type", "")).partition(";")[0].strip().lower() == "application/pdf"
                and row.get("location", {}).get("page")
            }
    elif action == "expand_research_evidence" and "case_gaps" in failed:
        card = products.get("literature_card", {})
        targets = {
            field for field in _ALTERNATIVES if not card.get(field) and research_alternatives(field, records)
        }
    elif action == "match_unique_clauses" and failed & {"case_gaps", "substantive_product"}:
        if not any("缺术语对照" in gap for gap in gaps):
            targets = _clause_targets(products)
    elif action == "apply_field_dictionary" and "case_gaps" in failed:
        targets = _geo_targets(case_id, records, gaps)
    if not targets:
        if failed == {"reading_coverage"}:
            return stop("译注未通过核查，冻结资料中没有可替代本次读法的操作；保留原文和定位。")
        return stop("冻结输入没有与失败原因对应的可用替代证据或字段映射。")
    return _plan(case_id, "revise", _REASONS[action], action=action, targets=targets, failed=failed)
