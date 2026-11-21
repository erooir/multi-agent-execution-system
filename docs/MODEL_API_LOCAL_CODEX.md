# 交给本地 Codex：连接已部署的 Qwen

请直接完成以下配置并验证。本地运行原多 agent 项目，模型由 A100 服务器提供；不在本地安装 vLLM 或下载权重。保留现有数据库、账号、资料和历史付费账本。先读本地 `AGENTS.md`，保存已有改动，不强制重置、覆盖或合并其他分支。

## 1. 拉取本次分支

在真正的 Git 根目录执行（原 Windows 工作区可能是 `Codes`）：

```powershell
git fetch origin
git switch --track origin/codex/qwen38-examples-20261121
```

本地已有同名分支时改用 `git switch codex/qwen38-examples-20261121`、`git pull --ff-only`。如有本地修改或分叉，先保留并报告，不自动丢弃。该分支未合并到 main。

## 2. 通过 SSH 获取模型 API 密钥

使用本机**已经能登录这台 A100 的 SSH Host 别名/地址**，沿用其用户名、端口、跳板机配置。可检查本机 SSH config；缺少连接信息才向用户询问，不猜地址。

在 PowerShell 中执行下列命令，将第一行换成实际 SSH 目标（通常为配置好的 Host 别名，或 `lxy@服务器地址`）：

```powershell
$modelSsh = '<已有的A100 SSH目标>'
$secretDir = Join-Path $env:LOCALAPPDATA 'Hangzhi'
$keyFile = Join-Path $secretDir 'qwen38-api-key'
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
icacls.exe $secretDir /inheritance:r /grant:r "${account}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict credential directory access.' }
scp "${modelSsh}:/home/lxy/projects/multi_agents/deployment-state/api-key" $keyFile
if ($LASTEXITCODE -ne 0) { throw 'API key transfer failed.' }
icacls.exe $keyFile /inheritance:r /grant:r "${account}:F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict API key file access.' }
```

密钥是服务器现有文件中的完整字符串。**不要自己生成新密钥，不要填 DeepSeek 密钥，不要把服务器路径当密钥。** 不要在终端输出内容、写进聊天、Git、截图或日志；不要用包含真实密钥的命令行参数。读取时 `.Trim()` 去除换行即可。SSH 登录凭据与模型 API 密钥是两回事。

## 3. 打开并保持 SSH 隧道

另开一个终端，将 SSH 目标换成上一步的实际值，保持此进程运行：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8001:127.0.0.1:8001 <已有的A100-SSH目标>
```

本地 `127.0.0.1:8001` 将转发至服务器模型 API。端口被占用时先识别占用进程，不终止未知服务；若需换本地端口，探测的 `--base-url` 和应用 `HANGZHI_UPSTREAM_MODEL_URL` 要同步修改。

## 4. 配置模型并先做无数据库探测

回到项目根目录的 PowerShell。`$keyFile` 如果不在当前终端，重新设置其文件路径；不要打印其内容。

```powershell
$keyFile = Join-Path $env:LOCALAPPDATA 'Hangzhi/qwen38-api-key'
./scripts/model_api/use-client.ps1 -KeyFile $keyFile
# 沿用本机 Python 3.12；也可用项目现有虚拟环境的 python。
python scripts/probe_model_api.py --key-file $keyFile --smoke --output .local/qwen38-smoke.json
if ($LASTEXITCODE -ne 0) { throw 'Model API smoke probe failed; inspect the report before starting business tasks.' }
```

探测不需要数据库、付费账本或 OpenAI SDK。预期模型列表、错误/缺失鉴权、中文文本、合成图片四项通过。连接失败先查隧道；401 先重新从同一服务器安全同步密钥，不能回退到付费模型。

配置脚本只在**当前 PowerShell 进程**设置环境变量：

| 配置 | 实际值 |
| --- | --- |
| `HANGZHI_UPSTREAM_MODEL_URL` | `http://127.0.0.1:8001/v1` |
| `HANGZHI_UPSTREAM_MODEL_ID` | `hangzhi-qwen38` |
| `HANGZHI_UPSTREAM_API_KEY` | 从本地受保护文件读取的密钥值 |
| `HANGZHI_UPSTREAM_BILLING` | `internal_zero` |
| `HANGZHI_CONTEXT_TOKENS` / `HANGZHI_MODEL_SLOTS` | `32768` / `2` |
| `HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS` / `HANGZHI_UPSTREAM_MAX_TASK_REQUESTS` | `16000` / `60` |
| `HANGZHI_OFFLINE` | `false`，当前应用开关也会拦截内网 HTTP；模型容器仍无外网路由 |

每次新开 PowerShell，重新执行 `use-client.ps1` 即可；不必重新取密钥。不要只写 `.env` 就认为已生效，现有启动入口不会自动加载它。密钥保存在文件，应用变量只在启动进程中加载。

## 5. 重启本地 API 和 Worker

在**已执行配置脚本的同一个 PowerShell**中，先确认本工作区没有正在执行的任务，沿用已有 DM8 连接文件/数据库启动方式：

```powershell
./scripts/stop.ps1
# 若本机 DM8 尚未运行，按已有方式启动；不初始化、迁移或重置已有数据库。
./scripts/start.ps1 -SkipBuild -Offline:$false
```

若以前使用 `-DmUrlFile <私有连接文件>`，此次继续带上；前端尚未构建时，沿用项目现有依赖与构建命令。缺少依赖按 `uv.lock` 安装，不升级锁文件。不要改用 SQLite 来替换本地正式数据库，不重建云付费账本。

确认 API 和 Worker 都已重启并继承同一内网配置；只改终端变量不会改变旧进程。先检查 `/api/v2/health` 就绪，再使用已有登录会话读取 `/api/v2/bootstrap`，确认 `system.provider=intranet`、`system.model=hangzhi-qwen38`、`system.key_configured=true`。用小型公开/合成资料提交一次真实模型任务，核对 Worker 心跳、产物与实际 token 计量。任务创建、登录和资料权限沿用原项目规则，不把私有资料改成 public。现有 local/rehearsal 标签不会自动代表使用 Qwen，应使用原有模型调用流程。

完成后只报告分支、模型、端口、探测结果及任务结果，不回显密钥/数据库凭据。B01 已在服务器完整通过；B05 仍有辅助工具选择缺口，宽泛 JSON 字段亦有已记录的不稳定，不能承诺任意用例全通过。更多说明见 [MODEL_API.md](MODEL_API.md)。
