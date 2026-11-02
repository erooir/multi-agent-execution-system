"""Relate candidate records by their actual evidence roles, text and declared local asset links."""

import unicodedata
from collections import defaultdict
from functools import partial

from . import document_cases


def _rows(view):
    payload = view["asset"].get("content")
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list) or len(records) > 1000:
        return []
    return [row for row in records if isinstance(row, dict)]


def _identity(asset):
    # Equal text alone cannot merge two independent sources or editions.
    fields = (
        "project_id",
        "source_id",
        "source_version",
        "checksum",
        "provenance",
        "classification",
        "media_type",
    )
    identity = tuple(asset.get(field) for field in fields)
    return identity if all(identity) else (asset["id"],)


def _collapse(candidates):
    distinct = {}
    for item in sorted(candidates, key=lambda row: row[0]["asset"]["id"]):
        distinct.setdefault(_identity(item[0]["asset"]), item)
    return list(distinct.values())


def _normalized(text):
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _role(row):
    return row.get("role") or row.get("source_role") or row.get("来源角色")


def _key(row):
    return str(row.get("question_id") or row.get("topic") or row.get("subject") or row.get("问题") or "")


def _add(view, reason):
    return (view, 3, [], reason)


def _matrix(related, views, allowed_roles):
    # The actual analysis handler supplies allowed_roles. A matching "question_id"
    # alone does not authorize unrelated categories of statements in the same project.
    selected, keys = [], set()
    for item in related:
        rows = _rows(item[0])
        roles = {_role(r) for r in rows if _role(r)}
        if roles - allowed_roles:
            continue
        if not rows or not roles:
            if any(r.get("kind") == "question" for r in rows):
                keys.update(_key(r) for r in rows if r.get("kind") == "question")
            selected.append(item)
        else:
            selected.append(item)
            keys.update(_key(r) for r in rows if _role(r) in allowed_roles)
    keys.discard("")
    selected_ids = {item[0]["asset"]["id"] for item in selected}
    for view in views:
        if view["asset"]["id"] in selected_ids:
            continue
        records = _rows(view)
        if {_role(row) for row in records if _role(row)} - allowed_roles:
            continue
        if any(
            _key(row) in keys and (row.get("kind") == "question" or _role(row) in allowed_roles)
            for row in records
        ):
            selected.append(_add(view, "资料的来源角色或问题清单与已选问题编号吻合。"))
    return _collapse(selected)


def _speech(related, views):
    spoken = {
        _normalized(row.get("utterance") or row.get("transcript") or row.get("原话"))
        for view, *_ in related
        for row in _rows(view)
        if row.get("start_ms") is not None and row.get("end_ms") is not None
    }
    spoken.discard("")
    if not spoken:
        return related
    result = []
    for view in views:
        records = _rows(view)
        lines = {
            _normalized(row.get("text") or row.get("transcript"))
            for row in records
            if row.get("kind") == "transcript"
        }
        shared = {line for line in spoken.intersection(lines) if len(line) >= 5}
        terms = {_normalized(r.get("term")) for r in records if r.get("kind") == "glossary"}
        if shared:
            result.append(_add(view, "逐字稿原文与已选时间码发言直接吻合。"))
        elif any(len(term) >= 2 and any(term in phrase for phrase in spoken) for term in terms):
            result.append(_add(view, "术语表含已选发言中出现的原文术语。"))
    for item in related:
        rows = _rows(item[0])
        if rows and all(r.get("kind") in {"transcript", "glossary"} for r in rows):
            continue
        result.append(item)
    return _collapse(result)


def _conflicting_clauses(items):
    claims = defaultdict(set)
    for view, *_ in items:
        for row in _rows(view):
            side, key = row.get("side"), row.get("clause_id")
            text = _normalized(row.get("clause_text"))
            if side in {"before", "after"} and key and text:
                claims[(side, str(key), _normalized(row.get("topic")))].add(text)
    return any(len(texts) > 1 for texts in claims.values())


def resolve_candidates(code, question, views, related):
    """Return normalized candidates and an optional unresolved ambiguity."""
    candidates = _collapse(related)
    handler = document_cases._HANDLERS.get(code)
    if not handler:
        return candidates, None
    implementation = handler.func if isinstance(handler, partial) else handler
    if isinstance(handler, partial) and "expected_roles" in handler.keywords:
        result = _matrix(candidates, views, set(handler.keywords["expected_roles"]))
        # Different substantive prompts sharing an identifier are not interchangeable.
        prompts = defaultdict(set)
        for view, *_ in result:
            for row in _rows(view):
                if row.get("kind") == "question" and _key(row):
                    prompts[_key(row)].add(_normalized(row.get("question") or row.get("query")))
        if any(len(texts - {""}) > 1 for texts in prompts.values()):
            return None, "不同问题共用了同一资料编号，现有说明无法可靠区分对应的来源。"
        return result, None
    if implementation is document_cases.media.speech:
        return _speech(candidates, views), None
    if (
        implementation is document_cases.compare.clauses
        and not all({"before", "after"} <= {r.get("side") for r in _rows(view)} for view, *_ in candidates)
        and _conflicting_clauses(candidates)
    ):
        return None, "同一侧的条款出现多个内容不同的版本，现有说明无法确定应对照哪一份。"
    return candidates, None


def linked_sources(code, views, chosen_ids):
    """Add only explicit source references from a matching record contract in the eligible project set."""
    from . import geo_cases

    handler = geo_cases.ANALYZERS.get(code)
    if handler not in (geo_cases.panels, geo_cases.buildings, geo_cases.cover):
        return [], None
    candidates = {v["asset"]["id"]: v for v in views}
    assets = []
    seen = set(chosen_ids)
    for key in chosen_ids:
        view = candidates.get(key)
        if not view:
            continue
        for row in _rows(view):
            references = []
            if handler is geo_cases.buildings:
                references.extend((row.get("before_image"), row.get("after_image")))
            if row.get("kind") == "raster_binding" and handler in (geo_cases.panels, geo_cases.cover):
                references.append(row.get("target_source_id"))
            for ref in references:
                if not isinstance(ref, str) or not ref:
                    continue
                target = candidates.get(ref)
                if not target or target["asset"].get("modality") != "image":
                    return [], "记录引用的影像尚未登记、已不可用或格式不符，无法核验影像结果。"
                if ref not in seen:
                    seen.add(ref)
                    assets.append({"id": ref, "reason": "来源记录明确引用了此项目内有效影像。"})
    return assets, None
