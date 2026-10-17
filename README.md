# 航智智能体管理平台

新版以智能体资产管理为中心，提供不可变版本、复合定义、流程复用、持久任务、自动评审、双视角监控和受控改进。旧研究工作台保存在 Git 归档标签，main 为新版实现。

## 本机启动

需要 Python 3.12、uv、Node.js 22+。首次准备依赖：

```powershell
uv sync --frozen --python 3.12
cd frontend
npm.cmd ci
cd ..
./scripts/start.ps1
```

访问 http://127.0.0.1:8000 。启动脚本同时运行 API 和独立 Worker。已有身份与历史预算保留。新身份库仅创建一个管理员：可事先设置 HANGZHI_ADMIN_PASSWORD；未设置时随机凭据写入忽略的 .local/bootstrap-admin.json，请在本机读取，勿提交或分享该文件。注册用户只有 operator 权限。

```powershell
./scripts/stop.ps1
```

Linux 可分别运行 uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 和 uv run python -m backend.app.platform.worker。正式部署使用 PostgreSQL，设置 HANGZHI_DATABASE_URL=postgresql+psycopg://...。SQLite 是本机开发运行方式。

## 已实现能力

- 资产定义、元/复合分类、版本发布、停用/启用/退役、引用保护和任务上下文隔离。
- 项目权限、数据登记/上传、来源版本、有效期、撤回、精确来源片段记忆与知识检索。
- 任务幂等、冻结执行图、租约与心跳、接管隔离、检查点、取消、截止时间和事件续传。
- 真实 JSON/CSV 整理、精确文本片段、Pillow 图片文件检查；云文本分析经过统一预算网关和独立自动评审。
- 有界修订、明确失败和部分交付；日历间隔计划持久运行。
- 冻结本地评测、候选拒绝/晋升/回滚；管理员可在部署前启用范围受限的自动改进。
- 深色中文管理台，智能体与任务互相跳转，产物、事件、模型用量和 Worker 状态均来自后端。
- 历史账本保护、SQLite一致性备份恢复、带哈希的公开源码/静态资源封装和 Linux/Podman 部署输入。

## 能力边界

本地提取式整理不是大模型推理；图片元数据不是图像语义理解。音频转写、视频理解及上游多模态模型尚待实际接入，任务会明确失败而不伪造结果。知识 HTTP 适配器需要管理员配置部署主机白名单和符合契约的服务。完整目标硬件断网安装、容量与时限验收需要实际部署环境。

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
- [实施和验收记录](docs/IMPLEMENTATION_PLAN.md)
- [使用手册](docs/USER_GUIDE.md)
- [运维与离线交付](docs/OPERATIONS.md)
- [验证结果](docs/VALIDATION.md)
- [上游下游接口](docs/INTEGRATION.md)

旧版归档：archive/v1-main-20261017、archive/v1-agent-lifecycle-20261017。保留历史，不强制推送。
