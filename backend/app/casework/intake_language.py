"""Bilingual subject, operation and context vocabulary for the installed case contracts.

These describe questions and source schemas. They contain no sample answers.
"""

# Discriminating subject; requested operation; supporting context. Shared operations
# (e.g. comparing clauses) need either a subject or discriminating project material.
INTENTS = {
    "B01": (
        "观察团日报|监测日报|每日监测报告|停火违规|违反停火|停火遭到破坏|ceasefire|monitoring mission",
        "地区对比|统计快报|上一报告期|区域观察|比前一天|daily report|previous reporting period",
        "地区|报告期|上一期|civilian|region",
    ),
    "B02": (
        "联合声明|停火协议|共同声明|公告承诺|joint statement|ceasefire agreement",
        "承诺|什么时候生效|生效条件|执行条件|各方同意|履行|commitment|entry into force|undertaking",
        "公告|声明|双方|生效|agreement",
    ),
    "B03": (
        "馆藏|典藏|历史照片|档案照片|老照片|museum collection|archival photograph",
        "编目|匹配档案|拍的是|藏品编号|辨认|catalogue|catalog entry|archive record",
        "图像|图片|照片|影像|photograph|image",
    ),
    "B04": (
        "记者会|记者招待会|新闻发布会|会议问答|记者提问|press briefing|press conference",
        "谁问|谁答|问答|发言人|逐字稿|时间码|timecode|question and answer|transcript",
        "发言|提问|字幕|speaker|会议",
    ),
    "B05": (
        "月度伤亡|平民伤亡|逐月伤亡|monthly casualties|civilian casualties",
        "逐月|按月|逐个月|月度|每个月|核对总数|monthly|month by month",
        "总数|总量|部分月份|覆盖|伤亡|deaths|injured|total",
    ),
    "B06": (
        "历史声明|历史条约|外交声明|历史宣言|俄文声明|historical declaration|historic treaty",
        "条款|条文|两个版本|两版|逐条|clauses|clause|provisions",
        "旧版|新版|术语|译注|俄文|declaration|treaty",
    ),
    "B07": (
        "民生|人道需求|民生指标|humanitarian needs|humanitarian indicators",
        "两期|双期|统计口径|两个时期|可比|compare periods|comparability",
        "地区|截至|覆盖面|定义|指标|cutoff",
    ),
    "B08": (
        "法院|司法认定|法庭|判决|司法文件|court judgment|judicial finding|tribunal",
        "事实认定|认定层次|调查结论|认定|调查与|evidence matrix|findings|investigation",
        "证据|调查|事实|judicial|court",
    ),
    "B09": (
        "解密|历史档案|档案录音|declassification|declassified|historical archive",
        "复盘|时间线|时间轴|先后顺序|形成时间|公开时间|chronology|timeline",
        "讲话|录音|事件|形成|公开|档案|archived",
    ),
    "B10": (
        "事件报道|事件简报|新闻简报|新闻报道|首份简报|incident report|news coverage|incident brief",
        "版本|叙述变化|说法|后续调查|来源追踪|最初|initial account|reporting changed",
        "事件|简报|调查|来源|报道|report",
    ),
    "T01": (
        "论文|科研论文|研究论文|科学文献|文献阅读卡|scientific paper|research paper|research article",
        "试验|实验|实测|模型结果|局限|研究结论|阅读卡|研究方法|limitations|measurements|experimental",
        "文献|研究|结论|作者|method|experiment",
    ),
    "T02": (
        "里程碑|重大节点|milestone",
        "去重|更新|合并|同一件事|重复报道|new milestones|deduplicate",
        "项目|时间|计划|project",
    ),
    "T03": (
        "技术路线|研发路线|技术路线图|roadmap|technology roadmap",
        "快照|路线变化|阶段变化|怎么调整|两版|路线对比|快照对比|snapshot|roadmap changes",
        "项目|阶段|计划|版本|stage",
    ),
    "T04": (
        "项目组合|经费分配|项目资金|参与机构|资助机构|项目资助|research portfolio|project portfolio|funding network",
        "机构关系|合作网络|哪家机构|总资助|多少经费|funding|organization graph|collaboration",
        "研究|项目|机构|经费|机构关系|participants",
    ),
    "T05": (
        "图表情景|图中标注|图注|图表|figure caption|plotted values|labelled values|figure labels",
        "情景|读数|图号|标注值|图上|场景|从图|chart values|scenario|figure",
        "数值|单位|年份|排放|emissions|图",
    ),
    "T06": (
        "标准草案|技术标准|征求意见稿|标准正式版|规范草稿|draft standard|final standard|technical standard",
        "条款|条文|两个版本|两版|逐条|clauses|clause|provisions",
        "草案|定稿|意见稿|正式版|draft|standard",
    ),
    "T07": (
        "访谈|采访|口述史|回听|interview|oral history",
        "时间码|专题|主题索引|术语表|哪一段|第几分钟|怎么定位|timecode|timestamp|topics",
        "字幕|逐字稿|发言|录音|transcript",
    ),
    "T08": (
        "项目收尾|项目结项|结项审计|项目验收复盘|项目方和审计|project closeout|project closure|completion audit",
        "收尾|审计|各方说法|独立研究|项目方|audit|independent study",
        "项目|研究|证据|核查|report",
    ),
    "T09": (
        "电池|容量衰减|充放电|循环寿命|battery|discharge|cycle life",
        "样本趋势|容量|循环|内阻|适用范围|测试条件|capacity|cycle|resistance",
        "样本|实验|限制|边界|sample",
    ),
    "T10": (
        "论文撤稿|撤稿|科研更正|论文更正|研究撤回|retraction|retracted|paper correction",
        "引用依赖|引用影响|哪些结论|仍然成立|被引用|citation|cited|dependent claims",
        "论文|简报|独立证据|成果|doi",
    ),
    "G01": (
        "火点|火情热点|热异常|火灾热点|热点批次|hotspot|active fire|thermal anomaly",
        "新增|相邻批次|热点|去重|前一天|new fires|fire detections",
        "卫星|热点|经纬度|sensor|frp",
    ),
    "G02": (
        "多时相影像|多时相图像|不同时相|多期影像|前后两张图|multi-temporal imagery|image annotation",
        "影像变化|像素位置|图像注记|影像对照|标注变化|pixel|bounding box",
        "卫星|图像|影像|拍摄|images",
    ),
    "G03": (
        "海冰|海上浮冰|浮冰范围|冰盖范围|sea ice|sea-ice|ice extent",
        "距平|基线|常年|多年平均|往年|历史排名|比历史|异常幅度|baseline|usual|anomaly|rank",
        "北极|南极|极地|北冰洋|九月|9月|arctic|antarctic|september",
    ),
    "G04": (
        "洪水|积水|淹没|洪涝|水淹|flood|inundation",
        "道路暴露|水域变化|受淹道路|淹了哪些路|淹了多少|路段受灾|道路受影响|淹没范围|inundated roads|road exposure",
        "道路|路段|水域|洪峰|水体|road|water",
    ),
    "G05": (
        "建筑损伤|建筑受损|房屋损毁|楼房受损|建筑倒塌|建筑物|房屋|buildings|building damage",
        "损毁|损伤|受损|倒塌|街区|灾前灾后|damaged|destroyed|damage",
        "建筑|街区|灾后|底账|building",
    ),
    "G06": (
        "土地覆盖|土地利用|地表覆盖|土地类别|土地类型|用地类型|land cover|land-cover|land use",
        "转移|转为|转成|变成|新增建设用地|类别变化|类别转换|transition|conversion|changed to",
        "栅格|地类|几期|三期|地表|raster",
    ),
    "G07": (
        "环境样点|环境监测|采样点|水样|土样|污染物|实验室样品|sampling sites|environmental sample|contaminant",
        "检出|覆盖|样点|质控|采样|检测结果|检测值|sampling|analyte|laboratory",
        "介质|土壤|水质|浓度|medium|coverage",
    ),
    "G08": (
        "岛屿|岛上|海岛|潮位|小岛|岛礁|island|tidal|tide",
        "观测时间线|回溯|何时|先后|观测顺序|潮位变化|chronology|timeline",
        "观测|调查|影像|帧|survey|observation",
    ),
    "G09": (
        "道路中断|道路封闭|封路|封闭路段|道路封锁|断路|绕行|绕路|替代路径|救援路线|detour|road closure|alternative route",
        "怎么走|还能走|能否到达|替代路线|通行限制|哪条路|少走|最短路|绕道|alternate route|reachable|reroute",
        "道路|救援车|路网|车辆|起点|终点|road|vehicle",
    ),
    "G10": (
        "农田|耕地|农作物|作物|cropland|agricultural|farmland|crops",
        "受淹|受灾|暴露|淹没|受影响|重复受灾|灾损|淹了|exposure|flooded|inundation",
        "多期|洪水|面积|县|网格|flood|district",
    ),
}
