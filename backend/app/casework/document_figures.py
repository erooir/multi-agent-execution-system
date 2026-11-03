"""T05: locate captioned raster charts and read printed labels. No curve digitization."""

import re
from itertools import pairwise
from statistics import median

from PIL import Image

from .control import pulse
from .document_chart_ocr import _words
from .document_common import number, value
from .provider_versions import ocr_args

TARGETS = {4, 6, 7}


def _position(cm, tm):
    return (tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5])


def _point_marker(im, axis, text_top, height):
    """Check the printed point itself sits on the year tick, independently of the label's margin."""
    left, top = round(axis - height * 0.65), round(text_top - height * 1.45)
    roi = im.crop((left, top, round(axis + height * 0.65), round(text_top + height * 0.12))).convert("L")
    dark = {(x, y) for y in range(roi.height) for x in range(roi.width) if roi.getpixel((x, y)) < 75}
    while dark:
        start = dark.pop()
        group = [start]
        stack = [start]
        while stack:
            x, y = stack.pop()
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in dark:
                    dark.remove(point)
                    group.append(point)
                    stack.append(point)
        xs, ys = zip(*group, strict=True)
        w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if not (
            height * 0.32 <= w <= height * 1.3
            and 0.7 <= w / h <= 1.4
            and abs(left + sum(xs) / len(xs) - axis) < height * 0.25
        ):
            continue
        # An isolated diamond centered directly over its labelled year; do not use its height for a value.
        mid = (min(ys) + max(ys)) // 2
        if (
            min(xs) > 0
            and max(xs) < roi.width - 1
            and ys.count(mid) > max(ys.count(min(ys)), ys.count(max(ys))) * 1.4
        ):
            return True
    return False


def printed_labels(im):
    """Accept only a unit-bearing printed numeral next to exactly one labelled year tick."""
    scale = min(3, 2200 / max(im.size))
    big = im.resize((round(im.width * scale), round(im.height * scale)), Image.Resampling.BICUBIC)
    words = _words(big)
    ticks = [
        (int(t), x + w / 2, y)
        for t, x, y, w, h, c in words
        if re.fullmatch(r"(?:19|20)\d{2}", t) and c >= 0.85 and 0.77 * big.height < y < 0.91 * big.height
    ]
    ticks.sort(key=lambda z: z[1])
    if len(ticks) < 3 or any(a[0] >= b[0] for a, b in pairwise(ticks)):
        return [], False
    spacing = median([b[1] - a[1] for a, b in pairwise(ticks)])
    labels = []
    uncertain = False
    for t, x, y, w, h, c in words:
        if (
            not re.fullmatch(r"\d+(?:[.,]\d+)?", t)
            or not 0.20 * big.height < y < min(p[2] for p in ticks) - h
        ):
            continue
        adjacent = [v for v in words if 0 <= v[1] - (x + w) <= h and abs(v[2] - y) <= max(h, v[4]) * 0.35]
        if len(adjacent) != 1:
            continue
        unit, ux, uy, uw, uh, confidence = adjacent[0]
        # Some raster fonts OCR lowercase t as I and a subscript 2 as a comma.
        # Only a caption explicitly identifying CO2 may later confirm this unit.
        if not re.fullmatch(r"(?i)M[tI]CO[₂2,]?", unit):
            continue
        aligned = [
            yr
            for yr, xaxis, _ in ticks
            if abs(xaxis - x) < spacing * 0.19 and _point_marker(big, xaxis, y, h)
        ]
        if len(aligned) != 1 or confidence < 0.75 or c < 0.88:
            uncertain = True
            continue
        labels.append(
            (
                aligned[0],
                number(t),
                f"{t} {unit}",
                [round(k / scale) for k in (x, min(y, uy), ux + uw, max(y + h, uy + uh))],
            )
        )
    if labels:
        # Require corroboration by a second segmentation pass on the same pixels.
        corroboration = _words(big, psm=6)
        candidates = labels
        labels = [
            row
            for row in candidates
            if any(
                number(t) == row[1]
                and c >= 0.85
                and abs(x / scale - row[3][0]) < 4
                and abs(y / scale - row[3][1]) < 5
                for t, x, y, w, h, c in corroboration
            )
        ]
        uncertain |= len(labels) != len(candidates)
    return labels, not uncertain


def chart_objects(page, raw):
    """Associate a caption with the nearest image immediately above it in the same column."""
    captions = {}
    objects = []

    def text(t, cm, tm, font, size):
        match = re.match(r"\s*FIGURE\s+(\d+)\s*:", t)
        if match and int(match[1]) in TARGETS:
            captions[int(match[1])] = _position(cm, tm)

    def operand(op, args, cm, tm):
        if op == b"Do" and args and abs(cm[1]) + abs(cm[2]) < 0.001 and cm[0] > 50 and cm[3] > 50:
            objects.append((str(args[0]).lstrip("/"), cm[4], cm[5], cm[0], cm[3]))

    page.extract_text(visitor_text=text, visitor_operand_before=operand)
    for fig, (cx, cy) in captions.items():
        matches = [o for o in objects if cx - 30 <= o[1] <= cx + 75 and 0 <= o[2] - cy <= 45]
        if len(matches) != 1:
            continue
        key = matches[0][0]
        resource = page.get("/Resources", {}).get_object().get("/XObject", {}).get_object().get("/" + key)
        if not resource:
            continue
        obj = resource.get_object()
        if (
            obj.get("/Subtype") != "/Image"
            or not 0 < int(obj.get("/Width", 0)) * int(obj.get("/Height", 0)) <= 8_000_000
        ):
            continue
        pulse()
        if not ocr_args("eng"):
            yield {"figure": fig, "object": key}, "", {"figure": fig, "image_review": "ocr_unavailable"}
            continue
        im = page.images["/" + key].image
        if not im:
            continue
        labels, clear = printed_labels(im)
        yield (
            {"figure": fig, "object": key},
            "",
            {"figure": fig, "image_review": "inspected" if clear else "uncertain", "pixels": list(im.size)},
        )
        cap = re.search(rf"\bFIGURE\s+{fig}\s*:\s*([^.]{{3,340}})", raw)
        if not cap or not re.search(r"CO\s*[2₂]", cap[1], re.IGNORECASE):
            continue
        for yr, val, token, box in labels:
            yield (
                {"figure": fig, "object": key, "bbox_pixels": box, "pixels": list(im.size)},
                token,
                {
                    "figure": fig,
                    "image_label": True,
                    "year": yr,
                    "value": val,
                    "unit": "Mt CO₂",
                    "raw_ocr": token,
                },
            )


NUM = r"(\d+(?:\.\d+)?)"
YEAR = r"((?:19|20)\d{2})"
FLAGS = re.IGNORECASE | re.DOTALL


def chart_data(run):
    cards = {
        n: {"captions": [], "labelled_values": [], "discussion": [], "assumptions": []}
        for n in sorted(TARGETS)
    }
    active = None
    prior_source = None
    anchors = {}
    images = {}
    pending_labels = []

    def emit(n, r, quote, yr, v, unit, scenario, role, kind="forecast", basis="text", extra=()):
        ref = run.cite(r, quote)
        cards[n]["labelled_values"].append(
            {
                "value": number(v),
                "unit": unit,
                "year": yr,
                "scenario": scenario,
                "basis": basis,
                "kind": kind,
                "metric_role": role,
                "page": r["location"].get("page"),
                "location": r["location"],
                "source_id": r["source_id"],
                "source_version": r["source_version"],
                "context": quote[:600] if quote else None,
                "evidence_ids": list(dict.fromkeys([ref, *extra])),
            }
        )

    for row in run.records:
        raw = row["text"]
        f = row["fields"]
        n = number(f.get("figure"))
        if f.get("image_review") and n in TARGETS and row["location"].get("object"):
            images[(row["source_id"], n)] = f["image_review"]
            if f["image_review"] != "inspected":
                run.gap(f"图{n}缺可用本地OCR或图轴/标注未可靠识别")
            continue
        if f.get("image_label") and n in TARGETS and row["location"].get("bbox_pixels"):
            pending_labels.append((n, row))
            continue
        if n in TARGETS and f.get("title"):
            cards[n]["captions"].append(
                {
                    "title": str(f["title"]),
                    "page": row["location"].get("page"),
                    "evidence_ids": [run.cite(row)],
                }
            )
            if (
                f.get("value_source") in {"text", "label", "正文", "图上标注"}
                and number(f.get("value")) is not None
                and f.get("unit")
                and f.get("scenario")
                and f.get("year")
                and f.get("value_kind") in {"historical", "forecast", "历史", "预测"}
            ):
                emit(
                    n,
                    row,
                    None,
                    f["year"],
                    f["value"],
                    f["unit"],
                    f["scenario"],
                    f.get("metric_role", "来源标注值"),
                    f["value_kind"],
                    f["value_source"],
                )
            else:
                run.gap(f"图{n}结构化标注缺少年份、归属、来源或情景")
            continue
        if row["source_id"] != prior_source:
            active = None
            anchors = {}
        prior_source = row["source_id"]
        refs = list(re.finditer(r"\bFIGURE\s+(\d+)\b", raw, re.IGNORECASE))
        parts = [(active, raw[: refs[0].start()] if refs else raw)]
        for i, m in enumerate(refs):
            active = int(m[1])
            parts.append((active, raw[m.start() : refs[i + 1].start() if i + 1 < len(refs) else len(raw)]))
        for fig, part in parts:
            if fig not in TARGETS:
                continue
            card = cards[fig]
            cap = re.match(
                r"FIGURE\s+\d+\s*:\s*([^.]{3,400}\.)",
                part,
                re.IGNORECASE,
            )
            if cap:
                card["captions"].append(
                    {
                        "title": " ".join(cap[1].split()),
                        "page": row["location"].get("page"),
                        "evidence_ids": [run.cite(row, cap[0])],
                    }
                )
            # Match the complete grammatical relation, retaining the comparison and meaning of the number.
            reductions = rf"For\s+(?:{YEAR}\s+)?Fuel\s+Scenario\s+(\d+)\s*\(([^)]{{2,48}})\)\s*aircraft\s+technology\s+accounts\s+for\s+a\s+reduction\s+of\s+{NUM}\s*Mt\s+and\s+operations\s+accounts\s+for\s+an\s+additional\s+{NUM}\s*Mt\s+for\s+a\s+total\s+reduction\s+of\s+{NUM}\s*Mt\s+as\s+compared\s+with\s+Fuel\s+Scenario\s+(\d+)"
            for m in re.finditer(reductions, part, FLAGS):
                if m[1]:
                    anchors[fig] = (int(m[1]), run.cite(row, m[0]))
                if fig not in anchors:
                    continue
                yr, parent = anchors[fig]
                scenario = f"燃油情景{m[2]}（{' '.join(m[3].split())}）相对情景{m[7]}"
                for role, g in [("技术改善减少量", 4), ("运营改善额外减少量", 5), ("合计减少量", 6)]:
                    emit(fig, row, m[0], yr, m[g], "Mt", scenario, role, extra=[parent])
            pair = rf"Fuel\s+Scenario\s+(\d+)\s+results\s+in\s+{NUM}%\s+and\s+{NUM}%\s+reduction\s+in\s+fuel\s+burn\s+as\s+compared\s+with\s+Fuel\s+Scenario\s+(\d+)\s+in\s+{YEAR}\s+and\s+{YEAR},\s*respectively"
            for m in re.finditer(pair, part, FLAGS):
                for yr, v in [(m[5], m[2]), (m[6], m[3])]:
                    emit(fig, row, m[0], int(yr), v, "%", f"燃油情景{m[1]}相对情景{m[4]}", "燃油消耗相对减少")
            efficiency = rf"Fuel\s+Scenario\s+(\d+)\s*\(([^)]{{2,48}})\),\s*an\s+average\s+fuel\s+efficiency\s+of\s+{NUM}%\s+would\s+be\s+observed\s+by\s+{YEAR},\s*increasing\s+to\s+about\s+{NUM}%\s+by\s+{YEAR}"
            for m in re.finditer(efficiency, part, FLAGS):
                for yr, v, role in (
                    (m[4], m[3], "平均燃油效率改善率"),
                    (m[6], m[5], "平均燃油效率改善率（约）"),
                ):
                    emit(fig, row, m[0], int(yr), v, "%", f"燃油情景{m[1]}（{m[2]}）", role)
            factor = rf"assuming\s+that\s+{NUM}\s*kg\s+of\s+jet\s+fuel\s+burned\s+generates\s+{NUM}\s*kg\s+of\s+CO\s*[2₂]"
            for m in re.finditer(factor, part, FLAGS):
                years = re.search(rf"from\s+{YEAR}\s+to\s+{YEAR}", part, FLAGS)
                if number(m[1]) and years:
                    emit(
                        fig,
                        row,
                        m[0],
                        years[1] + "–" + years[2],
                        number(m[2]) / number(m[1]),
                        "kg CO₂/kg 燃油",
                        "仅计喷气燃油燃烧排放",
                        "燃烧换算系数",
                        kind="assumption",
                        extra=[run.cite(row, years[0])],
                    )
                card["assumptions"].append(
                    run.excerpt(
                        row,
                        m[0],
                        meaning="燃烧换算系数",
                        unit="kg CO₂/kg 燃油",
                        value=number(m[2]) / number(m[1]) if number(m[1]) else None,
                    )
                )
            delta = rf"delta\s+between\s+the\s+(CAEP/\d+)\s+and\s+(CAEP/\d+)\s+CO\s*[2₂]\s+trends\s+baselines\s+in\s+{YEAR}\s+is\s+approximately\s+{NUM}%"
            for m in re.finditer(delta, part, FLAGS):
                emit(fig, row, m[0], int(m[3]), m[4], "%", f"{m[1]} 与 {m[2]} 预测基线", "基线差（约）")
            minimum = rf"highest\s+anticipated\s+fuel\s+consumption\s+in\s+{YEAR}\s*\(Fuel\s+Scenario\s+(\d+)\).*?lowest\s+anticipated\s+fuel\s+consumption\s+in\s+{YEAR}\s*\(Fuel\s+Scenario\s+(\d+)\).*?minimum\s+CO\s*[2₂]\s+emission\s+gap\s+of\s+{NUM}\s*Mt\s+in\s+{YEAR}\s+compared\s+to\s+{YEAR}\s+emissions"
            for m in re.finditer(minimum, part, FLAGS):
                if m[1] == m[7] and m[3] == m[6] and len(m[0]) <= 600:
                    emit(
                        fig,
                        row,
                        m[0],
                        int(m[6]),
                        m[5],
                        "Mt CO₂",
                        f"{m[6]}情景{m[4]}比{m[1]}情景{m[2]}",
                        "最低排放差额",
                    )
            residual = rf"for\s+the\s+year\s+{YEAR}\s+and\s+{YEAR},\s+with\s+alternative\s+fuels\s+replacement,\s+there\s+is\s+a\s+{NUM}%\s+reduction\s+in\s+residual\s+CO\s*[2₂]\s+emissions"
            for m in re.finditer(residual, part, FLAGS):
                for yr in [m[1], m[2]]:
                    emit(fig, row, m[0], int(yr), m[3], "%", "替代燃料替换", "剩余CO₂排放相对减少")
            for m in re.finditer(
                r"(?:ICAO.{0,6}?\d+(?:\.\d+)?%\s+annual\s+fuel\s+efficiency\s+goal[^.]*|addition\s+to\s+the\s+reduction\s+from\s+technology\s+and\s+operations\s+improvements|SAF\s+scenario\s+chosen[^.]{8,240})",
                part,
                FLAGS,
            ):
                card["assumptions"].append(run.excerpt(row, m[0], meaning="情景前提"))
    for n, row in pending_labels:
        others = [
            v
            for v in cards[n]["labelled_values"]
            if v["source_id"] == row["source_id"]
            and v["year"] == value(row, "year")
            and v["metric_role"] == "剩余CO₂排放相对减少"
        ]
        if others and images.get((row["source_id"], n)) == "inspected":
            emit(
                n,
                row,
                row["text"],
                value(row, "year"),
                value(row, "value"),
                value(row, "unit"),
                "替代燃料替换",
                "图上剩余排放量",
                basis="label",
                extra=others[0]["evidence_ids"],
            )
        else:
            run.gap(f"图{n}打印标注缺少可核对的情景/年份上下文")
    for n, card in cards.items():
        if not card["captions"]:
            run.gap(f"图{n}缺标题/图注")
        if not card["labelled_values"]:
            run.gap(f"图{n}未定位可完成情景归属的明确标注或正文值；不估读曲线")
        for caption in card["captions"]:
            if caption["page"] and not any(
                r["location"].get("figure") == n and r["fields"].get("image_review") == "inspected"
                for r in run.records
            ):
                run.gap(f"图{n}未检查原图图层；无法确认图上明确标注")
        card["value_scope"] = "仅列正文明确值和图上标注；图轴刻度及未标注曲线不作为观测数据。"
        source_ids = {v["source_id"] for v in card["labelled_values"]}
        for row in run.records:
            if row["source_id"] not in source_ids or not row["location"].get("page"):
                continue
            for pattern, meaning in [
                (
                    r"Data\s+presented\s+for\s+years\s+earlier\s+than\s+\d{4}\s+are\s+reproduced[^.]{5,100}\.",
                    "历史部分沿用较早评估，由此不能将曲线读作逐年原始观测",
                ),
                (
                    r"assuming\s+no\s+airport\s+infrastructure\s+or\s+airspace\s+operational\s+constraints",
                    "远期预测未计入机场基础设施或空域运行容量约束",
                ),
            ]:
                for match in re.finditer(pattern, row["text"], FLAGS):
                    card["assumptions"].append(run.excerpt(row, match[0], meaning=meaning))
            if "TABLE 1:" in row["text"]:
                for m in re.finditer(
                    r"Fuel\s+(\d+)\s*[-–]\s*([^\d]{3,130}?Operational\s+Improvements)", row["text"], FLAGS
                ):
                    card["assumptions"].append(
                        run.excerpt(row, m[0], meaning=f"燃油情景{m[1]}定义（来源表1）")
                    )
        original = card["captions"][0]["title"] if card["captions"] else ""
        if re.search(r"international aviation", original, re.IGNORECASE):
            card["图题"] = (
                "含替代燃料生命周期减排的国际航空燃烧CO₂"
                if re.search("Alternative Jet Fuels", original, re.IGNORECASE)
                else "国际航空燃油消耗"
                if re.search("Fuel Burn", original, re.IGNORECASE)
                else "国际航空CO₂排放"
            )
        else:
            card["图题"] = original
        for row in card["labelled_values"]:
            row["图题"] = card["图题"]
            row["取值方式"] = "图上标注" if row["basis"] in {"label", "图上标注"} else "正文明确值"
            row["时间属性"] = {"historical": "历史", "历史": "历史", "assumption": "核算前提"}.get(
                row["kind"], "预测"
            )
    return {
        "figure_cards": [{"figure": n, **card} for n, card in cards.items()],
        "comparison": [{"figure": n, **v} for n, card in cards.items() for v in card["labelled_values"]],
    }
