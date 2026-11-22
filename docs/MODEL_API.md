# Qwen 模型最小运行说明

模型在 A100 服务器运行，本地只运行原项目。按下面三步配置即可。

## 1. 服务器启动模型

服务器已配置好权重、镜像和鉴权。登录 A100 后执行：

```bash
cd /home/lxy/projects/multi_agents/multi-agent-execution-system
bash scripts/model_api/manage.sh start
```

模型会持续后台运行，加载需要一些时间。查看状态或日志，将末尾 `start` 换成 `status` 或 `logs`；停止用 `stop`，重启用 `restart`。
服务器配置在 `scripts/model_api/.env`；在其他服务器部署时从同目录 `compose.env.example` 复制填写。现有部署无需重填。

## 2. 本地开启 SSH 隧道

在本地电脑另开终端，使用平时登录 A100 的 SSH 地址或 Host 别名，并保持窗口运行：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8001:127.0.0.1:8001 <A100的SSH目标>
```

本地模型地址固定为 `http://127.0.0.1:8001/v1`，模型名为 `hangzhi-qwen38`。

## 3. 本地填写 `.env` 并启动项目

在本地项目 Git 根目录（原 Windows 工作区可能是 `Codes`）复制配置模板：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

已有 `.env` 时直接编辑，保留原数据库等配置。将以下密钥占位符替换为真实值：

```dotenv
HANGZHI_UPSTREAM_MODEL_URL=http://127.0.0.1:8001/v1
HANGZHI_UPSTREAM_MODEL_ID=hangzhi-qwen38
HANGZHI_UPSTREAM_API_KEY=粘贴一枚服务器密钥的完整内容
HANGZHI_UPSTREAM_BILLING=internal_zero
HANGZHI_CONTEXT_TOKENS=32768
HANGZHI_MODEL_SLOTS=2
HANGZHI_OFFLINE=false
```

密钥在服务器以下文件中，任选一枚打开复制；填入的是文件内容，不是路径：

```text
/home/lxy/projects/multi_agents/deployment-state/api-keys/client-01.txt
/home/lxy/projects/multi_agents/deployment-state/api-keys/client-02.txt
/home/lxy/projects/multi_agents/deployment-state/api-keys/client-03.txt
```

保存 `.env` 后，在本地项目目录按原方式启动：

```powershell
./scripts/stop.ps1
./scripts/start.ps1 -SkipBuild
```

以后修改 `.env` 后同样先停止再启动。API 和 Worker 自动读取同一配置，无需手动设置环境变量或运行密钥脚本。沿用现有数据库和依赖环境；原来使用 `-DmUrlFile` 的继续带上该参数。

`.env` 已被 Git 忽略。`internal_zero` 表示自部署模型仅计量 tokens；`HANGZHI_OFFLINE=false` 允许应用调用内网 HTTP，模型服务器本身仍禁止外网。普通任务权限和模型输出质量限制继续适用。
