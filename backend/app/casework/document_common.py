"""Common evidence ledger for offline document tools. Inputs are frozen source records."""

import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field


def value(row, *keys, default=None):
    fields = row.get("fields", {})
    for key in keys:
        if key in fields and fields[key] not in (None, ""):
            return fields[key]
    return default


def string(row, *keys):
    v = value(row, *keys)
    return str(v).strip() if v is not None else ""


def number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)", v):
            return None
        v = v.replace(",", "")
    try:
        result = float(v)
    except (ValueError, TypeError, OverflowError):
        return None
    return (
        int(result)
        if math.isfinite(result) and result.is_integer()
        else result
        if math.isfinite(result)
        else None
    )


def items(v):
    if v in (None, ""):
        return []
    if isinstance(v, list):
        return [str(i).strip() for i in v if i not in (None, "")]
    return [str(v).strip()]


def norm(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def units(text):
    """Literal text slices, never whitespace-normalized before citing."""
    # Keep decimal numbers, abbreviated English names and wrapped lines intact.
    for chunk in re.split(r"(?<=[!?。！？；;])\s*|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])|\n\s*\n|[•\uf0b7]", text):
        if chunk.strip():
            yield chunk.strip()


def tokens(text):
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", norm(text)))


@dataclass
class Run:
    records: list[dict]
    retry: bool = False
    repair_action: str | None = None
    repair_targets: tuple[str, ...] = ()
    gaps: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    _seen: dict = field(default_factory=dict)

    def __post_init__(self):
        usable = []
        for r in self.records:
            if (
                not isinstance(r, dict)
                or not r.get("source_id")
                or not r.get("source_version")
                or not isinstance(r.get("location"), dict)
                or not r["location"]
            ):
                self.gap("有输入记录缺少资料编号、版本或定位，未用于结论")
                continue
            if not isinstance(r.get("fields", {}), dict) or not isinstance(r.get("text", ""), str):
                self.gap("输入记录字段或文本类型无效")
                continue
            usable.append(r)
        self.records = usable
        if not usable:
            self.gap("没有带来源定位的可分析记录")

    def gap(self, message):
        if message not in self.gaps:
            self.gaps.append(message)

    def cite(self, r, quote=None):
        raw = r.get("text", "")
        structured = quote is None and not raw.strip()
        if quote is None:
            quote = json.dumps(r.get("fields", {}), ensure_ascii=False, sort_keys=True) if structured else raw
        # Keep exact spans even when input contains newlines or OCR uncertainty.
        if not structured and quote not in raw:
            raise ValueError("证据摘录未出现在所引输入记录")
        # A field-only citation is a serialization of the source row, not a literal quote.
        quote = quote[:600]
        key = (
            r["source_id"],
            str(r["source_version"]),
            json.dumps(r["location"], sort_keys=True, ensure_ascii=False),
            quote,
        )
        if key not in self._seen:
            identifier = f"E{len(self.evidence) + 1}"
            self._seen[key] = identifier
            self.evidence.append(
                {
                    "id": identifier,
                    "source_id": r["source_id"],
                    "source_version": r["source_version"],
                    "location": dict(r["location"]),
                    "quote": quote,
                    "kind": "record" if structured else "verbatim",
                }
            )
        return self._seen[key]

    def excerpt(self, r, quote=None, **extra):
        text = quote if quote is not None else r.get("text", "")
        return {"text": text[:600], **extra, "evidence_ids": [self.cite(r, quote)]}

    def find(self, patterns, limit=4):
        found = []
        seen = set()
        for r in self.records:
            for s in units(r.get("text", "")):
                if not any(re.search(p, s, re.IGNORECASE) for p in patterns) or norm(s) in seen:
                    continue
                seen.add(norm(s))
                found.append(self.excerpt(r, s))
                if len(found) >= limit:
                    return found
        return found

    def slots(self, definitions):
        """Prefer supplied fields; fallback to traceable sentence retrieval, never invented summaries."""
        result = {}
        for name, (keys, patterns) in definitions.items():
            entries = [
                self.excerpt(r, supplied_value=value(r, *keys))
                for r in self.records
                if value(r, *keys) is not None
            ]
            if not entries:
                entries = self.find(patterns, 5 if self.retry else 2)
            if (
                not entries
                and self.repair_action == "expand_research_evidence"
                and (not self.repair_targets or name in self.repair_targets)
            ):
                from .repair_plan import research_alternatives

                entries = [
                    self.excerpt(row, quote) for row, quote in research_alternatives(name, self.records)
                ]
            if not entries:
                self.gap(f"未找到 {name} 的可定位证据")
            result[name] = entries
        return result


def grouped(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    return groups
