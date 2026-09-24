# Implementation plan

## Delivery gates
1. Foundation: private-file exclusions, GitHub write access, environment, SQLite store, role sessions, model-budget gateway.
2. Working vertical slice: Agno-backed planning and report generation, actual document retrieval, editable valid workflow, human review and persistent execution.
3. Product modules: projects, agents, six skills, six workflow templates, run center, evidence-backed report versions, 30 samples, evaluation and audit/settings.
4. Verification: budget concurrency/uncertain responses, permissions, source isolation, workflow validation/recovery, API integration, frontend build and browser end-to-end.
5. Handoff: reproducible startup, public-safe Git commits, GitHub push, local preview and user guide. No original background documents or local data in Git.

## Implementation status
- Foundation and product modules implemented. Original documents, uploads, databases, model caches and credentials are excluded from Git and protected by a staged-file guard.
- Chinese UI includes ten work areas: overview, projects, knowledge, agents, skills, workflow editor, runs, reports/reviews, evaluations and system/audit.
- Six published starter workflows, three synthetic research scenarios, four agent roles and four report structures are available. All six skills have real implementations; no placeholder success responses.
- Real DeepSeek planning produced a 12-node workflow with conditional and batch paths. Real analysis and draft generation were exercised through the browser. Review rejection and persisted feedback were verified.
- All 30 seeded samples were executed through the local API in rehearsal mode and passed the execution/analysis/citation rules, stopping at the required human-review gate. This is not a model quality or factual accuracy score.
- Verified local OCR, cached BGE semantic retrieval and paid image understanding against a synthetic image. No original background material was sent to a model.
- Documentation and Windows startup/stop scripts are available. The startup script builds and serves the frontend on loopback; the stop script handles the Windows virtual-environment interpreter child.
- Final acceptance records, exact test counts and model spending are maintained in the handoff report.

## Follow-up deployment scope
After client feedback: production identity and tenant isolation, deployment packaging, controlled local-model inference, automatic graph extraction and stronger expert evaluation. These are not prerequisites for the current local demo and must not be presented as already delivered.

## Identity and interface refinement (2026-09-20)
- User selected the name **航智 · 航空情报平台** and direction D, a dark collaboration workspace inspired by Linear, with direction C's clear workflow nodes inspired by Dify. The four original comparison mockups remain at `/style-options.html`; references and design boundaries are documented in `docs/DESIGN.md`.
- Increase body, table and form text to 15–16 px, supporting text to at least 13 px. Apply the selected theme across authentication, navigation, resources, canvas, execution, review and settings while preserving readable report pages.
- Add ordinary self-registration and persistent password-hashed accounts with researcher permissions, retaining existing identities. Remove demonstration credential and backend-permission wording from the sign-in screen.
- Verify registration/login, duplicate and privilege checks, account persistence, desktop/narrow layouts and workflow controls without additional paid model calls. Keep private source documents and runtime data outside Git.

## 用户管理与规划体验改进（2026-09-21，dev/analysis-and-changes 分支）
- 新增管理员用户管理：`GET/POST /users`、`PUT /users/{id}`（仅 admin），管理员可创建研究员/审核员账号、改显示名与角色、启用/停用（停用即注销会话）、重置密码；不能创建管理员、不能停用或降级最后一个可用管理员、不能停用自己。自助注册仍固定为研究员。
- 任务规划改为异步可见：`POST /plan` 返回 202 规划任务，`GET /planning/{id}` 查询真实进度（阶段与每次校验记录）。真实模型规划的修复次数由 1 次扩为 2 次（PLAN_MAX_REPAIRS），每次修复携带校验错误与上次 JSON；同步 `engine.plan()` 语义保留不变。
- 工作流记录 `source_prompt` 与 `preferred_mode`；发起研究任务弹窗按所选流程预填研究需求与执行模式，供用户微调确认。
- 新增第五类智能体角色 parser（文档解析智能体，种子 agent-parser，旧库自动补齐）。parse 节点在未绑定技能但任务带上传资料时自动调用 document_parse 本地技能解析入证据；live 模式下追加一次计费摘要调用（purpose=document_parse_summary），演练模式输出带显著标注的确定性摘要。发起研究任务弹窗支持直接上传资料（可选可见性）并自动附加。
- 验证：`uv run pytest` 74 项全部通过；前端 `tsc -b && vite build` 与 `npm test`（12 项）通过。

## 上传体验与执行细节改进（2026-09-21，dev/analysis-and-changes 分支）
- 运行弹窗上传资料可选「保存到项目资料库」，默认仅本次任务使用（temporary 资料不进资料库列表与默认检索，仅显式选中时参与；仍持久保存以维持证据与引用可解析）。已选未上传的文件在点击开始运行时自动上传，不再静默丢弃。
- 审核节点内容不再为空：报告草稿 → 分析正文 → 如实标注的状态说明（任务与已收集证据摘要）依次兜底。
- 新增 DELETE /workflows/{id}（edit 角色），删除流程及其版本快照；已有运行持有不可变快照不受影响；前端流程卡片提供删除按钮。
- parse 节点只解析任务显式指定的资料（上传或资料库勾选的均可），不再自动解析项目内其他文档，避免无关内容污染下游；计费摘要仅对显式指定资料触发。
- 修复审核弹窗空白：前端此前只加载报告草稿，从未读取审批记录内容；现在中间审核节点会载入后端兜底内容（任务与证据状态说明）。
- 运行详情页「执行过程 / 节点输出」两个面板改为各自独立滚动。
- 验证：`uv run pytest` 79 项全部通过；前端构建与 12 项测试通过。

## Skill/Tool/MCP 三层架构阶段 A：新内核旁路构建（2026-09-21，dev/analysis-and-changes 分支）
- 新增 `backend/app/capabilities/` 包：contracts（ExecutionContext/ToolResult/SkillManifest/ToolDefinition/McpServerDefinition）、errors（稳定错误码）、registry、loader（启动校验：重复 ID、Skill/recipe 引用不存在的 Tool、非法 JSON Schema）、schema（极简 JSON Schema 校验器）、policy（授权交集/外发/确认/预算闸门）、audit（内存环形缓冲+脱敏）、runtime（ToolRuntime/SkillRuntime）、providers（local/http/mcp 基座）、services（documents/embeddings/graph）、tools（六项能力的 8 个 Tool 实现）。
- 六个技能迁移为 Manifest + recipe，Skill ID 与输出字段契约不变：knowledge_search→local.knowledge.keyword_search（evidence/count/method）；graph_query→local.graph.query（nodes/edges）；document_parse→local.document.read_chunks（evidence/count，不自动挑选项目文档，摘要仍属调用方）；ocr→local.ocr.rapidocr（text/evidence/region_count/elapsed）；semantic_search→local.knowledge.semantic_search + local.embedding.prepare（prepare_only 与 embedding 状态，recipe 条件步骤 when/unless）；multimodal→model.vision.analyze（保留外发/文件类型/8MB/预算检查，仍只走 model_gateway.complete）。
- `backend/app/skills/<kebab>/{SKILL.md,skill.yaml}` ×6；`backend/app/capability_manifests/tools/*.yaml` ×5（8 个 Tool）；`capability_manifests/mcp/docling-local.yaml` 仅配置清单（enabled:false，docling-mcp==1.2.0 占位，roots 用 ${AUTHORIZED_TEMP_ROOT}），本期不安装不接入。
- knowledge.py 的解析/分块/分词/Evidence 组装抽取到 capabilities/services，knowledge.py 内部委托、外部行为与 SKILL_DEFINITIONS/skills()/execute() 全部保留（阶段 B 再删除）；api.py、engine.py、workflows.py、model_gateway.py 未改动，新内核不接管流量。
- agent 模式：drill 返回 dry_run 预检（指令加载+工具绑定），live 在预算网关提供工具调用能力（run_agent，属后续阶段）前明确报 capability_disabled，不伪造模型调用。
- MCP Provider 支持 stdio/streamable_http 的发现、健康检查、allowlist 过滤与调用；测试用 fastmcp 子进程桩验证发现+调用+allowlist+不可用报错。
- 验证：`uv run pytest` 107 项全部通过（原 79 + 新 28）；`uv run ruff check backend/app/capabilities backend/tests` 通过（顺手修复 test_engine.py 既有的 3 处 RUF059 未使用变量）。

## Skill/Tool/MCP 三层架构阶段 B：原子切换全部后端调用方（2026-09-21，dev/analysis-and-changes 分支）
- `model_gateway.py`：保留 `complete()` 不变；新增 `run_agent(agent_spec, messages, tool_bindings, context, max_rounds)`，创建 Agno Agent 时传入真实 `tools=`（SkillRuntime.build_tool_functions 生成、经 ToolRuntime/Policy Gate 的受控 callable），工具轮数（tool_call_limit）、单次输出（≤2000 tokens）与总超时设上限，所有请求仍经 MeteredTransport 与预算账本（purpose=agent_run，携带 run_id），截断输出仍按 ModelOutputTruncated 处理。
- `engine.py`：`_invoke_skill` 替换为 `_execute_capability`，统一经 SkillRuntime.execute；删除对 `knowledge.execute()` 的调用、document_parse 自动挑选项目文档的兜底（用户已否决）和 graph_query 边补证据的特殊分支（该行为迁入 local.graph.query Tool）；运行记录新增 `capability_calls`（skill_id/node_id/status/error_code/duration_ms/tool_calls），技能失败带稳定错误码；规划提示词的技能目录改为 `_skill_catalog_text()` 从 SkillRegistry 动态生成；`_plan_model_loop`/`start_plan`/`_summarize_parsed_documents`/`_review_content` 行为不变。
- `workflows.py`：删除六个技能 ID 硬编码白名单，改为 SkillRegistry 存在性 + manifest.node_kinds 与节点类型（parse/retrieve）兼容校验。
- `api.py`：`GET /skills`、`POST /skills/{id}/test`、`system_info`/`/bootstrap`、`agent_data()` 全部改走 Registry/SkillRuntime（响应保留原字段并新增 trace）；新增 `GET /tools`、`POST /tools/{id}/test`、`GET /mcp-servers`、`GET /mcp-servers/{id}/health`、`POST /mcp-servers/{id}/refresh`（admin）、`GET /capability-events`；`/agents/{id}/test` live 模式改经 AgentRuntime→model_gateway.run_agent 真实工具链，模型未调用能力时返回明确标注 note。
- `knowledge.py`：删除 `SKILL_DEFINITIONS`、`Knowledge.skills()`、`Knowledge.execute()`；保留为纯文档服务层（ingest/search/chunks/graph/OCR/embedding/路径安全），全仓库已无 `SKILL_DEFINITIONS`/`knowledge.execute(` 引用。
- `main.py`：启动 seed 后执行 `validate_registered_skills(store)` 只读校验，已存 Agent/Workflow 引用未注册技能时报明确错误。
- 能力层配套增强：Registry 增加 upsert/unregister（MCP refresh 用）；build_tool_functions 生成带显式签名的 callable 供 Agno 真实参数绑定；graph Tool 返回边引用分块证据；ocr/vision Tool 兼容 document_id/document_ids 两种入参；multimodal manifest node_kinds 调整为 [parse, retrieve] 以保持旧工作流兼容。
- 验证：`uv run pytest` 115 项全部通过（阶段A后 107 + 新增/调整 8）；`uv run ruff check backend` 全部通过；uvicorn 启动冒烟通过（/api/bootstrap 的 skills 六个技能字段兼容、/api/tools、/api/mcp-servers、docling health=disabled 均正常，进程已关闭）。前端未改动，bootstrap 字段向后兼容。

## 前端接入三层能力架构（2026-09-21，dev/analysis-and-changes 分支）
- 后端：`GET /api/bootstrap` 新增 `tools`、`mcp_servers`、`capability_stats`（skills/tools/mcp_servers/healthy_tools 数量，healthy = 非 MCP 工具 + 所属服务已启用的 MCP 工具）；`/tools` 与 `/mcp-servers` 与 bootstrap 共用 tool_summaries()/mcp_server_summaries()；`skill_summaries` 新增 `evidence_required` 字段。ARCHITECTURE.md 契约已同步。
- 前端 `src/capabilities.ts`（新）：错误码/状态/提供方/外发等级中文文案映射（按稳定 code 分支）、skillsForNodeKind（节点类型兼容过滤）、skillToolSummary（授权工具数+最高外发等级）、toolHealth（MCP 工具健康跟随所属服务）、toolInputFields/collectToolInput（input_schema 简化表单）、flattenTrace（trace 展平，直接工具测试去重）、mcpHealthText（disabled 视为配置状态而非故障）。
- 技能工具箱拆三 Tab：技能（版本/执行模式/适用节点/Evidence 要求/依赖工具，测试展示 Skill→Tool 嵌套调用链与稳定错误码）；工具（provider 徽标/只读/网络/外发/超时/健康，测试表单按 input_schema 动态生成文本/数字/布尔输入）；MCP 服务（transport/启用状态/已允许与已发现工具数，健康检查全部可见、刷新发现仅管理员，docling-local 显示"已禁用"而非错误）。
- 智能体页：启用技能复选项下显示授权工具数量与外发等级摘要；测试结果如实显示后端 note（"本次模型未调用能力"），有 tool_calls 时显示实际调用次数。
- 工作流编辑器：「使用技能」下拉只列 node_kinds 兼容且已启用的技能；已绑定但不兼容的技能以禁用选项如实标注，旧数据不变。
- 运行详情：节点输出带 trace 时以 Skill→Tool 层级缩进展示（tool_id/provider/耗时/状态徽标），错误按稳定错误码中文呈现；`.execution-grid` 独立滚动样式未改动。
- 首页就绪情况将"技能工具"拆为 技能/工具（健康/全部）/MCP 服务 三项，数据源 capability_stats。
- 验证：`uv run pytest` 115 项全部通过（test_engine_api 增加 bootstrap 新字段断言）；`uv run ruff check backend` 通过；前端 `npm run build` 通过；`npm test` 20 项通过（原 12 + capabilities.test.mjs 新增 8）。

## Skill/Tool/MCP 阶段 D：真实外部工具 + 首个真实 MCP Server（2026-09-21，dev/analysis-and-changes 分支）
- 新增 HTTP 工具（provider=http，allowed_hosts 白名单、≤15s 超时、5MB 响应上限、data_egress=query）：`aviation.noaa.get_metar/get_taf/get_sigmet`（aviationweather.gov）、`research.openalex.search_works`、`research.crossref.search_doi`。HttpProvider 改为 entrypoint 约定 `module:base`（`base_request(args)` 构造请求、`base_parse(payload,args)` 解析），网络出口统一在 Provider 内强制白名单；测试用 httpx MockTransport，零真实请求。
- 新增 OurAirports 离线工具 `aviation.ourairports.lookup_airport/nearby_airports`（惰性加载 CSV+缓存，network:none，drill 下真实执行；快照缺失报 provider_unavailable 并提示补齐方式）；新增 `scripts/download_ourairports.py` 快照下载脚本；快照位于 gitignore 的 `.local/ourairports/`。
- 首个真实 MCP 垂直验收：`backend/app/mcp_servers/aviation_server.py`（fastmcp stdio，复用 tools/airports.py）；`capability_manifests/mcp/aviation-local.yaml`（enabled:true，command `uv run python -m backend.app.mcp_servers.aviation_server`，allowlist 仅两个工具）。真实 stdio 会话验证发现（2 工具）、健康检查 ready、调用 lookup_airport 命中 ZBAA、越 allowlist 报 permission_denied、经 ToolRuntime 注册为 `mcp.aviation-local.*` 并可调用；server 不可用时依赖其工具的 Skill 经 skill_summaries 标 degraded，本地能力不受影响。
- 新增三个 Skill（recipe，node_kinds [retrieve, analyze]，evidence_required:true）：aviation_weather（METAR+TAF）、airport_lookup（OurAirports）、literature_search（OpenAlex+Crossref）；规划提示词目录 `_skill_catalog_text()` 自动包含。
- engine：外部来源证据（无 document_id 的 origin=external 条目）只进 `run["external_references"]`，不混入必须能定位 chunk 的 `run["evidence"]` 引用池，报告防伪造引用校验不受影响。
- 验证：`uv run pytest` 134 项全部通过（新增 19）；`uv run ruff check backend` 通过；前端 `npm run build` 与 `npm test`（20 项）通过，未改动前端代码；uvicorn 冒烟确认 bootstrap 出现 9 技能/15 工具/2 MCP，aviation-local 健康检查经真实子进程 ready，工具与技能测试接口行为符合 drill/live 语义。

## MCP 修复：结果规范化 + 启动自动发现（2026-09-21，dev/analysis-and-changes 分支）
- 问题 1（结果未规范化）：根因是当前 mcp SDK 的 CallToolResult 字段为 `structured_content`/`is_error`（snake_case），Provider 此前读 `structuredContent`/`isError` 恒为 None/False，导致 fastmcp 信封原样进入 data。修复 `providers/mcp.py`：兼容两种字段名；解包优先级为 structured content（含 fastmcp 对非标量返回值的 `{"result": ...}` 包装，字符串值再尝试 JSON 解析）→ content JSON 文本 → 非 JSON 时 ToolResult(data={}, text=原文) 不丢结果；is_error 报 tool_failed。载荷中的 evidence/text 由 ToolRuntime 既有规范化提升到顶层。
- 问题 2（启动不发现）：`facade.discover_enabled_servers()` 在启动时对 enabled:true 的 MCP Server 逐个发现+注册（单 server 上限 min(startup_timeout_seconds, 20s)），失败标 unavailable（依赖 Skill 随之 degraded），平台照常启动；`main.py` lifespan 以后台任务执行，不阻塞 API；关停时取消该任务。测试环境经 `backend/tests/conftest.py` 默认置 `WORKBENCH_MCP_AUTODISCOVERY=off`，避免 TestClient 反复拉子进程。
- 新增测试 4 项：fastmcp 桩验证三种解包形态（结构化 dict / JSON 文本 / 非 JSON 回退）与 evidence/text 顶层提升；启动路径自动注册（桩）；启动超时不阻塞并标 unavailable。更新 2 项旧断言以匹配解包后的真实载荷。
- 验证：`uv run pytest` 138 项全部通过；`uv run ruff check backend` 通过。真实冒烟（8124 端口，admin 登录）：bootstrap 中 aviation-local status=ready 且 /api/tools 直接含 mcp.aviation-local.lookup_airport/nearby_airports（无需手动 refresh）；POST /api/tools/mcp.aviation-local.lookup_airport/test（live，ZBAA）返回 data 为真实载荷（airports/count/snapshot_date=2026-09-21）、顶层 evidence 非空、trace.provider=mcp。进程已关闭，无残留子进程。

## 工作流技能选择与自适应输入修复（2026-09-21）
- 根因 1：规划提示只限制了图片技能，没有表达 document_parse/ocr/multimodal 的附件前置条件；显式绑定的 document_parse 又绕过了 parse 节点原有的无文档规则解析兜底，最终以缺少 document_ids 中断。
- 修复 1：SkillManifest 新增 `requires_documents`，三个文档类 Skill 显式声明；规划技能目录展示前置条件，模型规划校验会拒绝无附件意图任务中的文档类绑定并进入既有修复轮次。旧流程或手工误配在运行时无 document_ids 时明确记录 capability_skipped，并继续以规则需求解析输出，不再触发 schema_validation_failed。
- 根因 2：airport_lookup/aviation_weather 原为 recipe，SKILL.md 从未交给执行模型；engine 只把静态 config.query（缺省为整句任务）直接送入工具，无法拆城市、处理中英文或消费上游 ICAO。
- 修复 2：两项技能改为 agent 模式并保留 drill recipe 回退；SkillRuntime 将完整 SKILL.md、原始任务、初始 config 和有界上游结果交给受控智能体。初始 JSON 仅为候选，智能体可在工具 schema/allowed_tools 范围内多次调用、检查空结果并修正英文城市名或 ICAO。只有真实 ToolResult 会聚合为 airports/reports/evidence/trace，未调用工具则报 tool_result_invalid，模型正文不能伪装成技能成功。
- engine 的技能输入改为按 manifest.input_schema 通用构造，节点中登记的 icao 等字段不再被硬编码丢弃；规划提示明确机场集合任务先 airport_lookup，后续 aviation_weather 从上游真实机场结果取得 ICAO，不把返回字段名拼成 query。
- 补齐外部证据下游链路：`external_references` 仍不混入本地 chunk 证据池，但会参与证据充分性条件、分析和报告；报告只接受带有效 HTTP(S) source_uri 的受控外部引用并单独保存，前端可查看详情和打开原始来源。由此机场/气象 Skill 的真实结果不会再被条件节点误判为“零证据”。
- API 技能摘要与前端技能卡片展示 `requires_documents`，让用户在编辑工作流前可见附件要求。
- 新增回归覆盖：无附件规划自动修复 document_parse、旧工作流无附件安全跳过、机场智能体把中文集合任务拆成 Beijing/Shanghai 多次真实本地调用并合并 3 个机场结果。`ruff check backend/app backend/tests` 通过；其余验证结果见本次交付说明。
- 最终验证：后端 `uv run pytest -q -p no:cacheprovider` 143 项通过（仅 Starlette 上游弃用警告）；`uv run ruff check backend/app backend/tests` 通过；前端 `npm test` 20 项通过，`npm run build` 通过。

## 工作流业务 Agent 闭环（2026-09-21）
- 澄清两层执行语义：`Skill.execution_mode` 只控制 Skill 内部是 recipe 还是受控 Agent；工作流节点新增 `execution_strategy: agent|direct_skill`，前者才是真正的业务 Agent。新模型规划的 retrieve 节点默认使用 `agent`；旧种子流程维持直接 Skill，避免历史行为和既有测试被静默改变。
- 新增业务 Agent Runtime：按 `agent.skill_ids`、节点类型、文档可用性和 Policy Gate 的交集生成 Skill callable。每个 callable 向模型暴露 Skill 输入 schema、描述和完整 SKILL.md；模型可观察任务、节点目标/config 与有界上游输出，自主选择、组合、修正参数及重复调用，但看不到越权的底层 Tool。
- 可靠性闭环：模型未调用 Skill 时自动进行一次带原因的修复调用；两次仍无真实调用才报 `tool_result_invalid`。成功必须来自实际 SkillRuntime/ToolRuntime 结果，模型文本不能伪装为能力结果。调用轨迹补充 agent_id，并保留 Skill→Tool 证据和耗时。
- airport_lookup/aviation_weather 改回 recipe：单次机场/气象查询保持确定性；大城市列表、中文转英文、逐城市机场检索、从真实结果提取 ICAO、逐机场 METAR/TAF 调用由上层检索 Agent 决策。recipe 多步骤结果会合并数组字段，避免 METAR 被后续 TAF 覆盖。
- 权限和迁移：工作流校验拒绝 Agent 模式未绑定智能体、非法策略及未授权 Skill；知识检索智能体自动补齐 airport_lookup/aviation_weather/literature_search，不覆盖用户自定义提示词；种子 parse 节点改绑文档解析智能体。
- 前端工作流编辑器新增“智能体自主决策 / 直接执行固定技能”选择；Skill 下拉按所选 Agent 权限过滤，自主模式明确提示 skill_id/config 只是初始建议。
- 新增回归：业务 Agent 第一次跳过 Skill 后自动修复、中文大城市任务拆成 Beijing/Shanghai 并多次调用 airport_lookup、工作流节点只看到已授权 Skill 且记录 Agent→Skill→Tool 轨迹。
- 验证：后端 145 项测试全部通过（仅 Starlette 上游弃用警告），Ruff 通过；前端生产构建与 20 项测试通过。Agno `Function.from_callable` 冒烟确认所有授权 Skill callable 均生成正确 properties/required schema。

## 研究任务入口整合（2026-09-24）
- 工作台的首要操作统一为“发起研究任务”，并列提供“新建研究流程”（按需求生成可编辑模板）和“复用现有流程”（选择已发布模板直接运行）两种明确路径；复用路径保留项目、执行模式、指定资料与临时上传能力。
- 运行中心移除发起入口与创建弹窗，只负责运行列表、执行详情、取消、重试和审核跟踪；流程卡片及流程画布的“运行”操作统一跳转到工作台并预选对应流程。
- 移除页面顶部不会改变页面数据范围的全局专题下拉框，项目选择保留在真正消费项目上下文的任务、资料等表单中。
- 验证：前端 `npm test` 20 项全部通过；`npm run build`（TypeScript + Vite 生产构建）通过。

## GitHub Actions MCP 测试隔离修复（2026-09-24）
- 根因：两项 aviation-local MCP stdio 集成测试直接读取本机忽略目录 `.local/ourairports/`；开发机有真实快照所以通过，干净的 GitHub Runner 没有运行数据而失败，并导致后续 Ruff 与前端构建步骤被跳过。
- 修复：MCP 集成测试显式复用仓库内的小型合成 OurAirports fixture；MCP Provider 仅按白名单向 stdio 子进程传递 `WORKBENCH_DATA_DIR` 与 `OURAIRPORTS_DIR`（不继承密钥等其他环境），同时修复自定义运行数据目录被子进程忽略的潜在问题。测试继续覆盖真实 MCP 发现、调用、结果解包、运行时注册和 evidence 提升，不提交运行快照，也不访问网络。
- 验证：在空 `WORKBENCH_DATA_DIR` 下，失败的两项 MCP 测试定向复测通过，完整后端 145 项测试通过；Ruff check 与 format check（backend/scripts 共 69 个文件）通过；前端 20 项测试及 TypeScript + Vite 生产构建通过。
