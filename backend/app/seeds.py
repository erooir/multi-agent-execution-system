"""Small invented demonstration corpus. Never reads background project documents."""

from __future__ import annotations

import io
import threading
from copy import deepcopy
from pathlib import Path

from .storage import store

_seed_lock = threading.RLock()
DATASET_VERSION = "synthetic-demo-v1"

PROJECTS = [
    {
        "id": "project-technology",
        "name": "航空科技专题研究",
        "category": "technology",
        "description": "以合成的民用航空技术资料演示文献检索、专题研究与证据报告。资料不对应真实项目。",
    },
    {
        "id": "project-geography",
        "name": "地理环境对比研究",
        "category": "geography",
        "description": "以虚构园区网格资料演示地理变化、数据质量与跨时相证据整理，不提供真实地理目标信息。",
    },
    {
        "id": "project-situational",
        "name": "态势事件协同研判",
        "category": "situational",
        "description": "以虚构民用机场服务事件演示时间线、来源交叉验证与人工复核，全部为合成演练数据。",
    },
]

AGENTS = [
    {
        "id": "agent-planner",
        "name": "任务规划智能体",
        "role": "planner",
        "description": "识别目标、约束与输出要求，将任务组织为可校验执行流。",
        "instructions": "明确目标、对象、时间范围与输出形式。只使用登记技能；缺少信息时列出假设。生成结构化任务，不编造工具能力。",
        "skill_ids": [],
    },
    {
        "id": "agent-retriever",
        "name": "知识检索智能体",
        "role": "retriever",
        "description": "组织检索与解析，保留来源位置、证据片段与资料外发边界。",
        "instructions": "引用实际检索片段并保留证据标识。区分事实与推断，明确资料缺口。仅本地资料禁止交给外部模型。",
        "skill_ids": [
            "knowledge_search",
            "graph_query",
            "document_parse",
            "ocr",
            "semantic_search",
            "multimodal",
        ],
    },
    {
        "id": "agent-writer",
        "name": "报告生成智能体",
        "role": "writer",
        "description": "按照模板组织有来源的结论、限制与人工修改意见。",
        "instructions": "每条重要事实应有真实证据。不得伪造来源或未检索到的数字。合成资料须明确标注，保留不确定性与审核意见。",
        "skill_ids": ["document_parse"],
    },
    {
        "id": "agent-parser",
        "name": "文档解析智能体",
        "role": "parser",
        "description": "解析上传的文档与图片，抽取结构化要点并交给下游智能体处理。",
        "instructions": "忠实整理上传资料的结构与要点，保留来源位置与证据标识。不补充资料之外的事实，无法解析时明确说明原因。仅本地资料禁止交给外部模型。",
        "skill_ids": ["document_parse", "ocr", "multimodal"],
    },
    {
        "id": "agent-coordinator",
        "name": "协同调度智能体",
        "role": "coordinator",
        "description": "协调节点依赖、批量处理、条件分支、人工审核和异常恢复。",
        "instructions": "遵循经过校验的流程和输入输出约束。失败应明确记录，禁止将跳过或模拟结果标为真实成功。",
        "skill_ids": ["knowledge_search", "graph_query"],
    },
]

CORPUS = {
    "technology": [
        (
            "合成资料_复合材料研究摘录.md",
            """# 合成演示资料：民用航空复合材料研究摘录
本文件完全虚构，仅用于软件功能演示；机构、结果、年份与数字不代表真实研究结论。

## 条目 T01：可回收热塑性复合材料
2024 年，虚构的青禾材料研究组在实验室测试 12 组热塑性复合材料试样。研究对象是民用客舱内饰板。演示指标记录回收工序、样本数量及质量一致性，不证明已通过适航认证。技术要点是加热重塑与纤维回收；限制是样本量较小、缺少长周期使用数据。

## 条目 T02：复合材料损伤检测
2025 年，虚构的云帆结构实验室使用超声检测方法检查复合材料试样。合成记录包含 18 个试样和 3 类人工缺陷。研究主题为无损检测、质量检查、数据可追溯。与 T01 的联系是回收材料也需要质量复核，但资料没有说明两项试验使用相同试样。

## 条目 T03：研究趋势与证据边界
2026 年，合成综述将复合材料研究分为材料循环利用、损伤检测和制造质量三条主题。趋势仅依据本演示语料，不能外推至整个行业；未提供成本对照试验，不应声称生产成本已经下降。
""",
        ),
        (
            "合成资料_民航低碳技术.json",
            [
                {
                    "id": "T04",
                    "year": 2024,
                    "topic": "电动滑行",
                    "source": "合成公开演示摘录",
                    "finding": "虚构的晨曦团队提出地面电动滑行方案，评估框架关注能耗、维护与运行衔接。",
                    "limitation": "没有真实运行数据，不能计算节能收益。",
                    "synthetic": True,
                },
                {
                    "id": "T05",
                    "year": 2025,
                    "topic": "民航低碳技术",
                    "source": "合成公开演示摘录",
                    "finding": "虚构的雨杉团队整理可持续航空燃料文献，区分燃料生命周期边界与运行阶段排放。",
                    "limitation": "演示语料不提供可比排放数值。",
                    "synthetic": True,
                },
                {
                    "id": "T06",
                    "year": 2026,
                    "topic": "低碳技术比较",
                    "source": "合成公开演示摘录",
                    "finding": "电动滑行与可持续航空燃料作用于不同环节，比较时须统一评价边界。",
                    "limitation": "不支持简单排名或真实采购建议。",
                    "synthetic": True,
                },
            ],
        ),
        (
            "合成资料_文献登记表.csv",
            "编号,主题,年份,方法,证据限制,性质\nT07,复合材料制造质量,2024,试样记录整理,未开展量产试验,合成演示\nT08,复合材料无损检测,2025,超声检测与人工复核,仅限虚构样本,合成演示\nT09,民航低碳技术综述,2026,文献主题比较,缺少一致口径的量化指标,合成演示\nT10,技术成熟度评估,2026,公开资料证据检查,无适航认证或实机验证材料,合成演示\n",
        ),
    ],
    "geography": [
        (
            "合成资料_园区网格对比.md",
            """# 合成演示资料：虚构园区网格变化
所有网格均为抽象编号，不对应真实经纬度、机场、设施或人员位置。数字只用于测试来源引用与批量对比。

## G01：A1 网格跨期观察
2025 年第一期，A1 网格登记绿地区块 12 个；2026 年第二期登记 14 个。第二期增加 2 个区块，但登记规则改为分开记录狭长绿带。没有原始遥感影像，因此不能直接认定实际绿地面积增长。

## G02：A2 网格数据质量
A2 网格第一期与第二期都登记 8 个地表分区，第二期备注中有 1 个分区缺少采集时间。跨期比较需先复核时间和统计口径，不得用缺失字段推断变化原因。

## G03：B1 网格环境记录
B1 网格在合成记录中包含道路、绿地和步行区域。演示任务仅整理环境要素与数据缺口；无路线通行测试和真实环境测量，不能据此生成实际通行或设施部署建议。
""",
        ),
        (
            "合成资料_环境要素清单.csv",
            "网格,期次,要素,登记数量,备注,性质\nA1,2025第一期,绿地区块,12,旧统计规则,合成演示\nA1,2026第二期,绿地区块,14,狭长绿带独立计数,合成演示\nA2,2025第一期,地表分区,8,时间字段完整,合成演示\nA2,2026第二期,地表分区,8,一项缺少采集时间,合成演示\nB1,2026第二期,步行区域,3,无实际通行验证,合成演示\n",
        ),
        (
            "合成资料_空间数据复核规则.txt",
            "合成演示资料：空间数据复核规则。\n规则 GQ1：先核对时间范围、网格编号和统计口径，再比较数量差异。\n规则 GQ2：数量变化不是面积变化，统计定义变化不是实地变化。\n规则 GQ3：对缺少时间、分辨率或原始影像的数据，在报告中列出证据缺口。\n规则 GQ4：地理环境分析报告包括研究范围、资料来源、变化记录、限制和人工复核意见。\n本文件不含真实地理位置，不可用于实际行动规划。",
        ),
    ],
    "situational": [
        (
            "合成资料_服务事件时间线.md",
            """# 合成演示资料：虚构星河民用机场服务事件
事件、地点、时间与人员角色均为虚构，只演示资料研判与人工审核，不代表实时运营状态。

## E01：第一条事件登记
演练日 09:00，服务台收到合成记录：候机区一块信息屏显示延迟。记录者注明信息来自值班观察，未确认延迟原因；该信息不涉及飞行运行状态。

## E02：第二条信息来源
演练日 09:08，维护登记称信息屏已重新同步，但记录尚未由服务台复核。维护记录与服务观察的来源不同，不能仅根据单条记录宣布全部服务恢复。

## E03：人工复核结果
演练日 09:15，服务台合成复核条目确认该信息屏显示已恢复。该结论仅适用于这块屏幕，不应推断其他设施状态。事件摘要需保留 09:00、09:08 与 09:15 三条记录及来源。
""",
        ),
        (
            "合成资料_事件来源核验.json",
            [
                {
                    "id": "E04",
                    "event": "信息屏显示延迟",
                    "source_type": "服务台观察",
                    "time": "演练日09:00",
                    "verified": False,
                    "note": "不能推断原因和影响范围",
                    "synthetic": True,
                },
                {
                    "id": "E05",
                    "event": "信息屏重新同步",
                    "source_type": "维护登记",
                    "time": "演练日09:08",
                    "verified": False,
                    "note": "需要服务台交叉复核",
                    "synthetic": True,
                },
                {
                    "id": "E06",
                    "event": "信息屏显示恢复",
                    "source_type": "人工复核",
                    "time": "演练日09:15",
                    "verified": True,
                    "note": "仅对应单块屏幕，不代表其他设施",
                    "synthetic": True,
                },
            ],
        ),
        (
            "合成资料_事件报告规则.txt",
            "合成演示资料：事件研判规则。\n态势事件研判需按时间排序，区分观察、维护登记和人工确认。\n来源冲突时应保留两种说法，标明各自时间和证据状态，不得擅自选择有利结论。\n任务执行摘要包括任务目的、实际运行节点、检索材料、异常、人工审核及结果。\n证据不足时转入人工复核，列出待补充资料；不得根据模板自动补造原因或影响范围。\n本演示不接入实时系统，不输出真实运行指令。",
        ),
    ],
}

TEMPLATES = [
    (
        "workflow-tech-trends",
        "technology",
        "航空科技专题研究",
        "整理研究主题与技术进展，保留来源和证据限制",
        ["技术主题", "研究方法", "证据限制"],
        "technology",
    ),
    (
        "workflow-tech-comparison",
        "technology",
        "文献方法对比与核验",
        "比较方法和评价边界，避免将不可比数据强行排序",
        ["方法比较", "数据口径", "待补充证据"],
        "technology",
    ),
    (
        "workflow-geo-change",
        "geography",
        "地理环境跨期对比",
        "基于合成网格记录整理变化及统计口径差异",
        ["跨期记录", "统计口径", "证据缺口"],
        "geography",
    ),
    (
        "workflow-geo-quality",
        "geography",
        "空间资料质量复核",
        "检查时间字段、计数定义与来源完备性",
        ["时间完整性", "要素一致性", "人工复核事项"],
        "geography",
    ),
    (
        "workflow-event-timeline",
        "situational",
        "态势事件时间线研判",
        "整理合成服务事件的多来源时间线与核验状态",
        ["事件时间线", "来源交叉核验", "不确定事项"],
        "situational",
    ),
    (
        "workflow-event-summary",
        "situational",
        "任务执行摘要与交接",
        "形成有审核记录的任务执行摘要和待办清单",
        ["执行证据", "复核记录", "待办清单"],
        "summary",
    ),
]

SAMPLE_PROMPTS = {
    "workflow-tech-trends": [
        "整理合成资料中 2024—2026 年复合材料研究的主题变化，生成带来源的专题报告。",
        "梳理合成资料中的可回收热塑性复合材料研究，列出试验对象、方法和限制。",
        "根据演示文献总结复合材料无损检测的研究方法，区分实测记录和综述归纳。",
        "研究合成资料中的民航低碳技术主题，列出电动滑行与可持续航空燃料的评价边界。",
        "基于文献登记表整理技术成熟度证据缺口，提交人工复核。",
    ],
    "workflow-tech-comparison": [
        "比较合成资料中复合材料回收与损伤检测的研究对象，避免混用试样数据。",
        "对比民航低碳技术中电动滑行与航空燃料的作用环节，说明不能直接排名的原因。",
        "核验演示语料能否支持复合材料生产成本下降的结论，并引用证据。",
        "批量整理 T07、T08、T09、T10 文献的方法、年份和证据限制。",
        "比较 2024 与 2026 年复合材料主题，列出仍需补充的认证和实机验证资料。",
    ],
    "workflow-geo-change": [
        "根据合成园区资料比较 A1 网格两期绿地区块数量，说明统计规则变化。",
        "整理 A2 网格跨期记录，标注缺失采集时间对比较结论的影响。",
        "批量对比 A1、A2、B1 网格环境要素，生成地理环境分析报告。",
        "核验 A1 登记数量增加能否证明实际绿地面积增长，提供原文依据。",
        "汇总 2025 第一期和 2026 第二期地理记录的可比项与不可比项。",
    ],
    "workflow-geo-quality": [
        "按照空间数据复核规则检查合成环境要素清单，列出时间和口径缺口。",
        "复核 B1 网格的环境记录是否足以支持实际通行建议，明确证据边界。",
        "检查合成园区资料是否包含真实经纬度和原始遥感影像，生成资料完整性说明。",
        "依据 GQ1 到 GQ4 规则整理人工复核清单，并引用相关资料。",
        "对地理环境变化结论进行质量复核，区分数量、面积和登记定义。",
    ],
    "workflow-event-timeline": [
        "整理合成机场信息屏事件的 09:00、09:08、09:15 时间线，标记来源和确认状态。",
        "交叉核验服务台观察与维护登记，说明哪些结论需要人工确认。",
        "根据合成事件资料说明信息屏恢复结论的适用范围，避免扩大影响范围。",
        "批量汇总 E04、E05、E06 的事件、时间、来源和核验状态。",
        "核验演示资料能否确认信息屏延迟原因，列出不确定事项。",
    ],
    "workflow-event-summary": [
        "为合成信息屏事件生成任务执行摘要，包括证据、异常、人工复核和待办。",
        "整理维护记录尚未得到服务台复核时需要的交接事项。",
        "基于事件报告规则生成来源冲突处理清单，并提交人工审核。",
        "将演示事件记录组织为服务台交接摘要，明确不代表实时运营状态。",
        "总结信息屏事件研判使用的资料和证据限制，生成可追溯执行摘要。",
    ],
}


def _workflow(definition: tuple) -> dict:
    identity, category, name, description, items, report_template = definition
    specs = [
        ("start", "开始任务", "start", "agent-coordinator", {}),
        ("parse", "解析目标与约束", "parse", "agent-planner", {}),
        ("retrieve", "检索资料与证据", "retrieve", "agent-retriever", {"limit": 8}),
        ("condition", "检查证据是否充分", "condition", "agent-coordinator", {"min_evidence": 1}),
        ("batch", "批量整理研究维度", "batch", "agent-coordinator", {"items": items, "count": len(items)}),
        ("analyze", "综合分析与核验", "analyze", "agent-writer", {"instruction": description}),
        ("report", "生成可追溯报告", "report", "agent-writer", {"template": report_template}),
        ("review", "人工复核与修订", "review", "agent-coordinator", {}),
        ("end", "归档完成", "end", "agent-coordinator", {}),
    ]
    nodes = [
        {
            "id": identity_,
            "type": "task",
            "position": {"x": 80 + 250 * (index % 3), "y": 70 + 180 * (index // 3)},
            "data": {
                "label": label,
                "kind": kind,
                "agent_id": agent,
                "config": config,
                **({"skill_id": "knowledge_search"} if kind == "retrieve" else {}),
            },
        }
        for index, (identity_, label, kind, agent, config) in enumerate(specs)
    ]
    connections = [
        ("start", "parse"),
        ("parse", "retrieve"),
        ("retrieve", "condition"),
        ("condition", "batch"),
        ("batch", "analyze"),
        ("analyze", "report"),
        ("condition", "report"),
        ("report", "review"),
        ("review", "end"),
    ]
    edges = [
        {
            "id": f"edge-{source}-{target}",
            "source": source,
            "target": target,
            **(
                {"sourceHandle": "pass", "label": "有证据"}
                if source == "condition" and target == "batch"
                else {}
            ),
            **(
                {"sourceHandle": "fail", "label": "证据不足 → 待核草稿"}
                if source == "condition" and target == "report"
                else {}
            ),
        }
        for source, target in connections
    ]
    return {
        "id": identity,
        "name": name,
        "description": description + "。使用合成演示资料。",
        "category": category,
        "project_id": "project-" + category,
        "version": 1,
        "status": "published",
        "nodes": nodes,
        "edges": edges,
        "synthetic": True,
    }


def seed_all() -> None:
    import json

    from .knowledge import knowledge

    with _seed_lock:
        if store.get("seed_state", DATASET_VERSION):
            _migrate_seed_workflows()
            _ensure_parser_agent()
            _ensure_image_fixture(knowledge)
            return
        for project in PROJECTS:
            if not store.get("projects", project["id"]):
                store.save("projects", project)
        for agent in AGENTS:
            if not store.get("agents", agent["id"]):
                store.save("agents", {**agent, "model": "deepseek-flash", "enabled": True, "version": 1})
        for template in TEMPLATES:
            workflow = _workflow(template)
            if not store.get("workflows", workflow["id"]):
                saved = store.save("workflows", workflow)
                store.save(
                    "workflow_versions",
                    {
                        "id": f"{saved['id']}:1",
                        "workflow_id": saved["id"],
                        "version": 1,
                        "snapshot": deepcopy(saved),
                    },
                )
            for index, prompt in enumerate(SAMPLE_PROMPTS[template[0]], 1):
                sample_id = f"sample-{template[0]}-{index}"
                if not store.get("samples", sample_id):
                    store.save(
                        "samples",
                        {
                            "id": sample_id,
                            "name": prompt[:30],
                            "prompt": prompt,
                            "category": template[1],
                            "project_id": "project-" + template[1],
                            "workflow_id": template[0],
                            "expected_goal": template[3],
                            "expected_category": template[1],
                            "criteria": [
                                "流程结构有效",
                                "检索结果可定位",
                                "人工复核节点到达",
                                "报告保留合成资料声明",
                            ],
                            "synthetic": True,
                            "dataset_version": DATASET_VERSION,
                        },
                    )
        documents: dict[str, list[dict]] = {}
        existing = {doc.get("seed_key"): doc for doc in store.list("documents") if doc.get("seed_key")}
        for category, records in CORPUS.items():
            documents[category] = []
            for index, (filename, content) in enumerate(records):
                seed_key = f"{DATASET_VERSION}-{category}-{index}"
                document = existing.get(seed_key)
                if not document:
                    raw = (
                        json.dumps(content, ensure_ascii=False, indent=2)
                        if isinstance(content, (dict, list))
                        else content
                    ).encode("utf-8")
                    document = knowledge.ingest(filename, raw, "project-" + category, "external")
                    document = store.save(
                        "documents",
                        {
                            **document,
                            "seed_key": seed_key,
                            "synthetic": True,
                            "source_note": "本项目自编合成演示资料，不对应真实研究或事件",
                        },
                    )
                documents[category].append(document)
        graph_definitions = {
            "technology": [
                ("复合材料", "材料循环利用", "研究方向"),
                ("复合材料", "无损检测", "质量复核"),
                ("民航低碳技术", "评价边界", "需要统一"),
            ],
            "geography": [
                ("A1 网格", "统计口径变化", "复核重点"),
                ("A2 网格", "采集时间缺失", "质量问题"),
                ("B1 网格", "通行验证缺失", "证据限制"),
            ],
            "situational": [
                ("信息屏事件", "服务台观察", "初始来源"),
                ("信息屏事件", "维护登记", "后续来源"),
                ("维护登记", "人工复核", "需要核验"),
            ],
        }
        for category, triples in graph_definitions.items():
            for index, (source, target, relation) in enumerate(triples):
                source_id = f"graph-{category}-{source}"
                target_id = f"graph-{category}-{target}"
                document = documents[category][0]
                chunks = knowledge.chunks(document["id"])
                matching = next(
                    (chunk for chunk in chunks if source in chunk["text"] or target in chunk["text"]),
                    chunks[0] if chunks else None,
                )
                for node_id, label in ((source_id, source), (target_id, target)):
                    if not store.get("graph_nodes", node_id):
                        store.save(
                            "graph_nodes",
                            {
                                "id": node_id,
                                "label": label,
                                "type": "concept",
                                "project_id": "project-" + category,
                                "description": "合成演示知识关系",
                                "synthetic": True,
                            },
                        )
                edge_id = f"graph-edge-{category}-{index}"
                if not store.get("graph_edges", edge_id):
                    store.save(
                        "graph_edges",
                        {
                            "id": edge_id,
                            "source": source_id,
                            "target": target_id,
                            "label": relation,
                            "relation": relation,
                            "project_id": "project-" + category,
                            "document_id": document["id"],
                            "chunk_id": matching["id"] if matching else None,
                            "visibility": "external",
                            "synthetic": True,
                        },
                    )
        _ensure_image_fixture(knowledge)
        _ensure_parser_agent()
        store.save("seed_state", {"id": DATASET_VERSION, "synthetic": True, "sample_count": 30})
        store.audit(
            "seed.initialize",
            DATASET_VERSION,
            {"projects": 3, "agents": len(store.list("agents")), "workflows": 6, "samples": 30, "documents": 10},
        )


def _ensure_parser_agent() -> None:
    """Backfill the document-parser agent into databases seeded before it existed."""
    for agent in AGENTS:
        if not store.get("agents", agent["id"]):
            store.save("agents", {**agent, "model": "deepseek-flash", "enabled": True, "version": 1})


def _migrate_seed_workflows() -> None:
    """Upgrade only untouched v1 seed templates; historical snapshots stay intact."""
    for definition in TEMPLATES:
        current = store.get("workflows", definition[0])
        if (
            not current
            or not current.get("synthetic")
            or current.get("version") != 1
            or current.get("status") != "published"
        ):
            continue
        desired = _workflow(definition)
        if current.get("nodes") == desired["nodes"] and current.get("edges") == desired["edges"]:
            continue
        previous_snapshot = f"{current['id']}:1"
        if not store.get("workflow_versions", previous_snapshot):
            store.save(
                "workflow_versions",
                {
                    "id": previous_snapshot,
                    "workflow_id": current["id"],
                    "version": 1,
                    "snapshot": deepcopy(current),
                },
            )
        updated = store.save(
            "workflows",
            {
                **current,
                "nodes": desired["nodes"],
                "edges": desired["edges"],
                "version": 2,
                "migration": "draft-before-review",
            },
        )
        store.save(
            "workflow_versions",
            {
                "id": f"{updated['id']}:2",
                "workflow_id": updated["id"],
                "version": 2,
                "snapshot": deepcopy(updated),
            },
        )
        store.audit(
            "workflow.seed_migrate",
            updated["id"],
            {"from_version": 1, "to_version": 2, "reason": "报告草稿生成后进入人工审核；历史执行快照保留"},
        )


def _ensure_image_fixture(knowledge) -> dict:
    """A locally generated image exercises OCR and vision without outside material."""
    seed_key = DATASET_VERSION + "-ocr-image"
    existing = next((doc for doc in store.list("documents") if doc.get("seed_key") == seed_key), None)
    if existing:
        return existing
    from PIL import Image, ImageDraw, ImageFont

    picture = Image.new("RGB", (1440, 900), "white")
    draw = ImageDraw.Draw(picture)
    font_paths = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    font_path = next((path for path in font_paths if path.is_file()), None)
    if font_path:
        title_font = ImageFont.truetype(str(font_path), 48)
        body_font = ImageFont.truetype(str(font_path), 35)
        lines = [
            "合成演示资料 / SYNTHETIC DEMO",
            "航空复合材料研究记录",
            "样例编号：DEMO-T01",
            "研究主题：材料回收与质量检测",
            "试样数量：12 组（虚构数据）",
            "记录年份：2026",
            "结论边界：仅供软件测试，不代表真实研究",
        ]
    else:
        title_font = ImageFont.load_default(size=48)
        body_font = ImageFont.load_default(size=35)
        lines = [
            "SYNTHETIC DEMO / INVENTED DATA",
            "Civil aviation materials research",
            "Sample ID: DEMO-T01",
            "Topic: recycling and quality inspection",
            "Samples: 12 groups (invented)",
            "Year: 2026",
            "For software testing only. Not real research.",
        ]
    draw.rectangle((0, 0, 1440, 145), fill="#e9f2f8")
    draw.text((70, 42), lines[0], fill="#19496b", font=title_font)
    for index, line in enumerate(lines[1:]):
        draw.text((75, 200 + index * 100), line, fill="#172938", font=body_font)
    output = io.BytesIO()
    picture.save(output, format="PNG")
    document = knowledge.ingest(
        "合成资料_OCR与图文理解测试.png", output.getvalue(), "project-technology", "external"
    )
    return store.save(
        "documents",
        {
            **document,
            "seed_key": seed_key,
            "synthetic": True,
            "source_note": "本地程序生成的 OCR 与视觉功能测试图，不含原始背景材料",
        },
    )
