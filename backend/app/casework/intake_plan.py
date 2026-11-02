"""Locally match a question to a supported contract and a minimal authorized input set."""

import csv
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import UTC, datetime
from itertools import islice
from pathlib import PurePath

from openpyxl.utils.exceptions import InvalidFileException
from pypdf.errors import PdfReadError

from . import intake_sources, reference_batches
from .intake_language import INTENTS
from .team import catalog

MAX_PREVIEW = 6144
MAX_FILE = 2_000_000
MAX_SCAN_BYTES = 12_000_000
_SAMPLE = re.compile(
    r"缩小样本|合成样本|样本验证|小样试算|小样验证|synthetic sample|sample validation", re.IGNORECASE
)
_IDENT = re.compile(r"[a-z][a-z0-9_]{2,}")
_GENERIC = {
    "records",
    "kind",
    "date",
    "id",
    "name",
    "value",
    "title",
    "unit",
    "scope",
    "role",
    "geometry",
    "crs",
    "policy",
    "project",
    "published_date",
}
_FILLERS = re.compile(
    r"请问|请根据|根据|请帮我|帮我|什么|如何|哪些|能否|为什么|这些|这个|项目中|资料中|资料|现有|分析|一下|给我|一份|问题|请|说明|总结|概括|提供|里面|关于|是否"
)
_ENGLISH_STOP = {
    "what",
    "how",
    "which",
    "where",
    "when",
    "why",
    "tell",
    "please",
    "could",
    "would",
    "should",
    "the",
    "these",
    "this",
    "from",
    "with",
    "have",
    "were",
    "are",
    "was",
    "their",
    "about",
    "provided",
    "records",
    "materials",
    "document",
    "project",
    "data",
    "explain",
    "compare",
    "give",
    "into",
    "does",
    "summarize",
    "summary",
    "selected",
}
_SHARED_INPUT = ({"B04", "T07"}, {"B06", "T06"})


def _normal(text):
    return unicodedata.normalize("NFKC", str(text)).casefold()


def _phrases(text, group):
    return [word for word in group.split("|") if word in text]


def _intent(code, text, profile):
    subject, action, context = (_phrases(text, group) for group in INTENTS[code])
    return 4 * bool(subject) + 2 * bool(action) + bool(context) + 4 * (profile["title"] in text)


def _eligible(asset, project, now):
    if asset.get("project_id") != project or asset.get("status") != "active" or not asset.get("id"):
        return False
    try:
        expires = datetime.fromisoformat(str(asset["expires_at"]))
        return expires.tzinfo is not None and expires > now
    except (KeyError, ValueError, TypeError):
        return False


def _terms(question):
    words = {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", question) if w not in _ENGLISH_STOP}
    for run in re.findall(r"[\u3400-\u9fff]{2,}", _FILLERS.sub(" ", question)):
        for n in (2, 3, 4):
            words.update(run[i : i + n] for i in range(len(run) - n + 1))
    return words


def _sample_value(value, limit=MAX_PREVIEW):
    """Read a bounded prefix of field names and scalar values, including separate GeoJSON properties."""
    pieces, fields = [], set()
    size = 0

    def visit(obj, depth):
        nonlocal size
        if depth > 4 or size >= limit:
            return
        if isinstance(obj, dict):
            for key, val in islice(obj.items(), 48):
                key = str(key)[:80]
                fields.add(_normal(key))
                pieces.append(key)
                size += len(key)
                if key not in {"geometry", "coordinates", "values", "valid_mask"}:
                    visit(val, depth + 1)
                if size >= limit:
                    break
        elif isinstance(obj, list):
            for row in obj[:12]:
                visit(row, depth + 1)
        elif isinstance(obj, (str, float, int)):
            part = str(obj)[: min(limit - size, 512)]
            pieces.append(part)
            size += len(part)

    visit(value, 0)
    return " ".join(pieces)[:limit], fields


def _preview_asset(asset, reader, remaining):
    content = asset.get("content")
    media = str(asset.get("media_type", "")).split(";")[0].lower()
    if content is None and asset.get("content_ref"):
        size = asset.get("bytes", MAX_FILE + 1)
        if size > min(MAX_FILE, remaining[0]) or not (
            media.startswith("text/")
            or media
            in {
                "application/json",
                "application/geo+json",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ):
            return "", set()
        remaining[0] -= size
        try:
            raw = reader(asset)
            if media == "application/pdf":
                from pypdf import PdfReader

                pdf = PdfReader(io.BytesIO(raw))
                if pdf.is_encrypted or not len(pdf.pages):
                    return "", set()
                # Stay within a bounded uncompressed page. Skip compressed image-heavy content here.
                page = pdf.pages[0]
                stream = page.get("/Contents")
                if stream is None:
                    return "", set()
                streams = stream.get_object()
                streams = streams if isinstance(streams, list) else [streams]
                if any(item.get_object().get("/Filter") for item in streams):
                    return "", set()
                return (page.extract_text() or "")[:MAX_PREVIEW], set()
            if media.endswith("spreadsheetml.sheet"):
                from openpyxl import load_workbook

                from .records import _zip_guard

                _zip_guard(raw)
                wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
                try:
                    sheet = wb.active
                    rows = list(sheet.iter_rows(max_row=12, max_col=24, values_only=True))
                    return _sample_value(
                        [dict.fromkeys([str(v) for v in row if v is not None], "") for row in rows]
                    )
                finally:
                    wb.close()
            text = raw[: 2 * MAX_PREVIEW].decode("utf-8-sig", errors="replace")
            if media in {"application/json", "application/geo+json"}:
                content = json.loads(raw)
            else:
                content = text
        except (
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
            KeyError,
            IndexError,
            zipfile.BadZipFile,
            InvalidFileException,
            PdfReadError,
        ):
            # Damaged, unavailable or unsupported previews contribute no content signal.
            return "", set()
    if isinstance(content, str):
        text = content[:MAX_PREVIEW]
        if media == "text/csv":
            try:
                keys = next(csv.reader(io.StringIO(text)), [])
            except csv.Error:
                keys = []
            return text, {_normal(x.strip()) for x in keys}
        return text, set()
    return _sample_value(content)


def _source_view(asset, reader, remaining):
    text, fields = _preview_asset(asset, reader, remaining)
    name = str(asset.get("name", ""))[:240]
    meta = _normal(
        " ".join([name, str(asset.get("source_id", ""))[:160], str(asset.get("provenance", ""))[:420]])
    )
    content = asset.get("content")
    rows = content.get("records", []) if isinstance(content, dict) else content
    targets = set()
    if isinstance(rows, list):
        for row in rows[:20]:
            if (
                isinstance(row, dict)
                and row.get("kind") in {"document_binding", "field_dictionary"}
                and isinstance(row.get("target_source_id"), str)
            ):
                targets.add(row["target_source_id"])
    return {
        "asset": asset,
        "name": _normal(name),
        "meta": meta,
        "text": _normal(text),
        "original": text,
        "fields": fields,
        "targets": targets,
    }


def _field_strength(code, view, contracts, frequencies):
    fields = view["fields"] & contracts[code]
    distinctive = sorted(k for k in fields if k not in _GENERIC and frequencies[k] <= 4)
    score = sum(3 if frequencies[k] == 1 else 2 for k in distinctive) + int(len(fields - _GENERIC) >= 2)
    return score, distinctive


def _relevance(code, view, contracts, frequencies):
    subject, action, _ = INTENTS[code]
    field_score, fields = _field_strength(code, view, contracts, frequencies)
    named = _phrases(view["meta"], subject)
    textual = _phrases(view["text"], subject)
    actions = _phrases(view["meta"] + " " + view["text"], action)
    score = field_score + 4 * bool(named) + 2 * bool(textual) + bool(actions)
    # Several matrices share column names but have different governed source roles.
    expected_roles = {
        "B08": ("judicial", "judicial finding"),
        "B10": ("brief", "incident brief"),
        "T08": ("audit", "project_owner"),
    }
    if any(role in view["text"].split() for role in expected_roles.get(code, ())):
        score += 5
    evidence = "、".join(fields[:3] or (named + textual + actions)[:3])
    return score, evidence


def _topic_matches(question, view):
    # Prefer longer matches over their Chinese subgrams and use the source's original spelling.
    terms = _terms(question)
    found = [t for t in terms if t in view["meta"] or t in view["text"]]
    kept = []
    for word in sorted(found, key=lambda s: (-len(s), s)):
        if not any(word in longer for longer in kept):
            kept.append(word)
        if len(kept) == 12:
            break
    return kept


def _explicit_name(question, view):
    stem = PurePath(view["name"]).stem
    return len(stem) >= 2 and stem in question


def _narrow_large_project(q, project_id, allowed, profiles):
    # No content is inspected here. Compute a full metadata match before applying the read cap.
    named = [a for a in allowed if _explicit_name(q, {"name": _normal(a.get("name", ""))})]
    if named:
        return named
    intents = sorted(((_intent(code, q, p), code) for code, p in profiles.items()), reverse=True)
    if intents[0][0] < 4 or intents[0][0] - intents[1][0] <= 1:
        return []
    code = intents[0][1]
    ref = reference_batches.freeze(project_id, code)
    registered = {(s["sha256"], s["source_version"]) for s in (ref.get("entry") or {}).get("sources", [])}
    matches = [a for a in allowed if (a.get("checksum"), a.get("source_version")) in registered]
    if registered and {(a.get("checksum"), a.get("source_version")) for a in matches} == registered:
        return matches
    matches = []
    for a in allowed:
        meta = _normal(" ".join(str(a.get(k, ""))[:420] for k in ("name", "source_id", "provenance")))
        if _phrases(meta, INTENTS[code][0]) and any(_phrases(meta, g) for g in INTENTS[code][1:]):
            matches.append(a)
    return matches


def _rows(view):
    content = view["asset"].get("content")
    rows = content.get("records", []) if isinstance(content, dict) else content
    return [r for r in rows[:200] if isinstance(r, dict)] if isinstance(rows, list) else []


def _policies(view):
    return [r for r in _rows(view) if r.get("kind") == "policy"]


def case_scope_conflict(code, question, policies):
    """Any explicit condition must match what the real handler will actually compute."""
    question = _normal(question)
    if code == "G09":
        if len({str(p["snapshot"]) for p in policies if "snapshot" in p}) > 1:
            return "已选资料包含不同的通行快照，无法确定用于本次绕行计算的唯一时点。"
        dates = re.findall(r"(?<!\d)(20\d\d-\d\d-\d\d)(?:[ t](\d\d:\d\d))?", question)
        dates += [
            (f"{year}-{int(month):02}-{int(day):02}", hm)
            for year, month, day, hm in re.findall(
                r"(20\d\d)年(\d{1,2})月(\d{1,2})日(?:\s*(\d\d:\d\d))?", question
            )
        ]
        if dates:
            snapshots = [str(p["snapshot"]).lower() for p in policies if "snapshot" in p]
            if not snapshots or any(
                not all(day == s[:10] and (not hm or hm == s[11:16]) for day, hm in dates) for s in snapshots
            ):
                return "所问日期或时刻与现有路网的冻结通行快照不一致。"
    if code != "G03":
        return None
    if re.search(
        r"为什么|成因|原因|预测|未来|相关性|过去[一二三四五六七八九十\d]+年|forecast|predict|why|causal|correlation|last decade",
        question,
    ):
        return "现有海冰流程可复算指定窗口的范围、距平和排名，无法据此认定成因、其他区间统计或未来变化。"
    if re.search(
        r"南极|南半球|antarctic|southern hemisphere|海冰浓度|ice concentration|实际冰面积|sea ice area|厚度|颜色|体积|thickness|volume",
        question,
    ):
        return "现有海冰流程计算北半球九月的月均范围，所问地区或指标不符合该计算口径。"
    months = re.findall(r"(?<!\d)(1[0-2]|0?[1-9])\s*月|([一二三四五六七八九十]{1,3})月", question)
    if any((n and int(n) != 9) or (zh and zh != "九") for n, zh in months) or re.search(
        r"\b(january|february|march|april|may|june|july|august|october|november|december)\b", question
    ):
        return "现有海冰流程仅计算九月，所问月份不符。"
    ranges = re.findall(r"((?:19|20)\d\d)\s*年?\s*(?:[-–—~至到]|through|to)\s*((?:19|20)\d\d)", question)
    years = set(re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", question))
    for a, b in ranges:
        years.discard(a)
        years.discard(b)
    for word, offset in (("今年", 0), ("去年", -1), ("前年", -2), ("this year", 0), ("last year", -1)):
        if word in question:
            years.add(str(datetime.now(UTC).year + offset))
    policies = policies or [{}]
    if (
        len({(str(p.get("target_year", 2023)), str(p.get("baseline_years", [1981, 2010]))) for p in policies})
        > 1
    ):
        return "现有资料给出了不同的海冰目标年或基线，无法确定本次计算所用的统一口径。"
    for policy in policies:
        target = str(policy.get("target_year", 2023))
        interval = policy.get("baseline_years", [1981, 2010])
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            return "资料中的海冰基线未提供可执行的两个端点年份。"
        baseline = list(map(str, interval))
        window = [str(policy.get("start_year", 1979)), target]
        if years and years != {target}:
            return f"所问年份与当前海冰流程的目标年 {target} 不符，无法按要求交付。"
        for span in ranges:
            baseline_request = re.search(
                rf"{span[0]}\s*年?\s*(?:[-–—~至到]|through|to)\s*{span[1]}\s*年?\s*(?:的|为|作为|作为参考)?\s*(?:平均|基线|基准|常年|baseline|normal)",
                question,
            )
            if list(span) not in (baseline, window) or (baseline_request and list(span) != baseline):
                return f"所问年份区间与当前基线 {'–'.join(baseline)} 及计算窗口 {'–'.join(window)} 不符。"
    return None


def _semantic(question, views, result):
    intent = re.search(
        r"总结|概括|梳理|比较|核对|解释|说明|依据|为什么|是什么|多少|几点|有哪些|写了什么|怎么|何时|先后|区别|变化|如何|[?？]|what|why|which|when|summari[sz]e|explain|compare",
        question,
    )
    matches = []
    for view in views:
        words = _topic_matches(question, view)
        named = _explicit_name(question, view)
        if intent and (named or sum(min(4, len(w)) for w in words) >= 4):
            matches.append((view, words, named))
    if any(m[2] for m in matches) and re.search(r"只依据|仅根据|仅使用|only us(e|ing)", question):
        matches = [m for m in matches if m[2]]
    if not matches:
        return result("unsupported", "现有任务契约和有效资料中未找到与问题明确相符的依据。")
    if re.search(
        r"识别图中|图中有谁|听录音|听这段|转写音频|转录视频|视频内容|听出|transcribe|identify objects",
        question,
    ):
        return result("missing_inputs", "所问操作需要媒体内容处理，当前公开文本取证无法核实。")
    if len(matches) > 12 or any(
        v["asset"].get("classification") != "public"
        or v["asset"].get("modality") not in {"text", "structured"}
        or v["asset"].get("content") is None
        or not v["text"]
        for v, _, _ in matches
    ):
        return result("missing_inputs", "开放问题所需资料不满足公开文本取证条件，无法完成完整取证。")
    focus = "claim_check"
    for pattern, mode in [
        (r"术语|缩写|缩略|全称|是什么意思|acronym|abbreviation|stands? for", "scientific_terms"),
        (r"时间线|何时|先后|timeline|chronology", "timeline"),
        (r"区别|差异|对比|比较|compare|difference", "comparison"),
    ]:
        if re.search(pattern, question):
            focus = mode
            break
    topics = []
    for view, words, _ in matches:
        for word in words:
            match = re.search(re.escape(word), view["original"], re.IGNORECASE)
            if match and match[0] not in topics:
                topics.append(match[0])
    items = [{"id": v["asset"]["id"], "reason": "问题主题与本项目的公开资料相符。"} for v, _, _ in matches]
    if not topics and not any(named for _, _, named in matches):
        return result("unsupported", "公开原文中没有可核实的问题主题。")
    return {
        **result("semantic", "使用项目公开资料完成有来源的语义核查。", items=items),
        "focus": focus,
        "topics": topics[:6],
    }


def plan_question(question, project_id, assets, *, read_bytes=None):
    """Plan only. The API authorizes project access and freezes/rechecks all inputs at actual task creation."""
    from ..platform.runtime import _asset_bytes

    q = _normal(question).strip()[:8000]
    scope = "project"
    if _SAMPLE.search(q):
        scope = "sample"
    elif re.search(r"指定参考批次|完整参考批次|原件验收|基准验收|benchmark|reference batch", q):
        scope = "reference"
    profiles = catalog()

    def result(status, reason, code=None, items=(), options=()):
        return {
            "status": status,
            "reason": reason,
            "case_id": code,
            "case_scope": scope,
            "gaps": [] if status in {"ready", "semantic"} else [reason],
            "input_refs": [item["id"] for item in items],
            "selections": list(items),
            "candidates": [{"case_id": c, "title": profiles[c]["title"]} for c in options[:3]],
        }

    now = datetime.now(UTC)
    allowed = [a for a in assets if _eligible(a, project_id, now)]
    candidates = _narrow_large_project(q, project_id, allowed, profiles) if len(allowed) > 500 else allowed
    if len(allowed) > 500 and (len(candidates) > 500 or not candidates):
        return result("ambiguous", "项目有效资料超过本地受限选材范围，需要更具体的资料范围。")
    remaining = [MAX_SCAN_BYTES]
    views = [_source_view(a, read_bytes or _asset_bytes, remaining) for a in candidates]
    contracts = {
        code: set(p.get("record_fields") or _IDENT.findall(_normal(" ".join(p["inputs"]))))
        for code, p in profiles.items()
    }
    frequencies = Counter(k for fields in contracts.values() for k in fields)
    scores = {}
    relevance = {}
    for code, profile in profiles.items():
        score = _intent(code, q, profile)
        if score < 2:
            continue
        by_asset = {v["asset"]["id"]: _relevance(code, v, contracts, frequencies) for v in views}
        relevance[code] = by_asset
        support = max((n for n, _ in by_asset.values()), default=0)
        scores[code] = score + min(2, support // 2)
    ranked = sorted(scores, key=lambda c: (-scores[c], c))
    if not ranked or scores[ranked[0]] < 4:
        return _semantic(q, views, result)
    code = ranked[0]
    if re.search(r"是什么意思|英文全称|缩写代表|stands? for|what does .{1,30} mean", q) and not _phrases(
        q, INTENTS[code][1]
    ):
        return _semantic(q, views, result)
    if len(ranked) > 1 and scores[ranked[1]] >= 4 and scores[code] - scores[ranked[1]] <= 1:
        return result(
            "ambiguous", "问题同时符合多个不同的交付口径，当前资料不足以确定唯一执行路径。", options=ranked
        )
    explicit = {v["asset"]["id"] for v in views if _explicit_name(q, v)}
    selected = {}
    registered = {}
    if scope != "sample":
        reference = reference_batches.freeze(project_id, code)
        for origin in (reference.get("entry") or {}).get("sources", []):
            registered[(origin["sha256"], origin["source_version"])] = origin
    for v in views:
        a = v["asset"]
        key = a.get("checksum"), a.get("source_version")
        if not v["targets"] and key in registered and (not explicit or a["id"] in explicit):
            selected.setdefault(
                key, {"id": a["id"], "reason": "来源版本和内容摘要与该任务的已登记参考清单一致。"}
            )
    chosen = list(selected.values())
    if chosen and set(selected) == set(registered):
        scope = "reference"
    elif scope == "project":
        chosen = []
    if not chosen:
        related = []
        for v in views:
            key = v["asset"]["id"]
            strength, evidence = relevance[code][key]
            if v["targets"] or strength < 3 or (explicit and key not in explicit):
                continue
            excluded = next((s for s in _SHARED_INPUT if code in s), {code})
            competing = max(
                (
                    _relevance(other, v, contracts, frequencies)[0]
                    for other in contracts
                    if other not in excluded
                ),
                default=0,
            )
            if strength + 1 < competing and not _phrases(v["meta"], INTENTS[code][0]):
                continue
            related.append((v, strength, _topic_matches(q, v), evidence))
        if code in {"G03", "G09"}:
            compatible = [
                row
                for row in related
                if not _policies(row[0]) or not case_scope_conflict(code, q, _policies(row[0]))
            ]
            if related and not compatible:
                message = case_scope_conflict(code, q, _policies(related[0][0]))
                return result("missing_inputs", message, code, options=[code])
            related = compatible
        pool = [v for v in views if not explicit or v["asset"]["id"] in explicit]
        related, ambiguity = intake_sources.resolve_candidates(code, q, pool, related)
        if ambiguity:
            return result("ambiguous", ambiguity, code, options=[code])
        related = related or []
        if any(code in pair for pair in _SHARED_INPUT) and len(related) > 1:
            complete = []
            for v, _, _, _ in related:
                rows = _rows(v)
                sides = {r.get("side") for r in rows}
                kinds = {r.get("kind") for r in rows}
                if {"before", "after"} <= sides or {"timed_asr", "transcript"} <= kinds:
                    complete.append(v["asset"]["id"])
            if len(complete) > 1:
                specific = [row for row in related if _phrases(row[0]["meta"], INTENTS[code][0])]
                if len(specific) != 1:
                    return result(
                        "ambiguous",
                        "多个独立资料集都已包含完整对照，问题和资料名称未能区分目标批次。",
                        code,
                        options=[code],
                    )
                related = [row for row in related if row[0]["asset"]["id"] not in complete or row in specific]
        # For single-paper readings each independent paper is a different task. A title
        # or distinctive scientific topic must identify the intended source.
        if code == "T01" and len(related) > 1:
            common = Counter(t for _, _, words, _ in related for t in words)
            distinguish = [sum(len(t) for t in words if common[t] == 1) for _, _, words, _ in related]
            best = max(distinguish)
            if best < 3 or distinguish.count(best) != 1:
                return result(
                    "ambiguous", "多个研究来源都符合阅读要求，问题未能区分目标研究。", code, options=[code]
                )
            related = [related[distinguish.index(best)]]
        chosen = [
            {"id": v["asset"]["id"], "reason": "与任务匹配的来源特征：" + evidence}
            for v, _, _, evidence in related
        ]
    ids = {item["id"] for item in chosen}
    candidate_ids = {v["asset"]["id"] for v in views}
    # Only source IDs explicitly referenced by accepted rows may add media from outside the metadata shortlist.
    link_views = [*views, *({"asset": a} for a in allowed if a["id"] not in candidate_ids)]
    linked, link_error = intake_sources.linked_sources(code, link_views, ids)
    if link_error:
        return result("missing_inputs", link_error, code, options=[code])
    chosen.extend(linked)
    ids.update(item["id"] for item in linked)
    for v in views:
        if v["targets"] and v["targets"] <= ids:
            chosen.append(
                {"id": v["asset"]["id"], "reason": "此资料直接绑定了已选来源的版本角色或字段映射。"}
            )
    if not chosen or len(chosen) > 50 or explicit - {item["id"] for item in chosen}:
        return result(
            "missing_inputs",
            "已识别分析目标，但有效资料中未能确定符合该任务契约的输入范围。",
            code,
            options=[code],
        )
    conflict = case_scope_conflict(
        code, q, [p for v in views if v["asset"]["id"] in ids for p in _policies(v)]
    )
    if conflict:
        return result("missing_inputs", conflict, code, options=[code])
    return result("ready", "问题与已安装的分析契约相符，已按来源特征选择项目资料。", code, chosen, [code])
