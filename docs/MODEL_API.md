# Qwen3.8 文本与视觉 API

此部署只提供自托管模型 API，使用同一份 `Qwen/Qwen3.8-27B` BF16 权重处理文本、图片、JSON 和工具协议。应用、Worker、前端和数据库在原有环境运行。模型服务就绪不等于完整业务流程或 Ascend 910B 验收通过。

## 固定模型与环境

模型基线为官方 Hugging Face revision `37561816e66b5b9153b1b64c10968c18c1e6f4a0`，共 32 个文件、55,586,107,015 字节、18 个权重分片。完整目录须包含 tokenizer、聊天模板、图片/视频处理配置和视觉权重。使用魔搭 Qwen 官方仓库时，逐文件固定其 revision，并与上述 HF revision 的官方大小、LFS SHA-256 或 Git blob 哈希核对；同名不代表版本相同。普通文档可能不同，不能不加区分地覆盖固定版本。

下载器、清单、日志、权重、私有配置和补丁放在仓库之外或 `.local/`。下载进程关闭代理，HTTP 客户端不读取环境代理；模型文件支持 Range 续传，先写临时文件，再核对官方哈希并原子改名。全部完成后生成 `SHA256SUMS`，离线迁移后在模型目录执行 `sha256sum -c SHA256SUMS`。

A100 实测使用 GPU 0、1 两张 80GB 卡，BF16、TP=2、32K 总上下文、最多 2 个活动请求、每次最多 4 张图片、每张最多约 1M 像素。按用户后续要求放宽输出限制：服务端输出边界为总上下文 32768，应用默认最多 16000 tokens，并可配置。输入、视觉 token、工具历史与输出共同占用上下文，不能取消这个物理容量约束。未启用量化、MTP 或文本专用模式。

本轮复用本机已有的魔搭官方 CUDA 12.9 镜像：vLLM 0.19.1、PyTorch 2.10.0+cu129，离线补入 Transformers 5.8.0。基础镜像须固定 registry digest，派生镜像须固定实际 image ID。实际版本清单与镜像记录保存在私有交付目录。不要为推理升级应用的 `uv.lock`。官方参考：[模型](https://huggingface.co/Qwen/Qwen3.8-27B)、[vLLM 配方](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)。版本支持和配方不能代替本机实测。

## 构建与启动

`scripts/model_api/Containerfile` 只接收固定基础镜像、公开 wheel 和启动脚本。准备最小构建目录，放入该 Containerfile、`entrypoint.sh`、`serve_with_keys.py` 和 `wheels/transformers-5.8.0-py3-none-any.whl`；先核对 wheel 哈希，再以 `--network=none --pull=false` 构建。权重、密钥和运行数据不得进入镜像构建上下文。

从 `scripts/model_api/compose.env.example` 创建仓库外的 `compose.env`，填入镜像 ID/digest、两张空闲 GPU 的 UUID、模型路径、私有密钥路径、缓存路径和实际 UID/GID。目录应由该 UID/GID 拥有。配置文件和至少 32 字符的随机密钥文件均使用 `chmod 600`，私有父目录使用 `chmod 700`。不要把密钥写进命令行或 Git。

```bash
export HANGZHI_MODEL_SERVER_ENV=/absolute/private/path/compose.env
bash scripts/model_api/manage.sh start
bash scripts/model_api/manage.sh status
bash scripts/model_api/manage.sh logs
bash scripts/model_api/manage.sh restart
bash scripts/model_api/manage.sh stop
```

`manage.sh start` 要求已存在校验清单并检查选定 GPU 是否空闲。不会停止其他 GPU 进程。Compose 使用 `restart: unless-stopped`，后台运行不依赖 SSH 会话。只有已下载的镜像可用，禁止运行时拉取。

vLLM 在内部网络监听 `0.0.0.0:8001`，该网络无外网路由。Docker 29 的内部网络没有发布宿主端口，因此使用固定目标的 Nginx 转发，宿主仅发布 `127.0.0.1:8001`，只转发原生鉴权覆盖的 `/v1/` 路径。Nginx 不持有模型密钥，不修改模型协议或实现业务调用。模型鉴权、工具 parser、JSON 和用量仍来自原生 vLLM。Nginx 使用只读文件系统、非 root 用户和临时目录，不记录访问正文或鉴权头。

运行期启用 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 和禁遥测配置。密钥只读文件支持单个明文字符串或最多 32 个字符串组成的 JSON 数组。`serve_with_keys.py` 将它们传给原生 vLLM 多密钥鉴权；没有增加业务服务或修改模型协议。密钥不进入 CLI 或容器配置环境，启动 Namespace 的字符串表示经过隐藏，因为当前 vLLM 会直接记录非默认参数。默认关闭 thinking，使用 `qwen3_coder` 工具 parser 和 `qwen3` reasoning parser。镜像必须包含实际运行 UID/GID 的 passwd 条目；否则 Torch 编译器的用户名查询会失败，本机首轮启动曾因此失败，修复后的镜像已实际运行。

## 本地电脑访问

使用实际可连接的服务器 SSH 地址，在本地保持隧道运行：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8001:127.0.0.1:8001 lxy@<服务器SSH地址>
```

本地 base URL 为 `http://127.0.0.1:8001/v1`，模型名为 `hangzhi-qwen38`。通过自己的 SSH/SCP 通道将私有 API key 文件保存到本地受保护位置，不要把值发到聊天、截图或源码。使用同一专用凭据访问 `/v1/models` 和 `/v1/chat/completions`。

独立探测脚本仅依赖 Python 标准库，不导入应用、ORM 或预算账本，也不需要数据库。可直接复制该脚本到本地运行：

```bash
python scripts/probe_model_api.py --key-file /absolute/private/path/api-key \
  --output .local/model-api/probe.json
```

脚本使用本地合成 PNG 的 base64 data URI。检查缺失/错误鉴权、中文内容、模型标识、usage、finish_reason、单图及换色、多图、图片 JSON 的值与类型、严格 JSON schema、真实工具执行与 tool_call_id 回传、工具与 JSON 组合、非法参数/过量图片/过长上下文及连续请求。截断、空输出、推理标签泄露、错误用量或非法协议均计失败。`--smoke` 可用于重启后的快速复测。脚本自身不能证明服务端断网或服务确已重启，必须结合独立容器网络和前后启动记录。

可用 `--long-prompt-file <合成长输入>` 增加近 32K 容量检查。先用同一份本地 tokenizer 核对输入约为 31K tokens；探测会再以服务实际 usage 验证输入处于 30K–32K，并检查最后的指令得到执行。默认探测不需要这个可选文件。

OpenAI SDK 最小调用示例：

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ['HANGZHI_UPSTREAM_MODEL_URL'],
    api_key=os.environ['HANGZHI_UPSTREAM_API_KEY'],
    timeout=180,
    max_retries=0,
)
reply = client.chat.completions.create(
    model='hangzhi-qwen38',
    messages=[{'role': 'user', 'content': '请用中文回答：北京是哪个国家的首都？'}],
    max_tokens=128,
    extra_body={'chat_template_kwargs': {'enable_thinking': False}},
)
print(reply.choices[0].message.content)
```

## 原应用接入与现存限制

原网关已支持 `HANGZHI_UPSTREAM_MODEL_URL`、`HANGZHI_UPSTREAM_MODEL_ID`、`HANGZHI_UPSTREAM_API_KEY` 和 `HANGZHI_UPSTREAM_BILLING=internal_zero`。本轮沿用该入口，新增以下配置：

| 配置项 | 默认值与约束 |
| --- | --- |
| `HANGZHI_UPSTREAM_TIMEOUT_SECONDS` | 180，有限数，1–3600 秒；单次 HTTP 超时，Agent 总执行时限另行约束 |
| `HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS` | 16000，必须为正且小于 `HANGZHI_CONTEXT_TOKENS`；同时受独立内网应用设置与调用者显式输出上限约束 |
| `HANGZHI_UPSTREAM_MAX_TASK_REQUESTS` | 60，0–10000；0 取消每任务模型请求次数限制，取消/截止时间及并发保护继续生效。云模型仍固定最多 20 次 |
| `HANGZHI_UPSTREAM_TEMPERATURE` | 0，有限数，0–2 |
| `HANGZHI_UPSTREAM_TOP_P` | 1，有限数，0 < 值 ≤ 1 |
| `HANGZHI_UPSTREAM_ENABLE_THINKING` | false，仅接受 true/false，经 `chat_template_kwargs` 发送 |

配置示例见 `scripts/model_api/client.env.example`。API 和 Worker 应使用相同配置。设置 `HANGZHI_CONTEXT_TOKENS=32768` 和 `HANGZHI_MODEL_SLOTS=2`，匹配服务端容量。内网请求由服务端实际 tokenizer 判断总上下文，网关仍限制绝对输入体积；云模型仍使用原有保守字节估算和费用预留。显式调用者输出上限（例如领域决策 2500/3000）继续生效，不会为短决策强制生成长输出。

环境文件示例不会自动设置 PowerShell 进程变量。Windows 可在启动原项目的同一个终端先设置：

```powershell
$env:HANGZHI_UPSTREAM_MODEL_URL = 'http://127.0.0.1:8001/v1'
$env:HANGZHI_UPSTREAM_MODEL_ID = 'hangzhi-qwen38'
$env:HANGZHI_UPSTREAM_BILLING = 'internal_zero'
$env:HANGZHI_UPSTREAM_API_KEY = (Get-Content -Raw 'C:/private/hangzhi/api-key').Trim()
$env:HANGZHI_CONTEXT_TOKENS = '32768'
$env:HANGZHI_MODEL_SLOTS = '2'
```

文本、原生 Agno 图片、JSON 和工具循环仍经过原网关与计量传输。内网请求不添加 DeepSeek 专属 thinking 字段，不回退云端凭据，不经过环境代理，不按 DeepSeek 费率收费。原云端计费、历史未决费用、资料权限和取消检查保留。独立探测不读取或初始化财务账本。

内网模式使用独立 `HANGZHI_UPSTREAM_USAGE_PATH`（默认数据目录中的 `intranet-usage.sqlite3`），只计请求、tokens 和资源；不创建、读取或修改云财务账本，不伪造 300 元余额。云模式仍要求唯一历史账本。完整应用需要业务存储，本次用例采用隔离 SQLite 兼容存储，没有安装达梦。

既有 cloud/local 任务标签与公开资料授权策略未全面改造：`HANGZHI_OFFLINE=true` 仍阻止网关模型调用，private/local 资料仍被现有外发检查阻止。**不能为了调用内网模型把私有资料改成 public。** 本次输入自身是公开 OSCE/OHCHR 报告。普通自主任务原图传递及私有资料内网信任策略仍需逐项接入；平台 `image_understand` 执行和独立评审已实际传入原图。

Qwen 模板不接受 developer 角色，内网客户端明确映射为 system。自动工具调用使用 XML 协议，不能同时强制 JSON 输出；网关在自动工具轮移除 `response_format`，最终仍做 Pydantic 校验。领域阶段首轮要求真实主工具，最多一轮辅助核查，然后提交决策。服务端使用 xgrammar 与 `disable_any_whitespace=true`，避免空参数命名工具生成大量空白后形成非法历史；当前 vLLM 会覆盖命名工具的请求级结构化参数，所以该修复必须使用服务端配置。没有把模型口头声称调用工具视为执行。

客户端补丁以本轮拉取的最新 `origin/main` 为基线。旧工作区先保留自己的改动，并核对代码是否已经包含内网适配；不要直接对旧版本强套补丁。推荐在相同基线的新分支先运行 `git apply --check <补丁>`，通过后应用。应用补丁不会安装数据库、移动运行数据、迁移账本或推送远端。

## 验证记录

客户端、独立计量、内网配置、云预算与持久请求数专项共 74 项通过，另 1 项依赖其他存储配置的旧测试未执行。HTTP 替身测试验证协议和保护，不代表模型语义正确；没有运行依赖达梦的全项目测试。

基线真实 API 16 项通过，另有约 31K 输入通过；最初图片 JSON 提示中 image_count 含义不清导致 2 项失败，明确为“上传图片数”后复测通过，原失败保留。服务已在无外网路由容器重启并通过文本/图片/鉴权复测。后续紧凑 JSON 配置复测为 14/16，宽泛 json_object 下两项视觉输出出现错拼/额外字段，严格 schema 项通过，不能声称任意 JSON 输出稳定正确。API 的工具+JSON 组合验收使用 required 选择，不能推广为任意 auto+JSON 组合兼容。命名空参数工具实测返回有效 `{}` 与 `tool_calls`，但 finish_reason 为 stop；调用方应验证实际调用结构，不能只凭这个标记判定工具执行。

`scripts/verify_self_hosted_examples.py` 将原始 B01/B05 PDF 经真实平台受理、冻结资料、Agno 工作流、领域工具和独立评审执行，原图另走平台视觉执行与评审。隔离目录保存每次任务、产物、工具轨迹及零费用 token 计量；不导入测试替身，不向模型提供预设数字答案。B01 已成功完成三阶段并独立核对统计与报告时窗。B05 图表的 13 期、26 个读数正确，但关闭 thinking 的视觉输出算错合计，独立模型评审未发现；开启 thinking 的同图小样本合计正确，不能据此保证所有算术。

最终 B05 完整流程为 partial/quality_not_met：经原始 PDF、受控 OCR、三阶段和一轮修订，13 期数字、工具复算合计 8006/13287、首尾非完整月均与独立参考一致；但模型选择了不适用 PDF 的 table.audit_keys，核验失败，质量门槛如实保留缺口。共 18 次请求、136146 输入/4987 输出 tokens、165.081 秒。原失败和最终部分产物全部保留，不以视觉成功替代完整任务验收。

`scripts/benchmark_model_api.py` 用合成文本、合成图片测量 2 次单文本、2 次单图和 2 并发文本请求；固定输出长度并故意截断，仅做性能测量。报告包括实际 usage、端到端输出 tokens/s、首内容延迟和首内容后近似生成速率。服务端 vLLM 计数器差分及每秒 GPU 显存/利用率/功耗采样另行保存。TP=2 的 token 速率属于两卡组成的整个模型服务，不能当作每卡速率或把两卡简单相加。工作流 output tokens/总耗时包含工具、解析与评审间隙，也不能称为纯解码速率。

真实模型验收结果、重启前后报告、GPU 采样、峰值显存、下载清单与镜像版本保存在私有交付目录；未产生真实结果的项目保持待验收。没有运行 NPU 环境或声明 910B 验收。
