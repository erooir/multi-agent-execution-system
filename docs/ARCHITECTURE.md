# 航智智能体管理平台架构 v2

2026-10-17。新版共享契约。旧业务由 Git 归档保留，私有原文只保存在忽略目录。

## 系统边界
智能体定义、不可变版本、运行实例分离。任务自动规划、执行、确定性校验、评审与有界修订，没有人工审核等待状态。首批支持结构化数据整理、来源文本整理/摘要、图片元数据检查。音频转写、图像语义和视频理解未验证时返回 unsupported_capability。local 是真实确定性工具；cloud 是网关模型；rehearsal 显著标记且不冒充离线推理。

## 部署与数据
Python/FastAPI + Agno 3.0.10 私有运行时，React/TypeScript 管理台。API 和独立 Worker 共享 SQLAlchemy 数据库；HANGZHI_DATABASE_URL 配置 PostgreSQL 正式部署，默认 SQLite .local/platform-v2.sqlite3 用于本机开发。schema_version 显式管理，不迁移旧任务。
实体表：projects, agents, agent_versions, workflows, workflow_versions, data_assets, tasks, nodes, instances, artifacts, events, schedules, evaluations, improvements, memories, connectors, workers, audits。每表独立主键、项目、状态、时间与配置；任务有幂等键、租约持有者、递增 token、过期时间、取消标记。状态与事件事务提交，事件表作为可重放通知源。
身份密码/会话复用已验证组件，身份库保留 .local/workbench.sqlite3，只访问身份与设置类别。新部署不生成固定默认密码。admin 全局管理；operator 操作所属项目；reviewer 只读。所有实体读取和写入均验证项目成员。
预算固定历史 .local/budget.sqlite3，不随新库目录迁移。所有模型调用只经过 backend/app/model_gateway.py，保留最坏预留、不确定费用、8000输出上限与300元历史累计上限。AgentOS原生执行API不公开。禁遥测、CDN、自动下载。

## API
业务前缀 /api/v2；身份 /api/auth/login|register|me|logout。集合为数组。错误 {detail:{code,message}}；身份保留detail字符串。写入校验同源和后端角色。输入拒绝未知字段。
- GET /health；GET /bootstrap 返回 {user,projects,agents,workflows,tasks,data_assets,schedules,improvements,connectors,capabilities,workers,budget,stats,system}。
- GET/POST /projects；PUT /projects/{id}/members（admin）。
- GET/POST /agents；GET/PUT /agents/{id}；POST /agents/{id}/publish|disable|enable|retire；DELETE /agents/{id}；GET /agents/{id}/versions|instances。
- GET/POST /workflows；GET/PUT /workflows/{id}；POST /workflows/{id}/publish；GET /workflows/{id}/versions。
- GET/POST /data-assets；POST /data-assets/upload（multipart）；POST /data-assets/{id}/revoke；GET /data-assets/{id}/content。
- GET/POST /tasks；GET /tasks/{id} 包含 nodes,instances,artifacts,events；POST /tasks/{id}/cancel；GET /tasks/{id}/events?after=N 数组；GET /tasks/{id}/stream SSE支持Last-Event-ID；GET /tasks/{id}/result。
- GET /artifacts/{id} 索引及内容；GET /artifacts/{id}/download 下载。
- GET/POST /schedules；POST /schedules/{id}/disable|enable。持久 interval_seconds,next_run_at,timezone,task_template；计划ID和触发时间构成幂等键。
- GET /capabilities|budget|workers|audits；GET/POST /connectors；POST /knowledge/search；GET/POST /memories。
- GET/POST /improvements；POST /improvements/{id}/evaluate|promote|rollback；GET /evaluations。候选仅改变授权配置，保存真实样本和输出，硬回归禁止晋升。

## 字段
AgentSpec: id,project_id,name,description,purpose(general|specialized),structure(atomic|composite),capabilities[],modalities[],tool_ids[],instructions,model_policy(local|cloud),members[{agent_id,version}],status(draft|published|disabled|retired|destroyed),current_version。发布冻结配置和成员版本，检查循环及深度。禁用阻止新任务，存量按快照执行；销毁保留tombstone并检查引用。
WorkflowSpec: id,project_id,name,description,task_type,nodes[{id,label,agent_id,depends_on:[],config:{}}],status,current_version。发布验证DAG和已发布成员，冻结agent版本。未选流程则按能力匹配或白名单模板组装任务级配置，不生成代码。
DataAsset: id,project_id,name,modality(text|structured|image|audio|video),media_type,content,source_id,source_version,provenance,classification(local|public),retention_days,expires_at,checksum,bytes,status(active|revoked)。文本/JSON内联受大小限制；文件content_ref只指向受控存储。只有public+cloud任务可外发。撤回/过期阻止读取并失效派生记忆。
TaskRequest: project_id,name,goal,task_type(structured_transform|source_summary|text_analysis|image_inspect|audio_transcribe|video_analyze),input_refs[],workflow_id?,mode(local|cloud|rehearsal),idempotency_key,deadline_seconds(1..86400),max_cost_cny(0..10),max_revisions(0..2),allow_partial(false),quality{min_records?,required_fields?:[],min_length?}。项目内幂等，同键异内容409。
TaskRun: id,project_id,status(accepted|planning|queued|running|evaluating|revising|succeeded|partial|failed|cancelled),reason_code,request,snapshot,attempt,revision,cancel_requested,lease_owner,lease_token,lease_until,result,created_at,updated_at。
ResultBundle: task_id,status,reason_code,artifacts[],quality_verdict,warnings[],lineage[],usage,version_refs[]。
Node: id,task_id,project_id,node_id,label,agent_id,agent_version,instance_id,status,attempt,output,error,started_at,finished_at。
Instance: id,project_id,task_id,agent_id,agent_version,status(running|released|abandoned),context_id。不同任务从不共享context。
Artifact: id,project_id,task_id,type,media_type,content,checksum,source_refs[],quality_status。
Event: id,sequence,project_id,task_id,node_id?,instance_id?,type,time,trace_id,payload。事件不复制私有全文。

## 模块协作接口
backend/app/platform/db.py: Repository(url=None), repo。transaction() 产生绑定事务的Repository；get(kind,id),list(kind,project_id=None,**filters),put(kind,record),delete(kind,id)；event(task_id,type,payload=None,**refs)；claim(worker_id,lease_seconds=30) 原子领取accepted/queued或过期任务并递增token；fenced(task_id,worker_id,token) 检查租约；heartbeat(task_id,worker_id,token,lease_seconds=30)；finish_claim(task_id,worker_id,token)释放。执行结果必须在transaction内先fenced，再put。
platform/service.py 负责定义/数据/任务受理；platform/contracts.py 是输入Schema；platform/api.py 是路由。
platform/runtime.py 提供 async process_claim(repository,task,worker_id) 以及 runtime_workflow()；platform/worker.py 独立CLI调度、心跳及计划。领域对象只依赖新Repository；模型和身份是显式保留边界。

## 恢复与质量
PostgreSQL领取FOR UPDATE SKIP LOCKED；SQLite BEGIN IMMEDIATE。所有结果经过租约令牌验证；旧worker不能写入。完成节点复用。外部调用前记录in_flight，接管未确认调用返回execution_uncertain，禁止盲重放付费请求。取消在途调用不能提前宣称释放。
硬Schema/来源/权限校验不能被模型自评分覆盖，评审上下文独立。有界修订失败保留原始问题；仅allow_partial时可部分交付。评测冻结样本、基线和候选，保留原始结果与资源数据，禁止伪造评分。

## 验收
成功、缺数据、错Schema、重复键、越权、撤回、过期、不支持能力、模型错误、质量不合格、超时、取消、重复领取、过期接管和旧实例隔离。UI只显示持久状态和账本；无样本不显示成功率。上游接口、多模态、目标硬件和断网部署必须实际验证，不能由本机演练替代。
技术核对：Agno固定版本由平台租约作唯一调度权威，AgentOS运行真实Workflow但不公开原生API。参考 https://docs.agno.com/agent-os/overview 和 https://docs.agno.com/telemetry 。模型目录参考 https://api-docs.deepseek.com/quick_start/pricing/ 。
