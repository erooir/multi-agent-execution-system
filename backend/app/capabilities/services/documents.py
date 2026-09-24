"""从 knowledge.py 抽取的文档解析/分块服务：显式参数、无隐式状态。

knowledge.py 保留同名私有别名以维持现有行为；新能力内核直接使用本模块。
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from xml.etree import ElementTree

MAX_EXTRACTED_CHARS = 2_000_000


def tokenize(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9][a-z0-9_\-]*", text.lower()))
    for part in re.findall(r"[㐀-鿿]+", text):
        if len(part) == 1:
            result.add(part)
        else:
            result.update(part[i : i + 2] for i in range(len(part) - 1))
    return result


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError("文件编码无法识别，请使用 UTF-8 文本")


def split_parts(text: str, location: str, size: int = 900) -> list[tuple[str, str]]:
    text = text.strip().replace("\x00", "")
    if not text:
        return []
    return [
        (f"{location} · 字符 {start + 1}–{min(start + size, len(text))}", text[start : start + size])
        for start in range(0, len(text), size)
    ]


def extract_parts(suffix: str, content: bytes) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("暂不支持加密 PDF，请先提供可读取版本")
        if len(reader.pages) > 1000:
            raise ValueError("PDF 页数超过 1000 页限制")
        for number, page in enumerate(reader.pages, 1):
            parts.extend(split_parts(page.extract_text() or "", f"第 {number} 页"))
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("DOCX 解压后超过大小限制")
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for number, paragraph in enumerate(root.findall(".//w:p", namespace), 1):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
            parts.extend(split_parts(text, f"第 {number} 段"))
    elif suffix == ".csv":
        rows = list(csv.reader(io.StringIO(decode_text(content))))
        if rows:
            header = rows[0]
            for number, row in enumerate(rows[1:], 2):
                text = "；".join(
                    f"{header[i] if i < len(header) else f'字段{i + 1}'}：{value}"
                    for i, value in enumerate(row)
                )
                parts.extend(split_parts(text, f"第 {number} 行"))
    elif suffix == ".json":
        value = json.loads(decode_text(content))
        objects = value if isinstance(value, list) else [value]
        for number, item in enumerate(objects):
            parts.extend(split_parts(json.dumps(item, ensure_ascii=False, indent=2), f"JSON $[{number}]"))
    elif suffix in {".txt", ".md"}:
        lines = decode_text(content).splitlines()
        buffer: list[str] = []
        start = 1
        for number, line in enumerate(lines, 1):
            if not buffer:
                start = number
            buffer.append(line)
            if sum(map(len, buffer)) >= 750 or (not line.strip() and len(buffer) > 1):
                parts.extend(split_parts("\n".join(buffer), f"第 {start}–{number} 行"))
                buffer = []
        if buffer:
            parts.extend(split_parts("\n".join(buffer), f"第 {start}–{len(lines)} 行"))
    if sum(len(text) for _, text in parts) > MAX_EXTRACTED_CHARS:
        raise ValueError("提取文本超过 200 万字符限制，请拆分资料")
    return parts


def build_evidence(chunk: dict, score: float) -> dict:
    return {
        key: chunk[key] for key in ("id", "document_id", "document_name", "location", "text", "visibility")
    } | {
        "score": round(score, 5),
        "project_id": chunk.get("project_id"),
    }
