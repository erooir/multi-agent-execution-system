"""Single-sheet field desk and scientific reading cards. The complete record stays in the ZIP."""

import io
import re
import unicodedata

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .delivery_brief import _table, clean, scope_text, show


def short(value, size):
    if value is None:
        return "未提取"
    text = clean(" ".join(str(value).split()), size + 1)
    width = 0
    for i, char in enumerate(text):
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
        if width > size:
            return text[:i].rstrip() + "…"
    return text


class Card:
    def __init__(self, report):
        self.report = report
        self.evidence = {e["id"]: e for e in report["evidence"]}
        self.readings = {r["evidence_id"]: r for r in report.get("reading_aids", [])}
        self.sources = {}
        self.doc = Document()
        section = self.doc.sections[0]
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.left_margin = section.right_margin = Cm(1.95)
        section.top_margin, section.bottom_margin = Cm(1.65), Cm(1.5)
        style = self.doc.styles["Normal"]
        style.font.name = "Microsoft YaHei"
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(10)
        style.font.color.rgb = RGBColor.from_string("243D4C")
        style.paragraph_format.line_spacing = Pt(15)
        style.paragraph_format.space_after = Pt(3)

    def line(self, label, value="", *, size=10, accent=False):
        p = self.doc.add_paragraph()
        if size > 10:
            p.paragraph_format.line_spacing = Pt(size * 1.5)
        r = p.add_run(label)
        r.bold = True
        r.font.size = Pt(size)
        r.font.color.rgb = RGBColor.from_string("167270" if accent else "253D50")
        if value:
            p.add_run("　" + value)

    def heading(self, text):
        self.line(text, size=12, accent=True)

    def entry(self, row, size, *, excerpt=False, comparison=False):
        """A field-only row cannot inherit a translation of a different field in the same record."""
        if not isinstance(row, dict):
            return "未提取"
        found = [self.evidence[key] for key in row.get("evidence_ids", []) if key in self.evidence]
        if not found:
            return "未取得可定位证据"
        first = found[0]
        supplied = row.get("supplied_value", row.get("value"))
        text = supplied if supplied is not None else row.get("text")
        aid = self.readings.get(first["id"])
        if not text:
            return "未提取"
        if (
            excerpt
            and supplied is None
            and aid
            and aid.get("text_zh")
            and row.get("text") == first.get("quote")
        ):
            text = "译摘：" + str(aid["text_zh"])
            fields = aid.get("source_fields") or {}
            if comparison and all(fields.get(k) for k in ("trial", "control", "condition", "scope")):
                # Long expanded names have their exact spellings preserved in the original evidence.
                trial, control = (
                    re.sub(r"\s*\([^()]+\)", "", fields[k]).strip() or fields[k] for k in ("trial", "control")
                )
                text = f"译摘：{trial} 与 {control}。条件：{fields['condition']}。范围：{fields['scope']}。"
            elif self.report["case_id"] == "B01" and "。" in text:
                # PDF extraction can append unrelated footnotes to a complete observation.
                first_sentence, remainder = text.split("。", 1)
                if remainder.lstrip().startswith(("*", "†")):
                    text = first_sentence + "。"
        return short(text, size) + self.locator(first)

    def locator(self, e):
        key = (e["source_id"], e["source_version"])
        source = self.sources.setdefault(key, f"S{len(self.sources) + 1}")
        loc = e["location"]
        if loc.get("page") is not None:
            place = "第" + short(loc["page"], 6) + "页"
        else:
            place = ""
        for k, name in (("sheet", "表"), ("row", "行"), ("paragraph", "段"), ("figure", "图")):
            if k in loc:
                place += ("·" if place else "") + name + short(loc[k], 10)
                break
        return f"〔{source}/{short(e['id'], 12)}·{place or '详见证据索引'}〕"

    def observation(self):
        prod = self.report["products"]
        win = prod.get("report_window", {})
        self.line(
            "报告编号",
            self.entry(win.get("report_number"), 28) + "　发布 " + self.entry(win.get("published_date"), 32),
        )
        self.line(
            "观测截至",
            self.entry(win.get("observed_until"), 45) + "　时区 " + self.entry(win.get("timezone"), 34),
        )
        self.heading("分区停火观察")
        rows = []
        for row in (prod.get("observations") or [])[:2]:
            e = next((self.evidence[i] for i in row.get("evidence_ids", []) if i in self.evidence), None)
            if e:
                rows.append(
                    [
                        short(row.get("region"), 21),
                        *[short(row.get(k), 15) for k in ("count", "previous", "change")],
                        self.locator(e),
                    ]
                )
        if rows:
            _table(
                self.doc,
                ["地区", "本期（起）", "上期（起）", "差额（起）", "来源位置"],
                rows,
                [3.0, 2.9, 2.9, 2.9, 5.25],
            )
        else:
            self.line("数量", "未取得可定位的分区统计。")
        self.heading("民用事项与监测边界")
        for key, label in (
            ("observed", "监测观察 / 设施"),
            ("reported", "受访者或报告转述 · reported"),
            ("assessed", "报告评估 · assessed"),
            ("limitations", "观察限制"),
        ):
            entries = prod.get("context", {}).get(key) or []
            self.line(label, self.entry(entries[0] if entries else None, 126, excerpt=True))
        self.line("口径", "数字仅覆盖报告监测所及的观察。")

    def research(self):
        prod = self.report["products"]
        info = prod.get("bibliography", {})
        self.line("题名", self.entry(info.get("title"), 105))
        self.line("作者（摘列）", self.entry(info.get("authors"), 82))
        self.line("机构（摘列）", self.entry(info.get("institution"), 84))
        self.line(
            "发表", self.entry(info.get("published_date"), 32) + "　DOI " + self.entry(info.get("doi"), 45)
        )
        self.heading("研究证据")
        card = prod.get("literature_card", {})
        for key, label in (
            ("question", "研究问题"),
            ("comparison", "比较对象与条件"),
            ("measurements", "实测结果"),
            ("model_results", "模型结果"),
            ("limitations", "适用限制"),
        ):
            entries = card.get(key) or []
            row = entries[0] if entries else None
            note = self.entry(
                row,
                170 if key in {"comparison", "measurements"} else 150,
                excerpt=True,
                comparison=key == "comparison",
            )
            self.line(label, note)
            if key == "model_results" and note.startswith("译摘："):
                self.line("模型原文", short(row["text"], 184))
        self.line("解读", short(prod.get("interpretation") or "结论仅限论文给定条件。", 66))

    def make(self, metadata):
        r = self.report
        b01 = r["case_id"] == "B01"
        title = "停火监测日报快报" if b01 else "试验论文阅读卡"
        self.doc.core_properties.title = title
        self.doc.core_properties.author = "航智平台"
        self.line(
            f"航智   /   {r['case_id']}   /   " + ("事件快报" if b01 else "文献速读"), size=10, accent=True
        )
        self.line(title, size=21)
        self.line("范围", short(scope_text(r), 80) + "　状态：" + show(metadata.get("status", "未提供"), 12))
        rejected = [c for c in r["review"] if not c["passed"] and c.get("hard")]
        if rejected:
            self.heading("复核未通过")
            self.line("待核", short(rejected[0].get("message") or "候选数据未通过强制核验。", 92))
        elif b01:
            self.observation()
        else:
            self.research()
        notes = r.get("reading_notes") or []
        if r["gaps"]:
            self.line(
                "缺项 / 限制",
                short(r["gaps"][0], 85) + (f"（共{len(r['gaps'])}项）" if len(r["gaps"]) > 1 else ""),
            )
        self.heading("回查与译注")
        for (asset, version), tag in list(self.sources.items())[:2]:
            self.line(tag, f"资料 {short(asset, 40)} · 版本 {short(version, 21)}")
        if len(self.sources) > 2:
            self.line("其余来源", "按正文证据编号回查 evidence.csv 的资料与版本。")
        self.line("阅读说明", "本页为摘录。“译摘”为辅助译文，以原文为准。编号与完整原文见 evidence.csv。")
        if notes:
            self.line(
                "未采用译文",
                short(notes[0], 70) + (f"（共{len(notes)}项，见 report.json）" if len(notes) > 1 else ""),
            )
        output = io.BytesIO()
        self.doc.save(output)
        return output.getvalue()
