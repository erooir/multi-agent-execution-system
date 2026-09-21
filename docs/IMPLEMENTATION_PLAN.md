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
