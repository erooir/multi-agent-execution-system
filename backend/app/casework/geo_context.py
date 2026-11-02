"""Cite relevant context from supplied documents without treating a report as measurement geometry."""

import re

TOPICS = {
    "G04": r"flood|inundat|洪水|淹没|inundaci",
    "G05": r"damaged|destroyed|building|损伤|损毁",
    "G06": r"landsat|sentinel|airport|land cover|机场|覆盖",
}


def document_context(ctx, code):
    out = []
    pattern = re.compile(TOPICS[code], re.IGNORECASE)
    for r in ctx.rows:
        if not r.get("text") or "page" not in r["location"]:
            continue
        raw = r["text"]
        hit = pattern.search(raw)
        if not hit:
            continue
        # Retain an exact text span, not generated facts, and explicitly constrain its use.
        left = max(0, hit.start() - 80)
        right = min(len(raw), hit.end() + 160)
        ref = ctx.ref(r)
        ctx.evidence[ref]["quote"] = raw[left:right]
        out.append(
            {
                "evidence_ids": [ref],
                "page": r["location"]["page"],
                "quote": raw[left:right],
                "offset": [left, right],
                "use": "材料内报告的背景；不代替原始栅格、逐对象观测或矢量底图。",
            }
        )
        if len(out) >= 12:
            break
    return out
