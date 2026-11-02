"""Independent invariants for document products; no call back into candidate analysis."""

import re
from datetime import date
from itertools import pairwise

from .document_common import items, norm, number, string, value


def _discussion_figure(records, source, offset):
    active = None
    for r in records:
        if r["source_id"] != source["source_id"] or r["source_version"] != source["source_version"]:
            continue
        text = r["text"][:offset] if r is source else r["text"]
        mentions = re.findall(r"\bFigure\s+(\d+)\b", text, re.IGNORECASE)
        if mentions:
            active = int(mentions[-1])
        if r is source:
            return active
    return None


def _printed_prose_coverage(records, rows):
    # Independently enumerate explicit comparisons in target discussions. A deleted row
    # cannot be hidden by deleting it from both the card and the comparison table.
    expected = set()
    patterns = {
        "技术改善减少量": r"technology\s+accounts\s+for\s+a\s+reduction\s+of\s+(\d+(?:\.\d+)?)\s*Mt",
        "运营改善额外减少量": r"operations\s+accounts\s+for\s+an\s+additional\s+(\d+(?:\.\d+)?)\s*Mt",
        "合计减少量": r"total\s+reduction\s+of\s+(\d+(?:\.\d+)?)\s*Mt",
        "基线差（约）": r"CO\s*[2₂]\s+trends\s+baselines\s+in\s+\d{4}\s+is\s+approximately\s+(\d+(?:\.\d+)?)%",
        "最低排放差额": r"minimum\s+CO\s*[2₂]\s+emission\s+gap\s+of\s+(\d+(?:\.\d+)?)\s*Mt",
        "剩余CO₂排放相对减少": r"(\d+(?:\.\d+)?)%\s+reduction\s+in\s+residual\s+CO\s*[2₂]",
    }
    for r in records:
        for role, pattern in patterns.items():
            for m in re.finditer(pattern, r["text"], re.IGNORECASE):
                fig = _discussion_figure(records, r, m.start())
                if fig in {4, 6, 7}:
                    expected.add((fig, r["source_id"], r["source_version"], number(m[1]), role))
    actual = {(v["figure"], v["source_id"], v["source_version"], v["value"], v["metric_role"]) for v in rows}
    return expected <= actual


def _chart_fact(row, records):
    source = next(
        (
            r
            for r in records
            if r["source_id"] == row["source_id"]
            and r["source_version"] == row["source_version"]
            and r["location"] == row["location"]
        ),
        None,
    )
    if not source or row.get("page") != source["location"].get("page") or not row["evidence_ids"]:
        return False
    f = source["fields"]
    if f.get("image_label"):
        return (
            row["basis"] == "label"
            and source["location"].get("figure") == row["figure"]
            and bool(source["location"].get("bbox_pixels"))
            and row["metric_role"] == "图上剩余排放量"
            and row["kind"] == "forecast"
            and all(row[k] == f[k] for k in ("unit", "year", "value"))
            and row["context"] == source["text"]
        )
    if f.get("figure") and f.get("value") is not None:
        return (
            f["figure"] == row["figure"]
            and f["value_source"] == row["basis"]
            and f["value_kind"] == row["kind"]
            and all(row[k] == f[k] for k in ("scenario", "unit", "year", "value"))
        )
    quote = row.get("context")
    if (
        not quote
        or quote not in source["text"]
        or _discussion_figure(records, source, source["text"].index(quote)) != row["figure"]
    ):
        return False
    if row["kind"] == "assumption" and quote and quote in source["text"]:
        masses = re.findall(r"(\d+(?:\.\d+)?)\s*kg", quote, re.IGNORECASE)
        intervals = re.findall(
            r"from\s+((?:19|20)\d{2})\s+to\s+((?:19|20)\d{2})", source["text"], re.IGNORECASE
        )
        return (
            row["metric_role"] == "燃烧换算系数"
            and row["basis"] == "text"
            and row["unit"] == "kg CO₂/kg 燃油"
            and str(row["year"]) in {"–".join(pair) for pair in intervals}
            and len(masses) == 2
            and number(masses[0])
            and row["value"] == number(masses[1]) / number(masses[0])
        )
    if (
        row["basis"] != "text"
        or row["kind"] != "forecast"
        or not quote
        or quote not in source["text"]
        or not row["scenario"]
    ):
        return False
    if type(row["year"]) is not int or not 1900 <= row["year"] < 2100:
        return False
    num = re.escape(str(row["value"]))
    patterns = {
        "技术改善减少量": rf"technology\s+accounts\s+for\s+a\s+reduction\s+of\s+{num}\s*Mt\b",
        "运营改善额外减少量": rf"operations\s+accounts\s+for\s+an\s+additional\s+{num}\s*Mt\b",
        "合计减少量": rf"total\s+reduction\s+of\s+{num}\s*Mt\b",
        "基线差（约）": rf"baselines\s+in\s+{row['year']}\s+is\s+approximately\s+{num}%",
        "最低排放差额": rf"gap\s+of\s+{num}\s*Mt\s+in\s+{row['year']}\b",
        "剩余CO₂排放相对减少": rf"{num}%\s+reduction\s+in\s+residual\s+CO\s*[2₂]",
        "平均燃油效率改善率": rf"efficiency\s+of\s+{num}%\s+would\s+be\s+observed\s+by\s+{row['year']}\b",
        "平均燃油效率改善率（约）": rf"about\s+{num}%\s+by\s+{row['year']}\b",
    }
    if row["metric_role"] == "燃油消耗相对减少":
        percentages = re.findall(r"(\d+(?:\.\d+)?)%", quote)
        years = re.findall(r"\b(?:19|20)\d{2}\b", quote)
        return (
            row["unit"] == "%"
            and len(years) == len(percentages) == 2
            and (str(row["year"]), str(row["value"])) in zip(years, percentages, strict=True)
        )
    role = row["metric_role"]
    unit = {
        "技术改善减少量": "Mt",
        "运营改善额外减少量": "Mt",
        "合计减少量": "Mt",
        "最低排放差额": "Mt CO₂",
    }.get(role, "%")
    return (
        role in patterns
        and row["unit"] == unit
        and re.search(patterns[role], quote, re.IGNORECASE) is not None
        and re.search(rf"\b{row['year']}\b", source["text"]) is not None
    )


def audit_products(code, products, records):
    checks = []

    def check(name, ok, message):
        checks.append({"name": name, "passed": bool(ok), "message": message, "hard": True})

    try:
        # A forged candidate with the right product keys but no substantive results must not pass.
        primary = {
            "B01": "observations",
            "B02": "commitments",
            "B03": "image_catalog",
            "B04": "timed_index",
            "B05": "monthly",
            "B06": "clause_map",
            "B07": "comparison",
            "B08": "matrix",
            "B09": "timeline",
            "B10": "matrix",
            "T01": "literature_card",
            "T02": "milestones",
            "T03": "route_changes",
            "T04": "portfolio",
            "T05": "comparison",
            "T06": "clause_map",
            "T07": "timed_index",
            "T08": "matrix",
            "T09": "trend_series",
            "T10": "citation_impact",
        }[code]
        checks.append(
            {
                "name": "substantive_product",
                "passed": bool(products.get(primary)),
                "message": "主要产物缺可核查条目",
                "hard": False,
            }
        )
        if code == "B01":

            def matches(x):
                given = [r for r in records if norm(string(r, "region", "地区")) == norm(x["region"])]
                numeric_match = any(
                    number(value(r, "current", "count", "数量")) == x["count"]
                    and number(value(r, "previous", "上期数量")) == x["previous"]
                    for r in given
                )
                if numeric_match:
                    return True
                for r in records:
                    raw = r.get("text", "")
                    rg = re.escape(x["region"])
                    for m in re.finditer(rf"(?i)\bIn\s+{rg}\s+region[,]?", raw):
                        fragment = re.split(
                            r"\bIn\s+[A-Z][\w'-]+\s+region[,]?", raw[m.end() : m.end() + 1600], maxsplit=1
                        )[0]
                        counts = [
                            number(v)
                            for v in re.findall(r"(?i)recorded\s+([\d,]+)\s+ceasefire violations", fragment)
                        ]
                        if counts[:2] == [x["count"], x["previous"]]:
                            return True
                return False

            check(
                "observed_delta",
                all(
                    r["change"] == r["count"] - r["previous"] and matches(r) for r in products["observations"]
                ),
                "区域差值须可复算，两个原始数量须能在同地区记录找到",
            )
        elif code == "B05":
            monthly = products["monthly"]
            observed = {
                (
                    r["source_id"],
                    str(r["source_version"]),
                    string(r, "month", "月份"),
                    number(value(r, "deaths", "killed", "死亡")),
                    number(value(r, "injured", "受伤")),
                )
                for r in records
            }
            check(
                "monthly_observations",
                all(
                    (row["source_id"], row["source_version"], row["month"], row["deaths"], row["injured"])
                    in observed
                    for row in monthly
                ),
                "逐月数值与冻结行或图中印刷标签不符",
            )
            check(
                "monthly_arithmetic",
                all(
                    r["deaths"] >= 0 and r["injured"] >= 0 and r["total"] == r["deaths"] + r["injured"]
                    for r in monthly
                ),
                "月度行人数之和错误",
            )
            for c in products["reconciliation"]:
                subset = [
                    r
                    for r in monthly
                    if r["source_id"] == c["source_id"]
                    and r["source_version"] == c["version"]
                    and (r["scope"] or "") == c["scope"]
                ]
                check(
                    "monthly_crossfoot",
                    (
                        c["computed"] is None
                        or c["computed"] == [sum(r[k] for r in subset) for k in ("deaths", "injured")]
                    )
                    and (not c["agrees"] or c["computed"] is not None and c["reported"] == [c["computed"]]),
                    "加总或一致性标志与逐月行/报告总数矛盾",
                )
        elif code == "B07":
            for group in products["comparison"]:
                s = group["series"]
                valid = len(s) == 2 and all(
                    s[0][k] and norm(s[0][k]) == norm(s[1][k])
                    for k in ("definition", "coverage", "method", "unit")
                )
                try:
                    valid = valid and date.fromisoformat(s[0]["cutoff"]) < date.fromisoformat(s[1]["cutoff"])
                except ValueError:
                    valid = False
                check(
                    "comparison_scope",
                    group["comparable"] == bool(valid)
                    and group["change"] == (s[1]["value"] - s[0]["value"] if valid else None),
                    "仅可对同口径、不同时点数据计算差额",
                )
        elif code in {"B06", "T06"}:
            for pair in products["clause_map"]:
                old, new = pair["before"], pair["after"]
                check(
                    "bilateral_clause",
                    bool(
                        old["text"]
                        and new["text"]
                        and old["evidence_ids"]
                        and new["evidence_ids"]
                        and set(old["evidence_ids"]).isdisjoint(new["evidence_ids"])
                    )
                    and (pair["change"] == "identical") == (norm(old["text"]) == norm(new["text"])),
                    "条款比较必须有两侧独立证据并正确判定相同措辞",
                )
        elif code in {"B04", "T07"}:
            for cue in products["timed_index"]:
                source = [
                    r
                    for r in records
                    if number(value(r, "start_ms", "开始毫秒")) == cue["start_ms"]
                    and number(value(r, "end_ms", "结束毫秒")) == cue["end_ms"]
                    and (string(r, "utterance", "transcript", "原话") or r.get("text", "")) == cue["text"]
                ]

                # When the source is a VTT/SRT cue, verify that the supplied cue text AND interval co-occur.
                def stamp(s):
                    chunks = s.replace(",", ".").split(":")
                    seconds = (
                        float(chunks[-1])
                        + 60 * int(chunks[-2])
                        + (3600 * int(chunks[0]) if len(chunks) == 3 else 0)
                    )
                    return round(1000 * seconds)

                vtt = any(
                    stamp(m[1]) == cue["start_ms"]
                    and stamp(m[2]) == cue["end_ms"]
                    and m[3].strip() == cue["text"]
                    for r in records
                    if "-->" in r.get("text", "")
                    for m in re.finditer(
                        r"(?ms)^\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)",
                        r["text"].replace("\r\n", "\n"),
                    )
                )
                check(
                    "media_timecode",
                    0 <= cue["start_ms"] < cue["end_ms"] and (bool(source) or vtt),
                    "回听起止码与原始记录不符",
                )
                if cue["alignment"] == "aligned":
                    check(
                        "transcript_pair",
                        len(cue["evidence_ids"]) >= 2 and cue["script"] is not None,
                        "已对齐条目须有字幕和独立逐字稿两侧证据",
                    )
        elif code == "T04":
            pids = [r["project_id"] for r in products["portfolio"]]
            ids = set(pids)
            check("project_unique", len(pids) == len(ids), "项目不得重复统计")
            aliases = {
                norm(string(r, "alias")): string(r, "canonical")
                for r in records
                if string(r, "kind") == "org_alias"
            }
            links = {
                (string(r, "project_id", "项目编号"), aliases.get(norm(o), o), role)
                for r in records
                for role, key in [("coordinator", "coordinator"), ("participant", "participants")]
                for o in items(value(r, key))
            }
            check(
                "organization_sources",
                all(
                    e["from"] in ids and (e["from"], e["to"], e["role"]) in links
                    for e in products["organization_graph"]["edges"]
                ),
                "机构关系必须存在于源项目字段",
            )
            for row in products["theme_summary"]["known_funding"]:
                for key in ("total_cost", "contribution"):
                    if key not in row:
                        continue
                    vals = [
                        p[key]
                        for p in products["portfolio"]
                        if p.get("theme") == row["theme"]
                        and p.get("currency") == row["currency"]
                        and p.get(key) is not None
                    ]
                    check(
                        "funding_basis",
                        row[key]["known_amount"] == sum(vals) and row[key]["records"] == len(vals),
                        "按主题、币种分别核算总成本和贡献",
                    )
        elif code == "T10":
            removed = {
                norm(string(r, "doi", "source_doi")).removeprefix("https://doi.org/")
                for r in records
                if string(r, "kind", "role") in {"status", "notice"}
                and norm(string(r, "status")) in {"retracted", "撤稿"}
            }
            for p in products["citation_impact"]:
                impacted = set(p["cites"]) & removed
                supported = {
                    norm(string(r, "doi", "source_doi")).removeprefix("https://doi.org/")
                    for r in records
                    if string(r, "kind", "role") in {"independent", "research"}
                    and p["claim_id"]
                    and p["claim_id"] in items(value(r, "supports_claims"))
                }
                check(
                    "retraction_dependency",
                    set(p["affected_dois"]) == impacted
                    and (p["action"] != "withdraw" or bool(impacted) and set(p["cites"]) <= removed)
                    and (p["action"] != "unaffected" or (not impacted and bool(supported & set(p["cites"])))),
                    "撤回必须由本条引用关系支持",
                )
        elif code == "T09":
            for s in products["trend_series"]:
                ps = s["points"]
                check(
                    "battery_trend",
                    len(ps) >= 2
                    and all(a["cycle"] < b["cycle"] for a, b in pairwise(ps))
                    and s["observed_difference"] == ps[-1]["value"] - ps[0]["value"]
                    and bool(s["unit"]),
                    "趋势须有单调循环、同一单位和可复算的首末差值",
                )
        elif code == "T05":
            check(
                "chart_origin",
                all(r["figure"] in {4, 6, 7} and _chart_fact(r, records) for r in products["comparison"]),
                "逐项核对原件位置、年份、单位与数值角色；比例、差额和总量不可互换",
            )
            expected = [
                {"figure": card["figure"], **row}
                for card in products["figure_cards"]
                for row in card["labelled_values"]
            ]
            check("chart_comparison", expected == products["comparison"], "对照表与分图数据卡必须一致")
            check(
                "chart_discussion_inventory",
                _printed_prose_coverage(records, products["comparison"]),
                "图文明确表述的减少量、基线差和排放差额须纳入对照表",
            )
            required = {
                (r["source_id"], r["source_version"], tuple(r["location"]["bbox_pixels"]))
                for r in records
                if r["fields"].get("image_label") and r["location"].get("bbox_pixels")
            }
            included = {
                (r["source_id"], r["source_version"], tuple(r["location"]["bbox_pixels"]))
                for r in products["comparison"]
                if r["location"].get("bbox_pixels")
            }
            check("chart_printed_inventory", required == included, "所有已识别的打印标注均应纳入图表卡")
        elif code in {"B08", "B10", "T08"}:
            for m in products["matrix"]:
                check(
                    "attribution",
                    all(c["role"] and c["basis"] and c["evidence_ids"] for c in m["claims"])
                    and (bool(m["claims"]) or m["status"] == "missing"),
                    "证据矩阵主张须保留来源角色和表述层次",
                )
        elif code == "B09":
            check(
                "timeline_provenance",
                all(
                    r["event_time"] or r["formed_at"] or r["published_at"] or r["declassified_at"]
                    for r in products["timeline"]
                ),
                "时间线中的时间须有明确语义",
            )
        elif code == "T02":
            m = products["milestones"]
            keys = {(r["project"], r["event_type"], r["event_date"]) for r in m}
            check(
                "event_identity",
                len(keys) == len(m) and products["brief"]["event_count"] == len(m),
                "里程碑不得重复或混淆事件日和发布日期",
            )
        elif code == "T03":
            check(
                "roadmap_identity",
                all(
                    c["before"]["project"] == c["after"]["project"]
                    and c["before"]["feature"] == c["after"]["feature"]
                    and c["before"]["date"] <= c["after"]["date"]
                    for c in products["route_changes"]
                ),
                "路线比较必须同项目、同主题、按时序",
            )
        elif code == "B03":
            cards = products["image_catalog"]
            ids = {r["image_id"] for r in cards}
            check(
                "catalog_identity",
                len(ids) == len(cards)
                and all(
                    r["status"] == "abstain" or bool(r["visible_features"] and r["reference_records"])
                    for r in cards
                ),
                "没有可见依据和馆藏对应时须拒绝视觉识别",
            )
        elif code == "B02":
            check(
                "commitment_topics",
                {r["topic"] for r in products["commitments"]} == {"cessation", "humanitarian", "services"},
                "承诺卡须覆盖约定主题",
            )
        elif code == "T01":
            check(
                "research_methods",
                {"question", "comparison", "measurements", "model_results", "limitations"}
                == set(products["literature_card"]),
                "研究卡必须分开实测与模型结果",
            )
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        check("invalid_product", False, "产物形状或数值非法，无法独立复算")
    return checks
