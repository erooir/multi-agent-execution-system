"""Build an in-memory, passive delivery ZIP *after* the API has rechecked source access."""

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime

from ..platform.contracts import CASE_ID_PATTERN
from .delivery_brief import brief_document
from .delivery_card import Card
from .delivery_files import csv_table, derived_files, encoded_json
from .delivery_html import brief_html, stage_html
from .delivery_trend import monthly_figures

MAX_BUNDLE_BYTES = 96_000_000


def bundle(
    report: dict,
    metadata: dict | None = None,
    *,
    source_names: dict[str, str] | None = None,
    edition_files=None,
) -> bytes:
    """Export case_report.content; no filesystem, network, model, or DB side effects.

    The caller is responsible for the authenticated artifact and live source checks.
    Metadata is allowlisted: never serialize a task snapshot, token, or arbitrary extras.
    """
    if not isinstance(report, dict) or metadata is not None and not isinstance(metadata, dict):
        raise ValueError("报告或任务元数据类型无效")
    metadata = metadata or {}
    if (
        report.get("content_withheld")
        or metadata.get("content_withheld")
        or report.get("unavailable_source_refs")
        or metadata.get("unavailable_source_refs")
    ):
        raise PermissionError("来源已失效，导出已屏蔽")
    if (
        not re.fullmatch(CASE_ID_PATTERN, str(report.get("case_id", "")))
        or not isinstance(report.get("title"), str)
        or not isinstance(report.get("products"), dict)
        or not isinstance(report.get("gaps"), list)
        or not isinstance(report.get("evidence"), list)
        or not report.get("review")
        or not isinstance(report["review"], list)
    ):
        raise ValueError("仅支持具有独立审核记录的用例报告")
    if (
        any(not isinstance(g, str) for g in report["gaps"])
        or any(not isinstance(r, dict) or type(r.get("passed")) is not bool for r in report["review"])
        or any(
            not isinstance(e, dict)
            or not all(k in e for k in ("id", "source_id", "source_version", "location"))
            or not isinstance(e["location"], dict)
            for e in report["evidence"]
        )
    ):
        raise ValueError("审核或证据记录结构无效")
    content = {k: report[k] for k in ("case_id", "title", "products", "gaps", "evidence", "review")}
    content.update(
        scope=report.get("scope", "unspecified"),
        reading_aids=report.get("reading_aids", []),
        reading_notes=report.get("reading_notes", []),
        reference_batch=report.get("reference_batch"),
        tool_findings=report.get("tool_findings", []),
    )
    if report.get("edition"):
        content.update(edition=report["edition"], attachments=report.get("attachments", []))
    selected = {
        k: v
        for k, v in metadata.items()
        if k in {"task_id", "project_id", "name", "status", "revision", "artifact_id", "finished_at", "mode"}
        and isinstance(v, (str, int, bool))
        and len(str(v)) <= 512
    }
    full = encoded_json({**content, "task": selected})
    if len(full) > 32_000_000:
        raise ValueError("报告超过32MB导出上限，请按项目任务范围拆分")
    extras, omitted = derived_files(content["products"])
    if (
        not content.get("edition")
        and content["case_id"] == "B05"
        and not any(c.get("hard") and not c["passed"] for c in content["review"])
    ):
        figures, skipped = monthly_figures(content["products"], content["evidence"])
        extras.extend(figures)
        omitted.extend(skipped)
    doc = brief_document(content, selected, source_names=source_names, edition_files=edition_files)
    brief = io.BytesIO()
    doc.save(brief)
    sources = csv_table(
        [
            {
                "evidence_id": e["id"],
                "source_id": e["source_id"],
                "source_version": e["source_version"],
                "location": e["location"],
                "quote": e.get("quote"),
            }
            for e in content["evidence"]
        ]
    )
    card_name = {"B01": "bulletin.docx", "T01": "reading-card.docx"}.get(content["case_id"])
    card_label = {
        "B01": "bulletin.docx：一页中文监测快报，保留报号、统计窗口、分区数量、观察与转述限定。\n",
        "T01": "reading-card.docx：一页中文论文阅读卡，区分实测与模型，标出页码、适用限制和译摘。\n",
    }.get(content["case_id"], "")
    if content.get("edition"):
        card_name, card_label = None, ""
        for name, data in (edition_files or {}).items():
            extras.append(("研究附件/" + name, data, "/attachments", "case_attachment"))
    readme_title = "航智用例研究成果\n\n".encode()
    html_note = "研究简报.html 可用浏览器离线阅读简报图文与表格。\n".encode()
    readme_body = (
        "brief.docx：中文研究简报，摘录主要发现、证据与材料缺口。\n"
        + card_label
        + "report.json：包含范围、可信参考批次状态、完整产品、审查，以及辅助译文的证据、方法与未译原因。\n"
        "evidence.csv：逐项来源、版本、位置和原文引文。\n"
        "tables/：UTF-8 BOM CSV；可能被电子表格解释为公式的文本已加单引号，原值保留在JSON。\n"
        "layers/：GeoJSON。figures/：经静态安全检查的SVG，不依赖网络。\n"
        "manifest.json：附件、来源产品JSON位置、编码后字节数、SHA-256及未单独导出的图件说明。\n"
        "结果仅适用于冻结材料和已声明规则；未确认项、推断和译文不得替代原件。\n"
    ).encode()
    parts = [
        ("brief.docx", brief.getvalue(), "/", "brief"),
        ("report.json", full, "/", "complete_report"),
        ("evidence.csv", sources, "/evidence", "evidence"),
    ]
    if card_name:
        extras.append((card_name, Card(content).make(selected), "/products", "single_page_brief"))
    base_size = len(readme_title) + len(readme_body) + sum(len(data) for _, data, _, _ in [*parts, *extras])
    if base_size > MAX_BUNDLE_BYTES:
        raise ValueError("文件包超过导出上限")
    html_budget = MAX_BUNDLE_BYTES - base_size - len(html_note)
    html = brief_html(doc, html_budget) if html_budget > 0 else None
    if html is not None and len(html) <= html_budget:
        parts.insert(1, ("研究简报.html", html, "/", "readable_brief"))
        readme_title += html_note
    else:
        omitted.append(
            {"pointer": "/", "reason": "研究简报.html 超出文件包剩余空间，完整简报仍可在 brief.docx 中阅读。"}
        )
    parts.append(("README.txt", readme_title + readme_body, "", "instructions"))
    parts.extend(extras)
    manifest = {
        "schema_version": "hangzhi.delivery.v1",
        "case_id": report["case_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "files": [
            {
                "name": path,
                "type": kind,
                "json_pointer": pointer,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for path, data, pointer, kind in parts
        ],
        "omitted": omitted,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path, data, _, _ in parts:
            archive.writestr(path, data)
        archive.writestr("manifest.json", encoded_json(manifest))
    return out.getvalue()


def stage_bundle(kind, content, task_name):
    """Export only this intermediate artifact, including its exact original payload."""
    raw = json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("过程产物超过文件包大小上限，可下载原始文件")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        archive.writestr("过程记录.html", stage_html(kind, content, task_name))
        archive.writestr("original.json", raw)
    return out.getvalue()
