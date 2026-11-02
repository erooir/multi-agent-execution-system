"""Business views of the audited historical/document products. Never infer a new event or identity."""

import re
from datetime import date


def table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows[:300], "total_rows": len(rows)}


def reply(lines, tables, gaps=()):
    return {
        "answer": "\n".join(lines),
        "tables": [t for t in tables if t["rows"]],
        "missing": list(dict.fromkeys(gaps)),
    }


def changes(p, _):
    rows, lines = [], []
    for r in p.get("observations", []):
        delta = r.get("change")
        detail = (
            ("与上期持平" if delta == 0 else f"{'增加' if delta > 0 else '减少'} {abs(delta)} 起")
            if isinstance(delta, (int, float))
            else "没有可比的上期数"
        )
        rows.append([r.get("region"), r.get("previous"), r.get("count"), delta, r.get("scope")])
        count = f"本期记录 {r['count']} 起" if r.get("count") is not None else "本期数未记录"
        lines.append(f"{r.get('region') or '该地区'}{count}，{detail}。")
    window = p.get("report_window", {})
    if end := (window.get("observed_until") or {}).get("value"):
        lines.append(f"观测截止于 {end}。")
    return reply(
        lines, [table("分地区观测", ["地区", "上期（起）", "本期（起）", "变化（起）", "观测范围"], rows)]
    )


def commitments(p, q):
    topics = {"cessation": "停火", "humanitarian": "人道援助", "services": "公共服务"}
    rows, lines, gaps = [], [], []
    docs = p.get("documents", [])
    for group in p.get("commitments", []):
        topic = topics.get(group.get("topic"), group.get("topic"))
        for r in group.get("items", []):
            statement = r.get("commitment") or r.get("text")
            if not statement:
                continue
            doc = next(
                (d for d in docs if set(d.get("evidence_ids", [])) & set(r.get("evidence_ids", []))), {}
            )
            parties = "、".join(doc.get("parties") or [])
            effective = r.get("effective_date")
            condition = r.get("condition") or r.get("conditions")
            if not isinstance(condition, str):
                piece = re.search(
                    r"(?:前提是|条件是|如果|只有在|provided that|subject to)[^。；;]{1,200}",
                    statement,
                    re.IGNORECASE,
                )
                condition = piece[0] if piece else None
            lines.append(
                f"{topic}方面，{parties + '提出' if parties else '资料记载'}“{statement}”。"
                + (f"生效日期为 {effective}。" if effective else "生效日期未说明。")
            )
            rows.append([topic, parties or None, statement, effective, condition, doc.get("name")])
            if not effective and re.search("生效|什么时候|何时", q):
                gaps.append(f"{topic}事项的生效时间没有明确记录。")
    if "条件" in q and any(not r[4] for r in rows):
        gaps.append("部分承诺没有写明执行条件，不能把发布日期当作生效条件。")
    return reply(
        lines, [table("声明事项", ["事项", "参与方", "原文", "生效日期", "执行条件", "文件"], rows)], gaps
    )


def photos(p, _):
    lines, rows, gaps = [], [], []
    for item in p.get("image_catalog", []):
        image = item.get("image_id") or "未编号图像"
        records = item.get("reference_records") or []
        names = "、".join(item.get("candidates") or [])
        lines.append(
            f"{image}可见“{item.get('visible_features') or '未记录特征'}”。"
            + (
                f"馆藏材料支持候选 {names}。"
                if item.get("status") == "supported_candidate"
                else "当前无法确认对应馆藏。"
            )
        )
        for rec in records or [{}]:
            rows.append(
                [image, item.get("visible_features"), names or None, rec.get("archival_name"), rec.get("url")]
            )
        if not records:
            gaps.append(f"{image}没有可核对的馆藏出处。")
    return reply(
        lines, [table("图像与馆藏出处", ["图像编号", "可见特征", "候选", "馆藏记录", "馆藏出处"], rows)], gaps
    )


def timecode(ms):
    if not isinstance(ms, (int, float)) or ms < 0:
        return None
    sec, milli = divmod(round(ms), 1000)
    return f"{sec // 3600:02}:{sec // 60 % 60:02}:{sec % 60:02}.{milli:03}"


def turns(p, _):
    lines, rows, gaps = [], [], []
    roles = {"question": "提问", "answer": "回答", "statement": "陈述", "followup": "追问"}
    for turn in p.get("timed_index", []):
        speaker = turn.get("speaker")
        role = roles.get(turn.get("turn_type"), "发言")
        start, end = timecode(turn.get("start_ms")), timecode(turn.get("end_ms"))
        when = f"{start}–{end}" if start and end else None
        said = turn.get("translation_zh") or turn.get("text")
        rows.append([turn.get("media_id"), when, speaker, role, turn.get("topic"), said, turn.get("text")])
        if len(lines) < 10:
            lines.append(
                f"{when or '时间未确定'}，{speaker or '发言人未确认'}{role}：“{said or '未记录发言内容'}”。"
            )
        if not speaker or not when or role == "发言":
            gaps.append("部分发言的身份、问答角色或起止时间未被材料明确标注。")
    if len(rows) > len(lines):
        lines.append(f"共核对 {len(rows)} 段发言，其余见回听索引。")
    return reply(
        lines,
        [
            table(
                "发言与回听索引",
                ["录像或录音", "起止时间", "发言人", "发言类型", "主题", "发言内容", "原文"],
                rows,
            )
        ],
        gaps,
    )


def monthly(p, _):
    lines, rows, gaps = [], [], []
    for r in p.get("monthly", []):
        full = r.get("complete_month")
        coverage = "整月" if full is True else "部分月份" if full is False else "覆盖未确认"
        rows.append(
            [
                r.get("month"),
                r.get("start_date"),
                r.get("end_date"),
                coverage,
                r.get("deaths"),
                r.get("injured"),
                r.get("total"),
                r.get("basis"),
            ]
        )
    for group in p.get("reconciliation", []):
        calc = group.get("computed")
        if isinstance(calc, list) and len(calc) == 2:
            line = f"月度记录加总为死亡{calc[0]}人、受伤{calc[1]}人。"
            if group.get("reported"):
                line += "与报告所列同口径合计一致。" if group.get("agrees") else "与报告所列数字未核对一致。"
            if group.get("scope"):
                line = f"{group['scope']}范围内，" + line
            lines.append(line)
        else:
            gaps.append("月度记录不足，无法核实同口径合计。")
    if rows:
        ordered = sorted(rows, key=lambda v: str(v[0] or ""))
        for row in [ordered[0], *([ordered[-1]] if ordered[-1] != ordered[0] else [])]:
            dates = f"，记录从 {row[1]} 到 {row[2]}" if row[1] and row[2] else ""
            lines.append(f"{row[0]} 为{row[3]}{dates}。")
    return reply(
        lines,
        [
            table(
                "逐月伤亡与覆盖",
                ["月份", "开始日期", "结束日期", "覆盖", "死亡人数", "受伤人数", "合计", "统计口径"],
                rows,
            )
        ],
        gaps,
    )


def clauses(p, q):
    rows, words, lines, gaps = [], [], [], []
    for row in p.get("clause_map", []):
        before, after = row.get("before", {}), row.get("after", {})
        topic = before.get("topic") or after.get("topic") or "条款"
        meaning = {
            "identical": "措辞一致",
            "expanded_wording": "扩充表述",
            "changed_wording": "措辞有变",
        }.get(row.get("change"))
        rows.append(
            [topic, before.get("id"), before.get("text"), after.get("id"), after.get("text"), meaning]
        )
        if len(lines) < 8:
            lines.append(
                f"「{topic}」{meaning or '已对照'}：原文为“{before.get('text') or '未记录'}”，修订后为“{after.get('text') or '未记录'}”。"
            )
        for side in (before, after):
            for word in side.get("glossary", []):
                pair = [word.get("term"), word.get("meaning")]
                if pair not in words and all(pair):
                    words.append(pair)
    if re.search(r"名词|术语|词汇|词表|俄文", q):
        if not words:
            gaps.append("现有条文没有附可核实的俄文术语与中文释义对应表。")
        else:
            lines.append("已核实的术语对应有" + "、".join(f"“{a}”（{b}）" for a, b in words[:8]) + "。")
    if p.get("unmatched"):
        gaps.append(f"另有 {len(p['unmatched'])} 条未建立两版对应关系，不能据此断言删除或新增。")
    return reply(
        lines,
        [
            table("两版条款对照", ["主题", "旧版条号", "旧文", "新版条号", "新文", "变化"], rows),
            table("术语对照", ["原文词语", "释义"], words),
        ],
        gaps,
    )


def indicators(p, _):
    lines, rows = [], []
    for r in p.get("comparison", []):
        region, indicator, series = r.get("region"), r.get("indicator"), r.get("series") or []
        for item in series:
            rows.append(
                [
                    region,
                    indicator,
                    item.get("cutoff"),
                    item.get("value"),
                    item.get("unit"),
                    item.get("definition"),
                    item.get("coverage"),
                    item.get("method"),
                ]
            )
        values = "、".join(
            f"{i.get('cutoff') or '时点未记录'}为 {i['value']} {i.get('unit') or ''}"
            for i in series
            if i.get("value") is not None
        )
        if r.get("comparable") and r.get("change") is not None:
            meaning = f"在所列定义和范围内可比较，变化 {r['change']:+g}"
        else:
            meaning = "现有口径不支持直接比较" + ("，" + "、".join(r["reasons"]) if r.get("reasons") else "")
        lines.append(f"{region or ''}的{indicator or '指标'}：{values}。{meaning}。")
    return reply(
        lines,
        [
            table(
                "地区、时点与统计口径",
                ["地区", "指标", "统计截止", "数值", "单位", "定义", "覆盖范围", "统计方法"],
                rows,
            )
        ],
    )


def timelines(p, question):
    lines, rows, gaps = [], [], []
    timeline = [*p.get("timeline", []), *p.get("unknown_time", [])]
    named = [r for r in timeline if r.get("event") and r["event"] in question]
    for r in named or timeline:
        facts = [
            (label, r.get(k))
            for k, label in (
                ("event_time", "事发"),
                ("formed_at", "形成记录"),
                ("published_at", "公开"),
                ("declassified_at", "解密"),
            )
        ]
        rows.append([r.get("event"), *(v for _, v in facts), r.get("timezone"), r.get("channel")])
        for label, recorded in facts:
            if not recorded and (label in question or label == "形成记录" and "形成" in question):
                gaps.append(f"“{r.get('event') or '此事件'}”的{label}时间未记载。")
        if len(lines) < 8:
            lines.append(
                f"{r.get('event') or '未命名事件'}："
                + "，".join(f"{label} {recorded if recorded else '未记载'}" for label, recorded in facts)
                + "。"
            )
    if p.get("variants"):
        gaps.append("部分档案对同一事件的发生时间记载不同，具体日期尚不能唯一确定。")
    return reply(
        lines,
        [table("档案时间对照", ["事件", "事发", "形成记录", "公开", "解密", "时区", "渠道"], rows)],
        gaps,
    )


ROLES = {"judicial": "司法材料", "investigation": "调查材料", "brief": "简报"}
RELATION = {
    "parallel_accounts": "材料分别陈述，尚不能互相替代",
    "same_statement": "对同一范围的陈述一致",
    "scope_differs": "陈述范围不同，不宜直接比较",
    "conflicting_values": "同范围内记载相互矛盾",
    "single_source": "只有单方材料",
    "missing": "材料不足以确认",
}


def _published(claim):
    try:
        return date.fromisoformat(str(claim.get("published_at"))[:10])
    except ValueError:
        return None


def evidence_matrix(code, p):
    lines, rows, gaps = [], [], []
    left_role, right_role = ("investigation", "judicial") if code == "B08" else ("brief", "investigation")
    for row in p.get("matrix", []):
        title = row.get("question") or "核查事项"
        claims = row.get("claims") or []
        for c in sorted(claims, key=lambda v: v.get("published_at") or "9999"):
            rows.append(
                [
                    title,
                    ROLES.get(c.get("role"), "其他来源"),
                    c.get("published_at"),
                    c.get("scope"),
                    c.get("statement"),
                    c.get("basis"),
                    c.get("qualifier"),
                ]
            )
        left = [c for c in claims if c.get("role") == left_role]
        right = [c for c in claims if c.get("role") == right_role]
        if not left or not right:
            gaps.append(
                f"“{title}”缺少{ROLES[left_role] if not left else ROLES[right_role]}，无法核查前后关系。"
            )
        statements = []
        for role, items in ((left_role, left), (right_role, right)):
            statements.extend(
                f"{c.get('published_at') or '日期未记载'}的{ROLES[role]}记载“{c['statement']}”"
                for c in items
                if c.get("statement")
            )
        relation = RELATION.get(row.get("status"), "现有材料无法核定两者的关系")
        ld = [_published(c) for c in left]
        rd = [_published(c) for c in right]
        if code == "B08" and ld and rd and all(ld + rd) and max(rd) < min(ld):
            relation += "；所列司法材料早于调查材料，无法据此认定法院后来采纳了该调查"
        elif code == "B08" and left and right:
            later_findings = [
                (a, b)
                for a in left
                for b in right
                if _published(a)
                and _published(b)
                and _published(a) < _published(b)
                and a.get("scope")
                and a["scope"] == b.get("scope")
                and a.get("comparable_value") is not None
                and a["comparable_value"] == b.get("comparable_value")
                and re.search(
                    r"法院认定|裁判认定|判决认定|court finding", str(b.get("basis") or ""), re.IGNORECASE
                )
            ]
            relation += (
                "；后出的法院认定与所列调查陈述在该范围一致"
                if later_findings
                else "；仅凭这些记载不能确认法院后来采纳了调查陈述"
            )
        if code == "B10" and left and right:
            pairs = [
                (a, b)
                for a in left
                for b in right
                if a.get("scope")
                and a.get("scope") == b.get("scope")
                and a.get("comparable_value")
                and b.get("comparable_value")
            ]
            if pairs and all(a["comparable_value"] == b["comparable_value"] for a, b in pairs):
                relation += "；可比事项的结论未发生变化"
        if len(lines) < 8:
            lines.append(f"{title} " + "；".join(statements) + f"。{relation}。")
    return reply(
        lines,
        [
            table(
                "各方记载与认定范围",
                ["核查事项", "来源角色", "发布日期", "范围", "所述内容", "依据或认定层次", "限定"],
                rows,
            )
        ],
        gaps,
    )


def project_events(case_id, products, question):
    if case_id in ("B08", "B10"):
        return evidence_matrix(case_id, products)
    handlers = {
        "B01": changes,
        "B02": commitments,
        "B03": photos,
        "B04": turns,
        "B05": monthly,
        "B06": clauses,
        "B07": indicators,
        "B09": timelines,
    }
    if case_id in handlers:
        return handlers[case_id](products, question)
    return None
