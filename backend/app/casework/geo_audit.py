"""Independent consistency gates for candidate products; never repair a failed check by assigning success."""

from collections import Counter, defaultdict
from datetime import timedelta
from math import isclose
from statistics import fmean

from .geo_common import day, lnglat, number, stamp


def near(a, b):
    return (
        number(a) is not None
        and number(b) is not None
        and isclose(float(a), float(b), rel_tol=1e-7, abs_tol=1e-5)
    )


def _lab_review(products, records, check):
    """Reconcile every reported measurement with an eligible, specific frozen row (not another result)."""
    quality = products["quality"]
    output = products["results"]
    policies = {}
    aliases = defaultdict(set)
    for row in records:
        fields = row["fields"]
        if fields.get("kind") == "policy":
            policies.update(fields)
        if fields.get("kind") == "field_dictionary" and isinstance(fields.get("aliases"), dict):
            for source_key, target_key in fields["aliases"].items():
                if isinstance(target_key, str) and target_key:
                    aliases[row["fields"].get("target_source_id"), source_key].add(target_key)
    start, end = day(policies.get("start", "2023-02-03")), day(policies.get("end", "2023-05-31"))
    rules = policies.get("qualifier_rules", {})
    if not start or not end or start > end or not isinstance(rules, dict):
        check("lab_policy", False, "无法从冻结资料核对采样时段或质控规则")
        return

    rows = {}
    sites = defaultdict(list)
    required = set()
    for i, source in enumerate(records, 1):
        original = source["fields"]
        if original.get("kind") in {"policy", "field_dictionary"}:
            continue
        fields = dict(original)
        for key, value in original.items():
            targets = aliases.get((source["source_id"], key), set())
            if len(targets) == 1:
                fields.setdefault(next(iter(targets)), value)
        rid = f"g{i}"
        if fields.get("kind") == "site":
            xy = lnglat(fields.get("longitude"), fields.get("latitude"))
            if xy:
                sites[str(fields.get("site_id", ""))].append((rid, xy))
        if "Samp_No" not in fields and "sample_id" not in fields:
            continue
        dt = day(fields.get("SampleDate_txt", fields.get("date")))
        if dt and start <= dt <= end:
            rows[rid] = (fields, dt.isoformat())
            # Dictionary-only fields can first appear during a bounded revision.
            original_date = day(original.get("SampleDate_txt", original.get("date")))
            if (
                ("Samp_No" in original or "sample_id" in original)
                and original_date
                and start <= original_date <= end
            ):
                required.add(rid)

    good_fields = good_qc = good_sources = True
    cited = []

    def field(source, epa, normalized):
        return str(source.get(epa, source.get(normalized, "")))

    for result in output:
        refs = result.get("evidence_ids")
        if not isinstance(refs, list) or not refs or not isinstance(refs[0], str) or refs[0] not in rows:
            good_sources = False
            continue
        rid = refs[0]
        cited.append(rid)
        f, iso_date = rows[rid]

        site = field(f, "Location", "site_id")
        raw = field(f, "Result_Final_Txt", "value").strip()
        qual = field(f, "Result_Qualifier_Final", "qualifier").strip()
        validation = field(f, "Validation_Level", "validation")
        facts = {
            "site_id": site,
            "sample_id": field(f, "Samp_No", "sample_id"),
            "date": iso_date,
            "medium": field(f, "SampleMedia", "medium"),
            "analyte": field(f, "Analyte", "analyte"),
            "unit": field(f, "Result_Units", "unit").strip(),
            "value_text": raw,
            "qualifier": qual,
            "validation": validation,
        }
        good_fields &= all(result.get(key) == val for key, val in facts.items())
        flag, measurement = rules.get(qual), number(raw)
        status = "pending_qc"
        if validation.casefold() in {"final", "validated", "最终", "已审核"} and (
            flag not in {"rejected", "pending"} and (not qual or flag is not None)
        ):
            if raw.startswith("<") or flag == "nondetect":
                status = "nondetect"
            elif measurement is not None and (not qual or flag == "detected"):
                status = "detected"
        expected_numeric = measurement if status == "detected" else None
        limit = number(f.get("Reporting_Limit", f.get("reporting_limit")))
        good_qc &= result.get("status") == status and all(
            key in result
            and (
                result[key] is None
                if val is None
                else type(result[key]) in (int, float) and result[key] == val
            )
            for key, val in (("numeric_value", expected_numeric), ("detection_limit", limit))
        )
        xy = lnglat(f.get("longitude"), f.get("latitude"))
        site_refs = []
        if not xy and site in sites and len({tuple(pt) for _, pt in sites[site]}) == 1:
            xy = sites[site][0][1]
            site_refs = [sid for sid, _ in sites[site]]
        good_sources &= refs[1:] == site_refs and result.get("coords") == xy
    check(
        "lab_source_rows",
        good_sources and len(cited) == len(set(cited)) and required <= set(cited),
        "每条窗口内实验记录须恰好引用自己的冻结行；补充坐标须来自同站点一致的坐标记录",
    )
    check(
        "lab_source_fields", good_fields, "样品、站点、日期、介质、分析物、单位和原始报告值须与所引冻结行一致"
    )
    check("lab_source_qc", good_qc, "报告限、限定符、审核级别和可用浓度须按冻结来源及其质控规则复核")
    check(
        "lab_interval",
        quality.get("interval") == [start.isoformat(), end.isoformat()],
        "结果时间窗必须与冻结采样策略一致",
    )


def audit_products(case_id, products, records):
    detail = []

    def check(name, passed, message):
        detail.append({"name": "geo_" + name, "passed": bool(passed), "message": message, "hard": True})

    # Missing observation inputs can yield a useful context note, but no full spatial result.
    def has_data(value):
        if isinstance(value, dict) and value.get("type") == "FeatureCollection":
            return bool(value.get("features"))
        return bool(value) or type(value) in (int, float)

    if not isinstance(products, dict) or not any(
        has_data(v) for k, v in products.items() if k not in {"source_notes", "map_view"}
    ):
        detail.append(
            {
                "name": "geo_products_unavailable",
                "passed": False,
                "hard": False,
                "message": "没有可供独立复算的领域产品，背景资料不能代替观测结果",
            }
        )
        return detail
    p = products
    try:
        if case_id == "G01":
            rows = p["hotspots"]
            fids = [f["properties"]["id"] for f in p["points"]["features"]]
            check(
                "point_inventory",
                len(set(fids)) == len(rows) == len(fids)
                and set(fids) == {r["id"] for r in rows}
                and sum(d["count"] for d in p["daily"]) == len(rows),
                "点图层、逐项ID和每日统计应守恒",
            )
            confirmed = {
                day(r["fields"].get("date"))
                for r in records
                if r["fields"].get("kind") == "batch" and r["fields"].get("complete") is True
            }
            check(
                "prior_batch",
                all(
                    stamp(r["observed_at"]).date() - timedelta(days=1) in confirmed
                    for r in rows
                    if r["match"] == "new"
                ),
                "缺少完整前日不能判定新增",
            )
        elif case_id == "G02":
            panels = {r["image_id"]: r for r in p["panels"]}
            check(
                "pixel_boxes",
                all(
                    _inside(x["bbox"], panels[x["image_id"]]["width"], panels[x["image_id"]]["height"])
                    and x["date"] == panels[x["image_id"]]["date"]
                    for x in p["changes"]
                ),
                "变更注记须定位到同日期来源图像的有效像素范围",
            )
        elif case_id == "G03":
            data = p["annual_series"]
            s = p["anomaly"]
            a, b = s["baseline_years"]
            values = [r["extent"] for r in data if r["extent"] is not None]
            base = [r["extent"] for r in data if a <= r["year"] <= b and r["extent"] is not None]
            if s["baseline_mean"] is not None:
                check(
                    "replay_baseline",
                    len(base) == b - a + 1 and near(fmean(base), s["baseline_mean"]),
                    "独立重算基线均值",
                )
            target = s["target_extent"]
            if s["difference"] is not None:
                check(
                    "replay_anomaly",
                    near(target - s["baseline_mean"], s["difference"])
                    and (
                        not s["baseline_mean"] or near((target / s["baseline_mean"] - 1) * 100, s["percent"])
                    ),
                    "异常值及百分比与年度数据一致",
                )
            if s["ascending_rank"] is not None:
                check(
                    "replay_rank",
                    len(values) == len(data) and s["ascending_rank"] == 1 + sum(v < target for v in values),
                    "年度排名只使用完整窗口内同口径值",
                )
            # Compare numeric values with the original rows, even if all derived summaries were tampered consistently.
            defs = {}
            for src in records:
                f = src["fields"]
                if f.get("kind") == "field_dictionary":
                    defs.setdefault(f.get("target_source_id"), set()).update(
                        k for k, v in f.get("aliases", {}).items() if v == "extent"
                    )
            valid = True
            for row in data:
                if row["extent"] is None:
                    continue
                matched = False
                for rid in row["evidence_ids"]:
                    src = records[int(rid[1:]) - 1]
                    f = src["fields"]
                    matched |= number(f.get("year")) == row["year"] and any(
                        near(f.get(col), row["extent"])
                        for col in {"extent", *defs.get(src["source_id"], set())}
                    )
                valid &= matched
            check("source_extent", valid, "每个范围数值必须与原记录或明示字段映射一致")
        elif case_id == "G04":
            cov = p["coverage"]
            if cov["aoi_m2"]:
                check(
                    "aoi_closure",
                    near(cov["observed_m2"] + cov["unknown_m2"], cov["aoi_m2"]),
                    "观测与不可判覆盖面积应闭合到AOI",
                )
            check(
                "road_bound",
                all(
                    -1e-8 <= r["potential_exposure_m"] <= r["observed_length_m"] + 1e-5
                    for r in p["road_exposure"]["records"]
                ),
                "暴露长度不得超过可观测道路长度",
            )
        elif case_id == "G05":
            rows = p["buildings"]
            actual = Counter((str(r["block"] or "unassigned"), r["status"]) for r in rows)
            agg = {(block, s): n for block, v in p["blocks"].items() for s, n in v.items()}
            check(
                "buildings_balance",
                len({r["building_id"] for r in rows}) == len(rows) and dict(actual) == agg,
                "建筑清册与街区汇总必须一致",
            )
            check(
                "new_visible",
                all(
                    r["before"] == "intact" and r["after"] in {"damaged", "destroyed"}
                    for r in rows
                    if r["status"] == "new_visible"
                ),
                "缺少灾前完好证据不能确认为新增",
            )
        elif case_id == "G06":
            grouped = Counter()
            for r in p["transitions"]:
                grouped[r["from_date"], r["to_date"]] += r["area_m2"]
            areas = {r["date"]: r["total_m2"] for r in p["areas"]}
            check(
                "transition_closure",
                all(near(sum(r["by_class_m2"].values()), r["total_m2"]) for r in p["areas"])
                and all(near(n, areas[a]) and near(n, areas[b]) for (a, b), n in grouped.items()),
                "转移矩阵面积须同时闭合到两个时相分类面积",
            )
        elif case_id == "G07":
            rows = p["results"]
            q = p["quality"]
            check(
                "sampling_counts",
                len(rows) == q["record_count"]
                and len({r["sample_id"] for r in rows}) == q["unique_samples"]
                and len({r["site_id"] for r in rows}) == q["unique_sites"]
                and Counter(r["status"] for r in rows) == q["status_counts"],
                "实验室记录、样品、判定状态统计必须分别一致",
            )
            check(
                "censoring",
                all(
                    r["numeric_value"] is None
                    for r in rows
                    if r["status"] != "detected" or str(r["value_text"]).startswith("<")
                ),
                "未检出或待质控结果不得作为实测浓度",
            )
            _lab_review(p, records, check)
        elif case_id == "G08":
            stamps = [stamp(r["observed_at"]) for r in p["timeline"]]
            check(
                "event_order",
                all(stamps)
                and stamps == sorted(stamps)
                and all(r["type"] != "survey" for r in p["timeline"]),
                "UTC时序须单调，调查记录与当日观察分列",
            )
        elif case_id == "G09":
            invalid = {(r["od_id"], r["edge_id"]) for r in p["exclusions"]}
            edge_map = {
                str(r["fields"]["edge_id"]): r["fields"] for r in records if r["fields"].get("kind") == "edge"
            }
            good = True
            length_ok = True
            for row in p["od_comparison"]:
                paths = row["candidates"]
                good &= len(paths) <= 2 and (bool(paths) == (row["status"] == "reachable"))
                for path in paths:
                    ids = path["edge_ids"]
                    curr = row["from"]
                    speed_mins = 0
                    known = True
                    visited = {curr}
                    for eid in ids:
                        e = edge_map[eid]
                        good &= (row["od_id"], eid) not in invalid
                        if curr == e["from"]:
                            curr = e["to"]
                        elif not e.get("oneway", True) and curr == e["to"]:
                            curr = e["from"]
                        else:
                            good = False
                        if curr in visited:
                            good = False
                        visited.add(curr)
                        speed = number(e.get("speed_kph"))
                        dist = number(e["distance_km"])
                        if speed and speed > 0:
                            speed_mins += 60 * dist / speed
                        else:
                            known = False
                    good &= curr == row["to"]
                    length_ok &= near(
                        sum(number(edge_map[e]["distance_km"]) for e in ids), path["distance_km"]
                    )
                    length_ok &= (
                        near(speed_mins, path["estimated_time_min"])
                        if known
                        else path["estimated_time_min"] is None
                    )
            check("admissible_routes", good, "路径需连通、无环且不含封闭、受限或状态未知边")
            check("route_cost", length_ok, "路线距离和有数据的估计时间必须可从边表重算")
        elif case_id == "G10":
            dates = Counter()
            for r in p["exposure"]:
                dates[r["district"], r["date"]] += r["exposed_m2"]
            within = True
            for r in p["priority"]:
                bounds = [n for (d, _), n in dates.items() if d == r["district"]]
                m = r["max_union_exposed_m2"]
                within &= (
                    m <= r["crop_area_m2"] + 1e-5 and max(bounds, default=0) <= m + 1e-5 <= sum(bounds) + 2e-5
                )
                if not r["coverage_complete"] and r["candidate_tier"] is not None:
                    within = False
            check(
                "union_area",
                within,
                "跨期并集面积应介于最大单期与逐期总和之间，且不超过耕地面积；缺覆盖不得分级",
            )
    except (IndexError, TypeError, ValueError, KeyError, AttributeError, OverflowError):
        check("invariant_inputs", False, "候选产品缺少独立复算所需字段或记录定位不匹配")
    return detail


def _inside(bbox, w, h):
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        return False
    x, y, dx, dy = map(float, bbox)
    return 0 <= x < x + dx <= float(w) and 0 <= y < y + dy <= float(h)
