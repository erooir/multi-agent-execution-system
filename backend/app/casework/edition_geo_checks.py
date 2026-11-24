"""Reproduce edition metrics from the frozen public files, without consulting cached attachments."""

import csv
import io
import json
import math
import re
import statistics
import struct
import zipfile
from collections import Counter, defaultdict

import numpy as np
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pyproj import Transformer
from shapefile import ShapefileException
from shapely import STRtree, points
from shapely.errors import ShapelyError


def _source(sources, suffix):
    matches = [s for s in sources if s["filename"].lower().endswith(suffix.lower())]
    if len(matches) != 1:
        raise ValueError(f"所需原件 {suffix} 的匹配数量不唯一")
    return matches[0]


def _csv(source):
    return list(csv.DictReader(io.StringIO(source["read"]().decode("utf-8-sig"))))


def _check(name, passed, message):
    return {"name": name, "passed": bool(passed), "message": message, "hard": True}


def _hotspots(sources, products):
    rows = _csv(_source(sources, ".csv"))
    groups = defaultdict(list)
    seen = set()
    for r in rows:
        lon, lat = float(r["lon"]), float(r["lat"])
        key = r["rep_date"], r["sensor"], r["satellite"], lon, lat
        if key in seen or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        seen.add(key)
        groups[r["rep_date"][:10]].append((lon, lat))
    transform = Transformer.from_crs(4326, 32198, always_xy=True)
    previous = None
    computed = []
    for date, coordinates in sorted(groups.items()):
        x, y = transform.transform(*zip(*coordinates, strict=True))
        current = points(x, y)
        if previous is not None:
            matches = STRtree(previous).query(current, predicate="dwithin", distance=500)
            matched = len(np.unique(matches[0]))
            computed.append(
                {
                    "date": date,
                    "total": len(current),
                    "new": len(current) - matched,
                    "matched": matched,
                    "previous": len(previous),
                }
            )
        previous = current
    return [
        _check(
            "geo_daily_hotspots",
            computed == products["日统计"] and len(computed) == 7,
            f"从 {len(rows)} 条原始 CSV 独立去重、投影并以 500 米查询前日近邻，核对各日新增、匹配和基线计数。",
        )
    ]


def _panels(sources, products):
    panels = products["三期观察"]
    dates = set()
    valid = True
    for rec in panels:
        date = rec["date"]
        owner = _source(sources, rec["source"])
        with Image.open(io.BytesIO(owner["read"]())) as im:
            bounds = rec["bbox"]
            valid &= bool(bounds)
            for left, top, right, bottom in bounds:
                if not (0 <= left < right <= im.width and 0 <= top < bottom <= im.height):
                    valid = False
                    continue
                low, high = im.crop((left, top, right, bottom)).convert("L").getextrema()
                valid &= high > low
        dates.add(date)
    return [
        _check(
            "geo_image_regions",
            valid and len(panels) == 3 and len(dates) == 3,
            "核对三个日期的源图文件、像素标注边界及框内具有实际像素差异。图示语义另由图件和模型审核。",
        )
    ]


def _ice(sources, products):
    workbook = load_workbook(io.BytesIO(_source(sources, ".xlsx")["read"]()), read_only=True, data_only=True)
    try:
        cells = list(workbook["September-NH"].values)
    finally:
        workbook.close()
    header = next(list(r) for r in cells if "extent" in r and "year" in r)
    yi, mi, hi, vi = (header.index(k) for k in ["year", "month", "hemisphere", "extent"])
    series = [
        {"year": r[yi], "extent_mkm2": r[vi], "excel_row": index}
        for index, r in enumerate(cells, 1)
        if type(r[yi]) is int and 1979 <= r[yi] <= 2023 and r[mi] == 9 and r[hi] == "N"
    ]
    values = {r["year"]: r["extent_mkm2"] for r in series}
    baseline = statistics.mean(values[y] for y in range(1981, 2011))
    observed = values[2023]
    delta = observed - baseline
    calculated = {
        "baseline": round(baseline, 6),
        "observed": observed,
        "difference": round(delta, 6),
        "percent": round(100 * delta / baseline, 2),
        "rank": 1 + sum(v < observed for v in values.values()),
    }
    return [
        _check(
            "geo_annual_series",
            len(series) == 45 and series == products["年度范围（百万平方公里）"],
            "从 September-NH 逐行核对北半球 1979—2023 年 9 月平均范围、单位和原始 Excel 行号。",
        ),
        _check(
            "geo_baseline_anomaly",
            calculated == products["异常统计"],
            f"从工作簿重算 30 年基线 {baseline:.6f}、2023 年 {observed:g} 百万平方公里，并核对差值、百分比和升序排名。",
        ),
    ]


def _sampling(sources, products):
    rows = _csv(_source(sources, ".csv"))
    geo = next(x for x in sources if "D01" in x["filename"])
    features = json.loads(geo["read"]())["features"]
    coords = {f["properties"]["Samp_No"]: f["geometry"]["coordinates"] for f in features}
    statuses = Counter()
    samples = Counter()
    grids = defaultdict(set)
    tr = Transformer.from_crs(4326, 32617, always_xy=True)
    for r in rows:
        sid = r["Samp_No"]
        samples[sid] += 1
        if r["Validation_Level"] != "Final":
            status = "待质控"
        elif "U" in r["Result_Qualifier_Final"].upper() or r["Result_Final_Txt"].startswith("<"):
            status = "未检出"
        elif r["Result_Units"].lower() in {"as noted", "% of sample"}:
            status = "其他实验室指标"
        else:
            status = "检出"
        statuses[status] += 1
        if sid in coords:
            x, y = tr.transform(*coords[sid][:2])
            grids[f"{int(x // 1000)}:{int(y // 1000)}"].add(sid)
    reported = {r["sample"]: r["n"] for r in products["样品分组"]}
    coverage = {r["grid"]: set(r["samples"]) for r in products["格网样品覆盖"]}
    return [
        _check(
            "geo_laboratory_qualifiers",
            dict(statuses) == products["实验室记录"] and dict(samples) == reported,
            f"从 {len(rows)} 条化验记录和检测限定符重新分组，核对检出、未检出、其他指标与逐样品数量。",
        ),
        _check(
            "geo_sampling_grid",
            dict(grids) == coverage and set(samples) <= set(coords),
            "按原始采样编号连接坐标，以 UTM 17N 重新计算 1 公里格网和每格样品集合。",
        ),
    ]


def _tides(sources, products):
    actual = []
    with zipfile.ZipFile(io.BytesIO(_source(sources, ".zip")["read"]())) as archive:
        for site in json.loads(archive.read("stations.json"))["stations"]:
            data = []
            for l in archive.read(site["member"]).decode("ascii").splitlines():
                r = l.split()
                if tuple(map(int, r[1:4])) != (2022, 1, 15):
                    raise ValueError("日期超出事件窗")
                level = float(r[9])
                if level != 9999 and math.isfinite(level):
                    data.append((f"{int(r[4]):02d}:{int(r[5]):02d}", level))
            peak = max(data, key=lambda pair: pair[1])
            trough = min(data, key=lambda pair: pair[1])
            actual.append(
                {
                    "station": site["station"],
                    "id": site["id"],
                    "lat": site["lat"],
                    "lon": site["lon"],
                    "peak_m": peak[1],
                    "peak_utc": peak[0],
                    "trough_m": trough[1],
                    "trough_utc": trough[0],
                    "records": len(data),
                }
            )
    expected = [{k: v for k, v in s.items() if k != "evidence"} for s in products["六站潮位极值"]]
    return [
        _check(
            "geo_six_gauge_extremes",
            len(actual) == 6 and actual == expected,
            "按每站分钟级原始记录排除缺测编码 9999，重算六站极值、UTC 时刻及有效观测数。",
        )
    ]


def _bridge(sources, products):
    endpoints = []
    for source in sorted(sources, key=lambda v: v["filename"]):
        reader = PdfReader(io.BytesIO(source["read"]()))
        txt = " ".join(" ".join(p.extract_text() or "" for p in reader.pages).split())
        pattern = r"[Ii]nner [Ll]oop (?:of I-695 )?(?:remains )?closed at (?:at )?(MD \d+)"
        m = re.search(pattern, txt)
        if not m:
            raise ValueError("原公告中未找到内环封闭端点")
        endpoints.append(m.group(1))
    notes = [r["事项"] for r in products["公告依据"]]
    valid = (
        len(endpoints) == 2
        and len(set(endpoints)) == 2
        and all(any(e in n for n in notes) for e in endpoints)
    )
    return [
        _check(
            "geo_notice_versions",
            valid and products["五组配送"].get("状态") == "待确认",
            "复读两份交通局 PDF，核对内环封闭端点的版本差异和无路网时的待确认状态。",
        )
    ]


def _layer(archive, part, *, geometries=False):
    import shapefile
    from shapely.geometry import shape

    shapefiles = [n for n in archive.namelist() if n.endswith(".shp") and part in n]
    if len(shapefiles) != 1:
        raise ValueError("图层名称无法唯一匹配")
    stem = shapefiles[0][:-4]
    requirements = ["dbf", "shp", "shx"] if geometries else ["dbf"]
    streams = {}
    for ext in requirements:
        member = archive.getinfo(f"{stem}.{ext}")
        if member.file_size > 128 * 1024 * 1024:
            raise ValueError("图层超过本次复核上限")
        streams[ext] = io.BytesIO(archive.read(member))
    with shapefile.Reader(**streams, encoding="utf8", encodingErrors="replace") as reader:
        if geometries:
            return [(shape(r.shape.__geo_interface__), r.record.as_dict()) for r in reader.iterShapeRecords()]
        return [r.as_dict() for r in reader.iterRecords()]


def _raster(src, bands):
    from rasterio.io import MemoryFile

    with MemoryFile(src["read"]()) as mem, mem.open() as ds:
        if ds.width * ds.height > 12000000 or ds.count < max(bands):
            raise ValueError("栅格规模或波段数量与本批复核条件不符")
        return ds.read(bands), (ds.width, ds.height, ds.crs, ds.transform)


def _valencia(sources, products):
    before, grid = _raster(_source(sources, "F01.tif"), [4])
    after, g2 = _raster(_source(sources, "F02.tif"), [4])
    water, g3 = _raster(next(s for s in sources if "AN01" in s["filename"]), [1])
    if not grid == g2 == g3 or grid[2].to_epsg() != 32630:
        raise ValueError("三份栅格没有对齐到同一投影网格")
    size = abs(grid[3].a * grid[3].e)
    usable = np.isin(before, [4, 5, 6]) & np.isin(after, [4, 5, 6]) & (water != 255)
    masks = {
        "双期不可判": ~usable,
        "永久水体": usable & (water == 1),
        "前后均为水体": usable & (water != 1) & (after == 6) & (before == 6),
        "有效非水体": usable & (water != 1) & (after != 6),
        "新增水体候选": usable & (water != 1) & (after == 6) & (before != 6),
    }
    expected = [
        {"光学类别": label, "像元数": int(m.sum()), "面积_km2": round(int(m.sum()) * size / 1e6, 4)}
        for label, m in masks.items()
    ]
    with zipfile.ZipFile(io.BytesIO(_source(sources, ".zip")["read"]())) as z:
        features = _layer(z, "GRA_PRODUCT_observedEvent", geometries=True)
        roads = _layer(z, "GRA_PRODUCT_transportation", geometries=True)
        boundary = _layer(z, "GRA_PRODUCT_areaOfInterest", geometries=True)
    quantities = Counter(r["notation"] for _, r in features)
    exposures = _road_counts(features, roads, boundary)
    reported = {r["类别"]: r["要素数"] for r in products["CEMS任务范围面积"]}
    return [
        _check(
            "geo_optical_flood_cells",
            expected == products["光学面积闭合"],
            f"读取三份冻结原始 GeoTIFF 并逐像元对账。{grid[0]}×{grid[1]} 网格，像元面积 {size:g} m²。",
        ),
        _check(
            "geo_flood_trace_counts",
            reported == {"洪水": quantities["Flooded area"], "洪痕": quantities["Flood trace"]},
            "直接核对分级图层 DBF 的洪水与洪痕要素数量；此项不核定路况。",
        ),
        _check(
            "geo_exposed_road_classes",
            exposures == products["道路分类与相交记录数"],
            "在原始分级产品 AOI 内重新投影道路与洪水/洪痕面，逐源要素以合并相交超过 0.01 米统计各道路类别。",
        ),
    ]


def _road_counts(features, roads, boundary):
    from shapely import intersection, make_valid
    from shapely.ops import transform, unary_union

    project = Transformer.from_crs(4326, 32630, always_xy=True)
    scope = transform(project.transform, make_valid(boundary[0][0]))
    surfaces = [
        transform(project.transform, make_valid(g)).intersection(scope)
        for g, r in features
        if r["notation"] in {"Flooded area", "Flood trace"}
    ]
    selected = [
        (transform(project.transform, g), r["simplified"])
        for g, r in roads
        if r["simplified"] in {"Local roads", "Main roads", "Highway", "Tracks"}
    ]
    lines, labels = zip(*selected, strict=True)
    row_ids, face_ids = STRtree(surfaces).query(lines, predicate="intersects")
    segments = intersection(np.asarray(lines)[row_ids], np.asarray(surfaces)[face_ids])
    matches = defaultdict(list)
    for index, segment in zip(row_ids, segments, strict=True):
        if segment.length > 0:
            matches[int(index)].append(segment)
    return dict(Counter(labels[i] for i, parts in matches.items() if unary_union(parts).length > 0.01))


def _quake(sources, products):
    with zipfile.ZipFile(io.BytesIO(_source(sources, ".zip")["read"]())) as archive:
        features = _layer(archive, "DamageStructure")
    grades = Counter(r["Main_Dmg"] for r in features)
    source = _source(sources, ".pdf")
    original = " ".join(
        " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(source["read"]())).pages).split()
    )
    pub = next(r for r in products["版本对照"] if "damaged" in r)
    damaged = re.search(r"identified (\d+) damaged structures and (\d+) potentially", original, re.IGNORECASE)
    return [
        _check(
            "geo_damage_versions",
            dict(grades) == products["矢量等级"]
            and bool(damaged)
            and int(damaged[1]) == pub["damaged"]
            and int(damaged[2]) == pub["possibly_damaged"]
            and products["无法逐栋判定的记录数"] == len(features),
            "按结构点 DBF 原始损伤等级重新计数，分别核对另一期 UNOSAT 专题图正文的两类数量。",
        )
    ]


def _proxy_classes(date, raw, offset):
    quality = raw[-1].astype(np.uint16)
    # The delivered pixel classifications use float32 reflectance. The numeric
    # precision must match at pixels sitting directly on a published threshold.
    spectral = raw[:5].astype(np.float32)
    valid = np.all(spectral > 0, axis=0)
    if date.startswith("2010"):
        valid &= (quality & 59) == 0
        refl = spectral * 0.0000275 - 0.2
    else:
        valid &= np.isin(quality, [4, 5, 6])
        refl = (spectral + offset) / 10000
    _, green, red, nir, swir = refl

    def ratio(a, b):
        return np.divide(a - b, a + b, out=np.full_like(a, np.nan), where=(a + b) > 1e-6)

    ndvi, mndwi, ndbi = ratio(nir, red), ratio(green, swir), ratio(swir, nir)
    valid &= np.isfinite(ndvi) & np.isfinite(mndwi) & np.isfinite(ndbi)
    return np.select([~valid, mndwi > 0.15, ndvi >= 0.55, ndvi >= 0.30, ndbi > 0], [0, 1, 2, 3, 4], default=5)


def _airport(sources, products):
    from rasterio.io import MemoryFile

    labels = ["不可判读", "水体候选", "浓密植被", "其他植被", "裸地/人工面候选", "其他地表"]
    areas = []
    changes = []
    previous = None
    grid = None
    with zipfile.ZipFile(io.BytesIO(_source(sources, ".zip")["read"]())) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        for rec in metadata["products"]:
            date = rec["observation_date"]
            with MemoryFile(archive.read(rec["file"])) as mem, mem.open() as ds:
                current_grid = ds.shape, ds.transform, ds.crs
                if grid is not None and current_grid != grid:
                    raise ValueError("分类网格不一致")
                if ds.count != 6 or ds.width * ds.height > 1000000 or ds.crs.to_epsg() != 32635:
                    raise ValueError("波段或栅格不在案例范围内")
                grid = current_grid
                km2 = abs(ds.transform.a * ds.transform.e) / 1e6
                classes = _proxy_classes(date, ds.read(), -1000 if "_N0510_" in rec["bands"][0]["url"] else 0)
            counts = np.bincount(classes.ravel(), minlength=6)
            areas.append({"date": date, **{k: round(int(counts[i]) * km2, 4) for i, k in enumerate(labels)}})
            if previous is not None:
                for i in [2, 3]:
                    n = int(np.count_nonzero((previous == i) & (classes == 4)))
                    changes.append(
                        {
                            "时期": areas[-2]["date"] + " → " + date,
                            "原类别": labels[i],
                            "新类别": labels[4],
                            "像元数": n,
                            "面积_km2": round(n * km2, 4),
                        }
                    )
            previous = classes
    return [
        _check(
            "geo_surface_proxy_areas",
            len(areas) == 3 and areas == products["三期光谱面积_km2"],
            "在三期原始反射率及质量波段上复算共同分析网格的光谱代理面积，核对各期闭合。",
        ),
        _check(
            "geo_surface_transitions",
            changes == products["主要转移摘录"],
            "独立计数相邻期的植被到裸地/人工面候选像元，并核对其面积。",
        ),
    ]


def _sanghar(sources, products):
    from pyproj import Transformer
    from shapely import make_valid
    from shapely.ops import transform, unary_union

    project = Transformer.from_crs(4326, 32642, always_xy=True)
    candidates = []
    scopes = []
    for source in sorted(sources, key=lambda item: item["filename"]):
        with zipfile.ZipFile(io.BytesIO(source["read"]())) as archive:
            polygons = _layer(archive, "observedEvent", geometries=True)
            aoi = _layer(archive, "areaOfInterest", geometries=True)
        extent = unary_union([make_valid(g) for g, r in polygons if r["notation"].lower() == "flooded area"])
        region = transform(project.transform, unary_union([make_valid(g) for g, r in aoi]))
        scopes.append(region)
        candidates.append(transform(project.transform, extent).intersection(region))
    common = scopes[0].intersection(scopes[1])
    areas = [p.intersection(common) for p in candidates]
    metrics = {
        "area_km2": round(common.area / 1e6, 3),
        "union_km2": round(areas[0].union(areas[1]).area / 1e6, 3),
        "intersection_km2": round(areas[0].intersection(areas[1]).area / 1e6, 3),
    }
    return [
        _check(
            "geo_flood_aoi_union",
            len(candidates) == 2 and metrics == products["共同AOI对照"],
            "从两个产品的实际 Shapefile 几何重投影，在共同 AOI 内复算洪水并集和交集面积。",
        )
    ]


_DISPATCH = {
    "G01": _hotspots,
    "G02": _panels,
    "G03": _ice,
    "G04": _valencia,
    "G05": _quake,
    "G06": _airport,
    "G07": _sampling,
    "G08": _tides,
    "G09": _bridge,
    "G10": _sanghar,
}


def checks(code, sources, products):
    """Only report checks that were actually run against authorized, fingerprint-verified originals."""
    if code not in _DISPATCH:
        return []
    try:
        return _DISPATCH[code](sources, products)
    except (
        ArithmeticError,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        IndexError,
        StopIteration,
        PyPdfError,
        ShapefileException,
        ShapelyError,
        struct.error,
        zipfile.BadZipFile,
    ) as exc:
        return [
            _check(
                "geo_original_recheck",
                False,
                f"原件独立复核未通过（{type(exc).__name__}），请检查源文件结构及交付表。",
            )
        ]
