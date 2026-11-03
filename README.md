# 航智智能体管理平台

默认工作台面向提问用户。用户在甲方已提供资料的项目中写下问题，平台选择资料与执行方式、构建或复用岗位、核查答案，并在需要时调整后续步骤。用户查看答复、材料出处和未能解决的部分。获授权的管理人员可在[管理工作台](http://127.0.0.1:8000/manage)查看工具、不可变版本、任务轨迹和改进评测。

## 本机启动

正式入口使用自托管 Agno AgentOS、达梦 DM8 和独立 Worker。已配置的 Windows 开发机运行以下命令，然后访问 [本机工作台](http://127.0.0.1:8000)。当前开发入口默认开放受控云调用，复用原身份和累计模型账本。管理员给资料标注公开、任务获得外发授权且有预算后，统一网关才会放行真实模型请求。

```powershell
./scripts/start_dm8_dev.ps1
./scripts/start.ps1 -SkipBuild
```

后续隔离部署执行 `./scripts/start.ps1 -SkipBuild -Offline`，也可由部署环境指定 `HANGZHI_OFFLINE=true`。切换时先停 API 和 Worker，再按目标策略启动。已在环境中设置离线时，可显式用 `-Offline:$false` 启动受控云模式。界面右上角显示实际生效策略。

用例审核未通过时，详情页的“流程调整记录”展示具体失败原因、采用的操作和复核结果。跨任务自动维护可在 Windows 启动时加 `-AutoImprove`；“评测与改进”显示在线 Worker 的实际开关。独立留出资料的重放达到晋升门槛后，后续任务才使用新版本。

新机器准备 Python 3.12、Node.js 22、构建好的前端、相应平台的离线依赖、授权的达梦实例以及机构发放的连接凭据。联网准备机可执行下列操作。目标机器从内网或介质安装经核对的依赖，使用专用达梦应用 schema。初始化、连接检查和迁移步骤见[达梦运行手册](docs/DAMENG.md)。

```powershell
uv sync --frozen --all-extras --python 3.12
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
./scripts/start.ps1 -DmUrlFile <本机私有连接文件> -SkipBuild
```

已有账号沿用原角色。新注册的提问用户为 requester，需要管理员为其分配项目。新身份库可事先设置 `HANGZHI_ADMIN_PASSWORD`，否则随机初始凭据保存在忽略的 `.local/bootstrap-admin.json`。停服使用 `./scripts/stop.ps1`。

## 已实现能力

- 资产定义、元/复合分类、版本发布、停用/启用/退役、引用保护和任务上下文隔离。
- 项目权限、数据登记/上传、来源版本、有效期、撤回、精确来源片段记忆与知识检索。
- 任务幂等、冻结执行图、租约与心跳、接管隔离、检查点、取消、截止时间和事件续传。
- 真实 JSON/CSV 整理、精确文本片段、Pillow 图片文件检查；云文本分析经过统一预算网关和独立自动评审。
- 使用 Agno 原生 Toolkit、MCPTools 与 LocalSkills/Skills 接入外部能力，支持纯文档 Skill 导入、只读 HTTP JSON Tool、stdio 与 Streamable HTTP MCP、不可变版本和项目授权。
- 本地 RapidOCR/ONNX 图片文字识别，输出区域和真实置信度；`deepseek-flash` 云视觉理解及独立视觉评审。
- 能力接入管理页、显式工具授权、图片预览、OCR/视觉产物和原生调用追溯。
- 有界修订、明确失败和部分交付；日历间隔计划持久运行。
- 冻结本地评测、候选拒绝/晋升/回滚；管理员可在部署前启用范围受限的自动改进。
- 深色中文管理台，智能体与任务互相跳转，产物、事件、模型用量和 Worker 状态均来自后端。
- 历史账本保护、SQLite一致性备份恢复、带哈希的公开源码/静态资源封装和 Linux/Podman 部署输入。

18 项可独立授权工具分别支持原文证据、表格、期限与时间序列、几何及路网。工作台的岗位对照显示每个专员实际使用的主算法和工具授权；任务详情记录实际调用。既有项目可在“用例研判”显式升级岗位工具授权，历史版本继续可查。

## 能力边界

“用例研判”提供 30 项任务契约。每个项目可安装 32 个版本化专员、30 个三节点流程。平台支持本地文件解析、OCR、带时间码的语音转写、受约束的图像比较、空间计算、证据核验和中文成果包。完整参考验收按可信来源清单校验，缩小输入须标记为样本。对未提供的底图、坐标、术语映射或观测记录，报告注明具体缺口。参考任务与实测边界见[用例操作手册](docs/CASEWORK.md)和[验证记录](docs/VALIDATION.md)。

通用任务另支持本地 RapidOCR 和云视觉理解。外部能力优先使用 Agno 原生 Toolkit、MCPTools、LocalSkills/Skills 与 Function；Skill 仅开放文档与参考资料，外部 Tool/MCP 仅开放管理员授权的只读、无未知按次费用服务，不提供任意代码执行、外部写操作或运行时下载。新依赖下的完整离线镜像、目标硬件、容量与时限验收需要实际部署环境。

所有付费模型流量只经过 backend/app/model_gateway.py。累计项目预算为300元，继续使用原 .local/budget.sqlite3；该账本缺失时关闭付费调用，禁止创建新账本绕过历史。模型密钥只从服务端环境读取。私有方案、交接材料、Office、数据与报告不进入 Git。

## 验证与文档

```powershell
uv run pytest -q
uv run ruff check backend scripts
uv run ruff format --check backend scripts
cd frontend
npm.cmd test
npm.cmd run build
```

- [架构与共享接口](docs/ARCHITECTURE.md)
- [11 月 2 日研发汇报](docs/PROGRESS_20261102.md)
- [实施和验收记录](docs/IMPLEMENTATION_PLAN.md)
- [使用手册](docs/USER_GUIDE.md)
- [运维与离线交付](docs/OPERATIONS.md)
- [验证结果](docs/VALIDATION.md)
- [上游下游接口](docs/INTEGRATION.md)

旧版归档：archive/v1-main-20261017、archive/v1-agent-lifecycle-20261017。保留历史，不强制推送。
