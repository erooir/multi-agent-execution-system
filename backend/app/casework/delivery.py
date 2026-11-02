"""Build an in-memory, passive delivery ZIP *after* the API has rechecked source access."""

import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime

from .delivery_brief import document_bytes
from .delivery_card import Card
from .delivery_files import csv_table, derived_files, encoded_json
from .delivery_trend import monthly_figures


def bundle(report: dict, metadata: dict | None = None) -> bytes:
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
        not re.fullmatch(r"[BTG](?:0[1-9]|10)", str(report.get("case_id", "")))
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
    )
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
    if content["case_id"] == "B05" and not any(c.get("hard") and not c["passed"] for c in content["review"]):
        figures, skipped = monthly_figures(content["products"], content["evidence"])
        extras.extend(figures)
        omitted.extend(skipped)
    brief = document_bytes(content, selected)
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
    readme = (
        "航智用例研究成果\n\nbrief.docx：中文研究简报，摘录主要发现、证据与材料缺口。\n"
        + card_label
        + "report.json：包含范围、可信参考批次状态、完整产品、审查，以及辅助译文的证据、方法与未译原因。\n"
        "evidence.csv：逐项来源、版本、位置和原文引文。\n"
        "tables/：UTF-8 BOM CSV；可能被电子表格解释为公式的文本已加单引号，原值保留在JSON。\n"
        "layers/：GeoJSON。figures/：经静态安全检查的SVG，不依赖网络。\n"
        "manifest.json：附件、来源产品JSON位置、编码后字节数、SHA-256及未单独导出的图件说明。\n"
        "结果仅适用于冻结材料和已声明规则；未确认项、推断和译文不得替代原件。\n"
    ).encode()
    parts = [
        ("brief.docx", brief, "/", "brief"),
        ("report.json", full, "/", "complete_report"),
        ("evidence.csv", sources, "/evidence", "evidence"),
        ("README.txt", readme, "", "instructions"),
        *extras,
    ]
    if card_name:
        parts.append((card_name, Card(content).make(selected), "/products", "single_page_brief"))
    if sum(len(data) for _, data, _, _ in parts) > 96_000_000:
        raise ValueError("文件包超过导出上限")
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
