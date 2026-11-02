"""Research cards, project graphs, labelled chart values, and dependency analysis."""

import re
from collections import defaultdict

from .document_common import items, norm, number, string, value


def research(run):
    card = run.slots(
        {
            "question": (
                ("research_question", "question"),
                [r"\baim(?:s|ed)?\b|we investigat|this study|research question|本研究|研究目的"],
            ),
            "comparison": (
                ("comparison", "comparator"),
                [r"\bcompar(?:e|ed|ing|ison)|relative to|instead of|对照|对比"],
            ),
            "measurements": (
                ("measurement", "measured_result"),
                [r"(?:measur|observ|实测|测量).{0,200}?(?:\d|decreas|reduc)|\d.{0,120}?(?:measur|观测)"],
            ),
            "model_results": (
                ("model_result", "simulation"),
                [
                    r"(?:simulat|model(?:led|ed|ling|ing)?|模拟|模型).{0,200}?(?:indicat|show|predict|estimat|\d|推断)"
                ],
            ),
            "limitations": (
                ("limitation", "limitations"),
                [r"\blimit(?:ation|ed)|uncertain|cannot.{0,55}(?:general|determine)|局限|不确定|适用范围"],
            ),
        }
    )
    metadata = {}
    # Prioritise first-party sentences that tie a measured quantity to an observed result.
    if not any(value(r, "measurement", "measured_result") is not None for r in run.records):
        quantified = run.find(
            [
                r"(?is)\d[\d.,]*\s*%\s*(?:reduction|increase|decrease|change).{0,190}\b(?:was|were)\s+(?:measured|observed)\b",
                r"(?s)观测到.{0,25}\d+[％%]",
            ],
            3,
        )
        if quantified:
            card["measurements"] = quantified
    for name, regex in {
        "doi": r"\b10\.\d{4,9}/[\w.()/-]+",
        "published_date": r"Published:\s*\d{1,2}\s+[A-Za-z]+\s+\d{4}",
    }.items():
        for r in run.records:
            mt = re.search(regex, r.get("text", ""), re.IGNORECASE)
            given = value(r, name)
            if given or mt:
                metadata[name] = {
                    "value": given or mt[0],
                    "evidence_ids": [run.cite(r, None if given else mt[0])],
                }
                break
    for name in ("title", "authors", "institution"):
        for r in run.records:
            if value(r, name) is not None:
                metadata[name] = {"value": value(r, name), "evidence_ids": [run.cite(r)]}
                break
    if "title" not in metadata:
        for r in run.records:
            mt = re.search(r"(?im)^Research article\s*\n(.{8,160}\n.{4,130})", r.get("text", ""))
            if mt:
                metadata["title"] = {"value": " ".join(mt[1].split()), "evidence_ids": [run.cite(r, mt[1])]}
                block = r.get("text", "")[mt.end() :]
                boundary = re.search(r"(?m)^\s*1\s*(?=[A-ZÀ-Ž])", block)
                if boundary:
                    byline = block[: boundary.start()].strip()
                    if byline and len(byline) <= 2000:
                        metadata["authors"] = {
                            "value": " ".join(byline.split()),
                            "evidence_ids": [run.cite(r, byline)],
                        }
                    affiliation = block[boundary.start() :].split("Correspondence:")[0].strip()
                    if affiliation and len(affiliation) <= 3000:
                        metadata["institution"] = {
                            "value": " ".join(affiliation.split()),
                            "evidence_ids": [run.cite(r, affiliation)],
                        }
                break
    for k in ["title", "published_date", "authors", "institution"]:
        if not metadata.get(k):
            run.gap(f"文献信息缺少可定位字段：{k}")
    return {
        "literature_card": card,
        "bibliography": metadata,
        "interpretation": "按证据类型保留可回查摘录；实验和模型结果不能外推到未研究的对象。",
    }


def projects(run):
    taxonomy = {}
    aliases = {}
    by_id = defaultdict(list)
    edge_rows = []
    themes = defaultdict(list)
    budget = defaultdict(lambda: defaultdict(list))
    out = []
    for r in run.records:
        kind = string(r, "kind")
        if kind == "taxonomy":
            theme = string(r, "theme")
            if theme:
                taxonomy[theme] = items(value(r, "keywords"))
        elif kind == "org_alias" and string(r, "alias") and string(r, "canonical"):
            aliases[norm(string(r, "alias"))] = string(r, "canonical")
        elif string(r, "project_id", "项目编号"):
            by_id[string(r, "project_id", "项目编号")].append(r)
    for pid, duplicates in sorted(by_id.items()):
        if len(duplicates) > 1 and len({repr(r.get("fields", {})) for r in duplicates}) > 1:
            run.gap(f"项目 {pid} 有不同记录版本，排除不确定金额并列出来源")
            out.append(
                {
                    "project_id": pid,
                    "status": "version_conflict",
                    "evidence_ids": [run.cite(r) for r in duplicates],
                }
            )
            continue
        r = duplicates[0]
        goal = string(r, "objective", "goal", "目标") or r.get("text", "")
        theme = string(r, "theme", "主题")
        hits = []
        if theme:
            hits = [theme]
        else:
            hits = [t for t, words in taxonomy.items() if any(norm(w) in norm(goal) for w in words)]
        if len(hits) == 1:
            theme = hits[0]
        else:
            theme = "未分类"
            run.gap(f"{pid} 主题缺失或词表出现多个匹配")
        themes[theme].append(pid)
        currency = string(r, "currency", "币种")
        ref = run.cite(r)
        record = {
            "project_id": pid,
            "theme": theme,
            "topic_matches": hits,
            "goal": goal[:500],
            "result": value(r, "published_result"),
            "start": value(r, "start"),
            "end": value(r, "end"),
            "currency": currency or None,
            "evidence_ids": [ref],
        }
        for k in ["total_cost", "contribution"]:
            amt = number(value(r, k))
            record[k] = amt
            if amt is not None and currency:
                budget[(theme, currency)][k].append((amt, ref))
            elif amt is not None:
                run.gap(f"{pid} 缺货币单位，未计入主题资金汇总")
        out.append(record)
        relations = set()
        for role, key in [("coordinator", "coordinator"), ("participant", "participants")]:
            for org in items(value(r, key)):
                canonical = aliases.get(norm(org), org)
                relations.add((canonical, role))
        for org, role in sorted(relations):
            edge_rows.append({"from": pid, "to": org, "role": role, "evidence_ids": [ref]})
        if not relations:
            run.gap(f"{pid} 无参与机构字段")
    if not by_id:
        run.gap("未取得带稳定项目编号的项目记录")
    return {
        "portfolio": out,
        "organization_graph": {
            "nodes": [{"id": k, "type": "project"} for k in sorted(by_id)]
            + [{"id": k, "type": "organization"} for k in sorted({e["to"] for e in edge_rows})],
            "edges": edge_rows,
        },
        "theme_summary": {
            "projects": {k: {"count": len(v), "representative": min(v)} for k, v in themes.items()},
            "known_funding": [
                {
                    "theme": t,
                    "currency": c,
                    **{
                        k: {
                            "known_amount": sum(a for a, _ in pairs),
                            "records": len(pairs),
                            "evidence_ids": [ref for _, ref in pairs],
                        }
                        for k, pairs in sums.items()
                    },
                }
                for (t, c), sums in budget.items()
            ],
        },
    }


def chart_data(run):
    from .document_figures import chart_data as read_charts

    return read_charts(run)


def battery(run):
    series = defaultdict(list)
    missing = defaultdict(int)
    claims = []
    units_seen = defaultdict(set)
    samples = []
    for r in run.records:
        cell = string(r, "battery_id", "cell_id", "电池编号")
        step = number(value(r, "cycle", "cycle_index"))
        kind = string(r, "kind")
        if not cell:
            if kind in {"paper", "study", "claim"}:
                claims.append(
                    run.excerpt(r, conditions=value(r, "conditions"), population=value(r, "population"))
                )
            continue
        record = {
            "cell": cell,
            "cycle": step,
            "temperature": value(r, "temperature"),
            "load": value(r, "load"),
            "evidence_ids": [run.cite(r)],
        }
        for k in ("capacity", "resistance"):
            val = number(value(r, k))
            unit = string(r, k + "_unit")
            record[k] = val
            record[k + "_unit"] = unit or None
            if val is None:
                missing[k] += 1
            elif not unit:
                missing[k + "_unit"] += 1
            elif step is not None:
                units_seen[(cell, k)].add(unit)
                series[(cell, k, unit)].append(
                    {"cycle": step, "value": val, "evidence_ids": record["evidence_ids"]}
                )
        samples.append(record)
    if not samples:
        run.gap("缺电池时序样本，不生成趋势图")
    if not claims:
        run.gap("缺论文结论及实验条件的出处")
    for (cell, key), us in units_seen.items():
        if len(us) > 1:
            run.gap(f"{cell} 的{key}单位不同，分开绘制")
    for k, v in missing.items():
        run.gap(f"{v} 条样本缺少{k}")
    plots = []
    for (cell, k, unit), ps in sorted(series.items()):
        ps.sort(key=lambda p: p["cycle"])
        cycles = [p["cycle"] for p in ps]
        if len(set(cycles)) != len(cycles) or len(ps) < 2:
            run.gap(f"{cell} 的{k}循环重复或不足两点，不估计趋势")
            continue
        plots.append(
            {
                "cell": cell,
                "metric": k,
                "unit": unit,
                "x": "cycle",
                "points": ps,
                "observed_difference": ps[-1]["value"] - ps[0]["value"],
                "method": "last_minus_first",
            }
        )
    if len({p["metric"] for p in plots}) < 2:
        run.gap("不足两种有单位的可复算趋势序列")
    return {
        "field_coverage": {"rows": len(samples), "missing": dict(missing), "samples": samples},
        "trend_series": plots,
        "applicability": {"literature": claims, "boundary": "只描述所给电池与工况；不推断装备预测准确率。"},
    }


def citation_impact(run):
    statuses = []
    brief = []
    independent = {}
    retracted = set()
    for r in run.records:
        kind = string(r, "kind", "role")
        doi = norm(string(r, "doi", "source_doi")).removeprefix("https://doi.org/")
        if kind in {"status", "notice"} and doi:
            state = norm(string(r, "status"))
            date = string(r, "date", "published_date")
            statuses.append(
                {"doi": doi, "status": state, "date": date or None, "evidence_ids": [run.cite(r)]}
            )
            if state in {"retracted", "撤稿"}:
                retracted.add(doi)
        elif kind in {"independent", "research"} and doi:
            independent[doi] = r
        elif kind in {"brief", "brief_claim"}:
            brief.append(r)
    affected = []
    for r in brief:
        refs = [norm(x).removeprefix("https://doi.org/") for x in items(value(r, "cites", "cited_dois"))]
        removed = sorted(set(refs) & retracted)
        # Independent sources count only when the upstream evidence explicitly supports THIS statement in scope.
        claim_id = string(r, "claim_id")
        support = [
            d
            for d in refs
            if d in independent and claim_id and claim_id in items(value(independent[d], "supports_claims"))
        ]
        if removed and set(refs) <= retracted:
            action, reason = "withdraw", "唯一依据均已撤稿"
        elif removed:
            action, reason = "downgrade", "部分依据已撤稿，剩余证据范围需复核"
        elif support:
            action, reason = "unaffected", "有本条主张可定位的独立支持，未依赖本次撤稿对象"
        else:
            action, reason = "unverified", "没有可核依赖或独立支持，不判定为不受影响"
        affected.append(
            {
                "claim_id": claim_id or None,
                "original": string(r, "statement", "claim") or r.get("text", ""),
                "cites": refs,
                "affected_dois": removed,
                "action": action,
                "reason": reason,
                "suggested_revision": {
                    "withdraw": "撤回该条依赖已撤稿来源的结论。",
                    "downgrade": "现有证据不足以维持原结论，待核验独立支持。",
                    "unaffected": "保留原条目及其独立来源。",
                    "unverified": "标注待核实。",
                }[action],
                "evidence_ids": [run.cite(r)]
                + [i for s in statuses if s["doi"] in removed for i in s["evidence_ids"]]
                + [run.cite(independent[d]) for d in support],
            }
        )
    if not retracted:
        run.gap("未给可定位到DOI的撤稿状态，不判定成果失效")
    if not brief:
        run.gap("缺已有简报逐条主张，不能判定引用影响")
    return {
        "status_timeline": sorted(statuses, key=lambda s: s["date"] or ""),
        "citation_impact": affected,
        "independent_research": [run.excerpt(r, doi=d) for d, r in independent.items()],
    }
