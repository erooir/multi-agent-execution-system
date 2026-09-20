# 知序 Research Agent Workbench

A local Chinese research workbench built on Agno AgentOS. Create research projects, configure agents and skills, edit executable workflows, review evidence, and produce traceable reports.

中文操作与演示步骤见 [使用说明](docs/USER_GUIDE.md)。预置三个合成研究场景、四类智能体、六种技能、六条流程模板和 30 条评测样例。

## Start on Windows

Prerequisites: Python 3.12 (managed by uv), Node.js 22+, uv, Git. No paid AgentOS subscription is required.

```powershell
uv sync --frozen --python 3.12
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Open http://127.0.0.1:8000 . Local demonstration accounts are `admin`, `operator`, and `reviewer`; password `demo12345`. The application is bound to the local machine. These accounts are for local demonstration, not internet deployment. Set `WORKBENCH_DEMO_PASSWORD` to replace the demonstration password.

Store your DeepSeek key in the Windows **user environment variable** `deepseek_api_key`. The backend reads it directly; never place it in source files, browser settings, or Git. The current default model is `deepseek-flash`. Model traffic is HTTPS to the official endpoint.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop.ps1
```

## Two explicit execution modes

- **Live / 真实模型**: Agno agents call DeepSeek through a metered gateway. Each call reserves budget first. Results include actual usage and model identifiers.
- **Rehearsal / 本地演练**: real local document processing and retrieval, deterministic analysis/report formatting, and no paid model calls. It is explicitly labeled and is not offline LLM inference.

## Model spending control

The lifetime project authorization is **CNY 300**. The ledger persists at `.local/budget.sqlite3`; restarting does not reset it. Pending and uncertain requests consume reserved budget. Prices use a conservative two-times peak-rate allowance (4 CNY/million input and 16 CNY/million output), ignoring cache/off-peak discounts. Displayed budget use is a conservative estimate rather than the provider invoice. The user may lower the limit but cannot increase it beyond 300 via the UI.

The cap covers requests made by this project's gateway, not unrelated use of the same account elsewhere. Do not delete or replace the budget database to reset spending. Provider pricing changes require review before further live use. Current pricing reference: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ (checked 2026-09-20).

## Private files and repository hygiene

Original background DOCX/PPTX files, uploaded documents, database files, exports, logs, credentials and model caches stay outside Git. The application only indexes explicit uploads and seeded synthetic demonstration data; it never scans the project root for research material. Samples are marked synthetic and must not be represented as real-world intelligence.

Enable the staged-file guard after cloning:

```powershell
git config core.hooksPath .githooks
```

## Validation

```powershell
uv run pytest
cd frontend
npm ci
npm run build
```

See [architecture](docs/ARCHITECTURE.md), [implementation plan](docs/IMPLEMENTATION_PLAN.md) and [acceptance record](docs/VALIDATION.md) for API contracts, delivery gates and verified behavior. Runtime data is intentionally not versioned. Core software dependencies are pinned by `uv.lock` and `frontend/package-lock.json`.
