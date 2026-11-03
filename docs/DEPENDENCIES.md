# 依赖与离线交付清单

2026-11-02 云端基线及后续原生能力适配。Python 精确版本与哈希在 `uv.lock`，前端在 `frontend/package-lock.json`。核心依赖沿用既有锁定与许可记录；DM8、文档、空间、语音、翻译、视觉、MCP 2 与 RapidOCR 依赖见后表。软件包能安装不等于安装介质、数据库或模型取得再分发授权。

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
| PostgreSQL 服务（历史兼容验证） | 17.11；不是当前正式数据库 | PostgreSQL License |
| React / ReactDOM / React Flow | 以package-lock为准 | MIT |
| Lucide | 以package-lock为准 | ISC |
| 官方 MCP Python SDK | 2.3.0（uv.lock） | MIT |
| FastMCP（Agno 原生 MCP 连接） | 4.1.4（uv.lock） | Apache-2.0 |
| RapidOCR / ONNX Runtime | 1.4.4 / 1.31.1 | Apache-2.0 / MIT |
| DDGS（Agno 公开搜索 Toolkit） | 9.16.1 | MIT |
| JSON Schema | 以uv.lock为准 | MIT |
| openapi-typescript（开发依赖） | 7.13.0 | MIT |
| Playwright Test（开发依赖） | 1.64.1 | Apache-2.0 |

本轮前端指南经 skill-installer 下载到 tools/frontend-skills；版本、校验和及许可依据保存在该目录的 PROVENANCE.json。React Best Practices 按其 SKILL.md 的 MIT 声明记录（上游没有独立根 LICENSE）；Web Interface Guidelines 的独立 LICENSE 已随资源保存。最新 command.md 也已保存，离线复核使用该固定版本。

Agno 运行时自托管，不依赖官方云控制台。用例双模式直接复用当前 Agno 3.0.10 的 `Agent.arun()`、Function 和工具循环，没有新增 Agent 框架、模型 SDK 或独立 Runner。正式业务和身份库使用[达梦 DM8](DAMENG.md)。`.local/budget.sqlite3` 继续由统一模型网关单独读取；它不是 DM 的业务库或用于安装测试的空数据库。独立服务可用 `HANGZHI_BUDGET_LEDGER_PATH` 指向同一原件，不得通过复制账本获得重复额度。项目不包含旧演示系统的外网文献/航空工具或运行时自动下载模型；MCP 连接使用 Agno 原生 MCPTools/FastMCP，通用 OCR 使用随包提供的 RapidOCR 模型，禁止运行时下载。PostgreSQL 仅保留历史兼容验证，不替代 DM8。

### Windows CPU 离线交付（2026-11-02 云端基线记录）

以下是云端基线的既有验收，不自动覆盖本次新增的 MCP 2、FastMCP、RapidOCR、DDGS 和 JSON Schema 依赖闭包；合并版本必须重新导出并断网复验。

本机准备位置为 `E:/HangzhiPlatform/offline-win312`。其中 `requirements.txt` 由 `uv export --frozen --all-extras --no-dev --no-emit-project --format requirements-txt` 从仓库锁文件导出，`wheelhouse/` 有 **91 个 wheel**。已在独立 Windows x64 / CPython 3.12 CPU 环境使用 `--offline --no-index` 完成安装验证。这个集合包含 dameng、speech、language、vision 和应用依赖；浏览器、前端构建工具、uv/Python 本体、数据库服务、模型权重和原生程序不是这 91 个 wheel 的一部分。

| 能力 | 本次安装的版本 | 用途 |
| --- | --- | --- |
| 达梦驱动与方言 | dmPython 2.6.1、dmSQLAlchemy 2.0.17 | 应用和身份通过专用 schema 连接独立 DM8；服务端介质单独准备 |
| 文档与表格 | pypdf 6.19.0、pypdfium2 5.14.0、python-docx 1.2.0、openpyxl 3.1.5 | 只读页/段/表/单元格定位、PDF 页面栅格化、生成研究简报 |
| 几何与安全图件 | Shapely 2.2.0、pyproj 3.8.0、Pillow 12.3.0、defusedxml 0.7.1 | 投影与几何、像素预览、受限静态导出 |
| 媒体解码与转写 | PyAV 19.0.2、faster-whisper 1.2.1、CTranslate2 4.8.4 | 视频采帧、CPU 离线 ASR；还需本机 FFmpeg/ffprobe 及 Whisper 权重 |
| 机器翻译 | CTranslate2 4.8.4、SentencePiece 0.2.2 | 英中、俄英中阅读辅助；词表、分词器和模型须预配 |
| 视觉比对 | opencv-python-headless 4.14.0.94 | B03 的 ORB 局部像素匹配/几何核查；不能据此宣称通用目标分类 |

在目标 Windows 机预装匹配版本的 Python、uv，复制已核验的 wheelhouse 和锁定清单到私有目录，建立**新的**虚拟环境再安装。示例中的路径均需替换为本机绝对路径：

```powershell
uv venv --offline --python "<已安装的 Python 3.12 可执行文件>" "<新的虚拟环境目录>"
uv pip install --offline --no-index --require-hashes --python "<新环境>/Scripts/python.exe" --find-links "<包目录>/wheelhouse" -r "<包目录>/requirements.txt"
```

部署 API/Worker 时使用含锁定运行依赖的环境；本仓库 `scripts/start.ps1` 可从私有 `.local/platform-python.txt` 读取该环境 Python 的绝对路径。生产包需已有前端静态构建，或另外用锁定的 Node 依赖在准备机构建。DM8 的应用账号、私有连接及迁移步骤见[数据库说明](DAMENG.md)。凭据、原件、权重、安装介质和历史运行库均不进入源包。

原生工具及 CPU 权重通过 `HANGZHI_MEDIA_HOME` 指向私有预配目录；当前适配工作区使用忽略的 `.local/offline-media`，已预配 Tesseract 5.5.3 及英文、俄文等词库。`scripts/enable_case_media.ps1` 由启动脚本载入，也可在专用测试进程中显式载入。Tesseract、FFmpeg、ffprobe 可各自指定绝对程序路径；词库由绝对 `TESSDATA_PREFIX` 和 `HANGZHI_OCR_LANG` 指定，本机为 `eng+rus`。ASR 使用 `HANGZHI_ASR_MODEL_DIR`，翻译使用 `HANGZHI_MT_EN_ZH`、`HANGZHI_MT_RU_EN`。缺少已核验词库或模型时返回不可用/材料缺口，不在任务运行时下载或替换模型；安装介质和权重不进入 Git。

本机已在冻结的工具/模型指纹下做三项实际运行：Tesseract 从合成图片读出数字 2468；英中离线翻译保留合成句中的数值 12；Whisper 为合成语音生成 1 个带时间码的片段。这些检查证明本机链路可用；不能外推为未知语种转写、复杂图像语义、全部译注质量或目标设备吞吐率验收。业务用例、验收范围和交付说明见[用例研判手册](CASEWORK.md)。

### 源包、Linux 容器及未验收项

`scripts/offline_package.py` 生成源码/静态资源哈希与已安装分发的许可元数据。完整再分发需携带相应 LICENSE/NOTICE，核清原生库、数据库介质、词库、tokenizer、编解码器和模型权重的适用条款；Windows 准备目录不是公开分发渠道。

`scripts/Containerfile` 要求显式提供已加载的 Python 3.12 基础镜像摘要，以 `podman build --pull=never` 构建。准备 Linux **对应架构**的已校验 wheelhouse（含所用 extras）、requirements-offline.txt、frontend/dist、backend、skills 和数据库初始化/迁移脚本；容器安装使用 `--no-index --require-hashes`。`scripts/compose.yaml` 使用已加载的应用镜像与独立 DM8 服务，不启动 PostgreSQL 容器；应用默认监听本机回环。

2026-10-17 的历史证据是 WSL/Podman 6.1.4、Linux amd64、Python 3.12、55 个 wheel 的断网镜像验证。它早于当前 DM8/媒体依赖，不能作为本版本镜像运行证据；Windows 的 91 个 wheel 也不能用于 Linux。客户目标环境仍需分别核对授权、硬件/运行库、断网安装及实际业务任务、容量、备份恢复和升级回退。预算和运行数据按私有运维流程迁移，不能以新空账本代替历史。
