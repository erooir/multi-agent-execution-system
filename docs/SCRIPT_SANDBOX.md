# 任务脚本与通用技能

新版保留原有 18 项工具，增加 12 项可复用工具，文档技能包由 2 个扩展为 12 个。目录数量不等于任务实际调用次数。每次调用仍由 ToolSession 核验版本、授权和资料状态，保存实际工具轨迹。

## 新增工具

| 工具 | 输入与边界 | 实际输出 |
| --- | --- | --- |
| `document.extract` | PDF、Word、XLSX、文本、JSON/CSV；有限记录窗口 | 原页/段落/表行位置、文本/字段、覆盖及截断标记 |
| `data.profile` | 本任务冻结资料 | 格式、字段、记录数/媒体元数据和解析缺口 |
| `table.describe` | 最多 1000 行 JSON/CSV/XLSX | 精确十进制统计、有效/无效/空值数 |
| `table.validate` | 必填字段、数值字段、唯一键 | 逐行检查与原位置 |
| `table.join` | 两个冻结表格、明确键 | 精确关联、两侧位置和未匹配数；拒绝右键重复 |
| `table.chart` | 最多 100 行、类别/数值/单位 | 带原行定位的柱状或折线图数据规范，不擅自插值 |
| `image.ocr` | 可解码图片 | 本地 RapidOCR 文字、置信度和边界框 |
| `pdf.ocr` | 明确 PDF 页或内嵌图片索引 | 页/图像位置及本地 OCR 阅读；需复核数值 |
| `media.probe` | 图片、音频或视频 | 实际解码元数据，不等同语义理解 |
| `audio.transcribe` | 音轨时间窗，最长 120 秒 | 预配本地 Whisper 转写和时间码；不自动下载模型 |
| `video.sample_frames` | 最多 6 个指定时间点 | 实际帧时间与 OCR；明确仅覆盖采样点 |
| `script.execute` | Python 标准库代码及本任务授权源 ID | 不可变代码版本、容器运行记录、日志、带哈希输出 |

12 个文档技能包分别为证据分析、表格分析、文档阅读、文档对照、数据清洗与关联、统计与图表、PDF 图表读取、音频分析、视频资料核查、地理空间分析、结果报告与交付、隔离脚本处理。它们描述工具使用条件、输入口径、交付和失败处理，不直接开放执行脚本。

工具目录响应的 `readiness.tools` 和 `readiness.skills` 是当前依赖检查，不属于不可变能力版本。缺失媒体模型或隔离容器会明确标不可用。动态依赖检查不会改变其他已冻结工具的版本。

这些通用领域工具沿用 FrozenSourceReader 的单份 10 MB 上限；上传入口允许的更大音视频文件不意味着每个工具都能处理整个文件。超限须先提供分片或由受支持的专用处理流程处理，不能静默省略。

## 脚本隔离契约

平台通过统一模型网关生成脚本；`script.execute` 自身不访问模型。只允许当前任务明确授权的源 ID。执行服务将源数据复制到独立临时目录，生成 `/inputs/manifest.json`，只读挂载该目录。容器内 `/inputs/script.py` 是本次代码，所有输出写 `/outputs`。

执行必须使用已安装的 rootless Podman、systemd cgroup 管理器、cgroup v2 和固定 SHA-256 镜像。缺一项即拒绝执行，**没有宿主 Python 回退**。镜像运行 `--pull=never`，无网络、无宿主代理、无模型密钥、无数据库/预算账本、无宿主 socket；根文件系统只读、非 root 容器用户、丢弃所有 capabilities、禁止提权。

配额：1 CPU、256 MiB 内存且无额外 swap、64 进程、单次 1–60 秒；输出 tmpfs 16 MiB、临时日志 tmpfs 24 MiB；接受最多 16 个输出文件、合计 4 MB，stdout/stderr 各保留 12000 字符。只接受 UTF-8 JSON/CSV/TXT/Markdown 或通过实际解码检查的 PNG。拒绝路径穿越、符号链接、硬链接和其他文件类型。

入口先探测实际容器内核的 `memory.max`、`pids.max` 和 `cpu.max`，每次任务容器也在执行用户代码前复核。不能仅依据 CLI 接受配额参数就宣称限额生效。取消、超时或异常会强制移除整个容器。静态代码检查只辅助解释错误，隔离边界是容器和内核限制。

`script_versions` 保存任务范围、代码、SHA-256、目的与源引用；`sandbox_runs` 保存实际执行状态、镜像、隔离/配额检查、日志与输出数据/哈希。退出 0 只是执行完成，结果先标 `unreviewed`，由任务审核核对公式、数量、范围、来源和语言一致性。重新生成脚本产生新版本，不修改历史代码。

## 运维配置

原生 Linux 设置 `HANGZHI_SANDBOX_ENABLED=true`、`HANGZHI_SANDBOX_PODMAN` 和 `HANGZHI_SANDBOX_IMAGE`，以非 root 用户在正常 systemd 用户会话运行。镜像必须预先装载，支持 Python 3.12 标准库，不在任务运行时安装 pip 包。

Windows 本机验证使用已有 Podman machine，`HANGZHI_SANDBOX_MACHINE` 指定名称，`HANGZHI_SANDBOX_PODMAN` 指向便携 Podman，`HANGZHI_SANDBOX_WSL_DISTRO` 标识 WSL 文件映射。通过 machine SSH 的真实用户会话执行 `podman --cgroup-manager=systemd`，所有参数逐项 shell quoting。运行数据位于 E 盘 ignored `.local`，不上传。

不能以 WSL 无登录会话的 cgroupfs 模式替代；该模式可能接受内存/CPU 参数但没有真正建立容器控制组。参考 [Podman cgroup manager 文档](https://docs.podman.io/en/latest/markdown/podman.1.html#cgroup-manager-manager) 和 [Podman machine SSH 文档](https://docs.podman.io/en/latest/markdown/podman-machine-ssh.1.html)。

## 验证

`backend/tests/test_general_tools.py` 覆盖真实 Word/XLSX/图片解码、表格精度、缺值、歧义连接、来源与分页。`backend/tests/test_script_sandbox.py` 默认验证拒绝回退、参数和输出边界；配置真实引擎后设置 `HANGZHI_TEST_SANDBOX=true`，验证网络阻断、只读输入/root、宿主秘密不可见、实际内核配额、符号链接/输出限额拒绝、超时、取消、脚本失败以及不可变运行记录。
