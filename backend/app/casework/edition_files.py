"""Immutable passive files that accompany an edition report."""

import io
import re
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

from ..platform.asset_originals import _verified_file, valid_filename

SAFE_TYPES = {
    ".csv",
    ".json",
    ".geojson",
    ".png",
    ".jpg",
    ".jpeg",
    ".txt",
    ".pdf",
    ".svg",
    ".vtt",
    ".srt",
    ".md",
    ".html",
    ".docx",
    ".zip",
}


def validate_attachment(path):
    """Published outputs are approved offline and downloaded inside a ZIP, never served as pages."""
    if path.suffix.lower() == ".html":
        html = path.read_text(encoding="utf-8-sig")
        if re.search(
            r"<\s*(script|iframe|object|embed|base|form|link|meta[^>]+http-equiv)\b|\son\w+\s*=|javascript\s*:|@import\b|\b(?:href|src)\s*=\s*['\"]?(?:https?:|//)",
            html,
            re.IGNORECASE,
        ):
            raise ValueError("交付 HTML 仅支持自包含的静态图文与 CSS 控件")
    elif path.suffix.lower() in {".docx", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 200 or sum(m.file_size for m in members) > 180_000_000:
                raise ValueError("压缩附件超出允许的展开大小")
            for m in members:
                if (
                    any(p in {"..", "."} for p in m.filename.split("/"))
                    or m.filename.startswith("/")
                    or "\\" in m.filename
                    or ":" in m.filename
                ):
                    raise ValueError("压缩附件包含无效路径")
                if (
                    path.suffix.lower() == ".zip"
                    and not m.is_dir()
                    and Path(m.filename).suffix.lower()
                    not in {".csv", ".geojson", ".json", ".png", ".jpg", ".txt"}
                ):
                    raise ValueError("数据压缩附件只可含被动数据或图片")
                if path.suffix.lower() == ".docx" and any(
                    s in m.filename.lower() for s in ("vbaproject", "activex", "embeddings/")
                ):
                    raise ValueError("Word 交付不能含活动组件")
            if archive.testzip() is not None:
                raise ValueError("交付附件校验失败")


def read_attachments(report, task):
    frozen = {
        item["sha256"]: item
        for asset in task["snapshot"]["inputs"]
        for item in (asset.get("content") or {}).get("attachments", [])
        if isinstance(item, dict)
    }
    result = {}
    for item in report.get("attachments", []):
        name = item["name"]
        if (
            frozen.get(item["sha256"]) != item
            or not valid_filename(name)
            or Path(name).suffix.lower() not in SAFE_TYPES
            or name in result
        ):
            raise ValueError("附件不在冻结的交付清单内")
        with _verified_file(
            task["project_id"], {"filename": name, "sha256": item["sha256"], "bytes": item["bytes"]}
        ) as stream:
            result[name] = stream.read()
    return result


def attachment_context(attachments, task):
    from docx import Document

    files = read_attachments({"attachments": attachments}, task)
    notes = {}
    for name, data in files.items():
        suffix = Path(name).suffix.lower()
        preview = ""
        if suffix in {".md", ".txt", ".vtt", ".srt", ".csv"}:
            preview = data[:16000].decode("utf-8-sig", errors="replace")
        elif suffix == ".docx":
            doc = Document(io.BytesIO(data))
            preview = "\n".join(
                [
                    *(p.text for p in doc.paragraphs),
                    *(
                        " | ".join(c.text for c in row.cells)
                        for table in doc.tables
                        for row in table.rows[:12]
                    ),
                ]
            )
        notes[name] = preview[:2000] or "随包图件或完整机器可读数据"
    return notes


def write_details(doc, report, files):
    from docx.shared import Cm

    from .delivery_brief import _table, clean

    figures = 0
    for name, data in files.items():
        if Path(name).suffix.lower() not in {".png", ".jpeg", ".jpg"} or figures >= 4:
            continue
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 32_000_000:
                continue
            image = ImageOps.exif_transpose(im).convert("RGB")
            image.thumbnail((1450, 1100))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            buffer.seek(0)
            doc.add_heading(clean(Path(name).stem, 100), 2)
            doc.add_picture(buffer, width=Cm(min(15.6, 12 * image.width / image.height)))
            figures += 1
    rows = []
    for e in report["evidence"][:14]:
        rows.append([e["id"], clean(e["location"]["原件位置"], 85), clean(e["quote"], 320)])
    if rows:
        doc.add_heading("证据与核算明细", 2)
        _table(doc, ["编号", "原件位置", "数据与观察"], rows, [1.45, 4.45, 10.5], cell_limit=330)
    limitations = report["products"].get("资料适用范围", [])
    if limitations:
        doc.add_heading("资料与方法的适用范围", 2)
        for line in limitations:
            doc.add_paragraph(clean(line, 480))
    if files:
        doc.add_paragraph("随包成果　" + "、".join(files))
