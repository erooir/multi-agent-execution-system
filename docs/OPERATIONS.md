# 航智平台运行与恢复

本文对应 v2 契约。Windows 是当前开发环境；本机 Windows CPU 环境已经接通达梦 DM8。最终交付设备、集群、内网推理服务及任务时限需分别在目标环境验收。不得将本机确定性工具运行或 rehearsal 作为离线模型推理证明。

当前正式部署请使用[达梦初始化、迁移和恢复手册](DAMENG.md)。下文注明日期的 PostgreSQL/SQLite 验证记录仅用于历史环境。

## 运行边界

日常用户从根路径提问。管理员或项目操作员通过 `/manage` 准备甲方资料、检查运行记录，已有只读观察员可查看其获授权的管理记录。给新注册的提问用户配置项目成员后，用户刷新即可提问，无需安装岗位或配置工作流。

公开测试环境可使用 `./scripts/start.ps1 -SkipBuild -QuestionPublicEgress`，也可在忽略目录的 `.local/question-public-egress.txt` 保存 `true`。这项部署策略只允许系统在选定资料均已明确获准公开外发时，经统一网关使用公开资料取证。单次问题受任务预算和历史账本共同约束，`-Offline` 会关闭外发。普通用户不选择模型或修改策略。

- API 默认绑定 `127.0.0.1`，Worker 作为独立进程共享平台数据库。浏览器使用平台受控的问答与管理 API，AgentOS 原生模型、团队和工作流路由不公开。
- `HANGZHI_DATABASE_URL` 配置平台业务库；正式入口强制达梦连接，连接配置缺失即停止启动。达梦凭据仅在服务端环境或受保护凭据文件中设置，不能写入 Git 或命令记录。
- 身份及设置随业务保存在达梦，迁移保留账号和历史任务，旧登录会话失效。上传文件位于 `.local/platform-assets/`；任务产物以业务库内容及索引保存。
- 模型历史账本固定为仓库 `.local/budget.sqlite3`。更换业务库、工作目录、`WORKBENCH_DATA_DIR` 或升级代码都不会获得新额度。默认网关在历史账本缺失时拒绝启动计费；只能从完整、可信的最新备份恢复。需要在独立适配目录或独立验收服务中共用同一历史账本时，可显式设置 `HANGZHI_BUDGET_LEDGER_PATH` 指向该唯一原件；不要复制成两份可分别消费的账本，API 与 Worker 必须配置相同路径。
- 模型密钥只从当前进程的 `DEEPSEEK_API_KEY_ZX` 或 Windows 当前用户注册表中的同名用户变量读取；旧变量名不再生效。日志、截图、异常和前端只显示是否已配置，不显示值。真实 deepseek-flash 验收须重启 API/Worker 后确认新变量已识别，不得打印密钥或使用旧进程缓存。
- 外部 HTTP Tool 需要 `HANGZHI_TOOL_HOSTS`，Streamable HTTP MCP 需要 `HANGZHI_MCP_HOSTS`；stdio MCP 通过 `HANGZHI_MCP_LAUNCHERS` JSON 登记预装绝对路径、固定参数和 cwd。`HANGZHI_ALLOW_LOCAL_HTTP=true` 仅用于明确的本机 fixture。所有 `secret_ref` 只引用 Worker 环境变量，不能把密钥值写入定义。
- 外部 MCP/Toolkit 由 Agno 原生驱动执行。新增依赖与下载缓存放E盘，使用 `UV_PROJECT_ENVIRONMENT` 指定E盘虚拟环境，按 `uv.lock` 准备含 `dameng` extra 的锁定依赖；离线交付使用预先核验的介质。核验后将实际Python绝对路径写入私有 `.local/platform-python.txt`，启动与维护使用同一环境。模板升级使用该解释器执行 `-m scripts.enable_agent_tools`。需要schema升级时须先停服，显式执行 `-m scripts.dm_database init` 和 `check`；启动只核验达梦结构，不自动迁移或重置身份、业务及预算数据。
- 禁止运行时安装依赖、下载模型/字体或启用遥测。RapidOCR 模型随固定依赖提供。对外部请求的许可由任务策略、部署白名单和网关共同执行。

## 跨任务自动维护开关

2026-11-09 起，同一开关覆盖智能体和工作流两类受限候选。工作流候选可以由真实文本覆盖不足生成，完整本地DAG重放；部署的任务插件同样执行原合同与限额。没有独立开发/留出来源、可测增益或可靠回归证据时不晋升。管理员在“效果评测”中可区分对象、查看逐样本输出和版本，手动触发管理操作不构成任务执行中的人工审核等待。

任务内修复默认可用，最多执行任务受理时批准的修订轮数。后台生成、评测和晋升跨任务候选由 Worker 的 `HANGZHI_AUTO_IMPROVE` 控制，未配置时关闭。Windows 可运行 `./scripts/start.ps1 -SkipBuild -AutoImprove` 开启，或 `-AutoImprove:$false` 关闭。需要正常启动也延续本机选择时，把单行 `true` 或 `false` 写入私有 `.local/auto-improve.txt`。显式启动参数优先，其次是环境变量和本机文件。切换策略需重启 API 与 Worker；“评测与改进”页显示实际在线 Worker 报告的状态。候选使用本地冻结参考资料，来源不独立、硬性回归或无开发收益时不会自动晋升。

## 基础任务并发

Worker 默认2个任务槽，合法范围1–32。Windows启动可用 `./scripts/start.ps1 -SkipBuild -WorkerConcurrency 4`，或配置 `HANGZHI_WORKER_CONCURRENCY=4`；独立进程可用 `python -m backend.app.platform.worker --concurrency 4`，CLI参数优先。Linux Compose读取同名环境变量。非法值直接报错，不静默降为串行。`--once`仍只执行至多一项。

管理设置显示已占用/总槽位。全局管理员可看真实空闲槽位，按项目查看时只显示可见任务占用，不能据此推断其他项目负载。停止中的Worker持续报告占用直到本地调用结束；仍在执行的工具不提前标记释放。多个Worker共享同一DM业务库和唯一历史预算账本，租约防止重复领取。网关模型并发限制目前是每进程2，不等于整个部署或供应商的速率上限。

目前共享账本适用于同一主机上的多个进程/容器。跨主机执行还需验证账本存储锁语义、网络与故障恢复，不能在多台机器复制账本各自消费同一额度。槽位配置变化需要停止旧Worker后重新启动；源码拉取、前端构建和数据库检查本身不会替换已运行进程。

槽位代表允许的任务并发，不是目标设备吞吐承诺。单任务DAG仍顺序执行就绪节点；大媒体、模型限流、数据库及CPU容量需要目标硬件实测。本机验收用阻塞同步工具证明两个以上任务真实重叠，同时验证心跳、停止与接管。

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

2026-10-17 核对 [DeepSeek 中文价格表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)时记录的 Flash 高峰输入为人民币 2 元/百万 tokens、输出为 8 元/百万 tokens。这是当时核价记录；网关采用 4/16 的保守费率，忽略缓存及低峰优惠，表示预算占用而非供应商实际账单。模型 ID 由 `HANGZHI_MODEL_ID` 配置，但仅允许已核价的 Flash 名称；更换供应商或计费模型需要更新适配、价格依据和测试，不能只改字符串绕过控制。

输入、输出 token 缺失或无效、超时、连接失败和在途取消均保留费用预留。收到未核价模型或实际用量超过预留时停止后续请求，需核对供应商及账本。不得用“调用失败”作为自动退款依据。费用不确定只影响账务展示与对账，不再阻止任务恢复：超时、连接失败、限流、服务端故障、截断和可纠正的输出错误在冻结修订次数、任务预算、20 次请求及截止时间上限内自动重试，全部尝试继续独立预留和计量。HTTP 400 等请求合同错误保留不含密钥和请求原文的供应商诊断，先修正请求，不盲目重放相同负载。

`usage` 包含 `cost_cny`、`reserved_cny`、`total_cny`、`request_count`、`input_tokens`、`output_tokens`、`uncertain_count`、`accounting_complete`。任务可按 purpose 查询，工具多轮调用返回所有实际请求合计。`accounting_complete=false` 必须在结果和管理界面保留，不可将未知费用显示为零。

取消在派发前不创建账单记录；在途调用取消会中断等待并保留 uncertain 记录。业务终态、调用是否停止及实例是否释放是不同事实。失去租约的 Worker 不得继续发起模型/工具请求，也不得写回任务结果。

## SQLite 开发环境备份

`scripts/runtime_backup.py` 使用 SQLite backup API，能够纳入 WAL 中已提交的数据；不直接复制打开的数据库主文件。它只备份三个登记数据库及 `platform-assets/`，不会打包整个 `.local`、私有交接包或密钥。

1. 先停止 API 和所有 Worker，确认没有活跃进程。跨数据库及文件的一致快照必须在此维护窗口中进行。
2. 从项目根目录执行：

```powershell
$platformPython = (Get-Content -LiteralPath .local/platform-python.txt -Raw).Trim()
& $platformPython scripts/runtime_backup.py backup --services-stopped
```

输出为全新的 `.local/backups/snapshot-<UTC时间>-<随机后缀>/`，已存在的目标不会被覆盖。可用 `--path` 指定 `.local/backups/` 下的新目录。文件 manifest 包含哈希、大小、备份时间、预算汇总和跨库一致性声明。

```powershell
& $platformPython scripts/runtime_backup.py verify --path .local/backups/实际目录
```

未带 `--services-stopped` 仍可生成每个 SQLite 数据库的一致备份，但 manifest 明确标记 `cross_database_consistent=false`，不得用作整套恢复。该参数是操作者对维护状态的声明，脚本不会替操作者关闭进程。

备份和恢复记录均含私有运行信息，只在受保护本地存储留存。不能提交、加入源码交付包或自动上传。离机副本需通过用户批准的私有迁移流程，并验证哈希。

## SQLite 恢复与回退

1. 停止 API 和所有 Worker；保存当前应用版本、数据库 schema 版本和最新一致备份。
2. 验证目标备份；确认该快照与计划恢复的代码/schema 兼容。代码回退不能代替数据库迁移回退。
3. 执行恢复：

```powershell
$platformPython = (Get-Content -LiteralPath .local/platform-python.txt -Raw).Trim()
& $platformPython scripts/runtime_backup.py restore --path .local/backups/实际目录 --services-stopped
```

恢复前自动备份当前运行态。非预算数据库和登记文件逐个原子替换；目标仍存在 WAL/SHM 时拒绝替换，应先确认服务关闭并做 SQLite 完整检查点。受控文件中快照外的现存文件不会自动删除，避免恢复操作破坏尚未归档的数据；它们没有新数据库引用时不会通过业务 API 暴露。

**预算账本永不随旧业务快照倒退。** 当前账本存在时仅追加备份中缺失的历史费用记录，保留当前同 ID 记录；计量异常计数不会降低，任务额度取更严格值。当前较新的消费及未决预留全部保留。

同一请求的预留不同、两份已结算金额不同或备份揭示更高的实际费用时，恢复会拒绝自动选择，必须核对完整历史。不能以保留当前文件为由忽略已知更高消费。

仅在原账本确实缺失、已确认选定备份包含项目全部历史时，才可使用 `--restore-missing-ledger` 恢复该备份账本。旧备份可能缺少备份后的付费请求；无法确认完整性时不能恢复云模型调用。应先与所有私有账本副本及供应商记录核对。

恢复多文件不构成跨文件原子事务。维护期间若中断，保持服务停止，利用自动生成的 pre-restore 快照恢复或重做已验证恢复；不可在部分恢复状态启动 Worker。恢复后检查 schema、项目隔离、费用基线、待恢复任务和外部调用不确定状态，执行公开/合成数据的完整任务后再开放服务。

## 历史 PostgreSQL 兼容环境（2026-10-17）

2026-10-17在开发机按用户授权安装独立PostgreSQL17.11测试二进制至`.local/tools/postgresql17/`，仅以127.0.0.1:55432执行合成并发验收，测试完成后停止。当时没有替换SQLite业务库，也没有迁移或重置历史预算账本。二进制来源为EDB官方Windows发行包，下载地址和SHA-256保存在该目录的installation.json；测试密码保存在私有文件，不在本手册复制。当前正式业务与身份存储为达梦，本段不能作为新的生产数据库选型或运行状态。

SQLite备份脚本拒绝在 `HANGZHI_DATABASE_URL` 指向 PostgreSQL 时声称完成平台备份。仅维护历史PostgreSQL环境时适用以下流程；达梦部署按前文的专门手册执行：

- 在维护窗口暂停接单，停止 Worker 并确认租约和在途外部调用。使用与服务端兼容的 `pg_dump --format=custom` 保存业务库；数据库连接用 libpq 环境及受保护 `.pgpass`，不在命令行暴露密码。
- 同一窗口用 SQLite backup API 保存身份库和固定预算账本，并复制受控资产目录。以共同恢复点记录 Git 提交、schema 版本、各文件哈希和账本汇总。
- 使用 `pg_restore` 先恢复到隔离的空数据库，执行约束、权限、任务恢复及费用基线校验。验证通过后切换配置；保留原数据库用于回退，不能直接覆盖唯一副本。
- PostgreSQL 迁移、WAL 归档/PITR、数据库角色、连接池、加密、容量、RTO/RPO 和多节点 Worker 行为需在目标环境实测后固定。当前本机 SQLite 测试不构成这些能力的验收。

## 离线源码与静态资源交付包

先完成前端生产构建、自动化测试和源码检查，再从根目录执行：

```powershell
$platformPython = (Get-Content -LiteralPath .local/platform-python.txt -Raw).Trim()
& $platformPython scripts/offline_package.py --target linux-x86_64-python3.12
```

产物写入 `.local/release/`，包含 **Git 索引中的公开版本**、单独核查的前端构建、依赖分发元数据清单和逐文件 SHA-256。新模块需先审核并提交或暂存；未跟踪文件和未暂存的源码修改不进入包。前端只接受 index.html 和当前 Vite 构建的带哈希 JS/CSS；混入其他文件时停止。manifest 同时记录 HEAD 与工作区是否有未提交修改。

可通过 `--wheelhouse` 加入准备机上名称和 SHA-256 与索引中 uv.lock 一致的 `.whl`。脚本不下载依赖；ZIP 是源码交付，不表示指定平台已有完整运行环境。manifest 标记 `offline_acceptance=NOT_RUN`、`redistribution_review=REQUIRED`，直到另有目标环境验收记录。

交付前仍需完成：

1. 按 `uv.lock` 导出完整固定版本依赖，准备目标 OS/架构/Python 的 wheel 闭包和必要原生库。安装过程使用无索引离线依赖源；缺包应失败，不能回退公网。
2. 准备内网数据库、推理服务、模型权重/tokenizer、媒体编解码组件及实际许可证/NOTICE；软件声明、权重许可和驱动授权分别核验。
3. 固定前端静态资源、本地字体、环境配置模板和运行服务定义。关闭遥测、CDN、文档外部资源及自动下载，检查依赖可能的运行时联网。
4. 在与生产一致的断网环境完成安装、冷启动、重启、任务执行、取消、Worker 崩溃接管、并发和过期租约隔离；记录所有外连尝试。
5. 完成备份恢复和代码/schema 升级回退演练，记录条件、样本规模、失败、资源开销、RTO/RPO 和未完成项。

历史账本和其他运行数据通过独立私有迁移流程进入离线环境，绝不能在源码包中夹带。不存在经过验证的内网多模态服务时，能力目录继续返回不支持；不得用测试替身补齐验收结果。

## Linux 容器：构建、私有挂载与运行模式（2026-11-02）

当前开发默认允许**通过统一网关**调用固定的已计价模型；没有密钥、当前来源不允许外发、账本不可用或预算不足仍会拒绝。30 个本地用例自身不需要云调用。容器镜像和 wheel 安装可在有网准备机制作，也保留无索引/离线安装路径；此处不声称新版本已在客户目标 Linux 服务上通过验收。

**生成干净的构建目录。** 对应目标工件为 Linux amd64 / ARM64、CPython 3.12；不能用 Windows 91 个 wheel 或旧 Linux 55 包闭包代替。在准备机导出 `uv export --frozen --all-extras --no-dev --no-emit-project --format requirements-txt -o <要求清单>`，为目标 OS/CPU 备齐 wheel。确认应用代码和控制脚本已在 Git 索引内，从这版源码构建 frontend/dist，再执行：

```text
python -m scripts.offline_package --target linux-x86_64-python3.12 --container-context .local/release/context-<版本> --requirements <要求清单> --wheelhouse <Linux wheelhouse>
podman build --pull=never --network=none --build-arg BASE_IMAGE=<本地已加载的镜像@sha256摘要> -f .local/release/context-<版本>/scripts/Containerfile -t hangzhi-app:<版本> .local/release/context-<版本>
```

ARM64 用 `linux-aarch64-python3.12`。上下文只含已审核的 backend 应用源码（含 casework）、公开 skills、DM 初始化/迁移 CLI、管理员参考批次登记和其清单读取程序、静态构建、去除准备机注释路径的哈希依赖清单、锁定 wheel。只允许新的 `.local/release` 子目录；目标平台或包哈希不符会拒绝。安装缺包即失败。**不要把整个工作区、客户原件、实际可信清单、模型、DSN 或 .local 加入构建上下文。** 基础镜像须事先具备目标 Linux 所需原生运行库或适当的外部供应方式；Windows EXE/DLL 不适用于 Linux。

**启动前分别准备以下挂载。** Linux Compose 使用已加载的 `HANGZHI_APP_IMAGE`（固定镜像摘要，禁止自动拉取），以受保护环境提供 `HANGZHI_DM_URL`。宿主机上的三个目录必须先存在，不能用空卷替代历史账本：

| 宿主变量 | 容器路径 / 权限 | 准备内容 |
| --- | --- | --- |
| HANGZHI_LOCAL_DIR | /app/.local，可写 | 专用私有运行目录，包括经核验的原 budget.sqlite3 和受控资产。不要直接挂载混有交接包的开发目录。应用 UID/GID 1000 应仅获所需权限。冻结媒体路径迁移条件见[达梦手册](DAMENG.md)。 |
| HANGZHI_REFERENCE_DIR | /mnt/hangzhi-reference，只读 | 部署人员核验并登记的 reference-batches.json。可在管理环境运行 `python -m scripts.register_reference_batches`，将输出指向此私有目录；同批复制历史。API/Worker 只读。无可信清单不能判定参考批次成功。 |
| HANGZHI_MEDIA_DIR | /mnt/hangzhi-media，只读 | 经授权私下迁移的词库、ASR/翻译模型，保留相对目录层级。可挂已存在的空目录进行无媒体的验证，此时相应媒体能力不可用。 |

Windows 准备目录 `E:/Hangzhi/offline-media` 中可移植的是与目标组件匹配且获准迁移的 `runtime/share/tessdata`、`models/whisper-base` 和 `translation/translate-{en_zh,ru_en}-1_9`。将这些数据放入 **Linux 宿主自己的 HANGZHI_MEDIA_DIR**，不用 E 盘绝对路径作为容器内路径。Tesseract、FFmpeg、ffprobe 和 DM 客户端要使用 Linux 发行版适配的程序/动态库；Compose 默认前三者位于 /usr/bin，可用 HANGZHI_CONTAINER_TESSERACT_PATH、HANGZHI_CONTAINER_FFMPEG_PATH、HANGZHI_CONTAINER_FFPROBE_PATH 调整容器内绝对路径。语言用 HANGZHI_OCR_LANG，默认 eng+rus；词库未知或缺失不会自动下载。API/Worker 需使用相同挂载和配置，避免受理时指纹与执行时不符。

1. 在 API 与 Worker 尚未运行时，按[DM8 初始化与迁移](DAMENG.md)在独立 schema 运行 `python -m scripts.dm_database init` / `check`。凭据仅由部署系统传入；不要展示含真实环境值的 compose config 或容器 inspect 输出。
2. 在 Linux 主机用 `scripts/compose.yaml` 启动两个服务，检查回环地址 `127.0.0.1:8000/api/v2/health` 与 Worker 心跳。默认 `HANGZHI_OFFLINE=false`；如需云取证，私下提供 DEEPSEEK_API_KEY_ZX，必要时提供 HANGZHI_MODEL_PROXY。即使允许云网关，HF_HUB_OFFLINE=1、PROJ_NETWORK=OFF、Agno 遥测关闭，避免运行时抓取模型/地理资源。
3. **可选全离线模式**：设置 `HANGZHI_OFFLINE=true` 并重新创建 API/Worker。应用拒绝付费模型调用；如果需要实际网络隔离，宿主/部署网络还须封禁公网、仅保留回环或获准 DM/内网服务。Compose 使用 Linux host 网络与专用 DM；一个策略开关或 Web 健康检查不能证明物理断网。验收专用 8100 实例保持独立，不复用正式运行目录。
4. 执行[用例验收](CASEWORK.md)核对合成样本、可信批次和权限，分别记录实际软件、权重、许可、耗时及结果；小样本不证明全部原件或目标机服务时限。

## 历史本机容器验收产物

2026-10-17，公开代码cc92ec5在本机WSL/Podman6.1.4完成Linux amd64应用断网构建与任务验证。生产依赖为55个固定哈希wheel，基础镜像为Python3.12.15，均在联网准备阶段获得；构建、依赖安装及应用运行阶段不依赖网络。具体范围见VALIDATION.md，不能替代目标生产硬件、模型推理或容量验收。

- 原始构建镜像：`.local/release/hangzhi-v2-cc92ec5-linux-amd64.oci.tar`，156304896字节；SHA-256 `b4c2f28175840259118a0abc20c1d2def40ac6a419e8ef54d52df48af0988b5f`。
- 依赖闭包：`.local/container-validation/build-context/wheelhouse/`；对应`requirements-offline.txt`同目录。
- 镜像digest、wheel哈希、实际API/任务结果及网络隔离证据：`.local/v2-validation/container-environment.json`及其引用文件。
- Podman便携工具和独立机器已保留；验证容器与卷已清理，机器hangzhi-validation已停止，避免持续占用资源。Windows8000主服务继续运行。

镜像仅含公开代码、依赖与前端静态资源，`/app/.local`为空。使用`podman load -i`载入后，正式启动前仍须按照前文恢复受保护运行目录、完整历史账本和外部配置；不得把空运行目录当作项目费用重新起算的依据。OCI文件及wheel保存在本地忽略目录，未上传到Git。

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
