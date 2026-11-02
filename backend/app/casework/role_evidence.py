"""Choose bounded, source-grounded document operations for each stage of the case workflow."""

import re

from .roles import CUES, DATES, DIFF, NUMBERS, PDF, TERMS

_PATTERNS = {
    TERMS: re.compile(r"\([A-Z][A-Z0-9-]{1,11}s?\)"),
    NUMBERS: re.compile(r"\d\s*(?:%|％|Mt\b|kg\b|km\b|ppb\b|ppm\b|万元|亿元|公里|千克)", re.IGNORECASE),
    DATES: re.compile(r"\b\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}|\b\d{1,2}\s+[A-Z][a-z]+\s+\d{4}"),
}


def passages(inputs, records):
    for src in inputs:
        mime = src["media_type"].split(";")[0]
        if mime == "application/pdf":
            for row in records:
                loc = row["location"]
                if row["source_id"] == src["id"] and set(loc) == {"page"} and row["text"]:
                    yield src["id"], {"page": loc["page"]}, row["text"]
        elif src.get("modality") == "text" and isinstance(src.get("content"), str) and src["content"]:
            yield src["id"], {}, src["content"]


def requests(stage, grants, inputs, records, candidate):
    pages = list(passages(inputs, records))
    calls = []
    if stage == "review":
        # Check an actual verbatim claim, not a table serialization, inferred OCR position or arbitrary page.
        for evidence in candidate.get("evidence", []):
            if evidence.get("kind") != "verbatim" or set(evidence.get("location", {})) != {"page"}:
                continue
            for source_id, loc, text in pages:
                quote = evidence.get("quote", "")
                if (
                    loc == evidence["location"]
                    and source_id == evidence["source_id"]
                    and quote
                    and quote in text
                ):
                    args = {"source_id": source_id, **loc, "start": text.index(quote), "length": len(quote)}
                    calls.append((PDF, args))
                    break
            if len(calls) >= 2:
                break
        return calls
    if PDF in grants:
        probe = next(((src, loc) for src, loc, _ in pages if loc), None)
        if probe:
            calls.append((PDF, {"source_id": probe[0], **probe[1], "length": 220}))
    if CUES in grants:
        for src in inputs:
            if src["media_type"].split(";")[0] not in {"application/x-subrip", "text/vtt"}:
                continue
            cue = next(
                (
                    r["location"]
                    for r in records
                    if r["source_id"] == src["id"]
                    and "start_ms" in r["location"]
                    and "end_ms" in r["location"]
                ),
                None,
            )
            if cue and cue["end_ms"] > cue["start_ms"]:
                calls.append(
                    (
                        CUES,
                        {
                            "source_id": src["id"],
                            "start_ms": cue["start_ms"],
                            "end_ms": min(cue["end_ms"], cue["start_ms"] + 60000),
                            "limit": 6,
                        },
                    )
                )
                break
    for tool, pattern in _PATTERNS.items():
        if tool not in grants:
            continue
        page = next(((src, loc) for src, loc, text in pages if pattern.search(text)), None)
        if page:
            calls.append((tool, {"source_id": page[0], **page[1], "limit": 8}))
    if DIFF in grants:
        # Pair only located passages from different original assets. This doesn't establish edition ordering.
        located = []
        for e in candidate.get("evidence", []):
            quote = e.get("quote", "")
            if e.get("kind") != "verbatim" or not quote:
                continue
            found = next(
                (
                    (src, loc, text.index(quote), quote)
                    for src, loc, text in pages
                    if src == e["source_id"] and (not loc or loc == e["location"]) and quote in text
                ),
                None,
            )
            if found and (not located or found[0] != located[0][0]):
                located.append(found)
            if len(located) == 2:
                (a, ap, ai, aq), (b, bp, bi, bq) = located
                args = {
                    "source_id": a,
                    **ap,
                    "start": ai,
                    "end": ai + len(aq),
                    "other_source_id": b,
                    "other_start": bi,
                    "other_end": bi + len(bq),
                }
                if bp:
                    args["other_page"] = bp["page"]
                calls.append((DIFF, args))
                break
    return calls


def check_page(arguments, result, records):
    if result.get("status") == "native_text_unavailable":
        return []
    src, page, start = arguments["source_id"], arguments["page"], arguments["start"]
    text = next((r["text"] for r in records if r["source_id"] == src and r["location"] == {"page": page}), "")
    return [
        {
            "name": "registered_pdf_quote",
            "passed": bool(result.get("quote"))
            and result["quote"] == text[start : start + arguments["length"]],
            "message": "独立读取的 PDF 页内字符与冻结解析记录不一致",
            "hard": True,
        }
    ]
