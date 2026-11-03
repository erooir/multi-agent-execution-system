# 上下游接入契约 v2

所有服务默认仅监听127.0.0.1，部署到受控内网前应配置身份、成员和网络边界。API前缀/api/v2；认证使用/api/auth/login返回的HttpOnly会话。不能通过前端按钮代替权限验证。机器服务身份须作为独立受限账号配置，不共享管理员凭据。

## 岗位授权与工具试用

### 用户问答

提问客户端通过 `GET /api/v2/question-projects` 读取本人可访问的项目标识和名称。受理请求为 `POST /api/v2/questions`，只传 `project_id`、`question` 和客户端生成的 `idempotency_key`。在同一项目内，同一用户以相同标识重复提交同一问题可恢复原记录。提问者无需提供素材 ID、用例编号、岗位、工作流或工具参数。

通过 `GET /api/v2/questions?project_id=…` 读取本人历史，`GET /api/v2/questions/{id}` 读取进度、答复、缺口、来源定位及适用的业务表格和地图。项目授权和材料有效性在读取时重新核验。引用中的项目资料由管理人员登记，用户页面不会获取底层资产、调用轨迹或预算数据。

正常项目问题以本次问题和实际材料为验收范围。明确的小样验证与已登记的特定参考批次使用相应的覆盖要求。找不到可靠依据、无法区分多种解释或存在数据缺口时，应读取返回的问题状态和缺口，不能根据 HTTP 成功响应推定业务成功。

### 管理界面

工作台首屏请求 `GET /api/v2/bootstrap?scope=current`。响应包含可见项目列表，业务记录、统计和 Worker 活跃任务 ID 仅限 `current_project_id`。切换项目时附加 `project_id`，服务端复核项目权限。已有集成可继续用无参数的 `/api/v2/bootstrap` 获取其所有可见项目的记录。

工具目录可通过 `GET /api/v2/capabilities?project_id=...` 读取。支持本地试用的条目包含 `local_preview=true`、参数 Schema、工具版本和实现摘要。使用 `POST /api/v2/agents/{agent_id}/tools/{tool_id}/preview`，提交当前发布的智能体 `version`、同项目资料资产 ID 列表 `input_refs` 和符合工具契约的 `arguments`。例如，释义核查的参数可为 `{"source_id":"登记的资料ID","acronym":"AOD"}`。返回实际工具版本、智能体版本、读取过的来源、结果及结果 SHA-256。

试用在本机计算，要求项目操作权限、该智能体的显式工具授权与输入模态匹配。返回前重新核验资料撤回、有效期、内容和版本，审计保存来源标识和摘要。云端智能体调用同一批操作时，还须满足任务的公开分类、外发授权与网关预算，实际调用写入任务记录。工具、技能和角色处理流程分别计数。

## 可选的公开资料语义取证

正式离线部署不需要此能力，`HANGZHI_OFFLINE=true` 时禁止云任务。联网开发/验收环境中，operator/admin 可显式发起 `POST /api/v2/casework/semantic-evidence`。提供 `project_id`、1–12个 `input_refs`、`focus`（`claim_check`、`timeline` 或 `comparison`）、`idempotency_key`、本任务正数费用上限 `max_cost_cny`（此入口至多0.8元）及 `public_egress_authorized: true`。返回202和标准任务ID。同键同内容复用任务，有差异返回冲突。0.8元仅是该辅助入口的单任务限制，不能修改全项目历史累计账本。

只交给模型明确选择并获准外发的公开文本或结构化来源。入口不接收自由提示词，不自动附加项目知识，不转发用例任务目标、报告或中间产物。若提供 `case_task_id`，必须是同项目已经交付结果的用例任务，全部冻结输入和产物引用必须与本次引用一致；不能只挑出其中公开部分来掩盖私有输入。来源版本和校验和须匹配，后续外发仍经既有统一网关、真实工具、执行期权限复查、独立自动评审及有限修订。

成功任务可鉴权下载 `GET /api/v2/tasks/{id}/semantic-evidence/report`（UTF-8纯文本）。报告给出来源编号、版本、逐字摘录的字符区间或计算记录校验标识。区间针对**已登记的文本表示**；若输入为原件节选，须另外保留原件哈希、页码和提取/规范化方法。报告是模型的辅助解释，不改变原用例终态、确定性复算或覆盖范围。未通过审核、资料撤回、过期、重新分类或变更时，不提供该报告。

## 数据
使用data_asset.schema.json，必填project_id/name/modality/content/source_id/provenance。source_version默认1；classification默认local。原始媒体通过/data-assets/upload的multipart file上传，另附project_id/source_id/provenance等元数据。文件路径不是外部可指定的content_ref。缺字段返回422，越权403，已撤回或过期409，过大413。

JSON/CSV使用UTF-8，结构化内容是对象数组或records对象。媒体必须声明真实模态，不把未处理的媒体伪装成文本。文本或结构化输入由程序校验，供应方应给出合法、缺字段、过期、撤回与重复样例。原始材料不自动获得云端处理授权。

## 任务与结果
使用task_request.schema.json。project_id/name/goal/task_type/input_refs/idempotency_key描述业务请求。截止时间以受理时刻起算，费用包括执行与评审。重复幂等键不创建重复任务；异内容409。

POST /tasks返回202和TaskRun对象，以其中id作为task_id。GET /tasks/{id}读取持久状态，GET /tasks/{id}/result读取ResultBundle。GET /tasks/{id}/events?after=N返回可续传事件；/stream支持Last-Event-ID。产物必须经过/artifacts/{id}权限检查，不公开文件存储路径。

成功以schema、来源和自动评审为准。reason_code包括insufficient_data、unsupported_capability、quality_not_met、deadline_exceeded、budget_exhausted、execution_uncertain、data_egress_blocked等。不要把非成功任务转换为成功回调。当前版本未启用外部副作用回调，避免未经协议约定的重复写入。

## 知识服务
本地知识连接器检索已授权、未撤回且未过期的数据。HTTP连接器由管理员配置，主机必须在部署环境HANGZHI_KNOWLEDGE_HOSTS白名单；平台向base_url/search发送POST {query,limit,project_id}。响应为{items:[{id,text,source_id,source_version,provenance}]}，可附带位置字段。响应上限1MB、超时10秒、禁止重定向；不把上游凭据放入模型上下文。

真实内网服务的鉴权、分页、增量、撤回推送与服务等级应由双方提供样例后扩展。当前适配器不能据此宣称已经接通上游。

## 外部 Skill、Tool 与 MCP

能力定义和版本位于 `capability_definitions/capability_versions`，发布时冻结校验和，Agent 只获得显式授权的原生 Function/Toolkit/Skills。外部协议由 Agno 原生驱动执行，不能以目录登记代替真实调用验收。

- Skill：上传不超过5MB的 ZIP，仅含 `SKILL.md` 与 `references/` 的 UTF-8 文本。Agno LocalSkills 负责规范校验；脚本、符号链接、路径穿越和其他资源拒绝。发布可提交 `tool_bindings` 显式映射 allowed-tools。
- Agno Toolkit：首批 `calculator`、`duckduckgo`，可用 include_tools 限定功能。后续 Toolkit 必须逐项增加工厂、权限与真实验收，不允许任意 import path。
- HTTP Tool：固定只读 POST JSON，输入/输出均受 JSON Schema 约束。部署设置 `HANGZHI_TOOL_HOSTS` 主机白名单；本机 HTTP fixture 还需显式 `HANGZHI_ALLOW_LOCAL_HTTP=true`。密钥只通过 `secret_ref` 引用 Worker 环境变量。
- MCP：stdio 使用 `HANGZHI_MCP_LAUNCHERS` JSON 中管理员预装的固定命令/参数/cwd；Streamable HTTP 使用 `HANGZHI_MCP_HOSTS` 白名单。发布前必须经 Agno MCPTools 原生发现，Schema 漂移拒绝执行。HTTP 客户端不继承系统代理或重定向。
- 本期外部服务只接受 unmetered、只读能力，不开放任意写操作、动态 OAuth、运行时 `npx/uvx` 下载或未知按次收费。撤销阻止后续派发；未确认调用返回 execution_uncertain，不盲重放。

`image_ocr` 在本地处理图片，输出文字块、坐标和真实置信度；`image_understand` 只接受 public 图片，原图经统一模型网关发送，独立评审也读取原图。图片 base64 不进入事件、日志、工具 checkpoint 或产物。

## 契约发布
docs/contracts中的JSON Schema由 scripts/export_contracts.py 从Pydantic生成。CI检查漂移。这里只包含公开工程契约，不复制私有方案全文。
