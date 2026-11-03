from .base import AnalyzeExecutor, compact


class DocumentAnalysisExecutor(AnalyzeExecutor):
    def main_description(self, context):
        role = context.agent["role_contract"]
        return f"执行{role['operation']}并生成权威{context.code}候选产品、证据和缺口；无参数，不得重复调用"

    def project(self, output, context):
        content = output["content"]
        return {
            "type": output["type"],
            "products": compact(content["products"], max_items=20, max_chars=650),
            "evidence": compact(content["evidence"], max_items=24, max_chars=500),
            "gaps": content["gaps"],
            "metrics": output["metrics"],
        }

    def patch_instruction(self):
        if not self.patch_fields:
            return "products_patch必须为空；数值、图表、媒体对齐和确定性比较结果只能采用工具产物。"
        return (
            "products_patch只能按完整字段值替换以下语义产品："
            + "、".join(sorted(self.patch_fields))
            + "；不得部分合并、增删产品键或伪造evidence_ids。其他产品保持工具原值。"
        )


class ObservationExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b01.observation_statistics"
    patch_fields = frozenset({"context"})

    def instructions(self, context):
        return (
            "你负责区域观察统计快报。先让主工具抽取region、current、previous、report_number、published_date和observed_until，"
            "再核对统计窗口、时区与民用事项是否一致；可调用数值和时间工具定位原文。"
            "观察值与比较值不得互换，报告期不能由发布日期猜算。context只能用已有证据解释统计背景。"
            + self.patch_instruction()
        )


class CommitmentExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b02.commitment_conditions"
    patch_fields = frozenset({"commitments", "missing_details"})

    def instructions(self, context):
        return (
            "你负责公告承诺与执行条件核读。按cessation、humanitarian、services逐主题检查承诺原文、当事方、公告日期和生效条件，"
            "区分公告、声明及后续执行文件。没有明确effective_date时必须保留未明确状态，不得从发布日期推算。"
            "可用时间抽取和术语工具回查；新增语义表述必须逐条引用主工具证据。" + self.patch_instruction()
        )


class ArchiveImageExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b03.archive_image_catalog"

    def instructions(self, context):
        return (
            "你负责历史影像与馆藏记录编目。主工具完成同编号候选匹配、可见特征和像素坐标整理；"
            "你应检查image_id唯一性、bbox坐标空间、图像宽高、观察与馆藏出处是否对应。"
            "可用table.audit_keys检查编号冲突，但不得凭文件名或相似文字宣称图像匹配成功。"
            + self.patch_instruction()
        )


class MonthSeriesExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b05.monthly_reconciliation"

    def instructions(self, context):
        return (
            "你负责月度统计复算与覆盖核查。先核对month、起止日期、scope和统计限制，再检查detail与total的同单位复算、"
            "月份唯一性和图表OCR标签覆盖。按需调用数值抽取、主键审计和total复算工具。"
            "不同人群、范围和单位不得合并；任何总数、趋势和差异只能来自主处理或复算工具。"
            + self.patch_instruction()
        )


class PeriodComparisonExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b07.period_method_comparison"
    patch_fields = frozenset({"coverage_notes"})

    def instructions(self, context):
        return (
            "你负责双期民生统计口径比较。按indicator和region配对两期记录，先核对定义、覆盖范围、方法、单位和截至日，"
            "再解释可比性；不能仅因指标同名就计算或暗示变化。可用数值与主键工具检查缺项和重复。"
            "coverage_notes只总结有证据的口径差异和覆盖限制。" + self.patch_instruction()
        )


class ChronologyExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.b09.archive_chronology"
    patch_fields = frozenset({"variants"})

    def instructions(self, context):
        return (
            "你负责历史档案多时间线复盘。围绕event_id分别整理事件发生、档案形成、公开和解密时间，"
            "保留time_precision、timezone、channel和recipient差异。可调用日期和时间码工具核验。"
            "未知时间不得由相邻档案日期补齐；variants只归纳各来源确有差异的记述，不能创造新事件。"
            + self.patch_instruction()
        )


class ResearchReadingExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t01.research_reading_card"
    patch_fields = frozenset({"interpretation"})

    def instructions(self, context):
        return (
            "你负责试验研究证据阅读卡。先核对title、authors、institution、DOI和研究问题，"
            "再区分comparison、measurement、model_result与limitation，避免把模型结果写成已证实现实结论。"
            "按需使用术语和数值工具定位实验条件。interpretation只组织工具证据支持的解释，局限不得删除。"
            + self.patch_instruction()
        )


class MilestoneExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t02.milestone_deduplication"
    patch_fields = frozenset({"brief", "unmatched"})

    def instructions(self, context):
        return (
            "你负责项目里程碑去重与更新。按project、event_type和event_date归并历史与新发布记录，"
            "区分事件发生日期和发布日期，保留role及outcome差异。使用日期和主键工具核查重复与冲突。"
            "无法唯一对应的里程碑必须留在unmatched，brief不能掩盖未匹配项。" + self.patch_instruction()
        )


class RoadmapExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t03.roadmap_snapshot"
    patch_fields = frozenset({"unverified_stages"})

    def instructions(self, context):
        return (
            "你负责研发路线快照对比。先以project和feature匹配历史、新路线，再比较stage、date和value；"
            "同名项目但技术主题不同不得强行对齐。可用日期与段落比较工具核验版本变化。"
            "unverified_stages只列缺乏双方证据或对应关系不唯一的阶段，不重写确定性route_history。"
            + self.patch_instruction()
        )


class ProjectPortfolioExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t04.project_portfolio_graph"
    patch_fields = frozenset({"theme_summary"})

    def instructions(self, context):
        return (
            "你负责研究项目组合与机构关系。以稳定project_id归一项目，区分coordinator和participants，"
            "核对主题词表、机构别名、币种、total_cost与contribution口径。按需调用主键和总额复算工具。"
            "关系图不能从同名机构猜测合并；theme_summary只归纳已归类的主题证据。" + self.patch_instruction()
        )


class ChartDataExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t05.figure_scenario_values"

    def instructions(self, context):
        return (
            "你负责图表情景与标注值核读。按figure、title、unit、year和scenario整理标注值，"
            "严格区分正文提及、图注、图中标签与计算所得value_kind。可用PDF页读取和数值抽取复核原文。"
            "不得把其他年份、情景或单位的数值移入图表；缺失坐标或标注依据时保留缺口。"
            + self.patch_instruction()
        )


class BatteryExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t09.sample_trend_applicability"
    patch_fields = frozenset({"applicability"})

    def instructions(self, context):
        return (
            "你负责研究样本趋势与适用边界。按battery_id和cycle核对capacity、resistance及各自单位，"
            "结合load、temperature、conditions和population判断字段覆盖与趋势。"
            "不同测试条件和样本群不得拼成单一趋势；数值序列来自工具，applicability只概括证据支持的适用范围。"
            + self.patch_instruction()
        )


class CitationImpactExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.t10.citation_dependency"
    patch_fields = frozenset({"independent_research"})

    def instructions(self, context):
        return (
            "你负责科研状态变化与引用依赖核查。以DOI和claim_id追踪status、date、statement、cites及supports_claims，"
            "区分简报自述、被引证据和独立研究。可用日期与主键工具核查成果标识和引用覆盖。"
            "没有独立来源时必须明确依赖关系；independent_research不得把引用清单包装成独立验证。"
            + self.patch_instruction()
        )


class ClauseExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.shared.clause_alignment"
    patch_fields = frozenset({"commentary"})

    def instructions(self, context):
        if context.code == "B06":
            scope = "旧版与新版历史声明的side、发布日期和条款角色"
        else:
            scope = "技术标准草案与定稿的side、发布日期、生效日期及评论角色"
        return (
            f"你负责条款版本对照。先核对{scope}，再按显式match_id、clause_id和受限唯一topic对齐双方条款。"
            "每项匹配必须同时具有双方证据；模糊或不唯一对应留在unmatched。"
            "使用段落比较与术语工具核查字符差异和解释，不把译注当作条款正文。commentary只组织有出处的评论或解释。"
            + self.patch_instruction()
        )


class SpeechExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.shared.timecoded_speech"
    patch_fields = frozenset({"topics", "glossary"})

    def instructions(self, context):
        if context.code == "B04":
            scope = "会议问答的说话人、turn_type、topic和独立逐字稿"
            tools = "时间码选择与段落比较"
        else:
            scope = "访谈说话人、专题、utterance、hearing和可选独立逐字稿"
            tools = "时间码选择与术语表"
        return (
            f"你负责时间码发言对照。围绕media_id和单调start_ms/end_ms整理{scope}，"
            "保证发言顺序不重叠，独立脚本未匹配与歧义不得被静默丢弃。"
            f"按需调用{tools}工具。topics和glossary可依据真实发言重组，但timed_index、时间码及differences不得由模型改写。"
            + self.patch_instruction()
        )


class EvidenceMatrixExecutor(DocumentAnalysisExecutor):
    executor_id = "analyze.shared.multisource_evidence_matrix"
    patch_fields = frozenset({"unverified"})

    def instructions(self, context):
        roles = {
            "B08": "investigation与judicial，并区分调查陈述和司法认定",
            "B10": "brief与investigation，并区分简报记述和后续调查版本",
            "T08": "project、audit与study，并区分项目方、审计和研究依据",
        }[context.code]
        return (
            f"你负责多来源证据矩阵。逐question_id核对{roles}，比较statement、scope、value/evidence_type和published_date。"
            "不得把一个来源的陈述升级为另一来源的认定；同一问题的冲突与版本变化必须保留在version_timeline。"
            "按需调用段落比较、日期或数值工具。unverified只归并确实缺乏对应来源或口径的项目，matrix不得由模型重写。"
            + self.patch_instruction()
        )
