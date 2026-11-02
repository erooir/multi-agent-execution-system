"""Version and issue reconciliation. Absence, disagreement and scope changes stay distinct."""

import difflib
import re
from collections import defaultdict
from itertools import pairwise

from .document_common import norm, string, tokens, units, value


def clauses(run):
    sides = {"before": [], "after": []}
    comments = []
    mapping = []
    unmatched = []
    bindings = {
        string(r, "target_source_id"): r
        for r in run.records
        if string(r, "kind") == "document_binding" and string(r, "target_source_id")
    }
    glossary = [
        r
        for r in run.records
        if string(r, "kind") == "glossary" and string(r, "term") and string(r, "meaning")
    ]
    themes = {
        "停火": r"ceasefire|cessation of hostilities|прекращен\w*\s+огня|停火",
        "监督": r"monitoring|verification|мониторинг|верификаци|监督|核查",
        "人道": r"humanitarian|гуманитар|人道",
        "政治程序": r"election|выбор|самоуправлен|政治程序|选举",
    }
    for r in run.records:
        role = norm(string(r, "kind", "role"))
        if role in {"document_binding", "glossary"}:
            continue
        bind = bindings.get(r["source_id"], {"fields": {}})
        side = norm(string(r, "side", "edition", "版本角色") or string(bind, "side", "edition"))
        if role in {"comment", "response", "评论", "意见回应"}:
            comments.append(run.excerpt(r))
            continue
        if side in {"old", "draft", "旧版", "草案"}:
            side = "before"
        if side in {"new", "final", "新版", "最终"}:
            side = "after"
        if side not in sides:
            run.gap("条款须提供before/after版本对应；文件排列或发布日期不能代替版本标注")
            continue
        body = string(r, "clause_text", "条文") or r.get("text", "")
        if not body.strip():
            run.gap("条款缺少OCR/原文")
            continue
        # Anonymous PDF/OCR pages can be bound to before/after via small, upstream-authored document metadata.
        if not string(r, "clause_id", "条款号") and not string(r, "clause_text", "条文"):
            numbered = list(re.finditer(r"(?m)^\s*(\d{1,3})[.)]\s+(?=\S)", body))
            for i, m in enumerate(numbered):
                quote = body[
                    m.start() : numbered[i + 1].start() if i + 1 < len(numbered) else len(body)
                ].strip()
                topics = [
                    name for name, pattern in themes.items() if re.search(pattern, quote, re.IGNORECASE)
                ]
                for topic in topics:
                    words = [
                        {
                            "term": string(g, "term"),
                            "meaning": string(g, "meaning"),
                            "evidence_ids": [run.cite(g)],
                        }
                        for g in glossary
                        if norm(string(g, "term")) in norm(quote)
                    ]
                    sides[side].append(
                        {
                            "id": m[1],
                            "topic": topic,
                            "text": quote[:3000],
                            "match_id": "",
                            "translation": None,
                            "glossary": words,
                            "uncertainty": "OCR原文须回查原页；词表释义不等于全文翻译",
                            "publication": value(bind, "published_date"),
                            "effective": None,
                            "evidence_ids": [run.cite(r, quote)],
                        }
                    )
            if numbered and not glossary and re.search(r"[А-я]", body):
                run.gap("俄文条款已提取；缺术语对照或核验翻译，未生成中文全文释义")
            continue
        sides[side].append(
            {
                "id": string(r, "clause_id", "条款号"),
                "topic": string(r, "topic", "主题"),
                "text": body,
                "match_id": string(r, "match_id", "对应旧条款"),
                "translation": value(r, "translation_zh", "中文释义"),
                "uncertainty": value(r, "ocr_uncertainty"),
                "publication": value(r, "published_date"),
                "effective": value(r, "effective_date"),
                "evidence_ids": [run.cite(r)],
            }
        )
    for name, entries in sides.items():
        if not entries:
            run.gap(f"缺少{name}版本可定位条文")
    used = set()
    for old in sides["before"]:
        # An explicitly supplied correspondence can be one-to-many. Never pair solely by row order.
        matched = [(i, n) for i, n in enumerate(sides["after"]) if old["id"] and n["match_id"] == old["id"]]
        method = "explicit"
        if not matched and old["id"]:
            matched = [
                (i, n)
                for i, n in enumerate(sides["after"])
                if not n["match_id"]
                and old["id"] == n["id"]
                and (not old["topic"] or not n["topic"] or norm(old["topic"]) == norm(n["topic"]))
            ]
            method = "clause_id"
        if (
            not matched
            and old["topic"]
            and (
                run.retry
                or (
                    run.repair_action == "match_unique_clauses"
                    and (not run.repair_targets or norm(old["topic"]) in run.repair_targets)
                )
            )
        ):
            candidates = [
                (i, n)
                for i, n in enumerate(sides["after"])
                if i not in used and not n["match_id"] and norm(old["topic"]) == norm(n["topic"])
            ]
            same_old = sum(norm(o["topic"]) == norm(old["topic"]) for o in sides["before"])
            if len(candidates) == same_old == 1:
                matched = candidates
                method = "unique_topic_suggestion"
        if not matched:
            unmatched.append({"side": "before", "item": old, "reason": "没有可核对应条款，不据此断言删除"})
            continue
        for i, new in matched:
            used.add(i)
            a, b = norm(old["text"]), norm(new["text"])
            changes = [{"op": op, "old": aa, "new": bb} for op, aa, bb in _diff(old["text"], new["text"])]
            kind = "identical" if a == b else "expanded_wording" if a and a in b else "changed_wording"
            mapping.append({"before": old, "after": new, "change": kind, "method": method, "diff": changes})
    for i, n in enumerate(sides["after"]):
        if i not in used:
            unmatched.append({"side": "after", "item": n, "reason": "未找到旧条款对应项，可能新增"})
    if not mapping and sides["before"] and sides["after"]:
        run.gap("没有可核对的版本映射；需条款编号、对应关系或唯一主题")
    return {"clause_map": mapping, "unmatched": unmatched, "commentary": comments}


def _diff(a, b):
    # Preserve punctuation and non-Latin scripts; word splitting alone loses Chinese clause changes.
    tokenize = lambda s: re.findall(r"[\u4e00-\u9fff]|[^\W_]+|[^\w\s]", s, re.UNICODE)
    left, right = tokenize(a[:12000]), tokenize(b[:12000])
    sm = difflib.SequenceMatcher(None, left, right)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op != "equal":
            yield op, " ".join(left[a0:a1]), " ".join(right[b0:b1])


def chronology(run):
    timeline = []
    unknown = []
    assertions = defaultdict(list)
    for r in run.records:
        f = r.get("fields", {})
        text = string(r, "event", "claim", "statement") or r.get("text", "")[:450]
        if not text:
            continue
        when = string(r, "event_date", "event_time")
        published = string(r, "published_date", "released_at")
        formed = string(r, "created_date", "formed_at")
        declassified = string(r, "declassified_at")
        row = {
            "event": text,
            "event_time": when or None,
            "formed_at": formed or None,
            "published_at": published or None,
            "declassified_at": declassified or None,
            "timezone": f.get("timezone"),
            "time_precision": f.get("time_precision")
            or ("day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else "unspecified"),
            "channel": value(r, "channel", "role"),
            "recipient": value(r, "recipient"),
            "evidence_ids": [run.cite(r)],
        }
        if not any((when, published, formed, declassified)):
            unknown.append(row)
        else:
            timeline.append(row)
        key = string(r, "event_id", "topic")
        if key:
            assertions[key].append(row)
    conflicts = [
        {"event_id": k, "variants": rs, "reason": "同一事件的资料标注了不同发生时间"}
        for k, rs in assertions.items()
        if len({r["event_time"] for r in rs if r["event_time"]}) > 1
    ]
    if unknown:
        run.gap("存在未给明确信息时间的材料，未用文件发布时间补写事件时间")
    if not timeline:
        run.gap("没有可定位到明确时间语义的档案记录")
    timeline.sort(
        key=lambda x: (
            x["event_time"] or x["formed_at"] or x["published_at"] or x["declassified_at"],
            x["evidence_ids"][0],
        )
    )
    return {"timeline": timeline, "unknown_time": unknown, "variants": conflicts}


def evidence_matrix(run, expected_roles):
    questions = {}
    groups = defaultdict(list)
    limits = []
    for r in run.records:
        key = string(r, "question_id", "topic", "subject", "问题")
        role = string(r, "role", "source_role", "来源角色")
        kind = string(r, "kind")
        if kind == "question":
            q = string(r, "question", "query") or r.get("text", "")
            questions[key or q] = q
            continue
        if not key:
            continue
        groups[key].append((r, role))
    matrix = []
    all_roles = set()
    for key in dict.fromkeys([*questions, *groups]):
        matches = groups[key][:]
        if not matches and questions.get(key):
            # Retrieve source-supported candidate passages; never treat a question as an assertion.
            qtokens = tokens(questions[key])
            rank = []
            for r in run.records:
                if string(r, "kind") == "question":
                    continue
                for s in units(r.get("text", "")):
                    overlap = len(qtokens & tokens(s))
                    if overlap >= (1 if run.retry else 2):
                        rank.append((overlap, s, r))
            rank.sort(key=lambda e: -e[0])
            matches = [(r, string(r, "role", "source_role"), s) for _, s, r in rank[:6]]
        claims = []
        for match in matches:
            r, role = match[:2]
            s = match[2] if len(match) > 2 else None
            text = string(r, "statement", "claim", "conclusion") or s or r.get("text", "")
            if not text:
                continue
            all_roles.add(role)
            claims.append(
                {
                    "statement": text[:600],
                    "role": role or "unknown",
                    "published_at": value(r, "published_date", "date"),
                    "event_at": value(r, "event_date"),
                    "basis": value(r, "basis", "evidence_type", "判断层次") or "来源表述，未单独鉴定",
                    "scope": value(r, "scope"),
                    "comparable_value": value(r, "value", "polarity"),
                    "qualifier": value(r, "qualifier"),
                    "evidence_ids": [run.cite(r, s)],
                }
            )
        status = (
            "missing"
            if not claims
            else "single_source"
            if len({c["role"] for c in claims}) < 2
            else "parallel_accounts"
        )
        if claims and len(claims) > 1:
            if len({norm(c["statement"]) for c in claims}) == 1:
                status = "same_statement"
            elif len({c["scope"] for c in claims if c["scope"]}) > 1:
                status = "scope_differs"
            elif (
                all(c["scope"] and c["comparable_value"] is not None for c in claims)
                and len({str(c["comparable_value"]) for c in claims}) > 1
            ):
                status = "conflicting_values"
        if status in {"missing", "single_source"}:
            limits.append({"question": key, "status": status})
        matrix.append(
            {"question_id": key, "question": questions.get(key, key), "status": status, "claims": claims}
        )
    if not matrix:
        run.gap("没有问题清单或可按问题定位的来源主张")
    for role in expected_roles:
        if role not in all_roles:
            run.gap(f"缺少明确来源角色 {role} 的证据")
    timeline = sorted(
        [{"question_id": m["question_id"], **c} for m in matrix for c in m["claims"] if c["published_at"]],
        key=lambda x: str(x["published_at"]),
    )
    return {"matrix": matrix, "version_timeline": timeline, "unverified": limits}


def milestones(run):
    entries = defaultdict(list)
    unknown = []
    for r in run.records:
        project = string(r, "project", "project_id")
        typ = string(r, "event_type", "里程碑类型")
        when = string(r, "event_date", "发生日")
        published = string(r, "published_date", "发布日期")
        if not all([project, typ, when]):
            unknown.append(
                {"reason": "缺项目、事件类型或发生日；不按新闻标题去重", "evidence_ids": [run.cite(r)]}
            )
            continue
        entries[(norm(project), norm(typ), when)].append((r, published))
    rows = []
    for (project, typ, when), members in sorted(entries.items(), key=lambda x: x[0][2]):
        history = [r for r, _ in members if string(r, "role", "kind") in {"baseline", "historical", "基线"}]
        new = [r for r, _ in members if r not in history]
        labels = {string(r, "outcome", "status") for r, _ in members if string(r, "outcome", "status")}
        status = (
            "baseline"
            if not new
            else "new_event"
            if not history
            else "updated_account"
            if len(labels) > 1
            else "repeat_report"
        )
        rows.append(
            {
                "project": project,
                "event_type": typ,
                "event_date": when,
                "status": status,
                "report_dates": [p for _, p in members],
                "evidence_ids": [run.cite(r) for r, _ in members],
                "observations": sorted(labels),
            }
        )
    if not rows:
        run.gap("没有具备发生日和事件类型的里程碑")
    if not any(string(r, "kind", "role") in {"historical", "baseline", "基线"} for r in run.records):
        run.gap("缺历史基线，新增仅相对当前输入")
    if unknown:
        run.gap("部分材料未给事件身份，保留待定，不强行归并")
    counts = {s: sum(x["status"] == s for x in rows) for s in sorted({x["status"] for x in rows})}
    return {
        "milestones": rows,
        "unmatched": unknown,
        "brief": {"status_counts": counts, "event_count": len(rows)},
    }


def roadmap(run):
    groups = defaultdict(list)
    history = []
    for r in run.records:
        project = string(r, "project", "project_id")
        feature = string(r, "feature", "topic", "技术主题")
        when = string(r, "date", "published_date")
        val = string(r, "value", "propulsion", "route", "statement") or r.get("text", "")
        if not project or not feature or not when or not val:
            continue
        row = {
            "project": project,
            "feature": feature,
            "date": when,
            "value": val[:500],
            "stage": string(r, "stage", "maturity") or "未说明",
            "evidence_ids": [run.cite(r)],
        }
        groups[(project, feature)].append(row)
        history.append(row)
    changes = []
    for (project, feature), members in groups.items():
        members.sort(key=lambda r: r["date"])
        for a, b in pairwise(members):
            changes.append(
                {
                    "project": project,
                    "feature": feature,
                    "before": a,
                    "after": b,
                    "change": "same" if norm(a["value"]) == norm(b["value"]) else "published_wording_changed",
                    "evidence_ids": a["evidence_ids"] + b["evidence_ids"],
                }
            )
    if not changes:
        run.gap("缺同一项目、同一主题的两个有日期路线快照")
    return {
        "route_history": sorted(history, key=lambda r: r["date"]),
        "route_changes": changes,
        "unverified_stages": [
            r for r in history if r["stage"] in {"concept", "plan", "概念", "计划", "未说明"}
        ],
    }
