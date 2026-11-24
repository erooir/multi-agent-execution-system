"""Independent checks against an edition's frozen civil aviation and battery research originals."""

import csv
import io
import math
import re
import struct
import unicodedata
import zlib
from collections import defaultdict
from decimal import Decimal
from zipfile import BadZipFile

from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .edition_battery import battery_sample


def _check(name, passed, message):
    return {"name": name, "passed": bool(passed), "message": message, "hard": True}


def _source(sources, prefix):
    (item,) = [s for s in sources if s["filename"].startswith(prefix)]
    return item


def _plain(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value)).casefold() if c.isalnum())


def _pdf(source, pages):
    reader = PdfReader(io.BytesIO(source["read"]()))
    return {n: reader.pages[n - 1].extract_text() or "" for n in pages}


def _record_text(source):
    return " ".join(str(r["text"]) for r in source["records"])


def _cites(sources, rows):
    ids = {r["id"] for s in sources for r in s["records"]}
    return bool(rows) and all(row.get("evidence_ids") and set(row["evidence_ids"]) <= ids for row in rows)


def _anchors(sources, tests):
    for prefix, pages, terms in tests:
        source = _source(sources, prefix)
        corpus = _plain(" ".join(_pdf(source, pages).values()))
        if not source["records"] or not all(_plain(t) in corpus for t in terms):
            return False
    return True


def _tower(sources, products):
    rows = products.get("机场年度状态", [])
    complete = {}
    for year, prefix, pages in [(2024, "T02-AN02", (21, 22)), (2025, "T02-AN03", (19, 20))]:
        raw = " ".join(_pdf(_source(sources, prefix), pages).values())
        raw = unicodedata.normalize("NFKC", raw)
        complete[year] = re.findall(r"AOP14\.1\(([A-Z]{4})\)\s*Remote Tower Services\s*100%\s*Completed", raw)
    expected = {(year, icao) for year, codes in complete.items() for icao in codes}
    actual = {(r["年度"], r["机场"]) for r in rows if r["状态"] == "Completed"}
    return [
        _check(
            "remote_tower_annual_tables",
            all(complete.values())
            and len(rows) == len(actual)
            and actual == expected
            and _cites(sources, rows),
            "按两份原版年度表逐机场代码与 Completed 字段复核。",
        ),
        _check(
            "remote_tower_trial",
            _anchors(sources, [("T02-AN03", (87,), ("trial", "final approval", "multi airport"))]),
            "核对 AOP14.2 条目明确区分多机场试运行与最终批准。",
        ),
    ]


def _amount(display):
    return None if display is None else Decimal(re.sub(r"[^0-9,.-]", "", display).replace(",", "."))


def _portfolio(sources, products):
    import json

    source = _source(sources, "T04-F01")
    original = json.loads(source["read"]().decode("utf-8-sig"))
    rows = products.get("协调与参与关系", [])
    by_id = {str(row["ID"]): row for row in rows}
    complete = len(original) == len(rows) == len(by_id) and _cites(sources, rows)
    fields = money = True
    themes = defaultdict(lambda: [[0, Decimal(0)], [0, Decimal(0)]])
    for index, project in enumerate(original):
        row = by_id.get(str(project["project_id"]), {})
        if not row:
            complete = False
            continue
        fields &= (
            row["协调机构"] == project["coordinator"]
            and sorted(row["参与机构"]) == sorted(project["participants"])
            and row["出处"] == project["source"]
        )
        located = [
            s["text"]
            for s in source["records"]
            if str(project["project_id"]) in s["text"] and f"$[{index}]" in s["locator"]
        ]
        for j, (label, key) in enumerate(
            (("总成本", "total_cost_display"), ("EU 贡献", "eu_contribution_display"))
        ):
            display = project.get(key)
            aggregate = themes[row["主题"]][j]
            money &= any(_plain(label + (display or "未披露")) in _plain(line) for line in located)
            if display is not None:
                aggregate[0] += 1
                aggregate[1] += _amount(display)
    combined = _record_text(source)
    for theme, aggregates in themes.items():
        line = next((x["text"] for x in source["records"] if x["text"].startswith(theme + "：")), "")
        for label, (count, total) in zip(("总成本", "EU 贡献"), aggregates, strict=True):
            money &= f"{label}已披露 {count} 项合计 €{total:,.2f}" in line
    return [
        _check(
            "cordis_project_relations",
            complete and fields,
            "从原始 JSON 比对唯一项目、协调方、全部参与方及出处。",
        ),
        _check(
            "cordis_funding_by_theme",
            money and bool(combined) and bool(themes),
            "累计各主主题已公开金额，分别核对总成本与欧盟贡献。",
        ),
    ]


def _fuel(sources, products):
    source = _source(sources, "T05-AN01")
    wb = load_workbook(io.BytesIO(source["read"]()), read_only=True, data_only=True)
    try:
        values = list(wb["Low"].iter_rows(min_row=1, max_row=33, max_col=10, values_only=True))
    finally:
        wb.close()
    rows = products.get("燃料构成", [])
    indexed = {(row["年份"], row["情景"]): row for row in rows}
    mix = len(indexed) == len(rows) == 4 and _cites(sources, rows)
    factors = True
    for pos, year, factor_row in [(4, 2035, 32), (13, 2050, 33)]:
        assert_year = values[pos - 1][0] == year and values[factor_row - 1][0] == year
        for scen, col, fc in (("F1", 4, 2), ("F2", 8, 3)):
            row = indexed.get((year, scen), {})
            amounts = [r[col] for r in values[pos - 1 : pos + 7]]
            total = amounts[0]
            if not isinstance(total, (int, float)) or total <= 0:
                raise ValueError("国际年度用量必须为正数")
            saf = sum(v for v in amounts[1:4] if isinstance(v, (int, float)))
            lcaf, petroleum = amounts[4], amounts[7]
            mix &= (
                assert_year
                and values[2][col] == "Intl"
                and math.isclose(total, saf + lcaf + petroleum)
                and math.isclose(saf + lcaf, amounts[5])
            )
            for key, num in [
                ("国际总量（千吨/年）", total),
                ("SAF占比%", 100 * saf / total),
                ("LCAF占比%", 100 * lcaf / total),
                ("常规占比%", 100 * petroleum / total),
            ]:
                mix &= key in row and math.isclose(row[key], num, rel_tol=0, abs_tol=0.005001)
            factor = 100 * values[factor_row - 1][fc]
            adoption = 100 * (saf + lcaf) / total
            located = [r["text"] for r in source["records"] if r["text"].startswith(f"{year} {scen}")]
            factors &= any(
                f"采用率 {adoption:.2f}%" in text
                and f"减排因子 {factor:.2f}%" in text
                and f"{adoption - factor:.2f} 个百分点" in text
                for text in located
            )
    return [
        _check(
            "icao_low_international_mix",
            mix,
            "逐单元格复算两年份两情景的国际用量与三类份额，核对替代燃料小计。",
        ),
        _check("icao_emission_factor", factors, "分别复核采用率、原表减排因子及其百分点差。"),
    ]


def _cert(sources, products):
    source = _source(sources, "T06-AN01")
    original = list(csv.DictReader(io.StringIO(source["read"]().decode("utf-8-sig"))))
    rows = products.get("timeline", [])
    actual = {(r["项目"], r["日期"], r["主体"], r["节点"], r["泳道"]) for r in rows}
    expected = set()
    for r in original:
        event = r["event"].casefold()
        if "air operator" in event or "air carrier" in event:
            lane = "运营主体"
        elif "production certificate" in event or "standard airworthiness" in event:
            lane = "单机及生产"
        else:
            lane = "型号设计"
        expected.add((r["program"], r["milestone_date"], r["subject"], r["event"], lane))
    return [
        _check(
            "certification_dated_lanes",
            bool(expected)
            and expected == actual
            and len(rows) == len(original) == len(actual)
            and _cites(sources, rows),
            "对照 CSV 事件、原日期精度、获证对象与三条泳道。",
        )
    ]


def _battery(sources, products):
    source = _source(sources, "T09-F01")
    names, count, start, end = battery_sample(source["read"]())
    rows = products.get("汇总", [])
    index = {r["电池"]: r for r in rows}
    sample = index.get(names[0], {})
    matches = (
        sample.get("参考放电次数") == count
        and math.isclose(sample.get("首容量Ah", -1), start, abs_tol=0.000501)
        and math.isclose(sample.get("末容量Ah", -1), end, abs_tol=0.000501)
    )
    return [
        _check(
            "battery_mat_inventory",
            len(names) == len(rows) and set(names) == set(index) and _cites(sources, rows),
            "从原 ZIP 遍历全部 MAT，核对电池编号与汇总。",
        ),
        _check(
            "battery_reference_capacity",
            matches,
            f"独立读取 {names[0]} MAT 的参考放电步骤，核对次数和首末时刻积分容量。",
        ),
    ]


def _podcast(sources, products):
    import av
    import numpy as np

    audio = _source(sources, "T07-M02")
    text = _source(sources, "T07-M01")
    notes = products.get("回听索引", [])
    transcript = _plain(" ".join(_pdf(text, (1, 2, 3, 4, 5)).values()))
    clips_ok = bool(notes) and _cites(sources, notes)
    quotes_ok = all(_plain(n["短摘"]) in transcript for n in notes)
    with av.open(io.BytesIO(audio["read"]())) as container:
        stream = container.streams.audio[0]
        duration = float(stream.duration * stream.time_base)
        for n in notes:
            a, b = n["起始秒"], n["结束秒"]
            if not 0 <= a < b <= duration or b - a > 90:
                clips_ok = False
                continue
            container.seek(int(a / stream.time_base), stream=stream)
            sound = False
            for frame in container.decode(stream):
                if frame.time is not None and frame.time > b:
                    break
                if frame.time is not None and frame.time >= a and np.any(frame.to_ndarray()):
                    sound = True
                    break
            clips_ok &= sound
    return [
        _check(
            "podcast_audio_windows", clips_ok, "按每段原始时间码解码 MP3 并核对时长、有效声信号和证据链接。"
        ),
        _check(
            "podcast_transcript_index",
            quotes_ok and bool(notes),
            "逐条回查回听索引的英文短摘与 NASA 原始逐字稿。",
        ),
    ]


def _document_case(code, sources, products):
    tests = {
        "T01": [
            ("T01-AN01", (2, 3, 5), ("V2527", "Ref2", "ice crystal")),
            ("T01-M01", (1,), ("HEFA", "ice particle", "soot", "model")),
        ],
        "T03": [("T03-M03", (1, 2), ("hydrogen-propulsion", "heat exchangers", "Grenoble", "Munich"))],
        "T08": [
            ("T08-AN01", (7,), ("supervised", "symbolic")),
            ("T08-AN02", (177, 178, 198, 218), ("structured knowledge base", "DNN", "damage detection")),
        ],
    }
    key = {"T01": "候选试验问题", "T03": "条件化路径", "T08": "应用研判矩阵"}[code]
    required = {"T01": 2, "T03": 3, "T08": 3}[code]
    verified = _anchors(sources, tests[code])
    if code in {"T01", "T03"}:
        source = _source(sources, "T01-M01" if code == "T01" else "T03-M03")
        raw = " ".join(" ".join(_pdf(source, (1,) if code == "T01" else (1, 2)).values()).split())
        terms = (
            [
                (r"(\d+)\s*% reduction in ice particle", "%"),
                (r"(\d+)\s*% reduction in soot", "%"),
                (r"(\d+)\s*% decrease in contrail radiative forcing", "%"),
            ]
            if code == "T01"
            else [
                (r"demonstrated a ([\d.]+)\s*MW", "MW"),
                (r"([\d.]+)-megawatt electric", "MW"),
                (r"ground testing is planned for (\d{4})", ""),
            ]
        )
        reported = re.sub(r"\s+", "", _record_text(source))
        for expr, unit in terms:
            found = re.search(expr, raw, re.IGNORECASE)
            verified &= found is not None and found[1] + unit in reported
    return [
        _check(
            "research_original_pages",
            verified,
            "从指定原始 PDF 的关键页独立复核研究对象、技术边界和文献依据。",
        ),
        _check(
            "research_cited_comparisons",
            len(products.get(key, [])) == required and _cites(sources, products[key]),
            "核对交付矩阵的全部分类或条件路径及其来源。",
        ),
    ]


def _historic(sources, products):
    original = _source(sources, "T15-M01")
    with Image.open(io.BytesIO(original["read"]())) as photo:
        w, h = photo.size
        photo.verify()
    matches = f"{w}×{h}" in _record_text(original) or any(
        f"{w}×{h}" in r["locator"] for r in original["records"]
    )
    if any(s["filename"].startswith("T10-AN01") for s in sources):
        project = close = _source(sources, "T10-AN01")
        archive = _pdf(project, (3, 4, 5))
        project_text = " ".join(archive[3].split())
        close_text = " ".join((archive[4] + " " + archive[5]).split())
        chapter = _source(sources, "T10-AN02")
        recalled = _pdf(chapter, (27, 30))
        matches &= "noise-shielding" in recalled[27] and "ground to flight" in recalled[30]
        matches &= bool(chapter["records"])
    else:
        project = _source(sources, "T15-M03")
        close = _source(sources, "NT-M01")
        project_text = project["read"]().decode("utf-8-sig")
        close_text = close["read"]().decode("utf-8-sig")
    for source, raw, patterns in [
        (project, project_text, (r"wingspan of ([\d.]+) feet", r"(\d+)-pound gross weight")),
        (
            close,
            close_text,
            (
                r"total of (\d+) flights",
                r"(\d+) of them as the C-model",
                r"B version that flew (\d+) flights",
            ),
        ),
    ]:
        reported = _record_text(source)
        for pattern in patterns:
            found = re.search(pattern, raw, re.IGNORECASE)
            matches &= (
                found is not None
                and re.search(r"(?<![\d.])" + re.escape(found[1]) + r"(?![\d.])", reported) is not None
            )
    refs = products.get("图文证据矩阵", [])
    return [
        _check(
            "historic_image_and_text",
            matches and _cites(sources, refs),
            "读取原始图片尺寸与 B 型项目规格，复核收尾文件的试验范围和架次。",
        )
    ]


HANDLERS = {
    "T02": _tower,
    "T04": _portfolio,
    "T05": _fuel,
    "T06": _cert,
    "T07": _podcast,
    "T09": _battery,
    "T10": _historic,
}


def checks(code, sources, products):
    """Fail closed if a required frozen source or work product cannot be independently inspected."""
    if code not in HANDLERS and code not in {"T01", "T03", "T08"}:
        return []
    try:
        if code in HANDLERS:
            return HANDLERS[code](sources, products)
        return _document_case(code, sources, products)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        StopIteration,
        ArithmeticError,
        struct.error,
        zlib.error,
        OSError,
        ImportError,
        BadZipFile,
        PdfReadError,
    ):
        return [
            _check(
                "research_original_readable",
                False,
                "指定公开原件或对应交付表的结构无法复核，请检查冻结版本和字段。",
            )
        ]
