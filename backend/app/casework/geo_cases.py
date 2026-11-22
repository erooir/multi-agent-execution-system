"""Reproducible spatial/temporal specialists over known, frozen project inputs.

An image/report about a data product does not substitute for its observation arrays
or vectors. Inputs below are open contracts; no original case material is embedded.
"""

from __future__ import annotations

from pyproj.exceptions import CRSError, ProjError
from shapely.errors import ShapelyError

from .geo_audit import audit_products
from .geo_common import Run
from .geo_context import TOPICS, document_context
from .geo_extended import (
    coral_heat_stress,
    deforestation_alerts,
    heat_islands,
    lake_sequence,
    night_lights,
    scene_features,
)
from .geo_observations import buildings, hotspots, panels
from .geo_overlay import agriculture, cover, flood
from .geo_raster import resolve_grids
from .geo_render import map_view
from .geo_routes import routes
from .geo_series import ice, samples, timeline

__all__ = ["CATALOG", "analyze", "audit_products"]


def spec(title, inputs, outputs, specialists):
    return {"title": title, "inputs": inputs, "outputs": outputs, "specialists": specialists}


CATALOG = {
    "G01": spec(
        "热点批次比对",
        [
            "kind=policy：start/end、match_radius_m、confidence_levels",
            "kind=batch：date/complete（包含前日）",
            "热点行：latitude/longitude、observed_at或acq_date/acq_time、sensor、confidence、frp；region或行政区面",
        ],
        ["hotspots", "daily", "points", "exceptions"],
        ["观测质检", "空间去重", "相邻批次比对"],
    ),
    "G02": spec(
        "多时相图像注记核查",
        [
            "policy.dates指定观测日",
            "已知图像观测行：image_id/date/width/height/bbox=[x,y,w,h]/observation；可给quality、interpretation、confidence",
        ],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
    "G03": spec(
        "年度海冰范围基线与异常",
        [
            "year/month/hemisphere/extent、unit及一致版本；支持规范化NSIDC北极9月表",
            "policy可指定start_year/target_year/baseline_years/extent_unit",
        ],
        ["annual_series", "anomaly", "trend_svg"],
        ["时序清洗", "基线统计", "排名复核"],
    ),
    "G04": spec(
        "水域变化与道路暴露",
        [
            "policy.metric_crs=约定米制EPSG；kind=aoi/region/road及crs/geometry",
            "flood/trace/coverage/unknown已有面产品，或before/after/permanent water_grid：date、values(水体类别或[绿波段,近红外])、valid_mask、origin_x/y、pixel_width/height",
        ],
        ["flood_layer", "road_exposure", "areas", "coverage"],
        ["水体变化", "路网叠加", "覆盖核算"],
    ),
    "G05": spec(
        "建筑损伤时相对照",
        [
            "逐建筑建筑id building_id/block、geometry/crs；before/after=intact/damaged/destroyed/unknown",
            "before_image/after_image、before_region/after_region、registered/clear及confidence",
        ],
        ["buildings", "blocks", "layer", "comparison_panels", "uncertain", "scope"],
        ["底账匹配", "图像证据对照", "分区汇总"],
    ),
    "G06": spec(
        "土地覆盖阶段转移",
        [
            "policy.metric_crs、aoi面及三期kind=cover_grid，共同网格origin_x/y、pixel_width/height、crs/date/values/valid_mask",
            "values为已知类别，或波段数组配kind=signature的class/bands及max_spectral_distance；提供kind=validation_pixel日期/零基row/column/class",
        ],
        ["land_cover", "areas", "transitions", "new_built", "method"],
        ["共同格网校验", "光谱分类", "转移矩阵"],
    ),
    "G07": spec(
        "环境样点观测与覆盖",
        [
            "EPA实验室原字段或sample_id、site_id、date、medium、analyte、unit、value、qualifier、validation",
            "kind=site给坐标；kind=grid给WGS84 bbox；policy.start/end/qualifier_rules",
        ],
        ["results", "sample_points", "coverage", "quality"],
        ["样品质控", "点位匹配", "网格覆盖"],
    ),
    "G08": spec(
        "岛屿多源观测时间线",
        [
            "policy.date/islands；观测行kind=frame/tide/report/survey、observed_at/island；调查另给event_at",
            "坐标、描述、帧号，潮位value/unit/datum与可选baseline；claim_id关联冲突检查",
        ],
        ["timeline", "retrospective", "island_matrix", "map", "uncertainty"],
        ["UTC时序对齐", "岛屿归属", "回溯证据核查"],
    ),
    "G09": spec(
        "道路中断与替代路径",
        [
            "policy.snapshot带时区、expected_od；kind=edge：edge_id/from/to/distance_km/oneway/state/geometry/crs、速度与灾前状态",
            "kind=od：od_id/from/to/vehicle；kind=notice：edge_id/state/effective_at、expires_at/priority",
        ],
        ["od_comparison", "route_layer", "exclusions", "notice_audit"],
        ["快照与限制核查", "有向图路径", "基线差额"],
    ),
    "G10": spec(
        "多期农业暴露评估",
        [
            "policy.metric_crs/start/end/priority_thresholds；district/grid/crop/flood/coverage面、crs/geometry",
            "县/网格标识、作物类别、分类年份精度、统计面积；洪水面与覆盖足迹均须有观测日期",
        ],
        ["exposure", "grid_exposure", "max_union", "repeat_coverage", "priority", "method"],
        ["多期空间并集", "耕地分区叠加", "分级规则复核"],
    ),
    # 专属分析函数已实现
    # 专属分析函数已实现
    "G11": spec(
        "德里与凤凰城夜间地表温度热岛快报",
        ["上游提供德里 ECOSTRESS 夜景 LST 栅格 1 景（约 12,350 km²，像元 70m×38m），凤凰城 VIIRS VNP21IMG 2023-07-01 至 07-19 约凌晨 2 点的夜间 LST 序列 8—10 景，均附采集 UTC 时刻、质量层与传感器说明；另供城市边界和土地覆盖参照分类。公开网页的展示图只能用于背景说明，不能替代执行所需带坐标栅格；热浪期间的气象站气温序列未取得。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
    # 专属分析函数已实现
    "G12": spec(
        "基于 Landsat 9 单景卫星影像的布卡尼尔群岛潮汐海岸地物要素判读",
        ["需要一张可直接下载的单景 Landsat 9 高清影像 JPG 作为主输入（G12-M01，约 22.9MB）；一份 NASA Earth Observatory 官方图文条目页用于核对影像身份、传感器、获取日期、潮汐时刻与图像署名（G12-M02）；一份西澳政府 Buccaneer Archipelago Fact Sheet 用于地貌、潮汐、采矿与海洋公园等背景要素的判读依据（G12-M03）。缺口：影像为纯图像、不含逐字要素标注与地理坐标，需模型自行识读；单景单时相，无法做变化检测；红树林与礁石等要素判读受潮位与水色影响，存在不确定性。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
    # 专属分析函数已实现
    "G13": spec(
        "亚马孙毁林预警驱动分区核查与口径对照",
        ["上游提供指定 AOI（建议不超过 2 万 km²）的 2024 年整合预警点/面（10m，含 driver 类别、置信度、检测日期、来源系统 GLAD-L/GLAD-S2/RADD）、道路与保护区矢量、2021 年 GFW 与 PRODES 对照统计表。PRODES 原始图斑和 DETER 实时数据不在所给材料中；驱动分类为 AI 推断结果，需保留原文置信度与复核边界。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
    # 专属分析函数已实现
    "G14": spec(
        "VIIRS 夜间灯光异常与城市活动变化的分区判读",
        ["上游提供波多黎各两景 VIIRS DNB 夜景（2016-09-21 02:50、09-22 02:31 地方时，附 UTC）、图森实验若干夜序列（含 100%/60%/30% 调光时段）、月相与气辉说明、城市与道路底图、滤光处理说明。未处理的原始 DNB 影像与 Black Marble 逐夜产品需上游提供；本次核验的公开页面仅为展示图与说明文字。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
    # 专属分析函数已实现
    "G15": spec(
        "青藏高原湖泊扩张与冰湖溃决风险的时序核验",
        ["上游提供两期已配准 Landsat 影像（30m，含云量/雪盖质量层）、1991—2023 湖泊年度面积 CSV（含口径与来源定义）、道路/居民点/草场矢量与坐标系、喀喇昆仑冰川时序 GIF 与关键帧说明、Imja Tsho 湖面积与冰川退缩速率表。公开网页提供展示图、文献 DOI 与统计数值，原始湖泊矢量、淹没模型、冰湖坝体实测参数不在所给材料中。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["timeline", "retrospective", "island_matrix", "map", "uncertainty"],
        ["UTC时序对齐", "岛屿归属", "回溯证据核查"],
    ),
    # 专属分析函数已实现
    "G16": spec(
        "第四次全球珊瑚白化热压力预警与影响范围核查",
        ["上游提供指定礁区（不超过 10 个 60°×40° 瓦片或限定 AOI）2023-01-01 至 2024-04-10 的 5km BAA 7 日最大逐日栅格或 CSV（含 HotSpot、DHW、等级）、全球最高等级图 1 幅、NOAA 2024-04-15 公告与 2026-06-02 NESDIS 公报各 1 份、礁区边界与现场白化观测点表。5km 格网与 reef-area 掩膜、现场观测点表需上游提供，公开材料不含这些图层。 结构化记录需包含 kind、frame_id、zone、feature、delta、bbox、confidence、date、sensor；G15 用 lake_id/date/area/unit。"],
        ["panels", "changes", "unreadable", "summary"],
        ["时相归一", "像素定位", "观察与解释分栏"],
    ),
}

ANALYZERS = {
    "G01": hotspots,
    "G02": panels,
    "G03": ice,
    "G04": flood,
    "G05": buildings,
    "G06": cover,
    "G07": samples,
    "G08": timeline,
    "G09": routes,
    "G10": agriculture,
    "G11": heat_islands,
    "G12": scene_features,
    "G13": deforestation_alerts,
    "G14": night_lights,
    "G15": lake_sequence,
    "G16": coral_heat_stress,
}

MAP_CASES = {"G01", "G04", "G05", "G06", "G07", "G08", "G09", "G10", "G11", "G12", "G13", "G14", "G15", "G16"}
for _code in MAP_CASES:
    CATALOG[_code]["outputs"].append("map_view")
for _code in TOPICS:
    CATALOG[_code]["outputs"].append("source_notes")


def _repair(records, targets=()):
    """Only explicit, scoped mapping dictionaries may translate unknown columns."""
    mappings = {}
    gaps = []
    applied = set()
    for idx, r in enumerate(records):
        f = r.get("fields", {})
        if f.get("kind") != "field_dictionary":
            continue
        scope = f.get("target_source_id")
        if targets and scope not in targets:
            continue
        aliases = f.get("aliases")
        if not isinstance(scope, str) or not scope or not isinstance(aliases, dict):
            gaps.append("字段字典缺少目标资料ID或映射对象")
            continue
        for src, dst in aliases.items():
            if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
                gaps.append("字段字典含无效列名")
                continue
            mappings.setdefault((scope, src), []).append((dst, idx))
    out = []
    for r in records:
        fields = r.get("fields", {})
        if not isinstance(fields, dict) or fields.get("kind") == "field_dictionary":
            out.append(r)
            continue
        translated = dict(fields)
        for src, v in fields.items():
            entries = mappings.get((r.get("source_id"), src), [])
            names = {dst for dst, _ in entries}
            if len(names) > 1:
                gaps.append("多个字段字典对同一列给出冲突映射")
                continue
            if names:
                dst = next(iter(names))
                if dst in translated and translated[dst] != v:
                    gaps.append("字段别名与已有目标值冲突，未覆盖原字段")
                    continue
                translated[dst] = v
                applied.update(idx for _, idx in entries)
        out.append(dict(r, fields=translated))
    return out, applied, gaps


def analyze(
    case_id: str,
    records: list[dict],
    *,
    feedback: list[str] | None = None,
    repair_action: str | None = None,
    repair_targets=(),
) -> dict:
    """Outputs contain only derived values or explicit, located observations."""
    if case_id not in ANALYZERS:
        raise ValueError("未知地理任务")
    use_dictionary = repair_action == "apply_field_dictionary" or (repair_action is None and feedback)
    mapped, used, issues = _repair(records, repair_targets) if use_dictionary else (records, set(), [])
    ctx = Run(mapped)
    for i in used:
        ctx.ref(mapped[i])
    for problem in issues:
        ctx.gap(problem)
    try:
        if case_id in {"G04", "G06"}:
            resolve_grids(ctx)
        products = ANALYZERS[case_id](ctx)
        if case_id in MAP_CASES:
            products["map_view"] = map_view(products)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        OverflowError,
        ShapelyError,
        CRSError,
        ProjError,
    ) as exc:
        # Data errors are terminal evidence gaps, not a fabricated empty map or zero total.
        ctx.gap(f"输入未满足分析契约：{str(exc)[:180]}")
        products = {key: None for key in CATALOG[case_id]["outputs"]}
    if case_id in TOPICS:
        products["source_notes"] = document_context(ctx, case_id)
    return ctx.done(products)
