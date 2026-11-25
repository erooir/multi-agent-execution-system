"""Offline reading copies of saved case artifacts. All data is rendered as text."""

from base64 import b64encode
from html import escape

from docx.oxml.ns import qn
from docx.table import Table

from .delivery_brief import LABELS, clean, show

STAGE_TITLES = {"case_records": "资料解析记录", "case_candidate": "专业分析草稿"}
_COLUMNS = {
    "id": "编号",
    "heading": "小标题",
    "evidence_ids": "引用证据",
    "fields": "原件字段",
    "passed": "通过",
    "message": "核查说明",
    "hard": "必需",
    "media_type": "文件类型",
}
_STYLE = """
body{max-width:70rem;margin:auto;padding:2rem;color:#182735;background:#fff;
font:16px/1.7 "Microsoft YaHei","Noto Sans CJK SC",sans-serif;overflow-wrap:anywhere}
h1,h2,h3{line-height:1.35}h1{font-size:1.9rem}h2{margin-top:2rem;color:#175f62}
p,td{white-space:pre-wrap}table{width:100%;border-collapse:collapse;font-size:.93rem}
th,td{padding:.45rem .65rem;border:1px solid #ced9dc;text-align:left;vertical-align:top}
th{background:#eef4f4}small{color:#455b68}@media print{body{padding:0}thead{display:table-header-group}}
figure{margin:1rem 0}figure img{max-width:100%;height:auto}
"""


def _page(title, sections):
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style><main>{''.join(sections)}</main></html>"
    ).encode()


def _table(headers, rows):
    head = "".join(f'<th scope="col">{escape(cell)}</th>' for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def brief_html(doc, max_bytes):
    """Share the Word brief's actual headings, evidence tables and review qualifications."""
    sections, images = [], []
    caption = "简报图件"
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            rows = [[cell.text for cell in row.cells] for row in block.rows]
            if rows:
                sections.append(_table(rows[0], rows[1:]))
        elif block.text:
            tag = {"Title": "h1", "Heading 1": "h2", "Heading 2": "h3"}.get(block.style.name, "p")
            if tag in {"h2", "h3"}:
                caption = block.text
            text = "".join(
                f"<strong>{escape(run.text)}</strong>" if run.bold else escape(run.text) for run in block.runs
            )
            sections.append(f"<{tag}>{text}</{tag}>")
        for blip in block._element.xpath(".//a:blip"):
            image = doc.part.related_parts.get(blip.get(qn("r:embed")))
            if image and image.content_type in {"image/png", "image/jpeg"}:
                images.append((len(sections), caption, image))
                sections.append("<p><small>图件见同包 brief.docx。</small></p>")
    page = _page(doc.core_properties.title, sections)
    if not images:
        return page
    remaining = max_bytes - len(page)
    for index, caption, image in images:
        prefix = f'<figure><img alt="{escape(caption)}" src="data:{image.content_type};base64,'
        suffix = '"></figure>'
        size = len((prefix + suffix).encode()) + 4 * ((len(image.blob) + 2) // 3)
        delta = size - len(sections[index].encode())
        if delta <= remaining:
            sections[index] = prefix + b64encode(image.blob).decode("ascii") + suffix
            remaining -= delta
    return _page(doc.core_properties.title, sections)


def _items(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def _record_table(title, rows):
    if not rows:
        return ""
    rows = _items(rows)
    visible = [row if isinstance(row, dict) else {"内容": row} for row in rows[:100]]
    keys = list(dict.fromkeys(k for row in visible for k in row))[:10]
    headings = [_COLUMNS.get(k, LABELS.get(k, k)) for k in keys]
    cells = [[show(row[k], 1200) if row.get(k) is not None else "" for k in keys] for row in visible]
    return (
        f"<h2>{escape(title)}</h2><p>共 {len(rows)} 条，摘列 {len(visible)} 条。完整字段见 original.json。</p>"
        + _table(headings, cells)
    )


def stage_html(kind, content, task_name):
    stage = STAGE_TITLES[kind]
    title = clean(task_name, 180)
    parts = [f"<h1>{escape(title)}</h1><p>{stage}</p>"]
    if kind == "case_records":
        if content.get("approach"):
            parts.append(f"<h2>研究步骤</h2><p>{escape(show(content['approach'], 2000))}</p>")
        if content.get("priority_ids"):
            ids = "、".join(show(i) for i in _items(content["priority_ids"]))
            parts.append(f"<p>优先核对的证据编号　{escape(ids)}</p>")
        parts.append(_record_table("原件核查", content.get("source_checks", [])))
        parts.append(_record_table("证据与原件位置", content.get("records", [])))
        attachments = content.get("file_context")
        if isinstance(attachments, dict):
            attachments = [{"文件": k, "内容摘录": v} for k, v in attachments.items()]
        parts.append(_record_table("随包文件", attachments))
    else:
        for finding in _items(content.get("findings")):
            if not isinstance(finding, dict) or not {"heading", "text"} <= finding.keys():
                parts.append(_record_table("分析记录", [finding]))
                continue
            citations = "、".join(show(i) for i in _items(finding.get("evidence_ids")))
            parts.append(
                f"<h2>{escape(show(finding['heading']))}</h2><p>{escape(show(finding['text'], 2000))}</p>"
            )
            if citations:
                parts.append(f"<p><small>引用证据　{escape(citations)}</small></p>")
        products = content.get("products") or {}
        for name, value in (products if isinstance(products, dict) else {"成果": products}).items():
            label = LABELS.get(name, name)
            if isinstance(value, list) and value and all(isinstance(row, dict) for row in value[:100]):
                parts.append(_record_table(label, value))
            else:
                parts.append(f"<h2>{escape(label)}</h2><p>{escape(show(value, 600))}</p>")
        parts.append(_record_table("引用证据", content.get("evidence", [])))
    if content.get("gaps"):
        parts.append(
            "<h2>资料缺口</h2><ul>"
            + "".join(f"<li>{escape(show(g, 1200))}</li>" for g in _items(content["gaps"]))
            + "</ul>"
        )
    return _page(f"{title} · {stage}", parts)
