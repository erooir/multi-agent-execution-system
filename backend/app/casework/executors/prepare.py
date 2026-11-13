from ..narrative_checks import monthly_facts
from .base import PrepareExecutor, compact


class SourcePreparationExecutor(PrepareExecutor):
    def instructions(self, context):
        common = (
            "你是资料与坐标解析员。主工具会按冻结来源版本解析PDF、Office、CSV、图片、GeoTIFF和音视频，"
            "完整records只能来自该工具。先检查主工具投影中的来源覆盖、定位和缺口，再按需调用PDF页读取或时间码工具。"
            "不要改写source_id、source_version、location、fields或text，不要把模型猜测补成解析记录。"
        )
        if context.code == "B03":
            return common + (
                "本用例重点核对图像编号、可见特征、bbox坐标空间、宽高和馆藏候选的解析完整性；"
                "像素匹配结果只承认受限视觉工具已经生成的记录，不根据文件名推断图像内容。"
            )
        if context.code == "T05":
            return common + (
                "本用例重点核对图号、图注、单位、情景、标注值及其value_source；"
                "区分正文数值、图表对象和OCR标签，不能把相邻文本自动当成图中标注值。"
            )
        if context.code == "B05" and context.execution.case_plan:
            return common + (
                "本轮是受限图表OCR修复。只处理修复计划targets指定的来源，比较修复前后月度标签、单位和总数口径；"
                "未列入targets的原件不得重解析或替换。"
            )
        return (
            common + "逐来源确认页、行、像素或时间位置是否齐全；无法解码或缺少必需字段时保留具体source_gaps。"
        )

    def main_description(self, context):
        return "解析本节点全部冻结原件并生成权威case_records；无参数，不得重复调用"

    def project(self, output, context):
        content = output["content"]
        by_source = {}
        for row in content["records"]:
            key = (row["source_id"], str(row["source_version"]))
            entry = by_source.setdefault(key, {"count": 0, "locations": []})
            entry["count"] += 1
            if len(entry["locations"]) < 8:
                entry["locations"].append(row["location"])
        return {
            "type": output["type"],
            "records": compact(content["records"], max_items=16, max_chars=350),
            "source_coverage": [
                {"source_id": key[0], "source_version": key[1], **value} for key, value in by_source.items()
            ],
            "record_count": len(content["records"]),
            "monthly_facts": monthly_facts(content) if context.code == "B05" else None,
            "gaps": content["gaps"],
            "metrics": output["metrics"],
        }
