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
