"""Select task inputs from a project-wide knowledge search, without loading the whole catalogue at once."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath

from ..casework.intake_plan import _eligible, _normal, _terms
from .contracts import CASE_DOCUMENT_MEDIA_TYPES, CASE_ID_PATTERN, MAX_TASK_INPUTS, is_supplementary
from .quality import text_of

_BATCH_SIZE = 32
_CASE = re.compile(
    r"(?<![a-z0-9])" + CASE_ID_PATTERN.removeprefix("^").removesuffix("$") + r"(?![a-z0-9])", re.IGNORECASE
)
_GENERAL_ZH = re.compile(
    r"查询|检索|核对|核查|计算|列出|使用|按照|整理|提取|汇总|输出|对比|比较|报告|数据|"
    r"任务|来源|参考|样本|公开|本地|文件|项目|材料|统计|结果|指定|所有|相关|基于"
)
_GENERAL_WORDS = {
    "acceptance",
    "analysis",
    "analyze",
    "catalog",
    "check",
    "compare",
    "csv",
    "data",
    "dataset",
    "demo",
    "docx",
    "extract",
    "file",
    "files",
    "find",
    "json",
    "normalize",
    "pdf",
    "project",
    "public",
    "report",
    "reports",
    "sample",
    "search",
    "source",
    "sources",
    "summarize",
    "summary",
    "supplied",
    "task",
    "text",
    "xlsx",
}


@dataclass(frozen=True)
class _Candidate:
    id: str
    source_id: str
    version: str
    checksum: str
    order: tuple[str, str]
    rank: tuple[int, ...] | None


def _topic_pattern(query):
    words = _terms(_GENERAL_ZH.sub(" ", _CASE.sub(" ", query))) - _GENERAL_WORDS
    english = sorted((re.escape(w) for w in words if w.isascii()), key=len, reverse=True)
    chinese = sorted((w for w in words if not w.isascii()), key=lambda w: (-len(w), w))
    alternatives = ([r"(?<![a-z0-9])(?:" + "|".join(english) + r")(?![a-z0-9])"] if english else []) + chinese
    return (re.compile("|".join(alternatives)) if alternatives else None), (1 if len(words) == 1 else 2)


def _topic_score(pattern, value):
    if not pattern or not value:
        return 0
    terms = set()
    score = 0
    for match in pattern.finditer(_normal(value)):
        term = match[0]
        if term not in terms:
            terms.add(term)
            score += 1 if len(term) == 2 else 3
            if score >= 8:
                return 8
    return score


def _named(query, label):
    key = _normal(label).strip()
    if len(key) < 3 or key in _GENERAL_WORDS or _CASE.fullmatch(key) or key not in query:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", query) is not None


def _rank(asset, query, topic, case_codes, fingerprints, case_task):
    pattern, minimum_body_score = topic
    source = asset["source_id"]
    filename = (asset.get("original_file") or {}).get("filename") or asset.get("uploaded_filename") or ""
    labels = (source, asset["name"], PurePath(filename).stem if filename else "")
    meta = _normal(" ".join(labels))
    tagged = set(_CASE.findall(meta))
    if tagged and case_codes and tagged.isdisjoint(case_codes):
        return None
    named = any(_named(query, label) for label in labels)
    trusted = (asset["checksum"], str(asset["source_version"])) in fingerprints
    if case_task and is_supplementary(asset) and not (named or trusted):
        return None
    same_case = bool(tagged & case_codes)
    title_hits = _topic_score(pattern, meta)
    body_hits = 0
    if (
        not (named or trusted or same_case)
        and pattern
        and asset.get("modality") in {"text", "structured"}
        and asset.get("content") is not None
    ):
        body_hits = _topic_score(pattern, text_of(asset))
    relevant = named or trusted or same_case or title_hits > 0 or body_hits >= minimum_body_score
    return (
        int(relevant),
        int(named),
        int(trusted),
        int(same_case),
        title_hits,
        body_hits,
        int(not is_supplementary(asset)),
    )


def _records(tx, project, ids):
    for start in range(0, len(ids), _BATCH_SIZE):
        yield from tx.project_records_by_id("data_assets", project, ids[start : start + _BATCH_SIZE]).values()


def select_project_knowledge(tx, body, inputs, modalities, task_case_id=None, *, reference_batch=None):
    """Return extra authorized sources and counts for the same transaction as task acceptance."""
    from .service import fail

    project = body["project_id"]
    goal = _normal(body.get("goal", ""))
    query = _normal(" ".join((body.get("name", ""), goal)))
    topic = _topic_pattern(goal)
    case = body.get("case_id")
    case_codes = {_normal(case)} if case else set(_CASE.findall(query))
    fingerprints = set()
    if case and body.get("case_scope") == "reference" and reference_batch:
        entry = reference_batch.get("entry")
        fingerprints = {(s["sha256"], str(s["source_version"])) for s in (entry or {}).get("sources", [])}
    pinned = {asset["source_id"] for asset in inputs}
    explicit = len({asset["id"] for asset in inputs})
    now = datetime.now(UTC)
    ids = [key for key, _ in tx.project_asset_revisions(project)]
    seen = {}
    conflicts = set()
    latest = {}
    eligible = 0
    generic = body["task_type"] not in {"case_analysis", "autonomous_analysis"}

    def permitted(asset, at):
        return (
            _eligible(asset, project, at)
            and asset.get("classification") == "public"
            and asset.get("modality") in modalities
            and (not asset.get("task_case_id") or asset["task_case_id"] == task_case_id)
            and not (generic and asset["media_type"] in CASE_DOCUMENT_MEDIA_TYPES)
        )

    for asset in _records(tx, project, ids):
        if not permitted(asset, now):
            continue
        eligible += 1
        source = asset["source_id"]
        if source in pinned:
            continue
        version = str(asset["source_version"])
        key = (source, version)
        if seen.setdefault(key, asset["checksum"]) != asset["checksum"]:
            conflicts.add(key)
        order = (asset.get("received_at") or asset["created_at"], asset["id"])
        if source in latest and latest[source].order >= order:
            continue
        latest[source] = _Candidate(
            asset["id"],
            source,
            version,
            asset["checksum"],
            order,
            _rank(asset, query, topic, case_codes, fingerprints, bool(case)),
        )

    candidates = [
        item
        for item in latest.values()
        if item.rank is not None and (item.source_id, item.version) not in conflicts
    ]
    matches = [item for item in candidates if item.rank[0]]
    if not case_codes and eligible <= MAX_TASK_INPUTS:
        # An opted-in small project was historically included in full. A query
        # such as “explain the main findings” need not contain any source terms.
        matches = candidates
    matched = explicit + len(matches)
    ranked = sorted(matches, key=lambda item: (item.rank, item.order), reverse=True)
    selected = ranked[: max(0, MAX_TASK_INPUTS - explicit)]
    rows = {asset["id"]: asset for asset in _records(tx, project, [item.id for item in selected])}
    extras = []
    for item in selected:
        asset = rows.get(item.id)
        if (
            not asset
            or asset["source_id"] != item.source_id
            or str(asset["source_version"]) != item.version
            or asset["checksum"] != item.checksum
            or not permitted(asset, datetime.now(UTC))
        ):
            fail("input_changed", "知识检索期间资料发生变化，请重新提交", 409)
        extras.append(asset)
    return extras, {"eligible": eligible, "matched": matched, "selected": explicit + len(extras)}
