# 达梦业务与身份数据库

正式部署使用 **DM8** 存储业务对象、智能体版本、任务快照、节点、事件、资料索引、评测和监测记录；账号、密码散列、会话、角色、限流和身份审计同样存放在 DM8。原始媒体仍在受控文件目录。**唯一特意独立保留的费用数据库是历史 `.local/budget.sqlite3`**：统一模型网关继续使用既有逐笔历史、最坏预留和 300 元累计上限，迁移程序不导入、重建或替换费用账本。

## 部署

已有平台升级到问答版本时，先停止 API 和 Worker，按既有私有连接配置运行 `python -m scripts.dm_database init`，再用 `python -m scripts.dm_database check` 核验后启动服务。升级程序仅在识别到完整旧版业务库时新增 `questions` 表及其索引，支持重复执行。缺少其他表、含未知表或身份写入锁异常时会停止。身份、历史任务、不可变版本与独立预算账本均保留。

1. 单独配置有权使用的 DM8 实例。使用 UTF-8 数据库、独立非管理员账号及与账号同名的专用 schema。开发测试版不能外推为生产授权。初始化用账号须具备建表和建索引权限；不要将 SYSDBA 凭据作为应用连接。
2. 使用 `pyproject.toml` 的 `dameng` extra：`dmPython==2.6.1` 和 `dmSQLAlchemy==2.0.17`。离线准备必须为目标 OS/CPU/Python 准备 **含该 extra** 的依赖，例如 `uv export --frozen --extra dameng --no-dev --no-emit-project --format requirements-txt --output-file requirements-offline.txt`，在无索引环境安装已经核验的 wheel。容器构建上下文同时包含 `scripts/dm_database.py` 和 `scripts/dm_business_migration.py`。Windows 支持把项目虚拟环境安装在 E 盘并将绝对 Python 路径写入私有 `.local/platform-python.txt`；不要把安装介质、数据、密码文件或运行日志交付到 Git。
3. 用受限的私有文件或服务环境提供连接，格式为 `dm+dmPython://<app-user>:<percent-encoded-password>@<host>:<port>/<app-schema>`。应用强制客户端 UTF-8（`local_code=1`），防止 Windows 客户端按 GBK 编码中文以外字符、单位和审计记录。生产部署使用 `HANGZHI_REQUIRE_DM=true`，连接错误或无连接时不能回退到 SQLite 或 PostgreSQL。
4. 停止 API 和 Worker 后，单进程运行 `python -m scripts.dm_database init`。空的专用 schema 会初始化。升级前的完整 v1 schema 若仅缺少 `questions`，则新增问题收件箱表，保留原有任务、资产与身份。再次执行会校验现有结构。存在未知表、其他缺表或版本异常时拒绝修改。DM DDL 可能隐式提交，中断后先检查结构。`python -m scripts.dm_database check` 只读核验表清单、版本标志、身份锁行及问题表预期列，使用应用账号即可完成。
5. Linux Compose 的 API 和 Worker 共用外部达梦服务，私有部署环境设置 `HANGZHI_DM_URL`；不再启动 PostgreSQL 容器。Windows 可运行 `scripts/start.ps1 -DmUrlFile <私有DSN文件> -SkipBuild`。也可将该私有文件的**路径**写入忽略的 `.local/dm-url-file.txt`，供以后正常重启使用。启动脚本拒绝复用连接配置不同的 API/Worker。只有明确维护历史归档时使用 `-LegacySQLite`，且不能将该模式结果作为达梦验收。

## 停服迁移与恢复

- 先停止 API 及全部 Worker，收束非终态任务，制作并核验 SQLite 原库、媒体文件、身份和历史预算的私有一致性备份。**不要运行整套还原覆盖最新费用库**。达梦投产后，业务/身份备份采用达梦原生备份恢复流程，历史 SQLite 备份脚本不具备达梦热备能力。
- 将 `HANGZHI_DATABASE_URL` 指向已经 `init` 的空 DM8 schema；迁移身份用 `python -m scripts.dm_database import-identity --services-stopped --identity-source <私有workbench.sqlite3备份>`。仅导入账号及密码散列、设置、身份审计和初始化标志；不复活历史会话或复制旧业务运行记录。新身份库非空即拒绝，逐条回读比对，在单个事务中完成。
- 迁移业务用 `python -m scripts.dm_database import-business --services-stopped --business-source <私有platform-v2.sqlite3备份>`。工具以只读事务打开源库，验证完整性、版本、表和列，检查非终态任务，确认每个达梦业务表为空，分批复制并比对每条记录的**全部字段**与逐表数量。保留不可变版本、任务快照、原主键、幂等键、事件 sequence 和其中已有空号；后续事件从已导入的序号继续。媒体引用须指向可用受控目录、校验和一致。按原路径私下迁移媒体文件；本程序不改写冻结内容、复制文件或修复跨主机的绝对路径。
- 迁移失败会回滚，不做覆盖、截断或删除源库；目标非空时不会再执行。测试覆盖媒体缺失时的失败回滚、二次导入拒绝、原始版本与游标保留及迁移后新事件序号。若正式启用后已有业务写入，切回旧库不是等价恢复，需要将新增业务变化纳入恢复计划。

## 验证与实现边界

业务实体的 JSON 记录采用达梦 CLOB，保留 Unicode、结构与大快照。监测查询用 `JSON_VALUE` 的**完整固定路径**在 SQL 中投影必要字段，不加载资料正文。并发项目操作、任务领取、心跳围栏、周期调度采用达梦行锁。实测发现 `FETCH FIRST 1` 与 `SKIP LOCKED` 组合会造成多个 Worker 同时跳过队首而未检查其他空闲任务；达梦分支先按原策略投影最多 64 个候选 ID，再对每个候选跳过已锁行、重验领取条件。身份数据库使用一行专用事务锁，保持跨进程初始化、防止重复账号及管理员并发降级等约束。

2026-11-02 使用官方 Windows x86_64 DM8 开发测试介质，在普通用户空间初始化 UTF-8 实例，实际数据库版本为 `03134284604-20260707-335949-20228`。官方原始 ISO 与随包 SHA-256 匹配；安装、运行文件、数据、驱动 wheel、私有凭据与诊断留在 E 盘。SQL 配置 `LISTEN_IP=127.0.0.1`、`IPV6_LSNR=0`、`INVITED_IP=127.0.0.1`；该 Windows 版本实测 OS socket 仍显示通配地址，因此同时采用针对该程序/端口的入站阻断规则并验证其他本机网卡地址登录失败，不能把系统监听描述成只绑定了回环地址。

本机重启后可用 `scripts/start_dm8_dev.ps1` 核对已配置实例的网络限制、启动或识别原实例；不会下载软件、修改防火墙或启动未知进程。该脚本是本地开发环境入口，生产实例由 DBA 管理。应用重启使用 `scripts/start.ps1 -SkipBuild` 及本机私有连接文件。该入口默认允许受控云模型，交付到隔离网络时加 `-Offline`。不要将多个环境的真实DSN或系统账号口令写入脚本。

真实专用达梦 schema 验证了 4 连接并发幂等、4 Worker 无重复领取、过期接管和旧令牌拒绝、有序事件、监测窄投影、大 CLOB 中文/平方符号/希腊文/俄文/emoji 往返、身份读写、迁移校验与拒绝覆盖。执行 `backend/tests/test_dameng.py` 时用 `HANGZHI_TEST_DM_URL` 显式指定用户同名且以 `_TEST` 结尾的独立空闲 schema，不能使用真实业务库；未配置服务时如实跳过真机测试。SQL 方言编译、SQLite 或 PostgreSQL 测试不能代替本项。生产设备的备份恢复、容量、集群故障切换和授权仍需单独验收。

厂商依据：[官方开发测试介质](https://eco.dameng.com/download/)、[安装与许可说明](https://eco.dameng.com/document/dm/zh-cn/pm/install-uninstall.html)、[dmPython](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-introduction.html)、[dmSQLAlchemy 方言](https://eco.dameng.com/document/dm/zh-cn/pm/dmsqlalchemy-dialect-package.html)、[查询与行锁](https://eco.dameng.com/document/dm/zh-cn/pm/check-phrases.html)。
