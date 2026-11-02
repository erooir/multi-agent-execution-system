"""Render audited scientific products as answers and complete, labeled business tables."""

from collections import Counter
from math import isfinite


def _list(data, key):
    items = data.get(key) or []
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


def _value(item):
    """Record-based slots use supplied_value; verbatim slots use text."""
    for key in ("supplied_value", "text", "value"):
        v = item.get(key)
        if isinstance(v, (float, int, str)) and v != "" and not isinstance(v, bool):
            return str(v)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return "、".join(v)
    return None


def _table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows, "total_rows": len(rows)}


def _result(lines, tables, missing=()):
    actual = [line for line in lines if line]
    return {
        "answer": "\n".join(actual) or "没有足以回答此问题的可核实记录。",
        "tables": [t for t in tables if t["rows"]],
        "missing": list(dict.fromkeys(missing))
        if actual
        else list(dict.fromkeys([*missing, "未取得可核实的业务结果。"])),
    }


def _short(lines, summary):
    # Full line-level results live in the corresponding table for large collections.
    return lines if len(lines) <= 8 and sum(map(len, lines)) <= 2400 else [summary]


def _research(p, _question):
    card, bib = p.get("literature_card") or {}, p.get("bibliography") or {}
    labels = {
        "question": "研究问题",
        "comparison": "试验对照",
        "measurements": "实测结果",
        "model_results": "模型结果",
        "limitations": "研究局限",
    }
    rows, lines, missing = [], [], []
    for key, label in labels.items():
        values = [v for item in _list(card, key) if (v := _value(item))]
        if values:
            rows.extend([label, v] for v in values)
            lines.append(f"{label}：{'；'.join(values)}")
        else:
            missing.append(f"未取得可核实的{label}。")
    source = [
        [label, _value(bib[key])]
        for key, label in (
            ("title", "论文题名"),
            ("authors", "作者"),
            ("institution", "机构"),
            ("doi", "DOI"),
            ("published_date", "发表日期"),
        )
        if isinstance(bib.get(key), dict) and _value(bib[key])
    ]
    return _result(
        lines,
        [
            _table("试验与结论", ["项目", "论文中可核实的内容"], rows),
            _table("书目信息", ["项目", "内容"], source),
        ],
        missing,
    )


def _milestones(p, _question):
    states = {
        "baseline": "历史已知",
        "new_event": "新增事件",
        "repeat_report": "重复报道",
        "updated_account": "已知事件的记载有更新",
    }
    types = {
        "first_trial": "首次试验",
        "second_trial": "第二次试验",
        "first_flight": "首飞",
        "test": "测试",
        "delivery": "交付",
        "launch": "启动",
        "review": "评审",
    }
    events = _list(p, "milestones")
    rows, lines = [], []
    for e in events:
        typ = types.get(e.get("event_type"), e.get("event_type"))
        status = states.get(e.get("status"), "归类待核")
        desc = "、".join(map(str, e.get("observations") or []))
        rows.append(
            [
                e.get("project"),
                typ,
                e.get("event_date"),
                status,
                "、".join(map(str, e.get("report_dates") or [])),
                desc,
            ]
        )
        lines.append(
            f"{e.get('event_date') or '日期未明'}，{e.get('project') or '未标明项目'}的{typ or '事件'}属于{status}。"
        )
    counts = Counter(row[3] for row in rows)
    summary = "已归并的事件中，" + "、".join(f"{k} {v} 项" for k, v in counts.items()) + "。"
    missing = (
        [f"有 {len(p['unmatched'])} 条记录缺少事件标识或日期，无法确认是否重复。"]
        if p.get("unmatched")
        else []
    )
    return _result(
        _short(lines, summary),
        [_table("里程碑更新", ["项目", "事件", "发生日期", "判断", "报道日期", "记录结果"], rows)],
        missing,
    )


def _roadmap(p, _question):
    history, changes = _list(p, "route_history"), _list(p, "route_changes")
    rows, lines = [], []
    for c in changes:
        old, new = c.get("before") or {}, c.get("after") or {}
        rows.append(
            [
                c.get("project"),
                c.get("feature"),
                old.get("date"),
                old.get("value"),
                old.get("stage"),
                new.get("date"),
                new.get("value"),
                new.get("stage"),
            ]
        )
        lines.append(
            f"{c.get('project') or '项目'}的{c.get('feature') or '技术路线'}，从 {old.get('date') or '较早时点'} 的“{old.get('value') or '未载明'}”（{old.get('stage') or '阶段未载明'}），到 {new.get('date') or '较晚时点'} 改为“{new.get('value') or '未载明'}”（{new.get('stage') or '阶段未载明'}）。"
        )
    notes = _short(lines, f"发现 {len(rows)} 处公开路线表述变化，逐项列在路线变更表。")
    if history and not changes:
        notes = ["现有快照没有记录到可核实的路线表述变化。"]
    missing = (
        ["概念或计划阶段的表述不能证实已经交付；实际交付顺序仍待核实。"] if p.get("unverified_stages") else []
    )
    return _result(
        notes,
        [
            _table(
                "路线变更",
                ["项目", "主题", "早期日期", "早期路线", "早期阶段", "后期日期", "后期路线", "后期阶段"],
                rows,
            ),
            _table(
                "已发布的路线快照",
                ["项目", "主题", "日期", "公开路线", "阶段"],
                [[h.get(k) for k in ("project", "feature", "date", "value", "stage")] for h in history],
            ),
        ],
        missing,
    )


def _funding(p, question):
    projects = _list(p, "portfolio")
    edges = _list(p.get("organization_graph") or {}, "edges")
    funding = _list(p.get("theme_summary") or {}, "known_funding")
    roles = {"coordinator": "牵头机构", "participant": "参与机构"}
    links = [[e.get("from"), e.get("to"), roles.get(e.get("role"), "职责未标明")] for e in edges]
    rows = [
        [
            x.get("project_id"),
            x.get("theme"),
            x.get("currency"),
            x.get("total_cost"),
            x.get("contribution"),
            x.get("goal"),
        ]
        for x in projects
    ]
    sums, lines = [], []
    for f in funding:
        cost, grant = f.get("total_cost") or {}, f.get("contribution") or {}
        currency, theme = f.get("currency"), f.get("theme")
        sums.append(
            [
                theme,
                currency,
                cost.get("known_amount"),
                cost.get("records"),
                grant.get("known_amount"),
                grant.get("records"),
            ]
        )
        parts = [
            f"{label} {obj['known_amount']:,} {currency}"
            for label, obj in (("已知项目总成本", cost), ("已知资助额", grant))
            if isinstance(obj.get("known_amount"), (int, float)) and currency
        ]
        if parts:
            lines.append(f"{theme or '未分类主题'}的" + "，".join(parts) + "。")
    for project in projects if len(projects) <= 8 else []:
        pid = project.get("project_id")
        members = [f"{name}（{role}）" for src, name, role in links if src == pid and name]
        grant, cur = project.get("contribution"), project.get("currency")
        funded = f"，项目资助额 {grant:,} {cur}" if isinstance(grant, (int, float)) and cur else ""
        if members:
            lines.append(f"{pid} 由{'、'.join(members)}合作{funded}。")
    if projects and not lines:
        lines.append(f"有 {len(projects)} 个可定位的项目，项目级金额与机构参与情况见表。")
    missing = []
    if projects and (
        not funding or any(x.get("contribution") is None or not x.get("currency") for x in projects)
    ):
        missing.append("部分项目没有可核实的资助额或币种，金额合计只覆盖已知部分。")
    if projects and any(
        word in question for word in ("机构", "分别", "合作", "分配", "拿了", "institution", "partner")
    ):
        missing.append("资料记录了项目级资助额，未列各机构实际分得的金额。")
    return _result(
        lines,
        [
            _table("各项目经费", ["项目编号", "主题", "币种", "项目总成本", "资助额", "研究目标"], rows),
            _table("机构合作关系", ["项目编号", "机构", "职责"], links),
            _table(
                "按主题及币种核算",
                ["主题", "币种", "已知总成本", "成本记录数", "已知资助额", "资助记录数"],
                sums,
            ),
        ],
        missing,
    )


def _figures(p, _question):
    cards = _list(p, "figure_cards")
    data = _list(p, "comparison") or [
        {"figure": card.get("figure"), **v} for card in cards for v in _list(card, "labelled_values")
    ]
    rows, lines = [], []
    for v in data:
        basis = v.get("取值方式") or {"label": "图上标注", "text": "正文明确值"}.get(
            v.get("basis"), "取值方式未确认"
        )
        kind = v.get("时间属性") or {"historical": "历史值", "projection": "预测值"}.get(
            v.get("kind"), "时间属性未确认"
        )
        figure, scenario, year, value, unit = (
            v.get(k) for k in ("figure", "scenario", "year", "value", "unit")
        )
        rows.append([figure, v.get("图题"), scenario, year, value, unit, basis, kind, v.get("context")])
        if value is not None:
            lines.append(
                f"图 {figure}，{scenario or '未标明情景'}{f'（{year}）' if year is not None else ''}为 {value} {unit or '（单位未明）'}，来自{basis}，时间属性为{kind}。"
            )
    if rows:
        notes = _short(lines, f"已核实 {len(rows)} 条图文数值，逐条列出情景、单位以及取值依据。")
        notes.append("未标明数值的曲线和坐标轴刻度没有计为观测值。")
    else:
        notes = []
    return _result(
        notes,
        [
            _table(
                "图表情景数值",
                ["图号", "图题", "情景", "年份", "数值", "单位", "依据", "时间属性", "上下文"],
                rows,
            )
        ],
    )


def _standards(p, _question):
    rows, lines = [], []
    for pair in _list(p, "clause_map"):
        old, new = pair.get("before") or {}, pair.get("after") or {}
        label = {"identical": "文字一致", "expanded_wording": "扩充", "changed_wording": "措辞变更"}.get(
            pair.get("change"), "待核"
        )
        topic = old.get("topic") or new.get("topic")
        rows.append([topic, old.get("id"), old.get("text"), new.get("id"), new.get("text"), label])
        if pair.get("change") == "identical" and old.get("text") and new.get("text"):
            lines.append(f"“{topic or '对应条款'}”在草案与定稿中措辞一致，均为“{old['text']}”。")
        elif old.get("text") and new.get("text"):
            lines.append(
                f"“{topic or '对应条款'}”由草案第 {old.get('id') or '未标号'} 条“{old['text']}”改为定稿第 {new.get('id') or '未标号'} 条“{new['text']}”（{label}）。"
            )
    unmatched = [
        [
            {"before": "草案", "after": "定稿"}.get(r.get("side"), "版本未明"),
            (r.get("item") or {}).get("id"),
            (r.get("item") or {}).get("text"),
            r.get("reason"),
        ]
        for r in _list(p, "unmatched")
    ]
    comments = [[_value(r)] for r in _list(p, "commentary") if _value(r)]
    missing = [f"{len(unmatched)} 条未能双向配对；无法仅凭缺少对应项认定新增或删除。"] if unmatched else []
    return _result(
        _short(lines, f"核实 {len(rows)} 对草案与定稿条款，逐条变化见表。"),
        [
            _table(
                "草案与定稿逐条对照", ["主题", "草案条号", "草案原文", "定稿条号", "定稿原文", "变化"], rows
            ),
            _table("未配对条款", ["版本", "条号", "内容", "原因"], unmatched),
            _table("意见和回应", ["原文"], comments),
        ],
        missing,
    )


def _clock(ms):
    if not isinstance(ms, (int, float)) or not isfinite(ms) or ms < 0:
        return None
    seconds, fraction = divmod(int(ms), 1000)
    minutes, second = divmod(seconds, 60)
    return f"{minutes:02d}:{second:02d}" + (f".{fraction:03d}" if fraction else "")


def _interview(p, _question):
    rows, lines = [], []
    for v in _list(p, "timed_index"):
        begin, end = _clock(v.get("start_ms")), _clock(v.get("end_ms"))
        rows.append([v.get("topic"), v.get("speaker"), begin, end, v.get("text"), v.get("translation_zh")])
        if begin and end:
            lines.append(
                f"{v.get('topic') or '未标明主题'}从 {begin} 到 {end} 回听，发言人为{v.get('speaker') or '身份未标明'}。"
            )
    diffs = _list(p, "differences")
    missing = [f"有 {len(diffs)} 处听辨或逐字稿差异待核。"] if diffs else []
    if p.get("transcript_only"):
        missing.append(f"另有 {len(p['transcript_only'])} 段只有逐字稿，无法标注回听时间。")
    return _result(
        _short(lines, f"已定位 {len(rows)} 段访谈，回听表按主题给出实际起止时间。"),
        [
            _table(
                "访谈回听索引", ["主题", "发言人", "开始（分:秒）", "结束（分:秒）", "原话", "中文译文"], rows
            ),
            _table(
                "术语表",
                ["术语", "释义"],
                [[k, v.get("meaning")] for k, v in (p.get("glossary") or {}).items() if isinstance(v, dict)],
            ),
        ],
        missing,
    )


def _closeout(p, _question):
    states = {
        "missing": "未取得陈述",
        "single_source": "单一来源",
        "parallel_accounts": "并列陈述，未确认实质矛盾",
        "scope_differs": "口径不同，不能直接认定矛盾",
        "same_statement": "陈述一致",
        "conflicting_values": "同一口径下记录有冲突",
    }
    roles = {"project": "项目方", "audit": "审计方", "study": "独立研究"}
    rows, lines, missing = [], [], []
    for item in _list(p, "matrix"):
        label = states.get(item.get("status"), "待核实")
        lines.append(f"“{item.get('question') or '未注明核查事项'}”的判断为{label}。")
        if item.get("status") in {"single_source", "missing"}:
            missing.append(f"“{item.get('question') or '核查事项'}”缺少足够的独立陈述。")
        claims = _list(item, "claims")
        if claims and len(claims) <= 4 and len(p["matrix"]) <= 5:
            lines.append(
                "；".join(
                    f"{roles.get(v.get('role'), '来源')}记载“{v['statement']}”"
                    for v in claims
                    if v.get("statement")
                )
                + "。"
            )
        for v in claims:
            rows.append(
                [
                    item.get("question"),
                    label,
                    roles.get(v.get("role"), "其他来源"),
                    v.get("published_at"),
                    v.get("statement"),
                    v.get("scope"),
                    v.get("basis"),
                ]
            )
    return _result(
        _short(lines, f"逐项核对了 {len(lines)} 个问题，各方陈述和可比性见表。"),
        [
            _table(
                "项目方、审计和研究陈述",
                ["核查问题", "判断", "来源角色", "公开日期", "陈述", "适用范围", "依据"],
                rows,
            )
        ],
        missing,
    )


def _battery(p, _question):
    rows, lines = [], []
    for series in _list(p, "trend_series"):
        metric = {"capacity": "容量", "resistance": "内阻"}.get(series.get("metric"))
        if not metric:
            continue
        points = _list(series, "points")
        unit = series.get("unit") or "（单位未明）"
        rows.extend([series.get("cell"), metric, x.get("cycle"), x.get("value"), unit] for x in points)
        if len(points) >= 2:
            first, last = points[0], points[-1]
            delta = series.get("observed_difference")
            change = f"，首尾差值 {delta:+g} {unit}" if isinstance(delta, (int, float)) else ""
            lines.append(
                f"{series.get('cell') or '所给样本'}的{metric}从第 {first.get('cycle')} 次循环的 {first.get('value')} {unit} 变为第 {last.get('cycle')} 次循环的 {last.get('value')} {unit}{change}。"
            )
    applicability = p.get("applicability") or {}
    limits = _list(applicability, "literature")
    condition = [[v.get("population"), v.get("conditions"), v.get("text")] for v in limits]
    notes = _short(lines, f"已核实 {len(rows)} 组循环观测；不同指标和单位分别列示。")
    if condition:
        notes.append(
            "材料记载的适用条件为"
            + "、".join(dict.fromkeys(str(row[1]) for row in condition if row[1]))
            + "。"
            if any(r[1] for r in condition)
            else "材料未明确给出测试条件。"
        )
    if rows and applicability.get("boundary"):
        notes.append(applicability["boundary"])
    return _result(
        notes,
        [
            _table("循环观测", ["电池样本", "指标", "循环次数", "观测值", "单位"], rows),
            _table("适用条件与对象", ["对象", "测试条件", "材料说明"], condition),
        ],
    )


def _citations(p, _question):
    states = {
        "withdraw": "应撤回",
        "downgrade": "应降级",
        "unaffected": "有独立支持，可保留",
        "unverified": "待核实",
    }
    statuses = {"retracted": "已撤稿", "published": "已发表", "corrected": "已更正", "withdrawn": "已撤回"}
    rows, lines = [], []
    for v in _list(p, "citation_impact"):
        label = states.get(v.get("action"), "待核实")
        rows.append(
            [
                v.get("original"),
                label,
                v.get("reason"),
                "、".join(v.get("cites") or []),
                v.get("suggested_revision"),
            ]
        )
        lines.append(f"“{v.get('original') or '未注明主张'}”{label}，依据是{v.get('reason') or '尚待核实'}。")
    timeline = [
        [r.get("doi"), r.get("date"), statuses.get(r.get("status"), r.get("status"))]
        for r in _list(p, "status_timeline")
    ]
    independent = [[v.get("doi"), _value(v)] for v in _list(p, "independent_research")]
    return _result(
        _short(lines, f"核查了 {len(rows)} 条引用主张，依赖判定与处理意见逐条列示。"),
        [
            _table("简报结论与处理意见", ["原主张", "处理", "判断依据", "引用 DOI", "修改建议"], rows),
            _table("文献状态时间线", ["DOI", "日期", "状态"], timeline),
            _table("独立研究", ["DOI", "研究陈述"], independent),
        ],
    )


_HANDLERS = {
    "T01": _research,
    "T02": _milestones,
    "T03": _roadmap,
    "T04": _funding,
    "T05": _figures,
    "T06": _standards,
    "T07": _interview,
    "T08": _closeout,
    "T09": _battery,
    "T10": _citations,
}


def project_science(case_id, products, question):
    handler = _HANDLERS.get(case_id)
    return handler(products, question or "") if handler else None
