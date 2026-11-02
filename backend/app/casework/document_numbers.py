"""Numerical checks. Reject incompatible populations, mixed revisions and missing cells."""

import calendar
import re
from collections import defaultdict
from datetime import date

from .document_common import norm, number, string, value


def observation(run):
    observations, context, window = {}, {}, {}
    pattern = re.compile(
        r"\bIn\s+([A-Z][\w'-]+)\s+region,\s+(?:the\s+)?(?:Mission|SMM)\s+recorded\s+([\d,]+)\s+ceasefire violations",
        re.IGNORECASE,
    )
    previous = re.compile(
        r"\b(?:previous|preceding)\s+24\s+hours,?\s+(?:(?:the\s+)?(?:Mission|SMM)|it)\s+recorded\s+([\d,]+)\s+ceasefire violations",
        re.IGNORECASE,
    )

    def add(region, current, prior, r, quote=None):
        if (
            current is None
            or prior is None
            or not all(isinstance(n, int) and n >= 0 for n in (current, prior))
        ):
            run.gap(f"{region} 本期或上期数量缺失/不合法")
            return
        key = (region.casefold(), current, prior)
        row = observations.setdefault(
            key,
            {
                "region": region,
                "count": current,
                "previous": prior,
                "change": current - prior,
                "scope": "本报告监测范围",
                "evidence_ids": [],
            },
        )
        ref = run.cite(r, quote)
        if ref not in row["evidence_ids"]:
            row["evidence_ids"].append(ref)

    for r in run.records:
        region = string(r, "region", "地区")
        if region:
            add(
                region,
                number(value(r, "current", "count", "数量")),
                number(value(r, "previous", "上期数量")),
                r,
            )
        raw = r.get("text", "")
        matches = list(pattern.finditer(raw))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else min(len(raw), m.end() + 1350)
            pm = previous.search(raw, m.end(), end)
            if pm:
                add(m[1], number(m[2]), number(pm[1]), r, raw[m.start() : pm.end()])
        for name, keys, regex in [
            ("report_number", ("report_number",), r"Daily\s+Report\s+([\d /-]+\d)"),
            (
                "published_date",
                ("published_date",),
                r"Daily\s+Report\s+\d+/\d+\s+([0-3]?\d\s+[A-Za-z]+\s+\d{4})",
            ),
            ("observed_until", ("observed_until", "截止时间"), r"as of\s+([^\.]{4,64})\."),
            ("timezone", ("timezone",), r"All times are in\s+([^\.]{4,75})\."),
        ]:
            if name in window:
                continue
            mt = re.search(regex, raw, re.IGNORECASE)
            explicit = value(r, *keys)
            if explicit is not None or mt:
                window[name] = {
                    "value": explicit if explicit is not None else " ".join(mt[1].split()),
                    "evidence_ids": [run.cite(r, None if explicit is not None else mt[0])],
                }
    for name, pattern in {
        "observed": r"facilitat\w*.{0,90}(?:repairs|infrastructure)|civilian infrastructure|民用设施|基础设施",
        "reported": r"reports? of.{0,70}(?:injur|civilian)|受访|据报",
        "assessed": r"was assessed|评估认为",
        "limitations": r"not operational|freedom of movement.{0,25}restrict|设备故障|观察限制",
    }.items():
        rows = run.find([pattern], 4 if run.retry else 2)
        # Never reclassify an explicit uncertainty merely because the sentence also mentions infrastructure.
        context[name] = rows
    if len({k[0] for k in observations}) < 2:
        run.gap("不足两个地区的本期及上期可核数值")
    if len({k[0] for k in observations}) != len(observations):
        run.gap("同一地区出现不一致的报告数量；各来源并列保留")
    if not {"report_number", "published_date", "observed_until"} <= window.keys():
        run.gap("报告编号、发布日期或观察截止时间缺失")
    if not context["observed"] or not context["limitations"]:
        run.gap("缺少民用设施事项或观察局限的原文出处")
    return {"observations": list(observations.values()), "report_window": window, "context": context}


def month_series(run):
    windows = defaultdict(dict)
    totals = defaultdict(list)
    rows = []
    checks = []
    plots = []
    for r in run.records:
        scope = (r["source_id"], str(r["source_version"]), string(r, "scope", "coverage"))
        month = string(r, "month", "月份")
        killed = number(value(r, "deaths", "killed", "死亡"))
        injured = number(value(r, "injured", "受伤"))
        if not month:
            if string(r, "kind", "role").casefold() in {"total", "reported_total"}:
                totals[scope].append((killed, injured, run.cite(r)))
            else:
                m = re.search(
                    r"including\s+([\d,]+)\s+killed\s+and\s+([\d,]+)\s+injured",
                    r.get("text", ""),
                    re.IGNORECASE,
                )
                if m and not totals[scope]:
                    totals[scope].append((number(m[1]), number(m[2]), run.cite(r, m[0])))
            continue
        try:
            y, m = map(int, month.split("-"))
            date(y, m, 1)
        except ValueError:
            run.gap("月份不符合YYYY-MM")
            continue
        month = f"{y:04}-{m:02}"
        if month in windows[scope]:
            run.gap(f"{month} 有重复观测，不得重复加总")
            windows[scope][month] = None
            continue
        if not all(isinstance(n, int) and n >= 0 for n in (killed, injured)):
            run.gap(f"{month} 含缺失或非整数人数")
            continue
        start, end = string(r, "start_date", "起日"), string(r, "end_date", "止日")
        complete = None
        if start and end:
            try:
                a, b = date.fromisoformat(start), date.fromisoformat(end)
                if a > b or a.strftime("%Y-%m") != month or b.strftime("%Y-%m") != month:
                    raise ValueError
                complete = a.day == 1 and b.day == calendar.monthrange(y, m)[1]
            except ValueError:
                run.gap(f"{month} 日期范围不合法")
        else:
            run.gap(f"{month} 缺少起止日，无法判定整月")
        row = {
            "month": month,
            "start_date": start or None,
            "end_date": end or None,
            "deaths": killed,
            "injured": injured,
            "total": killed + injured,
            "complete_month": complete,
            "basis": string(r, "basis", "口径") or None,
            "source_id": scope[0],
            "source_version": scope[1],
            "scope": scope[2] or None,
            "evidence_ids": [run.cite(r)],
        }
        windows[scope][month] = row
        rows.append(row)
    if not rows:
        run.gap("未提供可读月度表或可靠图表数值；不能由报告总数推造月度分布")
    for scope in sorted(set(windows) | set(totals)):
        series = windows[scope]
        source_rows = [r for _, r in sorted(series.items()) if r]
        missing = []
        if source_rows:
            yr, m = map(int, source_rows[0]["month"].split("-"))
            last = source_rows[-1]["month"]
            while f"{yr:04}-{m:02}" < last:
                if f"{yr:04}-{m:02}" not in series:
                    missing.append(f"{yr:04}-{m:02}")
                yr, m = (yr + 1, 1) if m == 12 else (yr, m + 1)
        if missing:
            run.gap("月度序列不连续：" + "、".join(missing))
        valid = bool(source_rows) and all(series.values()) and not missing
        actual = [sum(r[k] for r in source_rows) for k in ("deaths", "injured")] if valid else None
        targets = totals[scope]
        verified = bool(len(targets) == 1 and actual == list(targets[0][:2]))
        if not verified:
            run.gap("月度加总与报告总数尚未核对一致")
        checks.append(
            {
                "source_id": scope[0],
                "version": scope[1],
                "scope": scope[2],
                "computed": actual,
                "reported": [[a, b] for a, b, _ in targets],
                "agrees": verified,
                "missing_months": missing,
                "evidence_ids": [ref for _, _, ref in targets] + [r["evidence_ids"][0] for r in source_rows],
            }
        )
        plots.append(
            {
                "type": "line",
                "x": "month",
                "series": ["deaths", "injured"],
                "points": source_rows,
                "source_id": scope[0],
            }
        )
    return {
        "monthly": rows,
        "reconciliation": checks,
        "trend": plots,
        "limitations": run.find([r"actual numbers|corroborat|incomplete|漏报|核验|不完整"]),
    }


def compare_periods(run):
    groups = defaultdict(list)
    output = []
    for r in run.records:
        v = number(value(r, "value", "count", "数量"))
        indicator = string(r, "indicator", "metric", "指标")
        region = string(r, "region", "地区")
        if v is not None and indicator and region:
            groups[(indicator, region)].append((r, v))
    for (indicator, region), pairs in groups.items():
        pairs.sort(key=lambda p: string(p[0], "cutoff", "as_of"))
        comparable = len(pairs) == 2
        reasons = []
        if not comparable:
            reasons.append("需要且仅可比较两个统计期")
        series = []
        for r, v in pairs:
            series.append(
                {
                    "value": v,
                    "cutoff": string(r, "cutoff", "as_of"),
                    "published_date": string(r, "published_date"),
                    "definition": string(r, "definition", "口径"),
                    "coverage": string(r, "coverage", "覆盖范围"),
                    "method": string(r, "method", "方法"),
                    "unit": string(r, "unit", "单位"),
                    "evidence_ids": [run.cite(r)],
                }
            )
        for k in ("definition", "coverage", "method", "unit"):
            if any(not s[k] for s in series) or len({norm(s[k]) for s in series}) != 1:
                reasons.append(f"{k}缺失或不一致")
        try:
            dates = [date.fromisoformat(s["cutoff"]) for s in series]
            if len(dates) != len(set(dates)):
                raise ValueError
        except ValueError:
            reasons.append("缺少明确且不同的统计截至日")
        output.append(
            {
                "indicator": indicator,
                "region": region,
                "series": series,
                "comparable": not reasons,
                "change": series[-1]["value"] - series[0]["value"] if not reasons else None,
                "reasons": reasons,
            }
        )
    if not groups:
        run.gap("没有可按指标和地区配对的数值记录")
    return {"comparison": output, "coverage_notes": run.find([r"comparab|estimate|coverage|估计|分类|图例"])}
