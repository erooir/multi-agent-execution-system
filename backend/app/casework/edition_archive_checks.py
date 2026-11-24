"""Independent checks for the frozen civil and historical research edition."""

import calendar
import csv
import io
import json
import math
import re
from datetime import date
from itertools import pairwise

import pypdfium2 as pdfium
from PIL import Image


class _Mismatch(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Mismatch(reason)


def _near(value, expected, tolerance=0.011):
    return isinstance(value, (int, float)) and math.isclose(value, expected, rel_tol=0, abs_tol=tolerance)


def _one(sources, suffix):
    items = [s for s in sources if s["filename"].lower().endswith(suffix.lower())]
    _require(len(items) == 1, f"原件未唯一匹配 {suffix}")
    return items[0]


def _read(source):
    return source["read"]()


def _pages(source, numbers):
    result = {}
    doc = pdfium.PdfDocument(_read(source))
    try:
        for n in set(numbers):
            _require(1 <= n <= len(doc), "证据页码超出原件")
            pg = doc[n - 1]
            try:
                tp = pg.get_textpage()
                try:
                    result[n] = " ".join(tp.get_text_bounded().split())
                finally:
                    tp.close()
            finally:
                pg.close()
    finally:
        doc.close()
    return result


def _norm(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _capture(text, pattern):
    found = re.search(pattern, text, re.IGNORECASE)
    _require(found, "原始文件未找到预期指标或定位")
    return found[1]


def _integer(text, pattern):
    return int(_capture(text, pattern).replace(",", ""))


def _number_tokens(text):
    return {int(s.replace(",", "")) for s in re.findall(r"[0-9][0-9,]*", text)}


def _damage(sources, products):
    src = _one(sources, ".pdf")
    text = _pages(src, [1])[1]
    observations = {}
    for r in src["records"]:
        m = re.search(r"(\d{4}-\d{2}-\d{2}).*?摧毁(\d+)、严重损伤(\d+)、中度损伤(\d+)", r["text"])
        if m:
            observations[m[1]] = tuple(map(int, m.groups()[1:]))
    rows = sorted(products["五期统计"], key=lambda r: r["日期"])
    _require(
        len(observations) == len(rows) == 5 and {r["日期"] for r in rows} == observations.keys(),
        "五个观测时点未能逐一匹配",
    )
    previous = None
    for row in rows:
        observed = observations[row["日期"]]
        _require(
            tuple(row[k] for k in ("摧毁", "严重损伤", "中度损伤")) == observed,
            "柱图的分级读数与观察记录不一致",
        )
        total = sum(observed)
        _require(
            row["合计"] == total and row["区间净增"] == (None if previous is None else total - previous),
            "五期合计或相邻净增错误",
        )
        previous = total
    last = rows[-1]
    for key, pattern in (
        ("合计", r"total of ([\d,]+) affected"),
        ("摧毁", r"([\d,]+) of these were destroyed"),
        ("严重损伤", r"([\d,]+) severely damaged"),
        ("中度损伤", r"([\d,]+) moderately damaged"),
    ):
        _require(last[key] == _integer(text, pattern), "末期读数与原版正文不一致")
    return "由五组图上原始观察复算合计及四段净增，另与原版 PDF 正文的末期合计和三个分级交叉核对。各期柱标签使用人工读数。"


def _population(sources, products):
    text = _pages(_one(sources, ".pdf"), [15])[15]
    total = _integer(text, r"June 2017 \(cumulative\) ([\d,]+)")
    outstanding = _integer(text, r"still displaced on 29 June 2017 ([\d,]+)")
    items = [_integer(text, rf"Phase {i}:.*?\(cumulative\) ([\d,]+)") for i in (1, 2, 3)] + [
        outstanding,
        _integer(text, r"returnees as of 29 June 2017 ([\d,]+)"),
        _integer(text, r"camps and emergency sites as of 29 June 2017 ([\d,]+)"),
        _integer(text, r"out-of-camp settings as of 29 June 2017 ([\d,]+)"),
    ]
    prefixes = ("第一阶段", "第二阶段", "第三阶段", "仍在外", "已返乡", "营地及应急安置点", "营地外")
    rows = products["人数与分母对照"]
    _require(
        len(rows) == 7 and sum(items[:3]) == sum(items[3:5]) == total and sum(items[5:]) == outstanding,
        "三组人数未能闭合",
    )
    for i, prefix in enumerate(prefixes):
        (row,) = [r for r in rows if r["项目"].startswith(prefix)]
        base = total if i < 5 else outstanding
        _require(
            row["人数"] == items[i]
            and row["分母人数"] == base
            and _near(row["份额百分比"], 100 * items[i] / base),
            "人数、分母或占比与原始 IOM 表不符",
        )
    return "从公开报告关键指标表独立提取人数，重新核对三个阶段、两种期末状态和两类场所的七项份额及各自分母。"


def _catalogue(sources, products):
    meta = json.loads(_read(_one(sources, ".json")).decode("utf-8-sig"))["photos"]
    files = {s["filename"]: s for s in sources if s["filename"].lower().endswith(".jpg")}
    rows = {r["图片"]: r for r in products["逐图编目"]}
    _require(
        len(rows) == len(files) == len(meta) == 10 and set(files) == {s["file"] for s in meta},
        "照片与馆藏文件没有一一对应",
    )
    for item in meta:
        row = rows[item["id"]]
        with Image.open(io.BytesIO(_read(files[item["file"]]))) as im:
            _require(im.format == "JPEG" and list(im.size) == row["原图像素"], "图像类型或真实尺寸不符")
            im.verify()
        for key, field in (
            ("馆藏标注", "title"),
            ("档案号", "archive_id"),
            ("原始日期", "original_date_label"),
            ("页面", "catalogue_url"),
        ):
            _require(row[key] == item[field], "归档名称、编号、日期或来源链接不符")
        _require(row["所见"] and row["置信与拒判"], "缺少直接观察或判断边界")
    return (
        "已重读原始馆藏 JSON 和十张 JPEG，逐一核对编号、来源、日期及像素尺寸；具体部件描述属于照片人工判读。"
    )


def _price(sources, products):
    src = _one(sources, ".pdf")
    raw = _pages(src, [2])[2]
    _require("YER/L" in raw and "YER/Kg" in raw, "原图缺少两种商品的计量单位")
    table = {}
    for row in src["records"]:
        m = re.search(
            r"^(阿姆兰|荷台达|萨那)(汽油|面粉)，10月约 (\d+)–(\d+) .*?11月16日约 (\d+)–(\d+)", row["text"]
        )
        if m:
            table[m[1], m[2]] = list(map(int, m.groups()[2:]))
    rows = products["市场商品变化"]
    _require(
        len(rows) == len(table) == 6 and {(r["市场"], r["商品"]) for r in rows} == table.keys(),
        "六组市场与商品原始读数不全",
    )
    for r in rows:
        a, b, c, d = table[r["市场"], r["商品"]]
        _require(
            r["单位"] == ("里亚尔/升" if r["商品"] == "汽油" else "里亚尔/公斤")
            and r["十月读数区间"] == [a, b]
            and r["十一月十六日区间"] == [c, d],
            "单位或时点原始读数不符",
        )
        lo, hi = r["变化区间百分比"]
        _require(
            a > 0
            and a <= b
            and 0 < c <= d
            and _near(lo, (c / b - 1) * 100, 0.051)
            and _near(hi, (d / a - 1) * 100, 0.051)
            and _near(r["变化中点百分比"], ((c + d) / (a + b) - 1) * 100, 0.051),
            "上下界或中点变幅计算错误",
        )
    return "按原图人工取值区间逐项重算六组中点与端点涨跌幅，并独立核对两幅原图标注的计量单位。"


def _supply(sources, products):
    src = _one(sources, ".csv")
    lines = list(csv.DictReader(io.StringIO(_read(src).decode("utf-8-sig"))))
    expected = {}
    for r in lines:
        y, m = map(int, r["month"].split("-"))
        days = calendar.monthrange(y, m)[1]
        _require(days == int(r["days"]), "月份天数与日历不符")
        tons = float(r["us_short_tons"]) + float(r["uk_short_tons"])
        _require(_near(tons, float(r["total_short_tons"]), 0.11), "原始运输量分组未闭合")
        expected[r["month"]] = (tons, days, r)
    periods = sorted(expected)
    daily = {k: t / n for k, (t, n, _) in expected.items()}
    rows = products["历史日均供给"]
    _require(
        len(rows) == len(periods) == 10
        and {r["月份"] for r in rows} == set(periods)
        and all(_near(r["短吨每日"], daily[r["月份"]]) for r in rows),
        "日均运输量与原始分组 CSV 不符",
    )
    prev, nxt = min(pairwise(periods), key=lambda pair: daily[pair[1]] - daily[pair[0]])
    parts = {r["组别"]: r for r in products["最大降幅对称分解"]}
    _require(set(parts) == {"美国组", "英国组"}, "两组贡献不全")
    for name, g in (("美国组", "us"), ("英国组", "uk")):
        f = []
        q = []
        for month in (prev, nxt):
            _, days, r = expected[month]
            count = int(r[g + "_flights"])
            f.append(count / days)
            q.append(float(r[g + "_short_tons"]) / count)
        cf = (f[1] - f[0]) * sum(q) / 2
        cq = (q[1] - q[0]) * sum(f) / 2
        row = parts[name]
        _require(
            all(
                _near(row[k], v)
                for k, v in (("日均班次贡献", cf), ("单班载量贡献", cq), ("合计短吨每日", cf + cq))
            ),
            "最大降幅期间的对称分解错误",
        )
    balances = products["冬季月末余额"]
    stock = products["最低期初储备"]
    winter = [s for s in periods if "1948-10" <= s <= "1949-02"]
    _require(len(winter) == 5 and len(balances) == 15 and len(stock) == 3, "三档五个月的余额或储备缺失")
    for rate in (4500, 5500, 6500):
        running = 0
        values = {}
        for month in winter:
            ton, days, _ = expected[month]
            running += ton - days * rate
            values[month] = running
            (row,) = [r for r in balances if r["需求短吨每日"] == rate and r["月份"] == month]
            _require(_near(row["月末余额短吨"], running), "情景月末结转与原始运量不符")
        m = min(values, key=values.get)
        (row,) = [r for r in stock if r["日需求假设"] == rate]
        _require(
            row["最紧月末"] == m and _near(row["最低期初储备短吨"], max(0, -values[m])),
            "最紧月份或最低期初储备错误",
        )
    return "从原表及日历重新计算十个月的日均供给、降幅最大的相邻月、两组对称分解、三档需求的十五个月末余额和最低储备。"


def _visits(sources, products):
    src = _one(sources, ".pdf")
    observed = {}
    for record in src["records"]:
        for m in re.finditer(r"第(\d+)周 (\d+)/(\d+)/(\d+)", record["text"]):
            observed[int(m[1])] = tuple(map(int, m.groups()[1:]))
    rows = products["近似周趋势"]
    keys = ("冲突相关", "普通内科和非创伤外科", "意外创伤")
    _require(
        set(observed) == set(range(2, 23))
        and len(rows) == 21
        and {r["运行周"] for r in rows} == set(observed),
        "观察周数不对",
    )
    _require(
        all(tuple(r[k] for k in keys) == observed[r["运行周"]] for r in rows), "周趋势与原始近似读数不符"
    )
    doc = pdfium.PdfDocument(_read(src))
    try:
        pg = doc[3]
        try:
            bm = pg.render(scale=1057 / pg.get_width())
            try:
                img = bm.to_pil().convert("L")
            finally:
                bm.close()
        finally:
            pg.close()
    finally:
        doc.close()
    # Axes calibrated on Figure 1: week 1..23, equally spaced 0..300 visit grid.
    # Neighbourhood spans dotted-line gaps and the precision of the manual reading.
    for i, values in observed.items():
        x = round((268 + (i - 1) * (850 - 268) / 22) * img.width / 1057)
        for value in values:
            y = round((793 - value * 339 / 300) * img.height / 1420)
            ink = img.crop((x - 9, y - 8, x + 10, y + 9)).getextrema()[0]
            _require(ink < 125, "一项周度读数在原图刻度附近未找到曲线")
    summary = products["分段周均及构成"]
    _require(len(summary) == 3, "三个周段未列全")
    for lo, hi in ((2, 7), (8, 14), (15, 22)):
        sums = list(map(sum, zip(*(observed[n] for n in range(lo, hi + 1)), strict=True)))
        (row,) = [r for r in summary if r["周段"] == f"{lo}–{hi}"]
        for name, total in zip(keys, sums, strict=True):
            _require(
                _near(row[name]["周均约"], total / (hi - lo + 1), 0.051)
                and _near(row[name]["份额约"], total / sum(sums) * 100, 0.051),
                "阶段周均或份额分母错误",
            )
    return "对21周三类原始近似观察核对图1曲线像素，并按三个周段重新汇总。线型在交叉处难以自动区分，类别与近似读点仍以图上标注为准。"


def _media(sources, products, code):
    is_conference = code == "B04"
    pdf = _one(sources, "M02.pdf" if is_conference else "M01.pdf")
    medium = _one(sources, ".m4a" if is_conference else ".mp3")
    rows = products["发言与回听索引" if is_conference else "跨模态回听定位"]
    _require(len(rows) >= 3 if is_conference else len(rows) == 3, "回听片段数量不足")
    pdf_pages = _pages(pdf, [r["原稿页"] for r in rows])
    locations = {r["id"]: r["locator"] for r in medium["records"]}
    total_time = 0
    previous_end = 0
    for row in rows:
        quote = row["对应原稿英文" if is_conference else "核对锚点"]
        _require(quote and _norm(quote) in _norm(pdf_pages[row["原稿页"]]), "片段英文锚点不在原件标注页")
        times = [row["开始"], row["结束"]] if is_conference else row["音频区间"].split("–")
        interval = "–".join(times)
        _require(
            any(interval in locations.get(i, "") for i in row["evidence_ids"]),
            "片段没有指向本场原音轨的定位记录",
        )
        starts = []
        for time in times:
            h, m, s = time.split(":")
            _require(0 <= int(m) < 60 and 0 <= float(s) < 60 and int(h) >= 0, "时码格式不合法")
            starts.append(3600 * int(h) + 60 * int(m) + float(s))
        a, b = starts
        _require(previous_end <= a < b, "回听片段为空、倒置或重叠")
        previous_end = b
        total_time += b - a
    if not is_conference:
        _require(total_time <= 900, "抽听超过规定的15分钟")
        _require(len(products["时点与表述层次"]) >= 3, "私下讨论与公开发表的时间线缺失")
    return "逐条对照原版PDF中的英文锚点与页码，检查起止顺序、回听时段和对应音轨证据。声画对齐由预处理时的解码与听辨记录提供。"


def _documents(sources, products, code):
    if code == "B08":
        a = _pages(_one(sources, "M01.pdf"), [9, 15])
        b = _pages(_one(sources, "M02.pdf"), [2, 28])
        total = _integer(a[9], r"all ([\d,]+) persons")
        parts = [_integer(b[2], rf"([\d,]+) {s}") for s in ("passengers", "crew members")]
        rows = products["双源证据矩阵"]
        _require(len(rows) == 5 and total == sum(parts), "五项或两文人员统计不全")
        (row,) = [r for r in rows if r["问题"] == "遇难者口径"]
        _require(
            total in _number_tokens(row["2015年10月事故终报"])
            and set(parts + [total]) <= _number_tokens(row["2022年11月17日宣判摘要"]),
            "两机构旅客和机组口径不一致",
        )
        verdict = next(r for r in rows if r["问题"] == "交付与责任边界")["2022年11月17日宣判摘要"]
        name = _capture(b[28], r"court acquits the defendant ([A-Za-z]+)")
        _require(
            "blame or liability" in a[15].lower() and name in verdict and "无罪" in verdict,
            "调查范围或司法宣判的无罪记载不符",
        )
        return "从事故终报、宣判原文独立核对遇难者分组和合计，复核安全调查责任边界及宣判中的无罪记录。"
    a = _pages(_one(sources, "M01.pdf"), [1, 8])
    b = _pages(_one(sources, "M02.pdf"), [1, 17])
    press = _pages(_one(sources, "F01.pdf"), [1])[1]
    nums = [
        _integer(a[8], p)
        for p in (r"at least (\d+) individuals died", r"at least (\d+) individuals were injured")
    ]
    nums.extend(
        _integer(b[17], p) for p in (r"killing at least (\d+) civilian", r"injuring at least (\d+) others")
    )
    (row,) = [r for r in products["主张证据矩阵"] if r["议题"] == "伤亡"]
    values = re.findall(r"(\d+)\s*(?:名援助人员死亡|人受伤|死|伤)", row["跨版本核对"])
    _require(list(map(int, values)) == nums and "至少" in row["跨版本核对"], "跨版本人员统计或最低值口径不符")
    dates = {r["日期"] for r in products["发布时间线"]}
    for text in (a[1], b[1], press):
        stamp = _capture(text, r"(\d{1,2} (?:February|December) 20\d{2})")
        day, month, year = stamp.split()
        published = date(int(year), {"february": 2, "december": 12}[month.lower()], int(day))
        _require(published.isoformat() in dates, "文件标注日期与时间线不符")
    return "从两份调查原件独立抽取死伤数量和最低值口径，复核三份原件的发布日期与版本时间线。"


_CHECKS = {
    "B01": _damage,
    "B02": _population,
    "B03": _catalogue,
    "B05": _price,
    "B06": _supply,
    "B07": _visits,
}


def checks(code, sources, products):
    if code not in {*_CHECKS, "B04", "B08", "B09", "B10"}:
        return []
    try:
        if code in ("B04", "B09"):
            detail = _media(sources, products, code)
        elif code in ("B08", "B10"):
            detail = _documents(sources, products, code)
        else:
            detail = _CHECKS[code](sources, products)
    except _Mismatch as exc:
        return [{"name": "archive_source_validation", "passed": False, "message": str(exc), "hard": True}]
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        RuntimeError,
        StopIteration,
        ZeroDivisionError,
    ) as exc:
        return [
            {
                "name": "archive_source_validation",
                "passed": False,
                "message": f"原件或交付结构无法复核（{type(exc).__name__}）。",
                "hard": True,
            }
        ]
    return [{"name": "archive_source_validation", "passed": True, "message": detail, "hard": True}]
