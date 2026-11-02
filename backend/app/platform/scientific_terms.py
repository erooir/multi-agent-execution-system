"""Check an explicit abbreviation definition against claims that it was not supplied."""

import re

from .quality import text_of

WORD = r"[A-Za-z]+(?:-[A-Za-z]+)*"
ABBREVIATION = r"[A-Z][A-Z0-9-]{1,11}"


def initials(phrase):
    return "".join(
        w[0].upper()
        for w in re.findall(WORD, phrase)
        if w.lower() not in {"of", "the", "and", "for", "in", "to", "a", "an"}
    )


def definitions(text):
    """Evidence requires contiguous source wording and matching initials; never guess an expansion."""
    for match in re.finditer(rf"[（(]\s*({ABBREVIATION})(?:s)?\s*[)）]", text):
        key = match[1].replace("-", "")
        if not 2 <= len(key) <= 10:
            continue
        head = text[max(0, match.start() - 140) : match.start()]
        words = re.search(rf"((?:{WORD}[\s]+){{1,11}}{WORD})\s*$", head)
        if not words:
            continue
        tokens = list(re.finditer(WORD, words[1]))
        for start in range(len(tokens) - 1):
            phrase = words[1][tokens[start].start() :]
            if initials(phrase) == key:
                lo = match.start() - len(head) + words.start(1) + tokens[start].start()
                yield match[1], phrase, lo, match.end()
                break
    for match in re.finditer(
        rf"(?<![A-Za-z0-9-])({ABBREVIATION})(?:s)?\s*[（(]\s*((?:{WORD}\s+){{1,10}}{WORD})\s*[)）]", text
    ):
        if initials(match[2]) == match[1].replace("-", ""):
            yield match[1], match[2], match.start(), match.end()


NEGATIVE = re.compile(
    r"(?:未|没有|并无|缺少|无法|不能|未见|无从)[^。；，,\n]{0,20}?(?:全称|展开|释义|英文全名)"
    r"|(?:全称|展开|释义|英文全名)[^。；，,\n]{0,13}?(?:缺失|缺少|未知|未(?:给出|提供|说明|出现|定义|列明|见)|不详)"
    r"|(?:未(?:被|明确|直接)?定义|没有定义)(?:\s*[A-Za-z0-9-]+)?\s*(?:[。；，,.]|$)"
    r"|\b(?:not|never)\s+(?:explicitly\s+)?(?:defined|expanded|spelled out)\b"
    r"|\b(?:expansion|full (?:form|name))\b[^.;\n]{0,25}?\b(?:not (?:given|provided|specified)|absent|missing)\b",
    re.IGNORECASE,
)


def conflicts(content, inputs):
    if not isinstance(content, dict):
        return []
    parts = [content.get("text", ""), *(content.get("missing_information") or [])]
    clauses = [
        clause.strip()
        for part in parts
        if isinstance(part, str)
        for clause in re.split(r"[。！？;；\n]|(?<=[.?!])\s+|[，,]", part)
        if clause.strip()
    ]
    findings = []
    for asset in inputs:
        raw = text_of(asset)
        for short, meaning, start, end in definitions(raw):
            match = next(
                (
                    clause
                    for clause in clauses
                    if NEGATIVE.search(clause)
                    and re.search(rf"(?<![A-Za-z0-9-]){re.escape(short)}s?(?![A-Za-z0-9-])", clause)
                ),
                None,
            )
            if not match:
                continue
            findings.append(
                {
                    "code": "explicit_expansion_denied",
                    "acronym": short,
                    "expansion": " ".join(meaning.split()),
                    "source_id": asset["id"],
                    "source_version": asset["source_version"],
                    "start": start,
                    "end": end,
                    "quote": raw[start:end],
                    "message": f"模型称 {short} 未给出全称或未定义；冻结来源明确写有“{' '.join(raw[start:end].split())}”。",
                }
            )
    return findings
