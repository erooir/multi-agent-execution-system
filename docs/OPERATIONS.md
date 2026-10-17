# 航智平台运行与恢复

本文对应 v2 契约。Windows 是当前开发环境；Linux、PostgreSQL、内网模型及目标硬件的正式离线验收必须在目标环境完成。不得将本机确定性工具运行或 rehearsal 作为离线模型推理证明。

## 运行边界

- API 默认绑定 `127.0.0.1`，Worker 作为独立进程共享平台数据库。只有管理 API 可从浏览器访问，AgentOS 原生模型、团队和工作流路由不公开。
- `HANGZHI_DATABASE_URL` 配置平台业务库；未配置时使用 `.local/platform-v2.sqlite3`。PostgreSQL 凭据仅在服务端环境或受保护凭据文件中设置，不能写入 Git 或命令记录。
- 身份和旧兼容设置位于 `.local/workbench.sqlite3`；新版任务不继承旧任务状态。上传文件位于 `.local/platform-assets/`；任务产物以业务库内容及索引保存。
- 模型历史账本固定为仓库 `.local/budget.sqlite3`。更换业务库、工作目录、`WORKBENCH_DATA_DIR` 或升级代码都不会获得新额度。默认网关在历史账本缺失时拒绝启动计费；只能从完整、可信的最新备份恢复。
- 模型密钥通过服务端环境读取。日志、截图、异常和前端只显示是否已配置，不显示值。
- 禁止运行时安装依赖、下载模型/字体或启用遥测。对外部请求的许可由任务策略和网关共同执行。

## 预算与取消

所有模型访问仅能位于 `backend/app/model_gateway.py`。执行器按以下方式固定任务预算及外发许可；`cancelled` 是同步函数，可检查取消标志、截止时间和租约，并可抛出旧租约错误：

```python
with request_scope(task_id, max_cost_cny, allow_external, cancelled=cancelled):
    result = await model_gateway.complete(
        public_text, purpose="execution", run_id=task_id, evidence=source_metadata,
    )
usage = model_gateway.usage(task_id)
```

任务上限在 SQLite 同一事务中与历史累计上限共同检查；已记录的任务额度只能收紧。重启、并发请求和换 purpose 不会重新分配额度。项目累计上限始终为人民币 300 元，单次输出最多 8000 tokens。每次请求先按最坏输入/输出范围预留，实际可靠用量返回后结算。

2026-10-17 核对 [DeepSeek 中文价格表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：当前 Flash 高峰输入为人民币 2 元/百万 tokens、输出为 8 元/百万 tokens。网关继续用 4/16 的保守费率，忽略缓存及低峰优惠。它表示预算占用，不是供应商实际账单。模型 ID 由 `HANGZHI_MODEL_ID` 配置，但仅允许已核价的 Flash 名称；更换供应商或计费模型需要更新适配、价格依据和测试，不能只改字符串绕过控制。

输入、输出 token 缺失或无效、超时、连接失败和在途取消均保留费用预留。收到未核价模型或实际用量超过预留时停止后续请求，需核对供应商及账本。不得用“调用失败”作为自动退款依据。

`usage` 包含 `cost_cny`、`reserved_cny`、`total_cny`、`request_count`、`input_tokens`、`output_tokens`、`uncertain_count`、`accounting_complete`。任务可按 purpose 查询，工具多轮调用返回所有实际请求合计。`accounting_complete=false` 必须在结果和管理界面保留，不可将未知费用显示为零。

取消在派发前不创建账单记录；在途调用取消会中断等待并保留 uncertain 记录。业务终态、调用是否停止及实例是否释放是不同事实。失去租约的 Worker 不得继续发起模型/工具请求，也不得写回任务结果。

## SQLite 开发环境备份

`scripts/runtime_backup.py` 使用 SQLite backup API，能够纳入 WAL 中已提交的数据；不直接复制打开的数据库主文件。它只备份三个登记数据库及 `platform-assets/`，不会打包整个 `.local`、私有交接包或密钥。

1. 先停止 API 和所有 Worker，确认没有活跃进程。跨数据库及文件的一致快照必须在此维护窗口中进行。
2. 从项目根目录执行：

```powershell
& .venv/Scripts/python.exe scripts/runtime_backup.py backup --services-stopped
```

输出为全新的 `.local/backups/snapshot-<UTC时间>-<随机后缀>/`，已存在的目标不会被覆盖。可用 `--path` 指定 `.local/backups/` 下的新目录。文件 manifest 包含哈希、大小、备份时间、预算汇总和跨库一致性声明。

```powershell
& .venv/Scripts/python.exe scripts/runtime_backup.py verify --path .local/backups/实际目录
```

未带 `--services-stopped` 仍可生成每个 SQLite 数据库的一致备份，但 manifest 明确标记 `cross_database_consistent=false`，不得用作整套恢复。该参数是操作者对维护状态的声明，脚本不会替操作者关闭进程。

备份和恢复记录均含私有运行信息，只在受保护本地存储留存。不能提交、加入源码交付包或自动上传。离机副本需通过用户批准的私有迁移流程，并验证哈希。

## SQLite 恢复与回退

1. 停止 API 和所有 Worker；保存当前应用版本、数据库 schema 版本和最新一致备份。
2. 验证目标备份；确认该快照与计划恢复的代码/schema 兼容。代码回退不能代替数据库迁移回退。
3. 执行恢复：

```powershell
& .venv/Scripts/python.exe scripts/runtime_backup.py restore --path .local/backups/实际目录 --services-stopped
```

恢复前自动备份当前运行态。非预算数据库和登记文件逐个原子替换；目标仍存在 WAL/SHM 时拒绝替换，应先确认服务关闭并做 SQLite 完整检查点。受控文件中快照外的现存文件不会自动删除，避免恢复操作破坏尚未归档的数据；它们没有新数据库引用时不会通过业务 API 暴露。

**预算账本永不随旧业务快照倒退。** 当前账本存在时仅追加备份中缺失的历史费用记录，保留当前同 ID 记录；计量异常计数不会降低，任务额度取更严格值。当前较新的消费及未决预留全部保留。

同一请求的预留不同、两份已结算金额不同或备份揭示更高的实际费用时，恢复会拒绝自动选择，必须核对完整历史。不能以保留当前文件为由忽略已知更高消费。

仅在原账本确实缺失、已确认选定备份包含项目全部历史时，才可使用 `--restore-missing-ledger` 恢复该备份账本。旧备份可能缺少备份后的付费请求；无法确认完整性时不能恢复云模型调用。应先与所有私有账本副本及供应商记录核对。

恢复多文件不构成跨文件原子事务。维护期间若中断，保持服务停止，利用自动生成的 pre-restore 快照恢复或重做已验证恢复；不可在部分恢复状态启动 Worker。恢复后检查 schema、项目隔离、费用基线、待恢复任务和外部调用不确定状态，执行公开/合成数据的完整任务后再开放服务。

## PostgreSQL 目标环境

本脚本拒绝在 `HANGZHI_DATABASE_URL` 指向 PostgreSQL 时声称完成平台备份。正式环境协调以下操作：

- 在维护窗口暂停接单，停止 Worker 并确认租约和在途外部调用。使用与服务端兼容的 `pg_dump --format=custom` 保存业务库；数据库连接用 libpq 环境及受保护 `.pgpass`，不在命令行暴露密码。
- 同一窗口用 SQLite backup API 保存身份库和固定预算账本，并复制受控资产目录。以共同恢复点记录 Git 提交、schema 版本、各文件哈希和账本汇总。
- 使用 `pg_restore` 先恢复到隔离的空数据库，执行约束、权限、任务恢复及费用基线校验。验证通过后切换配置；保留原数据库用于回退，不能直接覆盖唯一副本。
- PostgreSQL 迁移、WAL 归档/PITR、数据库角色、连接池、加密、容量、RTO/RPO 和多节点 Worker 行为需在目标环境实测后固定。当前本机 SQLite 测试不构成这些能力的验收。

## 离线源码与静态资源交付包

先完成前端生产构建、自动化测试和源码检查，再从根目录执行：

```powershell
& .venv/Scripts/python.exe scripts/offline_package.py --target linux-x86_64-python3.12
```

产物写入 `.local/release/`，包含公开源码、存在时的前端构建、依赖分发元数据清单和逐文件 SHA-256。它扫描 Git 跟踪及未忽略的新源码，拒绝私有路径、Office、数据库和常见凭据。manifest 同时记录 HEAD 与工作区是否有未提交修改。

可通过 `--wheelhouse` 加入已经准备好的目标平台 `.whl`。脚本不下载任何依赖，也不将当前 Windows 依赖误当作 Linux 已可用安装包。manifest 始终标记 `offline_acceptance=NOT_RUN`、`redistribution_review=REQUIRED`，直到另有目标环境验收记录。

交付前仍需完成：

1. 按 `uv.lock` 导出完整固定版本依赖，准备目标 OS/架构/Python 的 wheel 闭包和必要原生库。安装过程使用无索引离线依赖源；缺包应失败，不能回退公网。
2. 准备内网数据库、推理服务、模型权重/tokenizer、媒体编解码组件及实际许可证/NOTICE；软件声明、权重许可和驱动授权分别核验。
3. 固定前端静态资源、本地字体、环境配置模板和运行服务定义。关闭遥测、CDN、文档外部资源及自动下载，检查依赖可能的运行时联网。
4. 在与生产一致的断网环境完成安装、冷启动、重启、任务执行、取消、Worker 崩溃接管、并发和过期租约隔离；记录所有外连尝试。
5. 完成备份恢复和代码/schema 升级回退演练，记录条件、样本规模、失败、资源开销、RTO/RPO 和未完成项。

历史账本和其他运行数据通过独立私有迁移流程进入离线环境，绝不能在源码包中夹带。不存在经过验证的内网多模态服务时，能力目录继续返回不支持；不得用测试替身补齐验收结果。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| `ledger_missing` / `ledger_invalid` | 保持云调用关闭，查找完整历史账本并按恢复流程核验，不能新建额度。 |
| budget exhausted | 查看任务与项目的 settled/uncertain 占用；停止新调用，不能释放未知费用或增大历史上限。 |
| `execution_uncertain` | 对照调用幂等记录和供应商记录；禁止自动重放可能已计费或已有副作用的调用。 |
| 心跳过期/旧 Worker | 查看任务租约 token 与实例；接管只能使用新 token，旧实例结果必须被拒绝。 |
| `unsupported_capability` | 查看已验证模型/工具能力；接入并验证对应能力后再提交任务，不伪造媒体理解结果。 |
| 恢复哈希不一致 | 停止恢复，保留原文件调查，使用其他已验证副本。 |

运行日志和审计只记录定位所需标识与错误码，不复制密钥、私有材料全文或模型请求正文。周期检查费用未决记录、备份可恢复性、模型价格依据和依赖离线条件。
