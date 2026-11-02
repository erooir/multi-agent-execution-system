"""Read-only document operations. Every quoted offset belongs to a frozen asset (and PDF page)."""

import io
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from itertools import chain, islice

from pydantic import Field, model_validator
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .contracts import Contract
from .domain_tool import DomainOperation
from .scientific_terms import definitions


class SearchPage(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    page: int | None = Field(default=None, ge=1, le=800, description="PDF 页码从1起；文本资料勿填")
    start: int = Field(default=0, ge=0, description="从本页或原文的字符偏移起查")
    limit: int = Field(default=12, ge=1, le=20)


class DefinitionSearch(SearchPage):
    acronym: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9-]{1,11}s?$")


class PassagePair(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    other_source_id: str = Field(min_length=1, max_length=100)
    page: int | None = Field(default=None, ge=1, le=800)
    other_page: int | None = Field(default=None, ge=1, le=800)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    other_start: int = Field(ge=0)
    other_end: int = Field(gt=0)
    limit: int = Field(default=16, ge=1, le=32)

    @model_validator(mode="after")
    def ranges(self):
        if self.source_id == self.other_source_id:
            raise ValueError("需要两份不同的冻结来源")
        if not (0 < self.end - self.start <= 2048 and 0 < self.other_end - self.other_start <= 2048):
            raise ValueError("比较区间必须非空，每侧最多2048字符")
        return self


class CueWindow(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    limit: int = Field(default=12, ge=1, le=20)

    @model_validator(mode="after")
    def window(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class PdfPage(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    page: int = Field(ge=1, le=800)
    start: int = Field(default=0, ge=0)
    length: int = Field(default=1800, ge=1, le=4000)


@dataclass(frozen=True)
class Passage:
    source: dict
    text: str
    page: int | None = None
    page_count: int | None = None

    def reference(self):
        result = {"source_id": self.source["id"], "source_version": self.source["source_version"]}
        if self.page is not None:
            result["page"] = self.page
        return result

    def span(self, start, end):
        return {**self.reference(), "start": start, "end": end, "quote": self.text[start:end]}

    def metadata(self):
        result = {**self.reference(), "total_chars": len(self.text), "status": "ok"}
        if self.page is not None:
            result["page_count"] = self.page_count
            if not self.text.strip():
                result["status"] = "native_text_unavailable"
        return result


def _passage(read_source, source_id, page=None):
    source = read_source(source_id)
    mime = source.get("media_type", "").partition(";")[0].lower()
    count = None
    if mime == "application/pdf":
        if page is None:
            raise ValueError("PDF必须指定页码")
        raw = read_source.read_bytes(source_id)
        if len(raw) > 10_000_000:
            raise ValueError("PDF超过文件上限")
        try:
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if reader.is_encrypted:
                raise ValueError("不能解析加密PDF")
            count = len(reader.pages)
            if count > 800 or not 1 <= page <= count:
                raise ValueError("PDF页数或页码超限")
            text = reader.pages[page - 1].extract_text() or ""
        except PyPdfError as exc:
            raise ValueError("PDF解析失败") from exc
    else:
        if page is not None:
            raise ValueError("非PDF资料不能指定页码")
        if mime not in {"text/plain", "text/markdown", "text/vtt", "application/x-subrip"}:
            raise ValueError("该操作需要正文文本、字幕或原生PDF页")
        text = source.get("content")
        if text is None:
            text = read_source.read_bytes(source_id).decode("utf-8-sig")
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError("单次处理的文本或PDF页超过范围")
    return Passage(source, text, page, count)


def _results(passage, args, mentions):
    if args.start > len(passage.text):
        raise ValueError("字符偏移超出冻结原文")
    found = list(islice((row for row in mentions if row["start"] >= args.start), args.limit + 1))
    truncated = len(found) > args.limit
    return {
        **passage.metadata(),
        "items": found[: args.limit],
        "truncated": truncated,
        "next_start": found[-1]["start"] if truncated else None,
    }


def compare_passages(read_source, args):
    before = _passage(read_source, args.source_id, args.page)
    after = _passage(read_source, args.other_source_id, args.other_page)
    if args.end > len(before.text) or args.other_end > len(after.text):
        raise ValueError("比较区间超出冻结原文")
    left = before.text[args.start : args.end]
    right = after.text[args.other_start : args.other_end]
    differences = []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if op != "equal":
            differences.append(
                {
                    "operation": op,
                    "before": before.span(args.start + a0, args.start + a1),
                    "after": after.span(args.other_start + b0, args.other_start + b1),
                }
            )
    return {
        "before_scope": {**before.reference(), "start": args.start, "end": args.end},
        "after_scope": {**after.reference(), "start": args.other_start, "end": args.other_end},
        "identical": left == right,
        "change_count": len(differences),
        "items": differences[: args.limit],
        "truncated": len(differences) > args.limit,
    }


_NUMBER = r"[+−-]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)(?:[Ee][+−-]?[0-9]+)?"
_UNIT = r"(?:[kMGT]?g\s*(?:CO[₂2](?:e|eq)?)\s*/\s*kg|[kMGT]?t\s*CO[₂2](?:e|eq)?|(?:mg|µg|μg|ug|ng|g)\s*/\s*(?:kg|L|m[³3])|m/s|km/h|km[²2³3]|m[²2³3]|℃|°C|MW|kW|kWh|GHz|MHz|kHz|Hz|kPa|MPa|Pa|ppm|ppb|km|cm|mm|nm|ms|kg|mg|µg|μg|Gt|Mt|kt|dB|K|m|g|t|%|％|毫米|千米|平方公里|公里|千克|毫克|分钟|秒)"
_QUANTITY = re.compile(
    rf"(?<![A-Za-z0-9_.+,−-])(?:(?P<bound>[<>≤≥≈~])[ \t]{{0,8}})?(?P<a>{_NUMBER})"
    rf"(?:\s*(?P<op>±|\+/-|[-–—~至到])\s*(?P<b>{_NUMBER}))?\s*(?P<unit>{_UNIT})"
    r"(?![A-Za-z0-9²³/])"
)


def extract_quantities(read_source, args):
    p = _passage(read_source, args.source_id, args.page)

    def mentions():
        for m in _QUANTITY.finditer(p.text):
            start = m.start("bound") if m["bound"] else m.start("a")
            yield {
                **p.span(start, m.end()),
                "value": m["a"],
                "second_value": m["b"],
                "relation": m["op"] or m["bound"] or "literal",
                "unit": m["unit"],
                "context": p.span(max(0, start - 40), min(len(p.text), m.end() + 40)),
            }

    return _results(p, args, mentions())


def list_definitions(read_source, args):
    p = _passage(read_source, args.source_id, args.page)
    # definitions() supports both 'expanded wording (AB)' and 'AB (expanded wording)'.
    # Merge its two passes in offset order before selecting a page.
    found = sorted(
        (start, end, acronym, expansion)
        for acronym, expansion, start, end in definitions(p.text)
        if args.acronym is None or args.acronym.removesuffix("s") == acronym
    )
    return _results(
        p,
        args,
        (
            {**p.span(start, end), "acronym": acronym, "expansion": expansion}
            for start, end, acronym, expansion in found
        ),
    )


_MONTHS = {
    name: i
    for i, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}
_MONTH = "|".join(_MONTHS)
_DATE = re.compile(
    rf"(?<![A-Za-z0-9])(?:(?P<year>[12][0-9]{{3}})(?P<sep>[-/])(?P<month>[0-9]{{1,2}})(?P=sep)(?P<day>[0-9]{{1,2}})"
    rf"|(?P<cy>[12][0-9]{{3}})年(?P<cm>[0-9]{{1,2}})月(?P<cd>[0-9]{{1,2}})日"
    rf"|(?P<ed>[0-9]{{1,2}})\s+(?P<em>{_MONTH})\s+(?P<ey>[12][0-9]{{3}})"
    rf"|(?P<am>{_MONTH})\s+(?P<ad>[0-9]{{1,2}}),\s+(?P<ay>[12][0-9]{{3}}))(?![0-9])",
    re.IGNORECASE,
)


def timeline_dates(read_source, args):
    p = _passage(read_source, args.source_id, args.page)

    def mentions():
        for m in _DATE.finditer(p.text):
            year = m["year"] or m["cy"] or m["ey"] or m["ay"]
            month = m["month"] or m["cm"] or _MONTHS[(m["em"] or m["am"]).title()]
            try:
                when = date(int(year), int(month), int(m["day"] or m["cd"] or m["ed"] or m["ad"]))
            except ValueError:
                continue
            yield {
                **p.span(*m.span()),
                "date": when.isoformat(),
                "context": p.span(max(0, m.start() - 40), min(len(p.text), m.end() + 40)),
            }

    result = _results(p, args, mentions())
    result["items"].sort(key=lambda row: (row["date"], row["start"]))
    result["ordering"] = "selected_mentions_sorted_by_date"
    return result


_TIME = r"(?:(?:[0-9]{1,5}):)?[0-5][0-9]:[0-5][0-9][,.][0-9]{3}"
_CUE = re.compile(
    rf"^[ \t]*(?P<a>{_TIME})[ \t]*-->[ \t]*(?P<b>{_TIME})(?:[ \t]+[^\r\n]*)?\r?\n", re.MULTILINE
)
_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")


def _ms(stamp):
    seconds, fraction = stamp.replace(",", ".").split(".")
    parts = [int(piece) for piece in seconds.split(":")]
    return (parts[-2] * 60 + parts[-1] + (3600 * parts[0] if len(parts) == 3 else 0)) * 1000 + int(fraction)


def select_cues(read_source, args):
    p = _passage(read_source, args.source_id)
    if p.source["media_type"].partition(";")[0].lower() not in {"application/x-subrip", "text/vtt"}:
        raise ValueError("回听定位需要 SRT 或 VTT 字幕")
    selected = []
    invalid = 0
    offset = 0
    # Only a real cue block may supply timestamps. WEBVTT comments can contain timestamp examples.
    boundaries = (m.span() for m in _BREAK.finditer(p.text))
    for end, next_offset in chain(boundaries, [(len(p.text), len(p.text))]):
        block = p.text[offset:end]
        start = offset
        offset = next_offset
        if not block.strip() or block.splitlines()[0].split(maxsplit=1)[:1] in (
            ["WEBVTT"],
            ["NOTE"],
            ["STYLE"],
            ["REGION"],
        ):
            continue
        match = _CUE.search(block)
        if not match or len(block[: match.start()].splitlines()) > 1:
            invalid += 1
            continue
        a, b = _ms(match["a"]), _ms(match["b"])
        body = start + match.end()
        if b <= a or not p.text[body:end].strip() or "-->" in p.text[body:end]:
            invalid += 1
            continue
        if a < args.end_ms and b > args.start_ms:
            selected.append(
                {
                    **p.span(body, min(end, body + 512)),
                    "start_ms": a,
                    "end_ms": b,
                    "cue_truncated": end - body > 512,
                }
            )
            if len(selected) > args.limit:
                break
    return {
        **p.metadata(),
        "items": selected[: args.limit],
        "truncated": len(selected) > args.limit,
        "invalid_cues_seen": invalid,
        "window_ms": [args.start_ms, args.end_ms],
    }


def read_pdf_page(read_source, args):
    p = _passage(read_source, args.source_id, args.page)
    if args.start > len(p.text):
        raise ValueError("字符偏移超出PDF原页")
    end = min(len(p.text), args.start + args.length)
    return {**p.metadata(), **p.span(args.start, end), "truncated": end < len(p.text)}


TOOLS = (
    DomainOperation(
        "evidence.compare_passages",
        "compare_passages",
        "对照两份原件的指定文字区间",
        PassagePair,
        compare_passages,
        packages=("pypdf",),
    ),
    DomainOperation(
        "evidence.extract_quantities",
        "extract_quantities",
        "定位带单位的数值、范围及误差",
        SearchPage,
        extract_quantities,
        packages=("pypdf",),
    ),
    DomainOperation(
        "evidence.list_definitions",
        "list_definitions",
        "核对原文中缩略语的显式释义",
        DefinitionSearch,
        list_definitions,
        dependencies=("backend/app/platform/scientific_terms.py",),
        packages=("pypdf",),
    ),
    DomainOperation(
        "evidence.timeline_dates",
        "timeline_dates",
        "从原文建立有据可查的绝对日期索引",
        SearchPage,
        timeline_dates,
        packages=("pypdf",),
    ),
    DomainOperation(
        "evidence.select_cues", "select_cues", "按半开时间窗定位原字幕片段", CueWindow, select_cues
    ),
    DomainOperation(
        "evidence.pdf_page",
        "read_pdf_page",
        "提取指定PDF原页的文字窗口",
        PdfPage,
        read_pdf_page,
        packages=("pypdf",),
    ),
)
