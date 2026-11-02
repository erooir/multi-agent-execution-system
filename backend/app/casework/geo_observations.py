"""Location-based comparisons. No semantic claim is inferred from an image filename."""

import math
from collections import Counter, defaultdict
from datetime import timedelta
from html import escape
from itertools import product

from shapely.geometry import Point, shape

from .geo_common import collection, day, feature, lnglat, number, stamp
from .geo_raster import bright_regions, preview_png


def _sphere(xy):
    x, y = map(math.radians, xy)
    return (math.cos(y) * math.cos(x), math.cos(y) * math.sin(x), math.sin(y))


def hotspots(ctx):
    start = day(ctx.policy.get("start", "2023-06-01"))
    end = day(ctx.policy.get("end", "2023-06-07"))
    radius = number(ctx.policy.get("match_radius_m"))
    if not start or not end or not 0 <= (end - start).days <= 366:
        raise ValueError("热点统计周期错误")
    if radius is None or not 0 < radius <= 100000:
        ctx.gap("缺少有效前日匹配半径(米)")
        radius = None
    confidence = ctx.policy.get("confidence_levels")
    if not isinstance(confidence, list) or not confidence:
        ctx.gap("缺少显式置信度筛选规则")
        confidence = None
    complete = set()
    region_shapes = []
    bad = []
    duplicates = []
    seen = {}
    days = defaultdict(list)
    last = None
    for r in ctx.select("region"):
        f = r["fields"]
        if f.get("crs") not in {"EPSG:4326", "OGC:CRS84"}:
            ctx.gap("分区边界需注明WGS84")
            continue
        poly = shape(f["geometry"])
        if not poly.is_valid or poly.geom_type not in {"Polygon", "MultiPolygon"}:
            ctx.gap("无效分区边界")
            continue
        region_shapes.append((poly, str(f["region"]), ctx.ref(r)))
    for r in ctx.rows:
        f = r["fields"]
        if f.get("kind") == "batch":
            if f.get("complete") is True and day(f.get("date")):
                complete.add(day(f["date"]))
                ctx.ref(r)
            continue
        if not ("latitude" in f or "longitude" in f or f.get("kind") == "hotspot"):
            continue
        dt = stamp(f.get("observed_at"))
        if dt is None and f.get("acq_date") and f.get("acq_time") is not None:
            hm = str(f["acq_time"]).zfill(4)
            dt = stamp(f"{f['acq_date']}T{hm[:2]}:{hm[2:]}:00+00:00")
        ref = ctx.ref(r)
        xy = lnglat(f.get("longitude"), f.get("latitude"))
        if not dt or not xy:
            bad.append({"ref": ref, "reason": "invalid_time_or_coordinates"})
            continue
        if not start - timedelta(days=1) <= dt.date() <= end:
            continue
        if confidence and str(f.get("confidence", "")).casefold() not in {
            str(v).casefold() for v in confidence
        }:
            continue
        sensor = f.get("sensor", f.get("satellite"))
        key = (dt, tuple(xy), sensor, f.get("orbit"), f.get("scan"))
        if key in seen:
            duplicates.append({"ref": ref, "duplicate_of": seen[key]})
            continue
        seen[key] = ref
        region = f.get("region")
        refs = [ref]
        if not region:
            regions = [(name, rid) for poly, name, rid in region_shapes if poly.covers(Point(*xy))]
            region = regions[0][0] if len(regions) == 1 else None
            refs.extend(rid for _, rid in regions)
        if not region:
            ctx.gap("部分观测没有唯一分区，未补造辖区")
        if not sensor:
            ctx.gap("部分观测缺少传感器")
        if not stamp(f.get("available_at")):
            ctx.gap("部分观测没有带时区的材料可用时刻")
        days[dt.date()].append(
            {
                "id": ref,
                "coords": xy,
                "observed_at": dt.isoformat(),
                "available_at": f.get("available_at"),
                "sensor": sensor,
                "frp": number(f.get("frp", f.get("FRP"))),
                "confidence": f.get("confidence"),
                "region": region,
                "refs": refs,
            }
        )
        last = max(dt, last) if last else dt
    cell = 2 * math.sin((radius or 1) / 6371008.8 / 2)
    items = []
    stats = []
    points = []
    current = start
    while current <= end:
        prev = current - timedelta(days=1)
        buckets = defaultdict(list)
        counts = Counter()
        for entry in days[prev]:
            xyz = _sphere(entry["coords"])
            k = tuple(math.floor(t / cell) for t in xyz)
            buckets[k].append((xyz, entry["id"]))
        if prev not in complete:
            ctx.gap(f"{prev}前日完整批次未确认，未匹配点不能判为新增")
        if current not in complete:
            ctx.gap(f"{current}批次未确认完整")
        for entry in days[current]:
            xyz = _sphere(entry["coords"])
            k = [math.floor(t / cell) for t in xyz]
            matches = []
            if radius:
                for delta in product((-1, 0, 1), repeat=3):
                    for other, ref in buckets[tuple(a + b for a, b in zip(k, delta, strict=True))]:
                        distance = math.dist(xyz, other)
                        if distance <= cell:
                            matches.append((distance, ref))
            entry["match"] = (
                "matched" if matches else "new" if prev in complete and radius and confidence else "unknown"
            )
            entry["previous_ref"] = min(matches)[1] if matches else None
            counts[(entry["region"], entry["match"])] += 1
            items.append(entry)
            points.append(
                feature(
                    {"type": "Point", "coordinates": entry["coords"]},
                    {k: entry[k] for k in ("id", "match", "region", "refs")},
                )
            )
        stats.append(
            {
                "date": str(current),
                "complete": current in complete,
                "count": len(days[current]),
                "by_region": [{"region": rg, "match": m, "count": c} for (rg, m), c in counts.items()],
            }
        )
        current += timedelta(days=1)
    return {
        "hotspots": items,
        "daily": stats,
        "points": collection(points),
        "exceptions": {
            "invalid": bad,
            "duplicates": duplicates,
            "last_observation": last.isoformat() if last else None,
            "match_radius_m": radius,
            "confidence_levels": confidence,
            "rule": "同一UTC时刻、坐标、传感器、轨道和扫描的观测去重；在指定半径内寻找前一日期最近点。热异常不是过火面积。",
        },
    }


def panels(ctx):
    panels = {}
    changes = []
    blind = []
    expected = set(ctx.policy.get("dates", ["2021-03-21", "2021-03-24", "2021-03-25"]))
    if not expected or any(not day(d) for d in expected):
        raise ValueError("目标时相必须为日期清单")
    previews = {r["source_id"]: r for r in ctx.select("raster_preview") if r["location"].get("image")}
    backgrounds = {}
    for bind in ctx.select("raster_binding"):
        f = bind["fields"]
        target = f.get("target_source_id")
        if f.get("role") != "panel" or target not in previews:
            continue
        image = previews[target]
        info = image["fields"]
        date = day(f.get("date"))
        iid = f.get("image_id", target)
        png = preview_png(info.get("png"))
        w = info.get("width")
        h = info.get("height")
        if not png or not date or not w or not h:
            ctx.gap("图像绑定缺少可解码原图、日期或尺寸")
            continue
        if str(date) not in expected:
            continue
        if iid in backgrounds:
            raise ValueError("同一图像ID绑定多个原件或时相")
        backgrounds[iid] = png
        panel = {
            "image_id": iid,
            "date": str(date),
            "width": w,
            "height": h,
            "sensor": f.get("sensor"),
            "annotations": [],
            "refs": [ctx.ref(image), ctx.ref(bind)],
        }
        panels[iid] = panel
        threshold = number(f.get("brightness_threshold"))
        if threshold is not None and 0 <= threshold <= 255:
            regions = bright_regions(png, threshold, int(f.get("min_preview_pixels", 2)))
            panel["brightness_candidates"] = [
                {
                    "bbox": [round(x * w, 2), round(y * h, 2), round(bw * w, 2), round(bh * h, 2)],
                    "method": "预览亮度连通区域",
                    "refs": panel["refs"],
                }
                for x, y, bw, bh in regions
            ]
    for r in ctx.rows:
        f = r["fields"]
        iid = f.get("image_id")
        dt = day(f.get("date"))
        if not iid or not dt or f.get("kind") == "raster_binding":
            continue
        if str(dt) not in expected:
            continue
        ref = ctx.ref(r)
        w = number(f.get("width"))
        h = number(f.get("height"))
        box = f.get("bbox")
        panel = panels.setdefault(
            str(iid),
            {
                "image_id": iid,
                "date": str(dt),
                "width": w,
                "height": h,
                "sensor": f.get("sensor"),
                "annotations": [],
            },
        )
        if (
            not w
            or not h
            or w <= 0
            or h <= 0
            or panel["date"] != str(dt)
            or (w, h) != (panel["width"], panel["height"])
        ):
            blind.append({"ref": ref, "reason": "unknown_or_conflicting_panel_dimensions"})
            continue
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any(number(v) is None for v in box):
            blind.append({"ref": ref, "reason": "missing_image_region"})
            continue
        x, y, bw, bh = map(float, box)
        if min(x, y) < 0 or min(bw, bh) <= 0 or x + bw > w or y + bh > h:
            blind.append({"ref": ref, "reason": "invalid_pixel_box"})
            continue
        obs = f.get("observation") or r.get("text")
        quality = f.get("quality")
        if not obs or quality in {"cloud", "shadow", "speckle", "unreadable"}:
            blind.append({"ref": ref, "bbox": box, "reason": quality or "uninterpreted_region"})
            continue
        item = {
            "image_id": iid,
            "date": str(dt),
            "bbox": box,
            "observation": obs,
            "interpretation": f.get("interpretation"),
            "confidence": f.get("confidence"),
            "refs": [ref],
            "coordinate_space": "image_pixels",
        }
        panel["annotations"].append(item)
        changes.append(item)
    covered = {r["date"] for r in changes}
    if expected - covered:
        ctx.gap(f"时相缺少可定位判读：{sorted(expected - covered)}")
    for p in panels.values():
        if p["width"] and p["height"] and p["width"] > 0 and p["height"] > 0:
            png = backgrounds.get(p["image_id"])
            if not png:
                ctx.gap("部分图像未绑定原件，已给出基于注记的覆盖层，不声称完成原图判读")
            bg = (
                f'<image width="{p["width"]}" height="{p["height"]}" href="data:image/png;base64,{png}"/>'
                if png
                else ""
            )
            rects = ""
            for a in p["annotations"]:
                x, y, w, h = a["bbox"]
                rects += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" stroke="#e67d13" fill="none"><title>{escape(str(a["observation"]))}</title></rect>'
            p["pixel_overlay_svg"] = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {p["width"]} {p["height"]}">{bg}{rects}</svg>'
            )
    return {
        "panels": list(panels.values()),
        "changes": changes,
        "unreadable": blind,
        "summary": {
            "dated_panels": len({p["date"] for p in panels.values()}),
            "observations": len(changes),
            "basis": "依据原件已定位的观测注记产生图像覆盖层。未配准时不推算地理坐标、船只身份或数量。",
        },
    }


def buildings(ctx):
    buildings = []
    groups = defaultdict(Counter)
    features = []
    exceptions = []
    seen = set()
    id_counts = Counter(str(r["fields"]["building_id"]) for r in ctx.rows if r["fields"].get("building_id"))
    previews = {}
    for r in ctx.select("raster_preview"):
        if r["location"].get("image"):
            encoded = preview_png(r["fields"].get("png"))
            if encoded:
                previews[r["source_id"]] = {
                    "png": encoded,
                    "width": r["fields"]["width"],
                    "height": r["fields"]["height"],
                    "refs": [ctx.ref(r)],
                }
    for r in ctx.rows:
        f = r["fields"]
        if not f.get("building_id"):
            continue
        ref = ctx.ref(r)
        bid = str(f["building_id"])
        if bid in seen:
            ctx.gap("建筑轮廓ID重复，不能重复汇总")
            continue
        seen.add(bid)
        before = f.get("before")
        after = f.get("after")
        loc_ok = all(f.get(k) for k in ("before_image", "after_image", "before_region", "after_region"))
        for phase in ("before", "after"):
            region = f.get(phase + "_region")
            img = previews.get(f.get(phase + "_image"))
            if (
                not isinstance(region, (list, tuple))
                or len(region) != 4
                or any(number(v) is None for v in region)
            ):
                loc_ok = False
                continue
            x, y, w, h = map(float, region)
            if min(x, y) < 0 or min(w, h) <= 0 or (img and (x + w > img["width"] or y + h > img["height"])):
                loc_ok = False
        if id_counts[bid] > 1 or not loc_ok or f.get("registered") is not True or f.get("clear") is not True:
            status = "unknown"
        elif before == "intact" and after in {"damaged", "destroyed"}:
            status = "new_visible"
        elif before in {"damaged", "destroyed"} and after in {"damaged", "destroyed"}:
            status = "preexisting"
        elif before in {"intact", "damaged", "destroyed"} and after == "intact":
            status = "no_new_visible"
        else:
            status = "unknown"
        block = f.get("block")
        groups[str(block or "unassigned")][status] += 1
        row = {
            k: f.get(k)
            for k in (
                "building_id",
                "block",
                "before",
                "after",
                "before_image",
                "after_image",
                "before_region",
                "after_region",
                "confidence",
            )
        }
        row.update(status=status, refs=[ref])
        buildings.append(row)
        if any(row[field] not in previews for field in ("before_image", "after_image")):
            ctx.gap("部分时相图像未绑定原件，保留有来源的注记对照，不能宣称独立验证影像语义")
        if status == "unknown":
            exceptions.append(
                {"building_id": bid, "refs": [ref], "reason": "缺少配准、清晰成像、定位或灾前状态"}
            )
        geometry = f.get("geometry")
        g = shape(geometry) if geometry and f.get("crs") == "EPSG:4326" else None
        bounds = g.bounds if g is not None and not g.is_empty else None
        if g is not None and g.is_valid and bounds and lnglat(*bounds[:2]) and lnglat(*bounds[2:]):
            features.append(feature(geometry, {"building_id": bid, "status": status, "refs": [ref]}))
        else:
            ctx.gap("部分建筑缺少可输出的WGS84轮廓或点位")
    if not buildings:
        ctx.gap("缺少逐建筑清册和灾前/灾后定位观测，不能用区域报告推断建筑损伤")
    if exceptions:
        ctx.gap(f"{len(exceptions)}栋无法确认灾前后损伤变化")
    return {
        "buildings": buildings,
        "blocks": dict(groups),
        "layer": collection(features),
        "comparison_panels": previews,
        "uncertain": exceptions,
        "scope": "仅比较已定位的可见损伤注记，不作工程结构安全鉴定。",
    }
