# 依赖与离线交付清单

核对日期2026-10-17。Python精确版本与哈希在uv.lock，前端在frontend/package-lock.json。下表是当前安装包元数据与官方说明核对结果；不代替所有传递依赖、原生库和模型权重的再分发审查。

| 组件 | 当前锁定版本 | 声明许可 |
| --- | --- | --- |
| Agno | 3.0.10 | Apache-2.0 |
| FastAPI | 0.141.1 | MIT |
| Uvicorn | 0.53.0 | BSD-3-Clause |
| SQLAlchemy | 2.0.54 | MIT |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only |
| OpenAI SDK（仅网关内部使用） | 3.16.2 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| Pydantic | 2.13.5 | MIT |
| Pillow | 12.3.0 | MIT-CMU |
| python-docx | 1.2.0 | MIT |
| pypdf | 6.19.0 | BSD-3-Clause |
| PostgreSQL服务 | 目标17系列，CI使用17.11 | PostgreSQL License |
| React / ReactDOM / React Flow | 以package-lock为准 | MIT |
| Lucide | 以package-lock为准 | ISC |

PostgreSQL版本依据 https://www.postgresql.org/support/versioning/ ，许可依据 https://www.postgresql.org/about/licence/ 。Agno运行时自托管，官方云控制台不是本平台依赖。本版本已移除旧演示的外网文献/航空工具、MCP自动发现和自动下载型嵌入/OCR依赖。

通过 scripts/offline_package.py 生成的包包括实际安装分发的许可证元数据、源码哈希及前端静态构建。完整再分发时必须携带依赖包中的LICENSE/NOTICE；LGPL库及其二进制附带库需要逐项处理许可义务。模型权重、tokenizer、媒体编解码及GPU驱动尚无目标供给，不声称已完成授权核验。

scripts/Containerfile不指定可隐式联网拉取的基础镜像。必须提供已核验且本地存在的Python3.12镜像摘要BASE_IMAGE；使用podman build --pull=never。scripts/compose.yaml要求显式应用和PostgreSQL镜像摘要，服务监听本机回环。生产凭据仅由私有环境提供，数据库URL中的密码应按URL规则编码。

离线构建输入：
1. uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements-offline.txt
2. 在联网准备机为目标OS/架构/Python收集带哈希的wheelhouse闭包，独立核查许可证和原生库。
3. 复制frontend/dist、backend、requirements-offline.txt、wheelhouse到洁净构建目录；以scripts/Containerfile构建。pip使用--no-index和--require-hashes。
4. 将镜像导出并记录摘要，私有运行目录及历史账本按运维文档单独迁移。
5. 目标机断网安装、启动、任务、备份恢复、升级回退和出网审计通过后，才能把NOT_RUN改为真实验收结论。

当前本机WSL/Podman6.1.4已构建并运行Linux amd64断网应用镜像。Python3.12生产wheel闭包共55包、42,313,536字节，锁定哈希验证通过；构建和运行均无网络。依赖与OCI镜像保留在.local的交付目录，未上传到Git。该结果只覆盖本机容器与确定性任务；目标硬件、模型权重/编解码/GPU驱动、其他架构和实际再分发审查仍需分别完成。
