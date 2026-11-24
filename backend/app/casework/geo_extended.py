"""Extended geospatial analyses for the added case catalogue.

These operate on already-structured upstream records (features, zones, time series),
never on raw image pixels. They carry source refs and report gaps honestly.
"""

from collections import Counter, defaultdict

from .geo_common import number


def _s(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return ""


def _features_and_zones(ctx, zone_kind="zone"):
    """Build panels-style output from structured feature/zone observation records."""
    panels = {}
    changes = []
    unreadable = []
    for r in ctx.rows:
        f = r["fields"]
        kind = _s(f, "kind", "type") or ""
        frame = _s(f, "frame_id", "panel_id", "scene_id")
        if not frame:
            if kind not in {"policy"}:
                ctx.gap("观测记录缺图幅编号，未纳入面板")
            continue
        p = panels.setdefault(
            frame,
            {
                "image_id": frame,
                "date": _s(f, "date", "observed_at") or None,
                "sensor": _s(f, "sensor") or None,
                "annotations": [],
                "refs": [ctx.ref(r)],
            },
        )
        if kind == zone_kind or "zone" in kind or "region" in kind:
            p["annotations"].append(
                {
                    "kind": "zone",
                    "value": _s(f, "zone", "zone_type", "class") or kind,
                    "bbox": f.get("bbox") if isinstance(f.get("bbox"), list) else None,
                    "refs": [ctx.ref(r)],
                }
            )
        elif "observation" in kind or "feature" in kind or f.get("feature"):
            p["annotations"].append(
                {
                    "kind": "feature",
                    "value": _s(f, "feature", "observation", "value") or "",
                    "confidence": _s(f, "confidence") or None,
                    "refs": [ctx.ref(r)],
                }
            )
        elif "change" in kind or f.get("delta") is not None:
            changes.append(
                {
                    "frame": frame,
                    "delta": f.get("delta"),
                    "basis": _s(f, "basis", "说明") or "上游结构化观测",
                    "refs": [ctx.ref(r)],
                }
            )
        elif "unreadable" in kind or "obscured" in kind:
            unreadable.append(
                {"frame": frame, "reason": _s(f, "reason", "cause") or "不可判读", "refs": [ctx.ref(r)]}
            )
    if not panels:
        ctx.gap("没有带图幅编号的可判读观测")
    return panels, changes, unreadable


def _panel_result(ctx, panels, changes, unreadable, note):
    return {
        "panels": list(panels.values()),
        "changes": changes,
        "unreadable": unreadable,
        "summary": {
            "frames": len(panels),
            "observations": sum(len(p["annotations"]) for p in panels.values()),
            "basis": note,
        },
    }


def heat_islands(ctx):
    """G11: 城市热岛地表温度分区判读（消费上游 LST 分区记录）。"""
    panels, changes, unreadable = _features_and_zones(ctx, zone_kind="heat_zone")
    return _panel_result(
        ctx,
        panels,
        changes,
        unreadable,
        "依据上游地表温度分区记录；地表温度不等同气象站气温，云/阴影区列为不可判读。",
    )


def scene_features(ctx):
    """G12: 单景卫星影像地物要素判读（消费上游影像要素注记记录）。"""
    panels, changes, unreadable = _features_and_zones(ctx, zone_kind="landform")
    return _panel_result(
        ctx,
        panels,
        changes,
        unreadable,
        "依据上游影像要素注记与条目元数据；潮汐时刻与图像署名随来源引用保留。",
    )


def deforestation_alerts(ctx):
    """G13: 毁林预警驱动分区核查（消费上游预警记录）。"""
    panels, changes, unreadable = _features_and_zones(ctx, zone_kind="driver_zone")
    res = _panel_result(
        ctx,
        panels,
        changes,
        unreadable,
        "预警不等于确认毁林；驱动分类不等于现场定性，时间窗口径差异按来源保留。",
    )
    res["summary"]["drivers"] = _zone_counts(panels)
    return res


def _zone_counts(panels):
    return dict(
        Counter(
            annotation["value"]
            for panel in panels.values()
            for annotation in panel["annotations"]
            if annotation["kind"] == "zone"
        )
    )


def night_lights(ctx):
    """G14: VIIRS 夜间灯光分区判读（消费上游灯光分区记录）。"""
    panels, changes, unreadable = _features_and_zones(ctx, zone_kind="light_zone")
    return _panel_result(
        ctx,
        panels,
        changes,
        unreadable,
        "VIIRS DNB 测夜间光照而非人口/GDP；月相、云量与观测角干扰列为不可判读。",
    )


def coral_heat_stress(ctx):
    """G16: 珊瑚白化热压力预警核查（消费上游 HotSpot/DHW 分区记录）。"""
    panels, changes, unreadable = _features_and_zones(ctx, zone_kind="bleach_zone")
    res = _panel_result(
        ctx,
        panels,
        changes,
        unreadable,
        "预警等级按产品定义核对（Level 1/2），现场白化与死亡为不同证据类别。",
    )
    res["summary"]["levels"] = _zone_counts(panels)
    return res


def lake_sequence(ctx):
    """G15: 高原湖泊扩张时序核验（消费上游湖泊面积/暴露记录）。"""
    timeline = defaultdict(list)
    for r in ctx.rows:
        f = r["fields"]
        lake = _s(f, "lake_id", "lake", "湖泊")
        if not lake:
            ctx.gap("湖泊记录缺湖泊编号")
            continue
        timeline[lake].append(
            {
                "date": _s(f, "date", "year") or None,
                "area": number(f.get("area")) if f.get("area") is not None else None,
                "unit": _s(f, "unit") or None,
                "driver": _s(f, "driver", "驱动") or None,
                "refs": [ctx.ref(r)],
            }
        )
    rows = []
    for lake, seq in sorted(timeline.items()):
        dated = sorted([s for s in seq if s["date"]], key=lambda x: x["date"])
        change = None
        if len(dated) >= 2 and dated[0]["area"] and dated[-1]["area"]:
            change = round((dated[-1]["area"] - dated[0]["area"]) / dated[0]["area"] * 100, 2)
        rows.append({"lake_id": lake, "observations": dated, "change_pct": change})
    if not rows:
        ctx.gap("没有可核验的湖泊时序记录")
    uncertain = [
        {"lake_id": r["lake_id"], "reason": "面积变化率不可重算或驱动证据缺失"}
        for r in rows
        if r["change_pct"] is None
    ]
    return {
        "timeline": rows,
        "retrospective": [{"lake_id": r["lake_id"], "change_pct": r["change_pct"]} for r in rows],
        "island_matrix": {"lakes": [r["lake_id"] for r in rows]},
        "map": {"lakes": rows},
        "uncertainty": uncertain,
    }
