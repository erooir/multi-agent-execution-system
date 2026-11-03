from .base import AnalyzeExecutor, compact


class GeospatialAnalysisExecutor(AnalyzeExecutor):
    heavy_products = frozenset(
        {
            "map_view",
            "points",
            "flood_layer",
            "layer",
            "comparison_panels",
            "land_cover",
            "new_built",
            "sample_points",
            "map",
            "route_layer",
            "max_union",
            "grid_exposure",
        }
    )

    def main_description(self, context):
        role = context.agent["role_contract"]
        return f"执行{role['operation']}及冻结网格/地图前后处理，生成权威{context.code}空间产品和证据；无参数"

    def project(self, output, context):
        products = output["content"]["products"]
        summaries = {}
        for key, value in products.items():
            if isinstance(value, dict) and isinstance(value.get("features"), list):
                summaries[key] = {"type": value.get("type"), "feature_count": len(value["features"])}
            elif isinstance(value, list):
                summaries[key] = {"rows": len(value)}
            elif isinstance(value, dict):
                summaries[key] = {"keys": sorted(value)[:30]}
            else:
                summaries[key] = {"type": type(value).__name__}
        return {
            "type": output["type"],
            "products": compact(
                {key: value for key, value in products.items() if key not in self.heavy_products},
                max_items=18,
                max_chars=400,
            ),
            "product_summaries": summaries,
            "evidence": compact(output["content"]["evidence"], max_items=24),
            "gaps": output["content"]["gaps"],
            "metrics": output["metrics"],
        }

    def instructions(self, context):
        raise NotImplementedError

    def numeric_boundary(self):
        return (
            "products_patch必须为空。坐标、面积、路径、分类、趋势、计数和地图图层只能采用主处理或独立计算工具结果；"
            "模型只检查覆盖、单位、时间、来源角色和具体缺口，不得手写数值或GeoJSON。"
        )


class HotspotExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g01.hotspot_batch_comparison"

    def instructions(self, context):
        return (
            "你负责热点批次比对。先核对policy时间窗、match_radius_m和confidence_levels，"
            "再检查batch完整性、热点坐标/观测时间/sensor/confidence/frp与行政区归属。"
            "使用主键工具检查重复观测；相邻批次匹配必须考虑半径和置信度，完整批次不足时不得当成完整日趋势。"
            + self.numeric_boundary()
        )


class PanelExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g02.multitemporal_panel_review"

    def instructions(self, context):
        return (
            "你负责多时相图像注记核查。按policy.dates和image_id对齐观测，核对width/height及零基bbox=[x,y,w,h]边界，"
            "分栏保留observation、interpretation、quality和confidence。主键工具只用于核查图像编号，不得由注记推断未提供的像素。"
            "不可读、越界或日期缺失的图幅须进入unreadable或具体缺口。" + self.numeric_boundary()
        )


class IceSeriesExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g03.sea_ice_baseline_anomaly"

    def instructions(self, context):
        return (
            "你负责年度海冰范围基线与异常。核对year/month/hemisphere/extent、单位、dataset_version和policy基线窗口，"
            "再调用series.baseline_anomaly独立复算目标年、基线均值、差值、百分比和排名。"
            "不同半球、月份、版本或单位不得拼接；缺单位证据时保留缺口而非假定Mkm²。" + self.numeric_boundary()
        )


class FloodExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g04.flood_road_exposure"

    def instructions(self, context):
        return (
            "你负责水域变化与道路暴露。先检查metric_crs、AOI/道路几何、观测日期和有效覆盖，"
            "再核对before/after/permanent水体或water_grid的网格配准与valid_mask。"
            "按需调用geo.overlay_area复核道路暴露面积；unknown覆盖不能算作无水，面积和道路长度只承认工具结果。"
            + self.numeric_boundary()
        )


class BuildingExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g05.building_damage_comparison"

    def instructions(self, context):
        return (
            "你负责建筑损伤时相对照。以building_id和block匹配底账，核对geometry/crs、before/after状态、"
            "前后图像与区域registered/clear/confidence。可用主键和叠加面积工具检查底账与分区。"
            "未配准、遮挡或低置信建筑必须保留uncertain，不得从区域总体变化推定单栋损伤。"
            + self.numeric_boundary()
        )


class LandCoverExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g06.land_cover_transition"

    def instructions(self, context):
        return (
            "你负责土地覆盖阶段转移。核对三期共同网格、日期、CRS、像元尺寸、valid_mask和metric_crs，"
            "区分已知类别与signature光谱分类，并使用validation_pixel检查分类证据。"
            "逐相邻期调用raster.class_transition独立复算；未知或无效像元不得纳入可判读转移面积。"
            + self.numeric_boundary()
        )


class EnvironmentalSampleExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g07.environmental_sample_coverage"

    def instructions(self, context):
        return (
            "你负责环境样点观测与覆盖。核对sample_id/site_id/date/medium/analyte/unit/value/qualifier/validation，"
            "再检查site坐标、grid时间窗和qualifier_rules。使用主键工具识别重复或冲突样品。"
            "不同介质、分析物和单位不得合计；检出限、无效样与未覆盖网格必须进入quality或coverage缺口。"
            + self.numeric_boundary()
        )


class IslandTimelineExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g08.island_observation_timeline"

    def instructions(self, context):
        return (
            "你负责岛屿多源观测时间线。按policy.date/islands对齐frame、tide、report和survey，"
            "区分observed_at与survey.event_at，核对坐标、潮位unit/datum/baseline和claim_id冲突。"
            "可用日期工具核验公开时间与观测时间；回溯记述不能改写原始观测时序，无法归属岛屿的记录保留uncertainty。"
            + self.numeric_boundary()
        )


class RouteExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g09.road_disruption_detour"

    def instructions(self, context):
        return (
            "你负责道路中断与替代路径。核对带时区snapshot、expected_od、edge方向/距离/状态/速度及notice有效期和优先级，"
            "再逐OD调用geo.route_detour复算可达状态、边序列、距离和时间。"
            "过期或尚未生效通知不得改变快照；baseline与灾后路径必须同口径比较，无路径时不得虚构绕行。"
            + self.numeric_boundary()
        )


class AgricultureExecutor(GeospatialAnalysisExecutor):
    executor_id = "analyze.g10.agriculture_exposure_union"

    def instructions(self, context):
        return (
            "你负责多期农业暴露评估。核对metric_crs、start/end、priority_thresholds、district/grid/crop分类年份和统计面积，"
            "以及flood/coverage几何的观测日期。使用geo.overlay_area复核分区叠加，重点检查多期并集与重复覆盖的区别。"
            "重复受淹次数不能累加成并集面积，未知覆盖不得当成未受淹；priority只按冻结阈值生成。"
            + self.numeric_boundary()
        )
