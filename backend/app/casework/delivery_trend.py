"""B05 charts derived from the audited monthly counts, preserving gaps and populations."""

import re
from collections import defaultdict
from xml.etree import ElementTree as ET

from .delivery_files import safe_svg


def _period(month):
    year, number = map(int, month.split("-"))
    return year * 12 + number - 1


def _draw(rows, reconciled):
    months = [r["month"] for r in rows]
    if len(set(months)) != len(months):
        raise ValueError("月度记录重复，未绘图")
    ordered = sorted(rows, key=lambda r: r["month"])
    periods = [_period(r["month"]) for r in ordered]
    span = periods[-1] - periods[0]
    if span > 239:
        raise ValueError("月度窗口超过240个月，完整明细见CSV")
    width, height = max(740, min(1500, 130 + 68 * (span + 1))), 397
    left, right, top, base = 70, width - 45, 95, 285
    high = max(max(r["deaths"], r["injured"]) for r in ordered) or 1
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", viewBox=f"0 0 {width} {height}")

    def node(tag, **attrs):
        return ET.SubElement(svg, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})

    def label(x, y, value, size=13, align="start", color="#294654"):
        node("text", x=x, y=y, font_size=size, text_anchor=align, fill=color).text = str(value)

    node("title").text = "逐月平民伤亡（人）"
    node("desc").text = "死亡和受伤为两条独立序列。仅连接相邻完整月份，不完整月份为空心点。"
    node("rect", x=0, y=0, width=width, height=height, fill="#ffffff")
    label(left, 33, "逐月平民伤亡", 24)
    label(left, 60, "人数（人）")
    first = ordered[0]
    scope = " ".join(str(first.get("scope") or "范围见来源").split())[:26]
    source = " ".join(str(first.get("source_id") or "见证据索引").split())[:40]
    label(right, 32, "资料 " + source + " · " + scope, 12, "end")
    for x, name, color in ((left + 115, "死亡", "#245682"), (left + 220, "受伤", "#a64523")):
        node("circle", cx=x, cy=55, r=4, fill=color)
        label(x + 9, 60, name)
    for fraction in (0, 0.5, 1):
        y = base - (base - top) * fraction
        node("line", x1=left, y1=y, x2=right, y2=y, stroke="#c6d3da", stroke_width=1)
        label(left - 7, y + 4, f"{high * fraction:g}", align="end")
    incomplete = []
    unknown = []
    for field, color in (("deaths", "#245682"), ("injured", "#a64523")):
        previous = None
        for i, (row, period) in enumerate(zip(ordered, periods, strict=True)):
            x = (left + right) / 2 if not span else left + (right - left) * (period - periods[0]) / span
            y = base - (base - top) * row[field] / high
            complete = row.get("complete_month") is True
            if complete and previous and period == previous[0] + 1:
                node("line", x1=previous[1], y1=previous[2], x2=x, y2=y, stroke=color, stroke_width=2)
            dot = node(
                "circle", cx=x, cy=y, r=4, stroke=color, fill=color if complete else "#ffffff", stroke_width=2
            )
            ET.SubElement(
                dot, "title"
            ).text = f"{row['month']} {'死亡' if field == 'deaths' else '受伤'} {row[field]} 人 · {' '.join(map(str, row.get('evidence_ids', [])))}"
            previous = (period, x, y) if complete else None
            if field != "deaths":
                continue
            if i == len(ordered) - 1 or i % max(1, (len(ordered) + 12) // 13) == 0:
                label(x, base + 20, row["month"], 11, "middle")
            if not complete:
                (incomplete if row.get("complete_month") is False else unknown).append(row["month"])
    label(left, 335, "只连接相邻完整月份。空心点表示非完整月或覆盖未核实。")

    def listed(items):
        return "、".join(items[:6]) + (f" 等{len(items)}个月" if len(items) > 6 else "")

    label(
        left,
        359,
        "不完整月份：" + (listed(incomplete) or "未标记") + "　覆盖未核实：" + (listed(unknown) or "未标记"),
    )
    label(
        left,
        381,
        "同口径总数："
        + ("已核对一致" if reconciled else "尚未核对一致，不能据此确认总数")
        + "。逐月人数、日期与引文见表格附件。",
    )
    return safe_svg(ET.tostring(svg, encoding="unicode"))


def monthly_figures(products, evidence):
    rows = products.get("monthly") or []
    groups = defaultdict(list)
    omitted = []
    figures = []
    sources = {e["id"]: (e["source_id"], e["source_version"]) for e in evidence if e["location"]}
    for r in rows:
        key = (r.get("source_id"), r.get("source_version"), r.get("scope") or "")
        groups[key].append(r)
    reconciliation = {
        (r.get("source_id"), r.get("version"), r.get("scope") or ""): r.get("agrees") is True
        for r in products.get("reconciliation") or []
    }
    for key, group in groups.items():
        if len(figures) >= 32:
            omitted.append({"pointer": "/products/monthly", "reason": "趋势图已达32组上限，完整数据见CSV"})
            break
        if not all(
            re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", str(r.get("month")))
            and all(type(r.get(f)) is int and 0 <= r[f] <= 10**18 for f in ("deaths", "injured"))
            and any(sources.get(e) == key[:2] for e in r.get("evidence_ids", []))
            for r in group
        ):
            omitted.append(
                {"pointer": "/products/monthly", "reason": "月份、人数或证据不齐，未生成该口径趋势图"}
            )
            continue
        try:
            data = _draw(group, reconciliation.get(key, False))
        except ValueError as error:
            omitted.append({"pointer": "/products/monthly", "reason": str(error)})
            continue
        figures.append((f"figures/monthly-{len(figures) + 1:03}.svg", data, "/products/monthly", "figure"))
    return figures, omitted
