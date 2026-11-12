"""Reconcile displayed monthly-count claims against complete tool output, not a truncated projection.

This is a narrow deterministic check, not a claim of general semantic verification.
Original rejected prose remains in the private model checkpoint for audit.
"""

import calendar
import re
from datetime import date


def monthly_facts(content):
    products = content.get("products", {})
    rows = products.get("monthly")
    if rows is None:
        rows = []
        for record in content.get("records", []):
            fields = record.get("fields", {})
            month = fields.get("month")
            if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
                continue
            complete = None
            try:
                start = date.fromisoformat(fields["start_date"])
                end = date.fromisoformat(fields["end_date"])
                if start.strftime("%Y-%m") == end.strftime("%Y-%m") == month and start <= end:
                    complete = start.day == 1 and end.day == calendar.monthrange(end.year, end.month)[1]
            except (ValueError, KeyError, TypeError):
                pass
            rows.append({"month": month, "complete_month": complete})
    if not rows:
        return None
    return {
        "monthly_records": len(rows),
        "unique_months": len({r.get("month") for r in rows}),
        "complete_months": sum(r.get("complete_month") is True for r in rows),
        "partial_months": sum(r.get("complete_month") is False for r in rows),
        "unclassified_months": sum(r.get("complete_month") is None for r in rows),
    }


def check_monthly_claims(text, facts):
    if not facts:
        return []
    patterns = {
        "complete_months": r"(\d+)\s*个?\s*(?:完整月(?:份)?|整月|月(?:份)?\s*(?:均?为|是)\s*(?:完整月|整月))",
        "partial_months": r"(\d+)\s*个?\s*(?:非完整月|部分月|不完整月|月(?:份)?\s*(?:均?为|是)\s*(?:部分月|不完整月))",
        "monthly_records": r"(\d+)\s*(?:个|条)?\s*月度(?:记录|数据|观测|条目)",
        # A bare '11 months' may describe a subset ('the other 11 months are full').
        # Only an explicit overall coverage/count claim can be compared with the total.
        "unique_months": r"(?:共(?:计|有)?|涵盖|覆盖|合计|总计)\s*(\d+)\s*(?:个\s*月(?:份)?|月份|个月)(?!度)",
    }
    checks = []
    for field, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            observed = int(match[1])
            checks.append(
                {
                    "name": "narrative_" + field,
                    "passed": observed == facts[field],
                    "hard": True,
                    "expected": facts[field],
                    "observed": observed,
                    "claim": match[0],
                    "message": f"文字中的{match[0]}须与完整工具产物的{facts[field]}一致",
                    "repair_target": "assessment",
                }
            )
    return checks


def monthly_summary(facts):
    text = (
        f"工具产物包含 {facts['monthly_records']} 条月度记录，"
        f"覆盖 {facts['unique_months']} 个月份；其中 {facts['complete_months']} 个完整月，"
        f"{facts['partial_months']} 个部分月"
    )
    if facts["unclassified_months"]:
        text += f"，{facts['unclassified_months']} 条记录尚不能判定是否为完整月"
    return text + "。完整性和总数一致性以独立产品审核为准。"


def reconcile_assessment(text, facts):
    checks = check_monthly_claims(text, facts)
    rejected = any(not check["passed"] for check in checks)
    return {
        "status": "corrected" if rejected else "checked",
        "scope": "explicit_month_counts_only",
        "original_assessment": text,
        "assessment": monthly_summary(facts) if rejected else text,
        "checks": checks,
        "facts": facts,
        "action": "render_from_verified_month_counts" if rejected else None,
        "post_checks": check_monthly_claims(monthly_summary(facts), facts) if rejected else checks,
    }


def monthly_delivery_summary(products):
    facts = monthly_facts({"products": products})
    if not facts:
        return "尚未获得可复核的月度序列，不能从报告总数推算月度分布。"
    text = monthly_summary(facts)
    for item in products.get("reconciliation", []):
        computed = item.get("computed")
        if item.get("agrees") is True and isinstance(computed, list) and len(computed) == 2:
            text += (
                f" 月度死亡人数加总为 {computed[0]:,}，受伤人数加总为 {computed[1]:,}，"
                f"合计 {sum(computed):,}，与该来源同口径报告总数一致。"
            )
        else:
            text += " 月度与报告总数尚未通过同口径复算，不能据此宣称统计完整。"
    text += " 部分月份不能直接与完整月份比较；已记录人数不代表实际发生人数的完整统计。"
    return text
