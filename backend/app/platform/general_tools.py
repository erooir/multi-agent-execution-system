"""Reusable document, table and media operations over frozen authorized sources."""

from __future__ import annotations

import io
import json
import math
import statistics
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Contract
from .domain_tool import DomainOperation


class Source(Contract):
    source_id: str = Field(min_length=1, max_length=100)


class DocumentExtract(Source):
    start: int = Field(default=0, ge=0, le=60000, description="Record offset, not a fabricated page number")
    limit: int = Field(default=12, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=100, le=2000)


class TableDescribe(Source):
    fields: list[str] = Field(default_factory=list, max_length=20)


class TableValidate(Source):
    required_fields: list[str] = Field(min_length=1, max_length=20)
    numeric_fields: list[str] = Field(default_factory=list, max_length=20)
    key_fields: list[str] = Field(default_factory=list, max_length=4)


class TableJoin(Source):
    other_source_id: str = Field(min_length=1, max_length=100)
    left_key: str = Field(min_length=1, max_length=100)
    right_key: str = Field(min_length=1, max_length=100)
    how: Literal["left", "inner"] = "left"
    limit: int = Field(default=30, ge=1, le=100)


class TableChart(Source):
    category_field: str = Field(min_length=1, max_length=100)
    value_field: str = Field(min_length=1, max_length=100)
    chart_type: Literal["bar", "line"] = "bar"
    unit: str = Field(min_length=1, max_length=60)


class PdfOCR(Source):
    page: int = Field(ge=1, le=800)
    image_index: int | None = Field(
        default=None, ge=1, le=30, description="Optional embedded image index on this page, starting at 1"
    )


class AudioTranscribe(Source):
    start_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    duration_seconds: int = Field(default=60, ge=1, le=120)


class VideoFrames(Source):
    times_seconds: list[float] = Field(min_length=1, max_length=6)


def _parse(read_source, source_id):
    from ..casework.records import extract

    source = read_source(source_id)
    mime = source["media_type"].partition(";")[0]
    if mime not in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
        "text/csv",
        "text/plain",
        "text/markdown",
        "text/vtt",
        "application/x-subrip",
        "application/geo+json",
    }:
        raise ValueError("document_type_unsupported")
    # The synthetic marker selects the already authorized bytes callback, never a filesystem path.
    parsed = {**source, "content_ref": "frozen-source"}
    rows, gaps = extract([parsed], lambda _: read_source.read_bytes(source_id))
    return source, rows, gaps


def document_extract(read_source, args):
    source, rows, gaps = _parse(read_source, args.source_id)
    selected = rows[args.start : args.start + args.limit]
    return {
        "source_id": source["id"],
        "source_version": source["source_version"],
        "total_records": len(rows),
        "records": [
            {
                **row,
                "text": row["text"][: args.max_chars],
                "text_truncated": len(row["text"]) > args.max_chars,
            }
            for row in selected
        ],
        "truncated": args.start + len(selected) < len(rows),
        "next_start": args.start + len(selected),
        "gaps": gaps[:20],
    }


def _table(read_source, source_id):
    source, rows, gaps = _parse(read_source, source_id)
    if gaps or not rows or len(rows) > 1000:
        raise ValueError("table_requires_complete_1_to_1000_rows")
    if source["media_type"].partition(";")[0] not in {
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        raise ValueError("table_type_unsupported")
    return source, rows


def _number(value):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return None
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _reference(source):
    return {"source_id": source["id"], "source_version": source["source_version"]}


def data_profile(read_source, args):
    source = read_source(args.source_id)
    result = {
        **_reference(source),
        "media_type": source["media_type"],
        "bytes": source["bytes"],
        "modality": source["modality"],
    }
    if source["modality"] in {"audio", "video", "image"}:
        return {**result, **media_probe(read_source, args)}
    _, rows, gaps = _parse(read_source, args.source_id)
    result.update(
        record_count=len(rows),
        fields=sorted({str(key) for row in rows for key in row["fields"]})[:100],
        location_kinds=sorted({key for row in rows for key in row["location"]}),
        text_characters=sum(len(row["text"]) for row in rows),
        gaps=gaps[:20],
    )
    return result


def table_describe(read_source, args):
    source, rows = _table(read_source, args.source_id)
    fields = args.fields or sorted({key for row in rows for key in row["fields"]})[:20]
    output = {}
    for field in fields:
        values = [row["fields"].get(field) for row in rows]
        numbers = [n for value in values if (n := _number(value)) is not None]
        nonempty = [value for value in values if value is not None and value != ""]
        summary = {
            "count": len(values),
            "missing": len(values) - len(nonempty),
            "numeric_count": len(numbers),
            "invalid_numeric": len(nonempty) - len(numbers),
            "distinct": len({json.dumps(value, ensure_ascii=False, sort_keys=True) for value in nonempty}),
        }
        if numbers:
            # Decimal sums are returned as strings so no hidden binary rounding is reported as exact.
            summary.update(
                sum=str(sum(numbers)),
                mean=str(sum(numbers) / Decimal(len(numbers))),
                minimum=str(min(numbers)),
                maximum=str(max(numbers)),
                median=str(statistics.median(numbers)),
            )
        output[field] = summary
    return {
        **_reference(source),
        "total_rows": len(rows),
        "fields": output,
        "numeric_format": "decimal-string",
        "scope": "all rows; null/empty/invalid numeric values excluded and counted, never imputed to zero",
    }


def table_validate(read_source, args):
    source, rows = _table(read_source, args.source_id)
    issues, seen = [], set()
    for row in rows:
        fields = row["fields"]
        missing = [key for key in args.required_fields if fields.get(key) in (None, "")]
        invalid = [key for key in args.numeric_fields if _number(fields.get(key)) is None]
        duplicate = False
        if args.key_fields:
            key = json.dumps([fields.get(key) for key in args.key_fields], ensure_ascii=False, sort_keys=True)
            duplicate = key in seen
            seen.add(key)
        if missing or invalid or duplicate:
            issues.append(
                {
                    "location": row["location"],
                    "missing_fields": missing,
                    "invalid_numeric_fields": invalid,
                    "duplicate_key": duplicate,
                }
            )
    return {
        **_reference(source),
        "total_rows": len(rows),
        "passed": not issues,
        "invalid_rows": len(issues),
        "issues": issues[:40],
        "truncated": len(issues) > 40,
    }


def table_join(read_source, args):
    left_source, left = _table(read_source, args.source_id)
    right_source, right = _table(read_source, args.other_source_id)
    index = {}
    for row in right:
        value = row["fields"].get(args.right_key)
        if value in (None, "") or isinstance(value, (dict, list)):
            raise ValueError("join_right_key_missing_or_invalid")
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in index:
            raise ValueError("join_right_key_not_unique")
        index[key] = row
    output, unmatched = [], 0
    for row in left:
        value = row["fields"].get(args.left_key)
        if value in (None, "") or isinstance(value, (dict, list)):
            raise ValueError("join_left_key_missing_or_invalid")
        match = index.get(json.dumps(value, ensure_ascii=False, sort_keys=True))
        if match is None:
            unmatched += 1
        if match is not None or args.how == "left":
            output.append(
                {
                    "left": row["fields"],
                    "right": match["fields"] if match else None,
                    "locations": [
                        {**_reference(left_source), **row["location"]},
                        *([{**_reference(right_source), **match["location"]}] if match else []),
                    ],
                }
            )
    return {
        "source_id": args.source_id,
        "other_source_id": args.other_source_id,
        "total_rows": len(output),
        "unmatched_left_rows": unmatched,
        "records": output[: args.limit],
        "truncated": len(output) > args.limit,
        "key_comparison": "exact JSON scalar values; no fuzzy match",
    }


def table_chart(read_source, args):
    source, rows = _table(read_source, args.source_id)
    if len(rows) > 100:
        raise ValueError("chart_requires_at_most_100_rows")
    values = []
    for row in rows:
        category, number = (
            row["fields"].get(args.category_field),
            _number(row["fields"].get(args.value_field)),
        )
        if category in (None, "") or number is None:
            raise ValueError("chart_fields_missing_or_nonnumeric")
        values.append({"category": str(category), "value": str(number), "location": row["location"]})
    return {
        **_reference(source),
        "chart_type": args.chart_type,
        "unit": args.unit,
        "category_field": args.category_field,
        "value_field": args.value_field,
        "values": values,
        "total_rows": len(rows),
        "note": "Chart specification over original rows; no aggregation or interpolation applied",
    }


def image_ocr(read_source, args):
    from .ocr import run_ocr

    source = read_source(args.source_id)
    if source["modality"] != "image":
        raise ValueError("image_required")
    result = run_ocr([{**_reference(source), "payload": read_source.read_bytes(source["id"])}])
    return {
        **_reference(source),
        **result,
        "review_required": "OCR is a reading, not verified truth; compare numeric labels and units against the original image",
    }


def pdf_ocr(read_source, args):
    from pypdf import PdfReader

    from .ocr import run_ocr

    source = read_source(args.source_id)
    if source["media_type"].partition(";")[0] != "application/pdf":
        raise ValueError("pdf_required")
    raw = read_source.read_bytes(source["id"])
    pdf = PdfReader(io.BytesIO(raw))
    if pdf.is_encrypted or len(pdf.pages) > 800 or args.page > len(pdf.pages):
        raise ValueError("pdf_page_invalid")
    if args.image_index is None:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(raw)
        try:
            page = document[args.page - 1]
            try:
                width, height = page.get_size()
                if width * height * 4 > 10_000_000:
                    raise ValueError("pdf_page_pixel_limit")
                bitmap = page.render(scale=2)
                try:
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    else:
        images = pdf.pages[args.page - 1].images
        if args.image_index > len(images):
            raise ValueError("pdf_image_index_invalid")
        image = images[args.image_index - 1].image
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = run_ocr([{**_reference(source), "payload": buffer.getvalue()}])
    return {
        **_reference(source),
        "page": args.page,
        "image_index": args.image_index,
        **result,
        "review_required": "Preserve bounding boxes; reconcile chart labels with body totals and coverage before using OCR numbers",
    }


def media_probe(read_source, args):
    from PIL import Image

    from ..casework.provider_versions import bound_binary

    source = read_source(args.source_id)
    raw = read_source.read_bytes(args.source_id)
    if source["modality"] == "image":
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("image_pixel_limit")
            image.verify()
            return {
                **_reference(source),
                "width": image.width,
                "height": image.height,
                "format": image.format,
                "mode": image.mode,
            }
    if source["modality"] not in {"audio", "video"}:
        raise ValueError("media_required")
    binary = bound_binary("ffprobe")
    if not binary:
        raise ValueError("media_decoder_unavailable")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "media.bin"
        path.write_bytes(raw)
        done = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "wav,mp3,flac,ogg,mov,matroska,asf,avi",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,sample_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    if done.returncode or len(done.stdout) > 40000:
        raise ValueError("media_decode_failed")
    return {**_reference(source), **json.loads(done.stdout)}


def audio_transcribe(read_source, args):
    import os

    import numpy as np

    from ..casework.media import _speech_model
    from ..casework.provider_versions import bound_binary

    source = read_source(args.source_id)
    if source["modality"] not in {"audio", "video"}:
        raise ValueError("audio_or_video_required")
    ffmpeg = bound_binary("ffmpeg")
    model = os.getenv("HANGZHI_ASR_MODEL_DIR", "")
    if not ffmpeg or not model or not Path(model).is_dir():
        raise ValueError("speech_model_unavailable")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "media.bin"
        path.write_bytes(read_source.read_bytes(args.source_id))
        done = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "wav,mp3,flac,ogg,mov,matroska,asf,avi",
                "-ss",
                str(args.start_seconds),
                "-i",
                str(path),
                "-t",
                str(args.duration_seconds),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    if done.returncode or not done.stdout or len(done.stdout) > 3_840_100:
        raise ValueError("audio_window_invalid")
    segments, info = _speech_model(model).transcribe(
        np.frombuffer(done.stdout, dtype="<i2").astype("float32") / 32768, beam_size=3, vad_filter=False
    )
    rows = []
    for segment in segments:
        read_source.checkpoint()
        if len(rows) >= 100:
            break
        rows.append(
            {
                "start_ms": round((args.start_seconds + segment.start) * 1000),
                "end_ms": round((args.start_seconds + segment.end) * 1000),
                "text": segment.text.strip()[:1000],
            }
        )
    return {
        **_reference(source),
        "language": info.language,
        "segments": rows,
        "window_start_seconds": args.start_seconds,
        "window_duration_seconds": args.duration_seconds,
        "method": "local_faster_whisper",
        "review_required": "ASR may misread names and numbers; retain timestamps for checking",
    }


def video_frames(read_source, args):
    import av

    from .ocr import run_ocr

    source = read_source(args.source_id)
    if source["modality"] != "video" or any(
        not math.isfinite(t) or t < 0 or t > 3600 for t in args.times_seconds
    ):
        raise ValueError("video_window_invalid")
    result = []
    with av.open(
        io.BytesIO(read_source.read_bytes(args.source_id)),
        options={"protocol_whitelist": "file,pipe", "format_whitelist": "mov,matroska,asf,avi"},
    ) as container:
        stream = container.streams.video[0]
        for target in args.times_seconds:
            read_source.checkpoint()
            container.seek(int(target / float(stream.time_base)), stream=stream, backward=True)
            found = False
            for frame in container.decode(stream):
                if frame.time is None or frame.time + 0.05 < target:
                    continue
                image = frame.to_image()
                if image.width * image.height > 10_000_000:
                    raise ValueError("video_frame_pixel_limit")
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                ocr = run_ocr([{**_reference(source), "payload": buffer.getvalue()}])
                result.append(
                    {
                        "frame_ms": round(frame.time * 1000),
                        "width": image.width,
                        "height": image.height,
                        "ocr": ocr,
                    }
                )
                found = True
                break
            if not found:
                result.append({"requested_seconds": target, "error": "frame_unavailable"})
    return {
        **_reference(source),
        "frames": result,
        "method": "local_decode_and_ocr",
        "coverage": "only the explicitly sampled timestamps; not full-video semantic analysis",
    }


_RECORD_DEPS = ("backend/app/casework/records.py",)
TOOLS = (
    DomainOperation(
        "document.extract",
        "document_extract",
        "按原位置读取 PDF、Word、表格与文本",
        DocumentExtract,
        document_extract,
        dependencies=_RECORD_DEPS,
        packages=("pypdf", "python-docx", "openpyxl"),
    ),
    DomainOperation(
        "data.profile",
        "data_profile",
        "检查资料格式、字段与覆盖范围",
        Source,
        data_profile,
        dependencies=_RECORD_DEPS,
    ),
    DomainOperation(
        "table.describe",
        "table_describe",
        "表格字段分布与精确数值统计",
        TableDescribe,
        table_describe,
        dependencies=_RECORD_DEPS,
    ),
    DomainOperation(
        "table.validate",
        "table_validate",
        "检查必填字段、数值与唯一键",
        TableValidate,
        table_validate,
        dependencies=_RECORD_DEPS,
    ),
    DomainOperation(
        "table.join", "table_join", "按明确键连接两份表格", TableJoin, table_join, dependencies=_RECORD_DEPS
    ),
    DomainOperation(
        "table.chart",
        "table_chart",
        "生成带原始行定位的统计图数据",
        TableChart,
        table_chart,
        dependencies=_RECORD_DEPS,
    ),
    DomainOperation(
        "image.ocr",
        "image_ocr_blocks",
        "识别图片文字及边界框",
        Source,
        image_ocr,
        dependencies=("backend/app/platform/ocr.py",),
        packages=("rapidocr-onnxruntime",),
    ),
    DomainOperation(
        "pdf.ocr",
        "pdf_ocr",
        "读取 PDF 页或内嵌图像文字",
        PdfOCR,
        pdf_ocr,
        dependencies=("backend/app/platform/ocr.py",),
        packages=("pypdfium2", "rapidocr-onnxruntime"),
    ),
    DomainOperation(
        "media.probe",
        "media_probe",
        "读取图片、音频和视频真实元数据",
        Source,
        media_probe,
        dependencies=("backend/app/casework/provider_versions.py",),
    ),
    DomainOperation(
        "audio.transcribe",
        "audio_transcribe_window",
        "转写指定音频时间窗",
        AudioTranscribe,
        audio_transcribe,
        dependencies=("backend/app/casework/media.py", "backend/app/casework/provider_versions.py"),
        packages=("faster-whisper",),
    ),
    DomainOperation(
        "video.sample_frames",
        "video_sample_frames",
        "读取指定视频帧并识别画面文字",
        VideoFrames,
        video_frames,
        dependencies=("backend/app/platform/ocr.py",),
        packages=("av", "rapidocr-onnxruntime"),
    ),
)
