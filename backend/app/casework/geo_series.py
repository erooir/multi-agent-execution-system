"""Time-series, environmental sampling and cross-modal evidence analyses."""

from collections import Counter, defaultdict
from statistics import fmean

from .geo_common import collection, day, feature, line_chart, lnglat, number, stamp


def ice(ctx):
    start = int(ctx.policy.get("start_year", 1979))
    end = int(ctx.policy.get("target_year", 2023))
    a, b = ctx.policy.get("baseline_years", [1981, 2010])
    if not 1 <= start <= a <= b <= end <= 9999 or end - start > 500:
        raise ValueError("基线和年份窗口不合法")
    observations = defaultdict(list)
    versions = set()
    units = set()
    for row in ctx.rows:
        f = row["fields"]
        if "year" not in f:
            continue
        y = number(f["year"])
        if y is None or not y.is_integer() or not start <= y <= end:
            continue
        if number(f.get("month")) != 9 or str(f.get("hemisphere", "")).upper() not in {
            "N",
            "NH",
            "NORTH",
            "北极",
        }:
            continue
        if "extent" not in f:
            continue
        # A daily minimum or ice area cannot stand in for the September mean extent.
        if f.get("metric", "monthly_mean_extent") not in {"extent", "monthly_mean_extent"}:
            continue
        val = number(f.get("extent"))
        unit = str(f.get("unit") or ctx.policy.get("extent_unit") or "").strip()
        if not unit and str(row.get("location", {}).get("sheet", "")).casefold() == "september-nh":
            # Public Sea Ice Index workbook: dedicated NH monthly sheet, F=extent, G=area.
            unit = "Mkm²" if "source_dataset" in f and "area" in f else ""
        units.add(unit)
        ver = str(f.get("dataset_version") or f"{row['source_id']}@{row['source_version']}")
        versions.add(ver)
        # NSIDC negative sentinel values are not physical extents.
        observations[int(y)].append((val if val is not None and val >= 0 else None, ctx.ref(row)))
    comparable = len(versions) == 1 and len(units) == 1 and "" not in units
    if not comparable:
        ctx.gap("缺少一致的数据版本或范围单位，不能比较")
    clean = []
    for yr in range(start, end + 1):
        entries = observations[yr]
        vals = {entry[0] for entry in entries}
        value = next(iter(vals)) if len(vals) == 1 else None
        if len(vals) > 1:
            ctx.gap("同一年度存在冲突观测，未选取其中一个")
        clean.append(
            {
                "year": yr,
                "extent": value,
                "status": "observed" if value is not None else "missing_or_conflict",
                "refs": [entry[1] for entry in entries],
            }
        )
    missing = [r["year"] for r in clean if r["extent"] is None]
    if missing:
        ctx.gap(f"缺少或冲突的年度记录：{missing}")
    baseline_values = [r["extent"] for r in clean if a <= r["year"] <= b and r["extent"] is not None]
    target = clean[-1]["extent"]
    base = fmean(baseline_values) if comparable and len(baseline_values) == b - a + 1 else None
    diff = target - base if target is not None and base is not None else None
    unit = next(iter(units)) if len(units) == 1 else None
    stats = {
        "target_year": end,
        "unit": unit,
        "versions": sorted(versions),
        "baseline_years": [a, b],
        "baseline_n": len(baseline_values),
        "baseline_mean": base,
        "target_extent": target,
        "difference": diff,
        "percent": 100 * diff / base if diff is not None and base else None,
        "ascending_rank": 1 + sum(r["extent"] < target for r in clean)
        if not missing and comparable
        else None,
        "ranking": "范围从小到大；并列采用相同名次，截止目标年",
        "formula": "difference=target−mean(baseline years); percent=100×difference/mean; rank=1+count(extent<target)",
        "note": "仅比较北半球9月月均范围；单日最小值及面积不是本指标。",
    }
    return {
        "annual_series": clean,
        "anomaly": stats,
        "trend_svg": line_chart([(r["year"], r["extent"]) for r in clean], unit or "", base)
        if comparable
        else None,
    }


def samples(ctx):
    start = day(ctx.policy.get("start", "2023-02-03"))
    end = day(ctx.policy.get("end", "2023-05-31"))
    if not start or not end or start > end:
        raise ValueError("采样时间窗错误")
    sites = defaultdict(list)
    grids = {}
    labs = []
    rejected = Counter()
    seen = set()
    units = defaultdict(set)
    for r in ctx.rows:
        f = r["fields"]
        if f.get("kind") == "site":
            xy = lnglat(f.get("longitude"), f.get("latitude"))
            if xy:
                sites[str(f.get("site_id", ""))].append((xy, ctx.ref(r)))
        if f.get("kind") == "grid":
            bb = f.get("bbox")
            if (
                isinstance(bb, list)
                and len(bb) == 4
                and lnglat(bb[0], bb[1])
                and lnglat(bb[2], bb[3])
                and bb[0] < bb[2]
                and bb[1] < bb[3]
            ):
                grids[str(f["grid_id"])] = (bb, ctx.ref(r))
            else:
                ctx.gap("覆盖网格缺少有效WGS84经纬度边界")
    for r in ctx.rows:
        f = r["fields"]
        if not ("Samp_No" in f or "sample_id" in f):
            continue
        dt = day(f.get("SampleDate_txt", f.get("date")))
        if not dt:
            rejected["invalid_date"] += 1
            continue
        if not start <= dt <= end:
            rejected["outside_window"] += 1
            continue
        sample = str(f.get("Samp_No", f.get("sample_id", "")))
        site = str(f.get("Location", f.get("site_id", "")))
        analyte = str(f.get("Analyte", f.get("analyte", "")))
        unit = str(f.get("Result_Units", f.get("unit", ""))).strip()
        media = str(f.get("SampleMedia", f.get("medium", "")))
        units[(analyte, media)].add(unit.casefold())
        raw = str(f.get("Result_Final_Txt", f.get("value", ""))).strip()
        q = str(f.get("Result_Qualifier_Final", f.get("qualifier", ""))).strip()
        validation = str(f.get("Validation_Level", f.get("validation", "")))
        mapping = ctx.policy.get("qualifier_rules", {})
        flag = mapping.get(q) if isinstance(mapping, dict) else None
        confirmed = validation.casefold() in {"final", "validated", "最终", "已审核"}
        numeric = number(raw)
        if flag in {"rejected", "pending"} or not confirmed or (q and flag is None):
            status = "pending_qc"
        elif raw.startswith("<") or flag == "nondetect":
            status = "nondetect"
        elif numeric is not None and (not q or flag == "detected"):
            status = "detected"
        else:
            status = "pending_qc"
        xy = lnglat(f.get("longitude"), f.get("latitude"))
        ref = ctx.ref(r)
        refs = [ref]
        matches = sites.get(site, [])
        if not xy and matches:
            if len({tuple(p) for p, _ in matches}) == 1:
                xy = matches[0][0]
                refs.extend(p[1] for p in matches)
            else:
                ctx.gap("样点坐标对照存在冲突")
        grid_ids = []
        if xy:
            grid_ids = [
                gid for gid, (bb, _) in grids.items() if bb[0] <= xy[0] < bb[2] and bb[1] <= xy[1] < bb[3]
            ]
            if len(grid_ids) > 1:
                ctx.gap("覆盖网格重叠，重叠点需复核")
        key = (
            site,
            sample,
            dt.isoformat(),
            analyte,
            unit.casefold(),
            media,
            str(f.get("Analytical_Method", "")),
            raw,
            q,
            validation,
        )
        labs.append(
            {
                "site_id": site,
                "sample_id": sample,
                "date": dt.isoformat(),
                "medium": media,
                "analyte": analyte,
                "unit": unit,
                "value_text": raw,
                "numeric_value": numeric if status == "detected" else None,
                "detection_limit": number(f.get("Reporting_Limit", f.get("reporting_limit"))),
                "qualifier": q,
                "validation": validation,
                "status": status,
                "duplicate_result": key in seen,
                "coords": xy,
                "grid_ids": grid_ids,
                "refs": refs,
            }
        )
        seen.add(key)
    unmatched = [x["refs"][0] for x in labs if not x["coords"]]
    if not labs:
        ctx.gap("时间窗内无实验室结果记录")
    if unmatched:
        ctx.gap(f"{len(unmatched)}条结果缺少无歧义坐标，不能生成对应点位")
    if not grids:
        ctx.gap("缺少网格边界，不能声称区域覆盖")
    if any(r["qualifier"] and r["qualifier"] not in ctx.policy.get("qualifier_rules", {}) for r in labs):
        ctx.gap("缺少部分质控代码字典；保留原码与待核状态")
    pointgroups = defaultdict(list)
    for r in labs:
        if r["coords"]:
            pointgroups[(r["site_id"], *r["coords"])].append(r)
    points = [
        feature(
            {"type": "Point", "coordinates": [x, y]},
            {
                "site_id": site,
                "samples": sorted({r["sample_id"] for r in members}),
                "records": [r["refs"][0] for r in members],
            },
        )
        for (site, x, y), members in pointgroups.items()
    ]
    covered = defaultdict(set)
    for r in labs:
        for gid in r["grid_ids"]:
            covered[gid].add(r["sample_id"])
    return {
        "results": labs,
        "sample_points": collection(points),
        "coverage": [
            {
                "grid_id": gid,
                "sample_count": len(covered[gid]),
                "bbox": bb,
                "status": "sampled" if covered[gid] else "no_sampling_evidence",
                "refs": [ref],
            }
            for gid, (bb, ref) in grids.items()
        ],
        "quality": {
            "interval": [str(start), str(end)],
            "record_count": len(labs),
            "unique_samples": len({r["sample_id"] for r in labs}),
            "unique_sites": len({r["site_id"] for r in labs}),
            "excluded": dict(rejected),
            "status_counts": dict(Counter(r["status"] for r in labs)),
            "unit_conflicts": [
                {"analyte": k[0], "medium": k[1], "units": sorted(v)} for k, v in units.items() if len(v) > 1
            ],
            "missing_coordinate_refs": unmatched,
            "note": "不将低于检出限的结果记为零；未采样不表示没有污染。",
        },
    }


def timeline(ctx):
    requested_day = day(ctx.policy.get("date", "2022-01-15"))
    if not requested_day:
        raise ValueError("时间窗日期无效")
    islands = ctx.policy.get("islands", [])
    if not isinstance(islands, list) or not islands:
        ctx.gap("缺少指定的岛屿范围")
    current = []
    retros = []
    issue = []
    groups = defaultdict(list)
    markers = []
    for r in ctx.rows:
        f = r["fields"]
        if not f.get("kind") or f.get("kind") == "field_dictionary":
            continue
        rid = ctx.ref(r)
        obs = stamp(f.get("observed_at"))
        event = stamp(f.get("event_at"))
        island = str(f.get("island", ""))
        if not obs or not island:
            issue.append({"ref": rid, "reason": "missing_time_or_island"})
            continue
        typ = f["kind"]
        xy = lnglat(f.get("longitude"), f.get("latitude"))
        entry = {
            "type": typ,
            "observed_at": obs.isoformat(),
            "event_at": event.isoformat() if event else None,
            "island": island,
            "value": f.get("value"),
            "unit": f.get("unit"),
            "datum": f.get("datum"),
            "description": f.get("description", r.get("text")),
            "frame": f.get("frame", r.get("location", {}).get("frame")),
            "confidence": f.get("confidence"),
            "refs": [rid],
        }
        retrospective = typ == "survey" or (event is not None and obs.date() != event.date())
        if retrospective:
            if event and event.date() == requested_day:
                retros.append(entry)
            else:
                issue.append({"ref": rid, "reason": "survey_event_time_unknown_or_outside"})
            continue
        if obs.date() != requested_day:
            issue.append({"ref": rid, "reason": "outside_window"})
            continue
        if typ == "tide":
            if not f.get("datum") or number(f.get("value")) is None or not f.get("unit"):
                issue.append({"ref": rid, "reason": "unqualified_tide_reading"})
            elif number(f.get("baseline")) is not None:
                entry["anomaly"] = number(f["value"]) - number(f["baseline"])
        if typ == "frame" and entry["frame"] is None:
            issue.append({"ref": rid, "reason": "missing_frame"})
        if f.get("claim_id"):
            groups[str(f["claim_id"])].append((obs.isoformat(), island, rid))
        if xy:
            markers.append(
                feature(
                    {"type": "Point", "coordinates": xy},
                    {"island": island, "refs": [rid], "time": obs.isoformat()},
                )
            )
        current.append(entry)
    if issue:
        ctx.gap(f"{len(issue)}件观测存在时间、位置、数值或定位问题，详见逐件清单")
    current.sort(key=lambda x: x["observed_at"])
    retros.sort(key=lambda x: x["observed_at"])
    matrix = {}
    for name in sorted(set(islands) | {x["island"] for x in current + retros}):
        matrix[name] = {
            "observations": [x["refs"][0] for x in current if x["island"] == name],
            "surveys": [x["refs"][0] for x in retros if x["island"] == name],
        }
        if not any(matrix[name].values()):
            ctx.gap(f"{name}缺少可定位证据")
    if not current:
        ctx.gap("指定窗口没有当日观测")
    conflicts = [
        {"claim_id": cid, "observations": entries}
        for cid, entries in groups.items()
        if len({z[:2] for z in entries}) > 1
    ]
    if conflicts:
        ctx.gap("同一事实的时刻或地点有冲突")
    return {
        "timeline": current,
        "retrospective": retros,
        "island_matrix": matrix,
        "map": collection(markers),
        "uncertainty": {
            "issues": issue,
            "conflicts": conflicts,
            "scope": "潮位相对基准，卫星云团不可解释为波高。",
        },
    }
