"""Read PRINTED labels on regular paired monthly bars. Never infer counts from bar heights."""

import calendar
import csv
import io
import re
from collections import Counter
from datetime import date

from PIL import Image, ImageOps

from ..platform.ocr import normalize_confidence, xywh_bbox
from .media import command
from .provider_versions import ocr_args


def _words(im, *, psm=11, digits=False):
    args = ocr_args("eng")
    if not args:
        return []
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    cmd = [*args, "--psm", str(psm), "-c", "tessedit_create_tsv=1"]
    if digits:
        cmd += ["-c", "tessedit_char_whitelist=0123456789,"]
    raw = command(cmd, buf.getvalue(), timeout=40)
    if not raw:
        return []
    result = []
    for r in csv.DictReader(
        io.StringIO(raw.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE
    ):
        if r.get("level") == "5" and r.get("text", "").strip():
            left, top, width, height = (
                round(value) for value in xywh_bbox(r["left"], r["top"], r["width"], r["height"])
            )
            result.append(
                (
                    r["text"].strip(),
                    left,
                    top,
                    width,
                    height,
                    normalize_confidence(float(r["conf"]) / 100),
                )
            )
    return result


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _months(title):
    mt = re.search(
        r"(?:from\s+)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+(?:to|[-–])\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        title,
        re.IGNORECASE,
    )
    if not mt:
        return []
    names = {s.lower()[:3]: i for i, s in enumerate(calendar.month_name) if s}
    try:
        a = date(int(mt[3]), names[mt[2].lower()[:3]], int(mt[1]))
        b = date(int(mt[6]), names[mt[5].lower()[:3]], int(mt[4]))
    except (KeyError, ValueError):
        return []
    months = []
    start = a
    while start <= b and len(months) < 120:
        end = min(date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]), b)
        months.append((start, end))
        start = date(end.year + int(end.month == 12), end.month % 12 + 1, 1)
    return months if months and months[-1][1] == b else []


def _series_colors(words, im):
    palette = []
    pix = im.load()
    for label in ["killed", "injured"]:
        options = [w for w in words if w[0].lower().strip(".:,") == label]
        colors = []
        for _, x, y, _, h, _ in options:
            samples = Counter(
                pix[i, j]
                for i in range(max(0, x - 3 * h), x)
                for j in range(y, min(im.height, y + h))
                if max(pix[i, j]) - min(pix[i, j]) > 45
            )
            if samples:
                colors.append(samples.most_common(1)[0])
        if not colors:
            return []
        palette.append(max(colors, key=lambda e: e[1])[0])
    return palette if _distance(*palette) > 1600 else []


def _bar_bounds(im, palette, expected):
    pix = im.load()
    w, h = im.size

    def near(x, y, c):
        return _distance(pix[x, y], c) < 2200

    def color(x, y):
        return next((i for i, c in enumerate(palette) if near(x, y, c)), -1)

    rows = [(sum(color(x, y) >= 0 for x in range(0, w, 3)), y) for y in range(h // 2, h * 94 // 100, 3)]
    maximum = max(c for c, y in rows)
    if maximum < w // 15:
        return None
    baseline = max(y for n, y in rows if n >= maximum * 0.985)
    # Use a small vertical neighborhood so diagonal hatching cannot split a bar into fragments.
    runs = []
    start = None
    prev = None
    for x in range(w + 1):
        k = -1
        if x < w:
            counts = Counter(color(x, y) for y in range(baseline - 5, baseline + 1))
            counts.pop(-1, None)
            if counts:
                k = counts.most_common(1)[0][0]
        if k != prev:
            if start is not None and prev is not None and prev >= 0 and x - start >= max(3, w // 300):
                runs.append((start, x - 1, prev))
            start = x
            prev = k
    if len(runs) != expected * 2 or any(k != i % 2 for i, (_, _, k) in enumerate(runs)):
        return None
    bounds = []
    for left, right, k in runs:
        margin = max(1, (right - left) // 6)
        xs = range(left + margin, right - margin + 1, 2)
        top = baseline
        missed = 0
        for y in range(baseline, -1, -1):
            if sum(near(x, y, palette[k]) for x in xs) >= len(xs) * 0.35:
                top = y
                missed = 0
            else:
                missed += 1
            if missed >= 3:
                break
        if baseline - top < 2:
            return None
        bounds.append((left, right, top, k))
    return baseline, bounds


def _read_labels(im, bounds, palette):
    pix = im.load()
    crops = []
    boxes = []
    for left, right, top, k in bounds:
        bw = right - left + 1
        cx = (left + right) // 2
        x0 = max(0, cx - int(2.4 * bw))
        y0 = max(0, top - int(1.35 * bw))
        x1 = min(im.width, cx + int(2.4 * bw) + 1)
        y1 = top - 1
        if y1 <= y0:
            return [], []
        mask = Image.new("L", (x1 - x0, y1 - y0), 255)
        mask.putdata(
            [
                0 if _distance(pix[x, y], palette[k]) < 2900 else 255
                for y in range(y0, y1)
                for x in range(x0, x1)
            ]
        )
        # Exclude neighbouring bar fills and antialias speckles; retain the glyph cluster nearest this bar.
        black = {(x, y) for x in range(mask.width) for y in range(mask.height) if mask.getpixel((x, y)) == 0}
        components = []
        while black:
            seed = black.pop()
            cells = [seed]
            queue = [seed]
            while queue:
                x, y = queue.pop()
                for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if p in black:
                        black.remove(p)
                        cells.append(p)
                        queue.append(p)
            xs = [p[0] for p in cells]
            ys = [p[1] for p in cells]
            a, b = min(xs), max(xs)
            u, v = min(ys), max(ys)
            if (
                u > 0
                and v < mask.height - 1
                and len(cells) >= 3
                and b - a < bw * 0.95
                and v - u >= 2
                and v - u < bw * 1.25
            ):
                components.append((a, b, u, v, cells))
        clusters = []
        for component in sorted(components):
            if not clusters or component[0] - max(c[1] for c in clusters[-1]) > max(bw * 0.32, 4):
                clusters.append([])
            clusters[-1].append(component)
        clusters = [g for g in clusters if max(c[3] - c[2] for c in g) > bw * 0.35]
        if clusters:
            best = min(
                clusters, key=lambda g: abs(x0 + (min(c[0] for c in g) + max(c[1] for c in g)) / 2 - cx)
            )
            mask = Image.new("L", mask.size, 255)
            for c in best:
                for p in c[4]:
                    mask.putpixel(p, 0)
            ink = ImageOps.invert(mask).getbbox()
            box = [x0 + ink[0], y0 + ink[1], x0 + ink[2], y0 + ink[3]]
            # Segmentation finds the bounding box; OCR reads the original antialiased glyphs.
            mask = ImageOps.autocontrast(im.crop(box).convert("L"))
        else:
            mask = Image.new("L", (1, 1), 255)
            box = [x0, y0, x1, y1]
        mask = mask.resize((max(1, mask.width * 3), max(1, mask.height * 3)), Image.Resampling.LANCZOS)
        crops.append(mask)
        boxes.append(box)
    values = []
    for crop in crops:
        padded = ImageOps.expand(crop, border=20, fill=255)
        results = _words(padded, psm=7, digits=True)
        label = "".join(w[0] for w in results)
        valid = bool(re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", label)) and all(w[-1] >= 0.45 for w in results)
        if not valid:
            alternate = _words(padded, psm=13, digits=True)
            other = "".join(w[0] for w in alternate)
            if other == label and re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", other):
                valid = True
        values.append((int(label.replace(",", "")), label) if valid else (None, label))
    return values, boxes


def extract_monthly_labels(image):
    """Return located OCR observations only for positively recognized legend/axis geometry."""
    if (
        image.width * image.height > 12_000_000
        or image.width < 480
        or image.height < 220
        or image.height > image.width
    ):
        return []
    im = image.convert("RGB")
    title = " ".join(w[0] for w in _words(im.crop((0, 0, im.width, im.height // 6)), psm=6))
    # Only the complete population is comparable to the report's headline totals.
    # Disaggregated causes/age groups can have identical months and legends.
    if not re.search(r"\btotal\s+civilian\s+casualties[\s*,:.–-]*per\s+month\b", title, re.IGNORECASE):
        return []
    months = _months(title)
    if not months:
        return []
    words = _words(im)
    palette = _series_colors(words, im)
    if not palette:
        return []
    bound = _bar_bounds(im, palette, len(months))
    if bound is None:
        return []
    base, bounds = bound
    confirmed = 0
    axis_height = min(im.height - base, int(im.height * 0.11))
    axis = im.crop((0, base, im.width, base + axis_height)).resize(
        (im.width * 2, axis_height * 2), Image.Resampling.LANCZOS
    )
    axis_words = _words(axis, psm=6)
    for i, (a, _b) in enumerate(months):
        lo = bounds[2 * i][0]
        hi = bounds[2 * i + 1][1]
        names = {
            word.lower()[:3]
            for word, x, _, w, _, _ in axis_words
            if lo - 6 <= (x + w / 2) / 2 <= hi + 6
            and re.fullmatch(r"[A-Za-z]{3,9}", word)
            and word.lower()[:3] in {m.lower() for m in calendar.month_abbr if m}
        }
        if names and names != {calendar.month_abbr[a.month].lower()}:
            return []
        confirmed += bool(names)
    if confirmed < len(months) * 0.65:
        return []
    values, boxes = _read_labels(im, bounds, palette)
    result = []
    if len(values) != 2 * len(months):
        return []
    for i, (start, end) in enumerate(months):
        k, inj = values[2 * i : 2 * i + 2]
        text = f"{start:%Y-%m}: Killed={k[1]}; Injured={inj[1]} (printed chart labels, OCR)."
        fields = {
            "month": start.strftime("%Y-%m"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "deaths": k[0],
            "injured": inj[0],
            "basis": "图中已印数字的OCR；待同口径总数复算",
            "kind": "chart_label_ocr",
        }
        result.append(({"label_bboxes": boxes[2 * i : 2 * i + 2], "image_size": list(im.size)}, text, fields))
    return result
