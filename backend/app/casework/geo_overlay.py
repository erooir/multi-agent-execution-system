"""Metric overlays of supplied vectors, co-registered grids and spectral observations."""

import itertools
import operator
from collections import Counter, defaultdict
from functools import lru_cache, reduce
from math import dist

from pyproj import CRS, Transformer, network
from shapely import transform
from shapely.geometry import GeometryCollection, box, mapping, shape
from shapely.ops import unary_union

from .geo_common import collection, day, feature, number

network.set_network_enabled(False)


def _crs(value):
    if not str(value).upper().startswith("EPSG:") or not str(value)[5:].isdigit():
        raise ValueError("输入/量算坐标系必须显式指定EPSG代码")
    return CRS.from_user_input(value)


@lru_cache(maxsize=32)
def _tx(src, dst):
    return Transformer.from_crs(_crs(src), _crs(dst), always_xy=True)


def metric_crs(ctx):
    code = ctx.policy.get("metric_crs")
    crs = _crs(code)
    if (
        not crs.is_projected
        or not crs.axis_info
        or any(abs(axis.unit_conversion_factor - 1) > 1e-8 for axis in crs.axis_info[:2])
    ):
        raise ValueError("须指定使用米制轴的投影量算坐标系，不以经纬度平方当面积")
    return code


def project(geom, src, dst):
    return transform(geom, _tx(src, dst).transform, interleaved=False) if src != dst else geom


def layer(ctx, kind, crs, types=("Polygon", "MultiPolygon")):
    result = []
    for r in ctx.select(kind):
        f = r["fields"]
        ref = ctx.ref(r)
        g = shape(f.get("geometry", {}))
        if g.geom_type not in types or g.is_empty or not g.is_valid:
            ctx.gap(f"{kind}图层有不可用几何")
            continue
        geom = project(g, str(f.get("crs", "")), crs)
        if not geom.is_valid or not all(number(x) is not None for x in geom.bounds):
            ctx.gap(f"{kind}图层投影失败")
            continue
        result.append((f, geom, ref))
    return result


def union(items):
    items = list(items)
    return unary_union(items) if items else GeometryCollection()


def mapped(g, crs, properties):
    return feature(mapping(project(g, crs, "EPSG:4326")), properties)


def _grid(f, crs):
    values = f.get("values")
    if not isinstance(values, list) or not values or not isinstance(values[0], list) or not values[0]:
        raise ValueError("分类或波段网格必须为非空二维数组")
    w = len(values[0])
    h = len(values)
    if w * h > 250000 or any(not isinstance(row, list) or len(row) != w for row in values):
        raise ValueError("网格不规则或超出单块25万像元上限")
    x, y, dx, dy = [number(f.get(k)) for k in ("origin_x", "origin_y", "pixel_width", "pixel_height")]
    if any(n is None for n in (x, y, dx, dy)) or dx == 0 or dy == 0 or f.get("crs") != crs:
        raise ValueError("网格须给定统一投影坐标系、像元原点及有符号分辨率")
    mask = f.get("valid_mask")
    if mask is not None and (
        not isinstance(mask, list)
        or len(mask) != h
        or any(not isinstance(row, list) or len(row) != w for row in mask)
    ):
        raise ValueError("质量掩膜与影像网格不匹配")
    for i, row in enumerate(values):
        for j, v in enumerate(row):
            g = box(x + j * dx, y + i * dy, x + (j + 1) * dx, y + (i + 1) * dy)
            yield (i, j), g, None if mask is not None and mask[i][j] is not True else v


def flood(ctx):
    crs = metric_crs(ctx)
    aoi_items = layer(ctx, "aoi", crs)
    aoi = union(g for _, g, _ in aoi_items)
    if not aoi_items:
        ctx.gap("缺少分析范围，不能核对覆盖")
    positives = layer(ctx, "flood", crs)
    traces = layer(ctx, "trace", crs)
    cloudy = layer(ctx, "unknown", crs)
    permanent = layer(ctx, "permanent_water", crs)
    coverage = layer(ctx, "coverage", crs)
    roads = layer(ctx, "road", crs, ("LineString", "MultiLineString"))
    regions = layer(ctx, "region", crs)
    by = defaultdict(list)
    gridrefs = []
    dates = set()
    for r in ctx.select("water_grid"):
        f = r["fields"]
        phase = f.get("phase")
        gridrefs.append(ctx.ref(r))
        if phase not in ("before", "after", "permanent"):
            raise ValueError("水体网格需标明before/after/permanent")
        if phase == "after" and f.get("date"):
            dates.add(str(f["date"]))
        for _, g, v in _grid(f, crs):
            if isinstance(v, list) and len(v) == 2:
                green, nir = map(number, v)
                threshold = number(f.get("ndwi_threshold"))
                if threshold is None:
                    raise ValueError("双波段水体判别需要显式NDWI阈值")
                v = (
                    None
                    if green is None or nir is None or green + nir == 0
                    else ("water" if (green - nir) / (green + nir) >= threshold else "dry")
                )
            if v is None or v not in ("water", "dry", "trace"):
                by[(phase, "unknown")].append(g)
                continue
            by[(phase, "visible")].append(g)
            by[(phase, v)].append(g)
    flood_poly = union(g for _, g, _ in positives)
    trace_poly = union(g for _, g, _ in traces)
    known = union(g for _, g, _ in coverage + positives + traces)
    unknown = union(g for _, g, _ in cloudy)
    if by:
        before = union(by["before", "visible"])
        after = union(by["after", "visible"])
        if before.is_empty:
            ctx.gap("灾前可观测基线缺失，不能把全部灾后水体标作洪水")
        common = before.intersection(after)
        permanent_mask = union([*(g for _, g, _ in permanent), *by["permanent", "water"]])
        if not permanent and not by["permanent", "visible"]:
            ctx.gap("永久水体参照未提供")
        flood_poly = flood_poly.union(
            union(by["after", "water"])
            .intersection(common)
            .difference(union(by["before", "water"]))
            .difference(permanent_mask)
        )
        trace_poly = trace_poly.union(union(by["after", "trace"]).intersection(common))
        known = known.union(common)
        unknown = unknown.union(union(by["after", "unknown"] + by["before", "unknown"]))
    for f, _, _ in positives + traces:
        if f.get("date"):
            dates.add(str(f["date"]))
    if not dates:
        ctx.gap("缺少灾后观测日期")
    if len(dates) > 1:
        ctx.gap("多期灾后数据已合并，仅表示观测并集，非单时点快照")
    if not positives and not traces and not gridrefs:
        ctx.gap("缺少洪水矢量或配准的灾前/灾后水体网格")
    known = known.difference(unknown)
    if not aoi.is_empty:
        known = known.intersection(aoi)
        unknown = aoi.difference(known)
    if not unknown.is_empty:
        ctx.gap("存在未观测/云遮挡范围，面积和暴露仅限有证据区域")
    flood_poly = flood_poly.intersection(known)
    trace_poly = trace_poly.intersection(known).difference(flood_poly)
    combined = flood_poly.union(trace_poly)
    refs = gridrefs + [rr for _, _, rr in positives + traces + permanent + coverage + cloudy + aoi_items]
    roads_out = []
    road_features = []
    for f, g, rr in roads:
        cut = g.intersection(combined)
        visible = g.intersection(known)
        row = {
            "road_id": f.get("road_id"),
            "class": f.get("class"),
            "observed_length_m": visible.length,
            "potential_exposure_m": cut.length,
            "observation_dates": sorted(dates),
            "refs": [rr, *refs],
        }
        roads_out.append(row)
        if cut.length > 0:
            road_features.append(mapped(cut, crs, row))
    if not roads:
        ctx.gap("未提供民用道路线图层")
    if not regions:
        ctx.gap("缺少分区图层")
    areas = [
        {
            "region": f.get("region"),
            "flood_m2": g.intersection(flood_poly).area,
            "trace_m2": g.intersection(trace_poly).area,
            "unobserved_m2": g.intersection(unknown).area,
            "refs": [rr, *refs],
        }
        for f, g, rr in regions
    ]
    polygons = [
        mapped(g, crs, {"class": name, "refs": refs})
        for name, g in (("flood", flood_poly), ("trace", trace_poly), ("unobserved", unknown))
        if not g.is_empty
    ]
    return {
        "flood_layer": collection(polygons),
        "road_exposure": {"records": roads_out, "segments": collection(road_features)},
        "areas": areas,
        "coverage": {
            "metric_crs": crs,
            "aoi_m2": aoi.area,
            "observed_m2": known.area,
            "unknown_m2": unknown.area,
            "method": "supplied_flood_product" if not gridrefs else "registered_cell_change",
            "scope": "道路相交仅表示潜在暴露，不表示不可通行。",
        },
    }


def _class(value, samples, limit):
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value.strip() or "unknown"
    if isinstance(value, list):
        bands = [number(v) for v in value]
        if any(v is None for v in bands) or not bands or limit is None:
            return "unknown"
        ds = sorted(
            (dist(bands, signature), name) for name, signature in samples if len(signature) == len(bands)
        )
        if ds and ds[0][0] <= limit and (len(ds) == 1 or ds[0][0] != ds[1][0] or ds[0][1] == ds[1][1]):
            return ds[0][1]
    return "unknown"


def cover(ctx):
    crs = metric_crs(ctx)
    frames = {}
    signature = None
    raw_refs = {}
    samples = []
    for r in ctx.select("signature"):
        f = r["fields"]
        bands = f.get("bands")
        if not f.get("class") or not isinstance(bands, list) or any(number(n) is None for n in bands):
            raise ValueError("分类样本缺少有效波段")
        samples.append((str(f["class"]), list(map(float, bands))))
        ctx.ref(r)
    for r in ctx.select("cover_grid"):
        f = r["fields"]
        dt = str(f.get("date", ""))
        grid = (
            f.get("crs"),
            f.get("origin_x"),
            f.get("origin_y"),
            f.get("pixel_width"),
            f.get("pixel_height"),
            len(f.get("values", [])),
            len(f["values"][0]) if f.get("values") else 0,
        )
        if not day(dt) or dt in frames:
            raise ValueError("土地覆盖日期缺失或时相重复")
        if signature and signature != grid:
            raise ValueError("跨期格网未配准；拒绝按数组下标比较不同地块")
        signature = grid
        raw_refs[dt] = ctx.ref(r)
        frames[dt] = {
            ij: (_class(v, samples, number(f.get("max_spectral_distance"))), g) for ij, g, v in _grid(f, crs)
        }
    dates = sorted(frames)
    if len(dates) != 3:
        ctx.gap("需要三期同尺度、同范围的配准网格")
    validation = defaultdict(list)
    for r in ctx.select("validation_pixel"):
        f = r["fields"]
        dt = str(f.get("date", ""))
        ij = (f.get("row"), f.get("column"))
        expected = f.get("class")
        if not expected or dt not in frames or ij not in frames[dt]:
            ctx.gap("验证样本与已定位时相/像元不匹配")
            continue
        actual = frames[dt][ij][0]
        validation[dt].append(
            {
                "row": ij[0],
                "column": ij[1],
                "reference": expected,
                "classified": actual,
                "matches": actual == expected,
                "refs": [raw_refs[dt], ctx.ref(r)],
            }
        )
    if any(not validation[d] for d in dates):
        ctx.gap("部分时相缺少独立类别验证样本，不能声称分类精度")
    areas = []
    transitions = []
    changes = []
    regions = []
    aoi_items = layer(ctx, "aoi", crs)
    aoi = union(g for _, g, _ in aoi_items)
    if aoi.is_empty:
        ctx.gap("未提供AOI几何；仅统计给定共同网格范围")
    for dt, frame in frames.items():
        acc = Counter()
        geometries = defaultdict(list)
        for cls, g in frame.values():
            cell = g if aoi.is_empty else g.intersection(aoi)
            if cell.area <= 0:
                continue
            acc[cls] += cell.area
            geometries[cls].append(cell)
        areas.append(
            {"date": dt, "by_class_m2": dict(acc), "total_m2": sum(acc.values()), "refs": [raw_refs[dt]]}
        )
        if not aoi.is_empty and abs(aoi.area - sum(acc.values())) > max(1e-6, aoi.area * 1e-8):
            ctx.gap("网格未完整覆盖AOI或面积未闭合")
        regions.extend(
            mapped(union(gs), crs, {"class": cls, "date": dt, "refs": [raw_refs[dt]]})
            for cls, gs in geometries.items()
        )
    for a, b in itertools.pairwise(dates):
        matrix = Counter()
        built = []
        for ij, (cls, g) in frames[a].items():
            nxt = frames[b][ij][0]
            cell = g if aoi.is_empty else g.intersection(aoi)
            if cell.area <= 0:
                continue
            matrix[(cls, nxt)] += cell.area
            if cls not in ("unknown", "built", "建设用地", "road") and nxt in {"built", "建设用地", "road"}:
                built.append(cell)
        transitions.extend(
            {
                "from_date": a,
                "to_date": b,
                "from_class": k,
                "to_class": l,
                "area_m2": v,
                "refs": [raw_refs[a], raw_refs[b]],
            }
            for (k, l), v in matrix.items()
        )
        if built:
            changes.append(
                mapped(
                    union(built),
                    crs,
                    {"period": [a, b], "class": "new_built_candidate", "refs": [raw_refs[a], raw_refs[b]]},
                )
            )
    return {
        "land_cover": collection(regions),
        "areas": areas,
        "transitions": transitions,
        "new_built": collection(changes),
        "method": {
            "metric_crs": crs,
            "validation": [
                {
                    "date": dt,
                    "n": len(validation[dt]),
                    "agreement": sum(x["matches"] for x in validation[dt]) / len(validation[dt])
                    if validation[dt]
                    else None,
                    "samples": validation[dt],
                }
                for dt in dates
            ],
            "classification": "给定类别或明确阈值的最近波谱样本；缺测像元为unknown",
            "limitation": "不同季节及传感器可能产生分类差异；建设用地不代表新增道路或工程验收。",
        },
    }


def agriculture(ctx):
    crs = metric_crs(ctx)
    districts = layer(ctx, "district", crs)
    crops = layer(ctx, "crop", crs)
    wet = layer(ctx, "flood", crs)
    coverage = layer(ctx, "coverage", crs)
    cells = layer(ctx, "grid", crs)
    if not districts or not crops or not wet:
        ctx.gap("缺少县界、耕地/作物或实测洪水面")
    if len(districts) != int(ctx.policy.get("expected_districts", 3)):
        ctx.gap("县清册数量与请求范围不一致")
    start = day(ctx.policy.get("start", "2022-08-01"))
    end = day(ctx.policy.get("end", "2022-09-15"))
    if not start or not end or start > end:
        raise ValueError("农田观测时间窗无效")
    by_date = defaultdict(list)
    cover_date = defaultdict(list)
    date_refs = defaultdict(list)
    for data, container in ((wet, by_date), (coverage, cover_date)):
        for f, g, rr in data:
            dt = day(f.get("date"))
            if not dt:
                ctx.gap("观测面缺少日期")
                continue
            if start <= dt <= end:
                container[str(dt)].append(g)
                date_refs[str(dt)].append(rr)
    ds = sorted(set(by_date) | set(cover_date))
    if len(ds) * max(len(districts), 1) * max(len(cells), 1) * max(len(crops), 1) > 1_000_000:
        raise ValueError("单块空间叠加规模超限，须按有界AOI分区")
    if len(ds) < 3:
        ctx.gap("缺少时窗内的多期观测")
    crop_groups = defaultdict(list)
    crop_refs = defaultdict(list)
    for f, g, rr in crops:
        name = str(f.get("crop", ""))
        if not name:
            ctx.gap("耕地缺少作物类别")
            continue
        crop_groups[name].append(g)
        crop_refs[name].append(rr)
        if not f.get("year") or not f.get("accuracy"):
            ctx.gap("耕地分类缺少年份或精度说明")
    land = {k: union(v) for k, v in crop_groups.items()}
    if sum(g.area for g in land.values()) - union(land.values()).area > max(
        1e-6, union(land.values()).area * 1e-8
    ):
        raise ValueError("不同作物面重叠；拒绝计算可能重复的面积")
    wet_geom = {dt: union(by_date[dt]) for dt in ds}
    obs_geom = {dt: union(cover_date[dt]) for dt in ds}
    if not cells:
        ctx.gap("缺少分析网格，未输出网格分摊")
    if any(g.is_empty for g in obs_geom.values()):
        ctx.gap("部分时相无完整观测足迹，不能将面外地区认定未淹")
    for dt in ds:
        if not obs_geom[dt].is_empty and not obs_geom[dt].covers(wet_geom[dt]):
            raise ValueError("洪水多边形超出声明的观测足迹")
    overall = union(wet_geom.values())
    previously_seen = GeometryCollection()
    repeated = GeometryCollection()
    for dt in ds:
        repeated = repeated.union(previously_seen.intersection(wet_geom[dt]))
        previously_seen = previously_seen.union(wet_geom[dt])
    for name, items in (("县界", districts), ("网格", cells)):
        shapes = [g for _, g, _ in items]
        total = sum(g.area for g in shapes)
        if total - union(shapes).area > max(1e-5, total * 1e-8):
            raise ValueError(f"{name}相互重叠，拒绝重复分摊")
    all_refs = list(dict.fromkeys(rr for v in date_refs.values() for rr in v))
    tiers = ctx.policy.get("priority_thresholds", [])
    if not tiers:
        ctx.gap("缺少援助候选区域分级规则，不自行确定优先级")
    levels = []
    for tier in tiers:
        bound = number(tier.get("min_exposed_fraction"))
        if bound is None or not 0 <= bound <= 1 or not tier.get("label"):
            raise ValueError("援助分级阈值无效")
        levels.append((bound, str(tier["label"])))
    levels.sort(reverse=True)
    rows = []
    maximum = []
    priority = []
    diffs = []
    gridrows = []
    for f, d, rr in districts:
        name = str(f.get("district", ""))
        crop_land = union(g.intersection(d) for g in land.values())
        max_area = overall.intersection(crop_land).area
        fraction = max_area / crop_land.area if crop_land.area else None
        complete = bool(ds) and all(obs_geom[dt].covers(crop_land) for dt in ds)
        label = (
            next((label for bound, label in levels if fraction is not None and fraction >= bound), None)
            if complete
            else None
        )
        priority.append(
            {
                "district": name,
                "candidate_tier": label,
                "max_union_exposed_m2": max_area,
                "repeated_exposure_m2": repeated.intersection(crop_land).area,
                "crop_area_m2": crop_land.area,
                "exposed_fraction": fraction,
                "coverage_complete": complete,
                "refs": [rr, *all_refs],
            }
        )
        if not complete:
            ctx.gap("部分县域观测不完整，仅给已观测暴露下限，未据此确定优先级")
        for dt in ds:
            for crop, g in land.items():
                local = g.intersection(d)
                affected = local.intersection(wet_geom[dt])
                known = local.intersection(obs_geom[dt])
                rows.append(
                    {
                        "district": name,
                        "crop": crop,
                        "date": dt,
                        "crop_area_m2": local.area,
                        "exposed_m2": affected.area,
                        "covered_m2": known.area if not obs_geom[dt].is_empty else None,
                        "fraction": affected.area / local.area if local.area else None,
                        "refs": [rr, *crop_refs[crop], *date_refs[dt]],
                    }
                )
                for cf, cell, cref in cells:
                    part = local.intersection(cell)
                    if part.area > 0:
                        gridrows.append(
                            {
                                "district": name,
                                "grid_id": cf.get("grid_id"),
                                "crop": crop,
                                "date": dt,
                                "exposed_m2": affected.intersection(cell).area,
                                "crop_area_m2": part.area,
                                "refs": [rr, cref, *date_refs[dt], *crop_refs[crop]],
                            }
                        )
        if max_area > 0:
            maximum.append(
                mapped(
                    overall.intersection(crop_land),
                    crs,
                    {"district": name, "area_m2": max_area, "refs": [rr, *all_refs]},
                )
            )
        official = number(f.get("official_crop_area_m2"))
        if official is not None:
            diffs.append(
                {
                    "district": name,
                    "classified_minus_reported_m2": crop_land.area - official,
                    "refs": [rr, *reduce(operator.iadd, crop_refs.values(), [])],
                }
            )
        else:
            ctx.gap("缺少县级上游统计或口径，统计差异无法复核")
    return {
        "exposure": rows,
        "grid_exposure": gridrows,
        "max_union": collection(maximum),
        "repeat_coverage": collection(
            [
                mapped(
                    repeated.intersection(union(land.values())),
                    crs,
                    {"class": "observed_flood_at_least_twice", "refs": all_refs},
                )
            ]
            if not repeated.is_empty
            else []
        ),
        "priority": priority,
        "method": {
            "metric_crs": crs,
            "dates": ds,
            "statistical_differences": diffs,
            "thresholds": tiers,
            "scope": "暴露为潜在受淹，不推算产量或经济损失；最大范围采用多期空间并集。",
        },
    }
