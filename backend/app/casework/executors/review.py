from .base import ReviewExecutor, compact


class IndependentReviewExecutor(ReviewExecutor):
    def main_description(self, context):
        return f"独立审核{context.code}的完整候选产品、原始解析记录、参考批次和阅读覆盖，生成权威case_report；无参数"

    def instructions(self, context):
        focus = {
            "B03": "额外核对archive图像编号、bbox和候选匹配审核，不以图像说明替代像素证据",
            "B05": "核对候选tool_findings中的月度主键与同范围总数复算，并比较产品月数、单位和人口范围",
            "T04": "核对项目编号主键复算与portfolio数量、机构关系和资金口径",
            "G03": "核对年度基线独立复算的单位、目标年、差值、百分比和排名",
            "G06": "核对逐期class_transition复算与转移面积、有效像元范围",
            "G09": "核对每个OD独立路径复算的可达状态、边序列、距离和时间",
        }.get(context.code, "按本角色acceptance逐项检查产品、证据和资料覆盖")
        return (
            "你是独立证据核查员，不是分析结果的润色者。主工具必须实际执行engine.audit、"
            "文档/地理专属审核、参考批次覆盖和阅读覆盖。检查主工具review及上游真实tool_findings，"
            "再按需用PDF页工具核对逐字证据。"
            + focus
            + "。确定性hard failure不可通过、删除或改写；模型附加检查只能以model_开头并引用已有evidence_ids，"
            "仅作为诊断建议保存，不写入权威review/gaps。不要因风格、篇幅或自设标准追加失败，只报告违反明确目标、合同或证据的具体问题。"
        )

    def project(self, output, context):
        content = output["content"]
        return {
            "type": output["type"],
            "case_id": content["case_id"],
            "scope": content["scope"],
            "products": compact(content["products"], max_items=16, max_chars=450),
            "evidence_ids": [item["id"] for item in content["evidence"]],
            "gaps": content["gaps"],
            "review": content["review"],
            "reading_notes": content["reading_notes"],
            "reference_batch": content.get("reference_batch"),
            "metrics": output["metrics"],
        }

    def previous(self, context):
        return [
            {
                "type": "case_records",
                "record_count": len(context.prepared["records"]),
                "gaps": context.prepared["gaps"],
                "tool_findings": compact(context.prepared.get("tool_findings", []), max_items=12),
            },
            {
                "type": "case_candidate",
                "products": compact(context.candidate["products"], max_items=16, max_chars=450),
                "evidence_ids": [item["id"] for item in context.candidate["evidence"]],
                "gaps": context.candidate["gaps"],
                "tool_findings": compact(context.candidate.get("tool_findings", []), max_items=12),
            },
        ]
