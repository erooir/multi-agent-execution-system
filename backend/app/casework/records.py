"""Bounded source extraction. Coordinates are assigned here, never by an analyst."""

import csv
import io
import json
import zipfile
from datetime import date, datetime

from openpyxl import load_workbook
from pypdf import PdfReader

from .control import pulse

MAX_RECORDS = 60000
MAX_TEXT = 16_000_000


def _zip_guard(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        entries = z.infolist()
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 48_000_000:
            raise ValueError("Office文档展开后超过限制")


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def extract(assets, read_bytes, *, revision=False, case_code=None):
    rows, gaps = [], []
    size = 0

    def add(asset, location, text="", fields=None):
        nonlocal size
        if len(rows) % 128 == 0:
            pulse()
        fields = fields or {}
        text = str(text or "")
        size += len(text) + len(json.dumps(fields, ensure_ascii=False, allow_nan=False))
        if len(rows) >= MAX_RECORDS or size > MAX_TEXT:
            raise ValueError("单任务解析记录超限，请按来源或时间窗分批")
        rows.append(
            {
                "source_id": asset["id"],
                "source_version": asset["source_version"],
                "location": location,
                "text": text,
                "fields": fields,
            }
        )

    for a in assets:
        mime = a["media_type"].split(";")[0].lower()
        value = a.get("content")
        data = read_bytes(a) if a.get("content_ref") else None
        if mime == "application/pdf" and data is not None:
            pdf = PdfReader(io.BytesIO(data), strict=False)
            chart_budget = 35
            examined = set()
            if pdf.is_encrypted or len(pdf.pages) > 800:
                raise ValueError("加密PDF或页数超过限制")
            for i, page in enumerate(pdf.pages, 1):
                pulse()
                value = page.extract_text() or ""
                # OCR when a frozen page has no text; revision may try a different segmentation.
                if not value.strip():
                    from .media import ocr_pdf_page

                    value = ocr_pdf_page(data, i, revision=revision)
                    if not value:
                        gaps.append(f"{a['id']} 第{i}页没有可读取文本或OCR结果")
                if value.strip():
                    add(a, {"page": i}, value, {"page": i})
                if case_code == "T05" and any(f"FIGURE {n}:" in value for n in (4, 6, 7)):
                    from .document_figures import chart_objects

                    for loc, text, fields in chart_objects(page, value):
                        add(a, {"page": i, **loc}, text, fields)
                if revision and case_code == "B05" and chart_budget > 0:
                    from .document_chart_ocr import extract_monthly_labels

                    for index, image in enumerate(page.images):
                        if index >= 30:
                            break
                        im = image.image
                        token = getattr(getattr(image, "indirect_reference", None), "idnum", None)
                        if token is not None and token in examined:
                            continue
                        if token is not None:
                            examined.add(token)
                        if im and im.width >= 320 and im.height >= 150 and im.width * im.height <= 10_000_000:
                            if chart_budget == 0:
                                gaps.append("超过单份报告的候选图表处理上限")
                                break
                            chart_budget -= 1
                            for loc, text, fields in extract_monthly_labels(im):
                                add(a, {"page": i, "image_index": index + 1, **loc}, text, fields)
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" and data:
            from docx import Document

            _zip_guard(data)
            doc = Document(io.BytesIO(data))
            for i, p in enumerate(doc.paragraphs, 1):
                if p.text.strip():
                    add(a, {"paragraph": i}, p.text)
            for i, table in enumerate(doc.tables, 1):
                for j, row in enumerate(table.rows, 1):
                    cells = [c.text for c in row.cells]
                    add(a, {"table": i, "row": j}, " | ".join(cells), {"cells": cells})
        elif mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" and data:
            _zip_guard(data)
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
            try:
                for sheet in book:
                    if (
                        sheet.max_row
                        and sheet.max_row > MAX_RECORDS
                        or sheet.max_column
                        and sheet.max_column > 512
                    ):
                        raise ValueError("工作表尺寸超过解析限制")
                    headers = None
                    for i, raw in enumerate(sheet.values, 1):
                        values = [_plain(v) for v in raw]
                        while values and values[-1] is None:
                            values.pop()
                        if not values:
                            continue
                        if headers is None:
                            labels = [str(v).strip() if v is not None else "" for v in values]
                            populated = [v for v in labels if v]
                            if (
                                len(populated) >= 2
                                and len(populated) == len(set(populated))
                                and all(isinstance(v, str) or v is None for v in values)
                            ):
                                headers = [v or f"col_{j + 1}" for j, v in enumerate(labels)]
                                continue
                        fields = {
                            (headers[j] if headers and j < len(headers) else f"col_{j + 1}"): v
                            for j, v in enumerate(values)
                            if v is not None
                        }
                        add(a, {"sheet": sheet.title, "row": i}, fields=fields)
            finally:
                book.close()
        elif mime in {"text/csv", "application/vnd.ms-excel"}:
            if value is None and data is not None:
                encoding = "cp1252" if "charset=windows-1252" in a["media_type"] else "utf-8-sig"
                value = data.decode(encoding)
            reader = csv.DictReader(io.StringIO(value))
            if (
                not reader.fieldnames
                or any(not h.strip() for h in reader.fieldnames)
                or len(set(reader.fieldnames)) != len(reader.fieldnames)
            ):
                raise ValueError("CSV表头为空或重复")
            for i, row in enumerate(reader, 2):
                if None in row or None in row.values():
                    raise ValueError(f"CSV第{i}行列数不一致")
                add(a, {"row": i}, fields=row)
        elif a["modality"] in {"image", "audio", "video"}:
            from .media import read_medium

            observations, issues = read_medium(a, data, revision=revision)
            for loc, text, fields in observations:
                add(a, loc, text, fields)
            gaps.extend(issues)
        else:
            if value is None and data is not None:
                value = data.decode("utf-8-sig")
            if isinstance(value, str) and mime in {"application/json", "application/geo+json"}:
                value = json.loads(value)
            if isinstance(value, str) and mime in {"application/x-ndjson", "application/jsonl"}:
                value = [json.loads(line) for line in value.splitlines() if line.strip()]
            if isinstance(value, str) and mime in {"application/x-subrip", "text/vtt"}:
                import re

                for i, block in enumerate(re.split(r"\r?\n\s*\r?\n", value.strip()), 1):
                    if "-->" in block:
                        add(a, {"cue": i}, block, {"kind": "caption"})
            elif isinstance(value, str):
                # Preserve real paragraph offsets in the supplied string.
                offset = 0
                for i, part in enumerate(value.splitlines(keepends=True), 1):
                    text = part.rstrip("\r\n")
                    if text.strip():
                        add(a, {"paragraph": i, "start": offset, "end": offset + len(text)}, text)
                    offset += len(part)
            else:
                if isinstance(value, dict):
                    if value.get("type") == "FeatureCollection":
                        values = value.get("features")
                    else:
                        values = value.get("records", value.get("segments", [value]))
                else:
                    values = value
                if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                    raise ValueError("用例输入需要JSON对象、记录数组或GeoJSON要素集")
                for i, v in enumerate(values, 1):
                    f = dict(v)
                    if v.get("type") == "Feature":
                        f = {**(v.get("properties") or {}), "geometry": v.get("geometry")}
                        if "id" in v:
                            f.setdefault("id", v["id"])
                    loc = {"row": i}
                    # Supplied media coordinates are annotations, not model-generated observations.
                    loc.update({k: f[k] for k in ("segment_id", "start_ms", "end_ms", "page") if k in f})
                    add(a, loc, f.get("text") or f.get("quote") or f.get("transcript") or "", f)
    if case_code == "B03":
        from .vision import enrich_archive

        rows, issues = enrich_archive(rows, assets, read_bytes)
        gaps.extend(issues)
    return rows, list(dict.fromkeys(gaps))
