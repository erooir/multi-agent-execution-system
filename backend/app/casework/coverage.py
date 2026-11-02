"""Known reference-batch inventory. A reduced specimen must not pass as the full reference set."""

import re

# Only explicit reference-scale counts; do not invent thresholds for qualitative work.
COUNTS = {
    "B01": ("observations", 2),
    "B03": ("image_catalog", 10),
    "B05": ("monthly", 13),
    "T05": ("figure_cards", 3),
    "G02": ("panels", 3),
    "G03": ("annual_series", 45),
    "G08": ("island_matrix", 5),
    "G09": ("od_comparison", 5),
    "G10": ("priority", 3),
}


def inventory(code, products, records=()):
    if code in {"B08", "B10"}:
        expected = {
            str(r["fields"]["question_id"])
            for r in records
            if r.get("fields", {}).get("kind") == "question" and r["fields"].get("question_id")
        }
        actual = {
            str(r["question_id"])
            for r in products.get("matrix", [])
            if isinstance(r, dict) and r.get("question_id")
        }
        bound = 20 if code == "B08" else 15
        return [
            {
                "name": "reference_question_inventory",
                "hard": False,
                "passed": bool(expected) and len(expected) <= bound and actual == expected,
                "message": f"逐项覆盖输入问题（最多{bound}项）；已收到{len(expected)}项、产物含{len(actual)}项",
            }
        ]
    rule = COUNTS.get(code)
    if not rule:
        return []
    field, count = rule
    rows = products.get(field)
    actual = len(rows) if isinstance(rows, (list, dict)) else 0
    checks = [
        {
            "name": "reference_inventory",
            "passed": actual == count,
            "hard": False,
            "message": f"参考批次要求{field}含{count}项，实际{actual}项",
        }
    ]
    if code == "G03":
        years = {r.get("year") for r in (rows or []) if isinstance(r, dict)}
        checks.append(
            {
                "name": "reference_window",
                "passed": years == set(range(1979, 2024))
                and products.get("anomaly", {}).get("baseline_years") == [1981, 2010],
                "hard": False,
                "message": "参考时间窗须逐年覆盖1979至2023年（含明确缺测）",
            }
        )
    if code == "T05":
        labels = {str(r.get("figure", "")) for r in (rows or []) if isinstance(r, dict)}
        figures = {int(n) for label in labels for n in re.findall(r"\d+", label)}
        checks.append(
            {
                "name": "reference_figures",
                "passed": figures == {4, 6, 7},
                "hard": False,
                "message": "需覆盖指定三张图并保留编号",
            }
        )
    return checks
