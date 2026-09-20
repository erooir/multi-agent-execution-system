"""Only paid-model boundary. Durable reservations survive errors and restarts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import MODEL_ID, get_api_key, get_settings
from .storage import DATA_DIR

# Twice the verified 2026-09-20 CNY peak prices. Ignore cache/off-peak discounts.
# These are conservative budget charges, NOT provider invoice amounts.
INPUT_RATE = 4.0
OUTPUT_RATE = 16.0
HARD_CAP = 300.0


class BudgetExceeded(RuntimeError):
    pass


class ModelOutputTruncated(RuntimeError):
    """The provider charged a request but did not finish its generated output."""

    def __init__(self, metadata: dict):
        self.metadata = dict(metadata)
        self.request_id = metadata.get("request_id")
        super().__init__(
            "模型输出达到单次 token 上限而被截断（finish_reason=length），本次内容未作为完整结果保存。"
            "请缩短任务或报告篇幅后重试；本次请求费用已按返回用量记账，缺失用量时仍保留预算预留。"
        )


def _require_complete_output(metadata: dict) -> None:
    if "length" in metadata.get("finish_reasons", []):
        raise ModelOutputTruncated(metadata)


class BudgetLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY,created_at TEXT,purpose TEXT,run_id TEXT,state TEXT,reserved REAL,cost REAL DEFAULT 0,input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0,model TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER DEFAULT 0)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def reserve(self, amount: float, purpose: str, run_id=None, limit=HARD_CAP) -> str:
        if amount <= 0 or amount > HARD_CAP:
            raise BudgetExceeded("单次请求预估费用不合法或超过预算")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE reserved END),0) FROM charges"
            ).fetchone()[0]
            if total + amount > min(float(limit), HARD_CAP):
                db.execute(
                    "INSERT INTO counters(name,value) VALUES('blocked',1) ON CONFLICT(name) DO UPDATE SET value=value+1"
                )
                db.commit()
                raise BudgetExceeded("模型预算不足：已阻止请求，项目累计授权上限为300元")
            request_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO charges(id,created_at,purpose,run_id,state,reserved,model) VALUES(?,?,?,?,?,?,?)",
                (request_id, datetime.now(UTC).isoformat(), purpose, run_id, "reserved", amount, MODEL_ID),
            )
            return request_id

    def settle(self, request_id: str, usage: dict, model=MODEL_ID) -> float:
        prompt = max(0, int(usage.get("prompt_tokens", 0)))
        completion = max(0, int(usage.get("completion_tokens", 0)))
        cost = (prompt * INPUT_RATE + completion * OUTPUT_RATE) / 1_000_000
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM charges WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise RuntimeError("模型费用预留不存在")
            if row["state"] == "settled":
                return row["cost"]
            db.execute(
                "UPDATE charges SET state='settled',cost=?,input_tokens=?,output_tokens=?,model=? WHERE id=?",
                (cost, prompt, completion, model, request_id),
            )
            if cost > row["reserved"]:
                # Fail closed on any broken token-bound assumption.
                db.execute(
                    "INSERT INTO counters(name,value) VALUES('accounting_violation',1) ON CONFLICT(name) DO UPDATE SET value=value+1"
                )
        return cost

    def uncertain(self, request_id: str):
        with self.connect() as db:
            db.execute("UPDATE charges SET state='uncertain' WHERE id=? AND state='reserved'", (request_id,))

    def budget(self, limit=HARD_CAP):
        with self.connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE 0 END),0) spent,COALESCE(SUM(CASE WHEN state!='settled' THEN reserved ELSE 0 END),0) reserved,COUNT(*) count,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(CASE WHEN state='uncertain' THEN 1 ELSE 0 END),0) uncertain FROM charges"
            ).fetchone()
            counters = dict(db.execute("SELECT name,value FROM counters").fetchall())
        limit = min(float(limit), HARD_CAP)
        return {
            "limit_cny": limit,
            "spent_cny": round(row["spent"], 6),
            "reserved_cny": round(row["reserved"], 6),
            "remaining_cny": round(max(0, limit - row["spent"] - row["reserved"]), 6),
            "request_count": row["count"],
            "blocked_count": counters.get("blocked", 0),
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "uncertain_count": row["uncertain"],
            "accounting_violation": bool(counters.get("accounting_violation")),
            "pricing_note": "保守预算占用，按高峰单价2倍计账，非DeepSeek账单实扣；异常请求保留预留。",
            "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
            "pricing_verified_at": "2026-09-20",
            "input_rate_cny_per_million": INPUT_RATE,
            "output_rate_cny_per_million": OUTPUT_RATE,
        }

    def recent(self):
        with self.connect() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM charges ORDER BY created_at DESC LIMIT 100")
            ]


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger: BudgetLedger, purpose: str, run_id=None, inner=None):
        self.ledger, self.purpose, self.run_id = ledger, purpose, run_id
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.result = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.scheme != "https"
            or request.url.host != "api.deepseek.com"
            or request.method != "POST"
            or request.url.path not in ("/chat/completions", "/v1/chat/completions")
        ):
            raise RuntimeError("模型请求目标不在授权范围")
        body = await request.aread()
        payload = json.loads(body)
        if payload.get("model") != MODEL_ID or payload.get("stream"):
            raise RuntimeError("模型或流式请求不符合预算网关约束")
        output_cap = payload.get("max_tokens", payload.get("max_completion_tokens", 0))
        if not isinstance(output_cap, int) or not 1 <= output_cap <= 6000:
            raise RuntimeError("模型请求必须指定不超过6000 tokens的输出上限")
        if self.ledger.budget()["accounting_violation"]:
            raise BudgetExceeded("计量异常，已停止所有模型调用")
        # UTF-8 bytes bound text BPE tokens; media conservatively reserves full context.
        input_cap = 1_000_000 if b'"image_url"' in body else len(body) + 4096
        if input_cap > 1_000_000:
            raise ValueError("输入超出模型上下文限制")
        reserved = (input_cap * INPUT_RATE + output_cap * OUTPUT_RATE) / 1_000_000
        request_id = self.ledger.reserve(
            reserved, self.purpose, self.run_id, get_settings()["budget_limit_cny"]
        )
        try:
            response = await self.inner.handle_async_request(request)
            content = await response.aread()
            if 200 <= response.status_code < 300:
                data = json.loads(content)
                usage = data.get("usage")
                choices = data.get("choices") or []
                self.result = {
                    "request_id": request_id,
                    "model": data.get("model", MODEL_ID),
                    "finish_reasons": [
                        choice.get("finish_reason") for choice in choices if isinstance(choice, dict)
                    ],
                    "max_output_tokens": output_cap,
                }
                if isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage:
                    cost = self.ledger.settle(request_id, usage, data.get("model", MODEL_ID))
                    self.result.update(
                        {
                            "usage": usage,
                            "cost_cny": cost,
                        }
                    )
                else:
                    self.ledger.uncertain(request_id)
            else:
                self.ledger.uncertain(request_id)
            # aread() returns decoded bytes. Do not advertise compression twice.
            headers = httpx.Headers(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(response.status_code, headers=headers, content=content, request=request)
        except BaseException:
            self.ledger.uncertain(request_id)
            raise

    async def aclose(self):
        await self.inner.aclose()


class ModelGateway:
    def __init__(self, ledger=None):
        self.ledger = ledger or BudgetLedger(DATA_DIR / "budget.sqlite3")
        self.semaphore = asyncio.Semaphore(2)

    def budget(self):
        return self.ledger.budget(get_settings()["budget_limit_cny"])

    def config_status(self):
        import importlib.metadata

        return {
            "model": MODEL_ID,
            "key_configured": bool(get_api_key()),
            "agno_version": importlib.metadata.version("agno"),
            "mode": get_settings()["default_mode"],
            "budget_limit_cny": 300,
            "deployment": "localhost",
        }

    async def complete(
        self,
        prompt,
        *,
        system="",
        purpose="general",
        run_id=None,
        max_tokens=2000,
        json_mode=False,
        images=None,
        evidence=None,
    ):
        if evidence and any(item.get("visibility") == "local" for item in evidence):
            raise PermissionError("所选证据包含仅本地资料，已阻止发送至云端模型")
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError("未配置deepseek_api_key，请在Windows用户环境变量中设置后重试")
        if len(str(prompt).encode("utf-8")) + len(system.encode("utf-8")) > 200_000:
            raise ValueError("单次模型输入限制为200KB，请缩小资料范围")
        from agno.agent import Agent
        from agno.media import Image
        from agno.models.deepseek import DeepSeek
        from openai import AsyncOpenAI

        transport = MeteredTransport(self.ledger, purpose, run_id)
        async with (
            self.semaphore,
            httpx.AsyncClient(transport=transport, timeout=90, follow_redirects=False) as http_client,
        ):
            client = AsyncOpenAI(
                api_key=api_key, base_url="https://api.deepseek.com", max_retries=0, http_client=http_client
            )
            request_params = {"response_format": {"type": "json_object"}} if json_mode else None
            model = DeepSeek(
                id=MODEL_ID,
                api_key=api_key,
                async_client=client,
                max_tokens=min(max(1, max_tokens), get_settings()["max_output_tokens"], 6000),
                use_thinking=False,
                retries=0,
                max_retries=0,
                request_params=request_params,
            )
            agent = Agent(
                name=f"workbench-{purpose}",
                model=model,
                system_message=system or "你是严谨的研究助手。仅使用提供的资料，区分事实、推断和信息缺口。",
                telemetry=False,
                markdown=not json_mode,
                retries=0,
                add_history_to_context=False,
            )
            if json_mode:
                prompt = str(prompt) + "\n请仅输出一个合法JSON对象。"
            try:
                output = await agent.arun(
                    str(prompt), images=[Image(url=value) for value in (images or [])] or None
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK errors must not expose credentials or request contents.
                # Some SDK versions reject truncated JSON before returning an Agent output.
                # Preserve the provider's exact failure reason instead of hiding it in a generic SDK error.
                _require_complete_output(transport.result)
                # Never propagate SDK request dumps, input data or credentials.
                raise RuntimeError(
                    f"模型调用未完成（{type(exc).__name__}），请检查连接、余额与模型配置；未确认费用仍计入预留"
                ) from None
            _require_complete_output(transport.result)
            text = (
                output.content
                if isinstance(output.content, str)
                else json.dumps(output.content, ensure_ascii=False, default=str)
            )
            return {
                "text": text,
                "usage": transport.result.get("usage", {}),
                "cost_cny": transport.result.get("cost_cny", 0),
                "model": transport.result.get("model", MODEL_ID),
                "request_id": transport.result.get("request_id"),
                "finish_reason": (transport.result.get("finish_reasons") or [None])[0],
            }


model_gateway = ModelGateway()
