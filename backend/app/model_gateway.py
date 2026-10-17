"""Only paid-model boundary. Durable reservations survive errors and restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from urllib.request import getproxies, proxy_bypass

import httpx

from .config import (
    BUDGET_LEDGER_PATH,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    PRICED_MODEL_IDS,
    get_api_key,
    get_settings,
)

# Twice the verified 2026-10-17 CNY peak prices. Ignore cache/off-peak discounts.
# These are conservative budget charges, NOT provider invoice amounts.
INPUT_RATE = 4.0
OUTPUT_RATE = 16.0
HARD_CAP = 300.0


class SDKMetadataOnly(logging.Filter):
    """Agno validation messages can include raw source text and tool arguments."""

    def filter(self, record):
        record.msg = f"agno_sdk_{record.levelname.lower()} (详情请查看平台任务事件)"
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def _protect_sdk_logs():
    from agno.utils.log import agent_logger, team_logger, workflow_logger

    for logger in (agent_logger, team_logger, workflow_logger):
        if not any(isinstance(item, SDKMetadataOnly) for item in logger.filters):
            logger.addFilter(SDKMetadataOnly())


_protect_sdk_logs()


class BudgetExceeded(RuntimeError):
    pass


class ModelRequestCancelled(RuntimeError):
    pass


class InvalidModelUsage(RuntimeError):
    pass


class ModelCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def model_proxy():
    """Custom transports must explicitly honor the configured system proxy."""
    configured = os.environ.get("HANGZHI_MODEL_PROXY")
    if configured is not None:
        return configured or None
    return None if proxy_bypass("api.deepseek.com") else getproxies().get("https")


def _validated_run(output, transport):
    if transport.policy_failure is not None:
        raise transport.policy_failure
    _require_complete_output(transport.result)
    if "tool_calls" in transport.result.get("finish_reasons", []):
        raise ModelCallError("tool_call_limit", "工具调用已达到本轮上限，未生成完整产物")
    status = getattr(getattr(output, "status", None), "value", getattr(output, "status", None))
    if status in {"ERROR", "CANCELLED", "PAUSED"} or not transport.result.get("request_id"):
        raise ModelCallError(
            "model_call_failed", "模型未完成有效响应，请检查连接与模型配置；未确认费用保留预留"
        )


@dataclass(frozen=True)
class RequestScope:
    task_id: str
    max_cost_cny: float
    allow_external: bool
    cancelled: Callable[[], bool] | None = None
    before_dispatch: Callable[[], None] | None = None


_request_scope: ContextVar[RequestScope | None] = ContextVar("model_request_scope", default=None)


@contextmanager
def request_scope(
    task_id: str,
    max_cost_cny: float,
    allow_external: bool,
    cancelled: Callable[[], bool] | None = None,
    before_dispatch: Callable[[], None] | None = None,
) -> Iterator[RequestScope]:
    """Freeze task authorization around complete(); inherited by async child tasks.

    The callback must be synchronous and can also raise a stale-lease exception.
    Cancellation before dispatch spends nothing; in-flight cancellation retains reservation.
    """
    if not task_id or not math.isfinite(float(max_cost_cny)) or not 0 < max_cost_cny <= HARD_CAP:
        raise ValueError("任务模型预算必须大于0且不超过项目上限")
    parent = _request_scope.get()
    if parent:
        if parent.task_id != task_id:
            raise PermissionError("不能在运行中的模型权限范围切换任务")
        max_cost_cny = min(max_cost_cny, parent.max_cost_cny)
        allow_external = bool(allow_external and parent.allow_external)
        current_cancelled = cancelled

        def cancelled() -> bool:
            return bool(
                (parent.cancelled and parent.cancelled()) or (current_cancelled and current_cancelled())
            )

    scope = RequestScope(
        str(task_id),
        float(max_cost_cny),
        bool(allow_external),
        cancelled,
        before_dispatch or (parent.before_dispatch if parent else None),
    )
    token = _request_scope.set(scope)
    try:
        yield scope
    finally:
        _request_scope.reset(token)


def _check_cancelled(scope: RequestScope | None) -> None:
    if scope and scope.cancelled and scope.cancelled():
        raise ModelRequestCancelled("任务已取消或租约失效，已停止模型请求")


def _authorize_egress(*, run_id=None, evidence=None, context=None) -> str | None:
    scope = _request_scope.get()
    _check_cancelled(scope)
    if scope and not scope.allow_external:
        raise PermissionError("任务策略禁止数据发送至外部模型")
    if scope and run_id is not None and run_id != scope.task_id:
        raise PermissionError("模型请求任务标识与预算范围不一致")
    restricted = {"local", "private", "restricted", "confidential"}
    context_values = (
        [context.get(key) for key in ("data_visibility", "visibility", "classification")]
        if isinstance(context, dict)
        else [getattr(context, "data_visibility", None)]
    )
    if any(value in restricted for value in context_values):
        raise PermissionError("所选证据包含仅本地资料，已阻止发送至云端模型")
    for item in evidence or []:
        if (
            not isinstance(item, dict)
            or any(item.get(key) in restricted for key in ("visibility", "data_visibility", "classification"))
            or item.get("allow_external") is False
        ):
            raise PermissionError("所选证据不允许发送至云端模型")
    return scope.task_id if scope else run_id


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
    def __init__(self, path: str | Path, *, require_existing: bool = False):
        self.path = Path(path)
        self.require_existing = require_existing
        if require_existing and not self.path.is_file():
            raise RuntimeError("ledger_missing: 历史预算账本不存在；请恢复账本，不能新建或重置项目额度")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if (
                require_existing
                and not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='charges'"
                ).fetchone()
            ):
                raise RuntimeError("ledger_invalid: 缺少历史费用表，不能初始化新的项目预算")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY,created_at TEXT,purpose TEXT,run_id TEXT,state TEXT,reserved REAL,cost REAL DEFAULT 0,input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0,model TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER DEFAULT 0)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS task_limits(run_id TEXT PRIMARY KEY,limit_cny REAL NOT NULL)"
            )

    def connect(self):
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw" if self.require_existing else self.path,
            timeout=30,
            uri=self.require_existing,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def reserve(self, amount: float, purpose: str, run_id=None, limit=HARD_CAP, *, task_limit=None) -> str:
        if not math.isfinite(float(amount)) or amount <= 0 or amount > HARD_CAP:
            raise BudgetExceeded("单次请求预估费用不合法或超过预算")
        if not math.isfinite(float(limit)) or not 0 < limit <= HARD_CAP:
            raise BudgetExceeded("项目预算配置无效")
        # Never round a worst-case reservation down. The historical schema remains intact.
        amount = float(Decimal(str(amount)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
        if task_limit is not None and (
            not run_id or not math.isfinite(float(task_limit)) or not 0 < task_limit <= HARD_CAP
        ):
            raise BudgetExceeded("任务预算配置无效")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM counters WHERE name='accounting_violation'").fetchone():
                raise BudgetExceeded("计量异常，已停止所有模型调用")
            malformed = db.execute(
                "SELECT COUNT(*) FROM charges WHERE reserved IS NULL OR reserved<=0 OR cost IS NULL "
                "OR cost<0 OR state IS NULL OR state NOT IN ('settled','reserved','uncertain')"
            ).fetchone()[0]
            if malformed:
                raise BudgetExceeded("历史账本存在无效记录，已停止模型调用")
            total = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE reserved END),0) FROM charges"
            ).fetchone()[0]
            prior_limit = db.execute("SELECT limit_cny FROM task_limits WHERE run_id=?", (run_id,)).fetchone()
            effective_task_limit = prior_limit[0] if prior_limit else task_limit
            if task_limit is not None:
                effective_task_limit = min(effective_task_limit, task_limit)
                db.execute(
                    "INSERT INTO task_limits(run_id,limit_cny) VALUES(?,?) "
                    "ON CONFLICT(run_id) DO UPDATE SET limit_cny=MIN(limit_cny,excluded.limit_cny)",
                    (run_id, effective_task_limit),
                )
            task_total = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE reserved END),0) "
                "FROM charges WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            if total + amount > min(float(limit), HARD_CAP) or (
                effective_task_limit is not None and task_total + amount > effective_task_limit
            ):
                db.execute(
                    "INSERT INTO counters(name,value) VALUES('blocked',1) ON CONFLICT(name) DO UPDATE SET value=value+1"
                )
                db.commit()
                raise BudgetExceeded("模型预算不足：已阻止请求，受任务额度及项目累计300元上限约束")
            request_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO charges(id,created_at,purpose,run_id,state,reserved,model) VALUES(?,?,?,?,?,?,?)",
                (request_id, datetime.now(UTC).isoformat(), purpose, run_id, "reserved", amount, MODEL_ID),
            )
            return request_id

    def settle(self, request_id: str, usage: dict, model=MODEL_ID) -> float:
        if str(model).lower() not in PRICED_MODEL_IDS | {"deepseek-v4.1-flash"}:
            self.uncertain(request_id)
            with self.connect() as db:
                db.execute(
                    "INSERT INTO counters(name,value) VALUES('accounting_violation',1) "
                    "ON CONFLICT(name) DO UPDATE SET value=value+1"
                )
            raise InvalidModelUsage("服务端返回未核价模型；保留预留并停止后续请求")
        # Missing, negative, boolean, fractional and string values are not reliable billing evidence.
        values = (
            [usage.get(key) for key in ("prompt_tokens", "completion_tokens")]
            if isinstance(usage, dict)
            else []
        )
        if len(values) != 2 or any(type(value) is not int or value < 0 for value in values):
            self.uncertain(request_id)
            raise InvalidModelUsage("模型用量无效；保留全部费用预留")
        prompt, completion = values
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

    def usage(self, run_id: str | None = None, purpose: str | None = None, *, request_ids=None) -> dict:
        """Aggregate every request in a task/purpose, including uncertain reservations."""
        conditions, parameters = [], []
        if run_id is not None:
            conditions.append("run_id=?")
            parameters.append(run_id)
        if purpose is not None:
            conditions.append("purpose=?")
            parameters.append(purpose)
        if request_ids is not None:
            conditions.append("id IN (" + ",".join("?" for _ in request_ids) + ")")
            parameters.extend(request_ids)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) request_count,"
                "COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE 0 END),0) cost_cny,"
                "COALESCE(SUM(CASE WHEN state!='settled' THEN reserved ELSE 0 END),0) reserved_cny,"
                "COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,"
                "COALESCE(SUM(CASE WHEN state='uncertain' THEN 1 ELSE 0 END),0) uncertain_count "
                "FROM charges" + where,
                parameters,
            ).fetchone()
        result = dict(row)
        result.update(
            cost_cny=round(result["cost_cny"], 6),
            reserved_cny=round(result["reserved_cny"], 6),
            total_cny=round(result["cost_cny"] + result["reserved_cny"], 6),
            accounting_complete=result["reserved_cny"] == 0,
        )
        return result

    def budget(self, limit=HARD_CAP):
        if not math.isfinite(float(limit)) or not 0 < limit <= HARD_CAP:
            raise BudgetExceeded("项目预算配置无效")
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
            "pricing_verified_at": "2026-10-17",
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
        self.inner = inner or httpx.AsyncHTTPTransport(retries=2, proxy=model_proxy())
        self.result = {}
        self.request_ids: list[str] = []
        # SDKs wrap transport failures; retain trusted policy exceptions without request dumps.
        self.policy_failure: BaseException | None = None

    def _check_scope(self, scope):
        try:
            _check_cancelled(scope)
        except BaseException as exc:
            self.policy_failure = exc
            raise

    async def _send(self, request: httpx.Request, scope: RequestScope | None):
        async def send_and_read():
            response = await self.inner.handle_async_request(request)
            try:
                return response, await response.aread()
            except BaseException:
                await response.aclose()
                raise

        pending = asyncio.create_task(send_and_read())
        try:
            while scope and scope.cancelled and not pending.done():
                await asyncio.wait({pending}, timeout=0.1)
                self._check_scope(scope)
            return await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    def summary(self) -> dict:
        aggregate = self.ledger.usage(request_ids=self.request_ids)
        return {
            "usage": {
                "prompt_tokens": aggregate["input_tokens"],
                "completion_tokens": aggregate["output_tokens"],
            },
            "cost_cny": aggregate["cost_cny"],
            "reserved_cny": aggregate["reserved_cny"],
            "accounting_complete": aggregate["accounting_complete"],
            "request_ids": list(self.request_ids),
            "request_count": aggregate["request_count"],
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._handle_async_request(request)
        except (BudgetExceeded, ModelRequestCancelled, PermissionError) as exc:
            self.policy_failure = exc
            raise
        except httpx.TimeoutException:
            self.policy_failure = ModelCallError(
                "model_timeout", "模型请求超时；请检查网络，未确认费用仍保留预留"
            )
            raise
        except httpx.TransportError:
            self.policy_failure = ModelCallError(
                "model_connection_failed", "无法连接模型服务；请检查网络和代理配置"
            )
            raise

    async def _handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            run_id = _authorize_egress(run_id=self.run_id)
        except BaseException as exc:
            self.policy_failure = exc
            raise
        scope = _request_scope.get()
        if (
            request.url.scheme != "https"
            or request.url.host != "api.deepseek.com"
            or request.url.port not in (None, 443)
            or request.url.userinfo
            or request.url.query
            or request.method != "POST"
            or request.url.path not in ("/chat/completions", "/v1/chat/completions")
        ):
            raise RuntimeError("模型请求目标不在授权范围")
        body = await request.aread()
        payload = json.loads(body)
        if MODEL_ID not in PRICED_MODEL_IDS or payload.get("model") != MODEL_ID or payload.get("stream"):
            raise RuntimeError("模型或流式请求不符合预算网关约束")
        if payload.get("n", 1) != 1 or payload.get("best_of", 1) != 1:
            raise RuntimeError("模型网关只允许每次一个输出，避免未计量候选")
        if any(tool.get("type", "function") != "function" for tool in payload.get("tools", [])):
            raise RuntimeError("模型网关不允许额外计费的内置服务工具")
        output_cap = payload.get("max_tokens", payload.get("max_completion_tokens", 0))
        if type(output_cap) is not int or not 1 <= output_cap <= MAX_OUTPUT_TOKENS:
            raise RuntimeError(f"模型请求必须指定不超过{MAX_OUTPUT_TOKENS} tokens的输出上限")
        if "max_completion_tokens" in payload and payload["max_completion_tokens"] != output_cap:
            raise RuntimeError("模型请求输出上限字段不一致")
        if self.ledger.budget()["accounting_violation"]:
            raise BudgetExceeded("计量异常，已停止所有模型调用")
        # UTF-8 bytes bound text BPE tokens; media conservatively reserves full context.
        input_cap = 1_000_000 if b'"image_url"' in body else len(body) + 4096
        if input_cap > 1_000_000:
            raise ValueError("输入超出模型上下文限制")
        reserved = (input_cap * INPUT_RATE + output_cap * OUTPUT_RATE) / 1_000_000
        self._check_scope(scope)
        if scope and scope.before_dispatch:
            try:
                scope.before_dispatch()
            except BaseException as exc:
                self.policy_failure = exc
                raise
        request_id = self.ledger.reserve(
            reserved,
            self.purpose,
            run_id,
            get_settings()["budget_limit_cny"],
            task_limit=scope.max_cost_cny if scope else None,
        )
        self.request_ids.append(request_id)
        try:
            response, content = await self._send(request, scope)
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
                    try:
                        cost = self.ledger.settle(request_id, usage, data.get("model", MODEL_ID))
                    except InvalidModelUsage:
                        # A useful body may still be returned; accounting remains visibly uncertain.
                        self.result["accounting_complete"] = False
                        cost = None
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
                code, message = {
                    401: ("model_auth_failed", "模型密钥无效，请检查密钥配置"),
                    403: ("model_auth_failed", "模型账户未获授权"),
                    402: ("model_balance_insufficient", "模型服务账户余额不足"),
                    429: ("model_rate_limited", "模型服务限流，请稍后新建任务重试"),
                }.get(response.status_code, ("model_service_failed", "模型服务返回失败响应，请稍后重试"))
                self.policy_failure = ModelCallError(code, message)
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
        self._ledger = ledger
        self.semaphore = asyncio.Semaphore(2)

    async def connectivity(self):
        checked_at = datetime.now(UTC).isoformat()
        result = {
            "key_configured": bool(get_api_key()),
            "reachable": None,
            "checked_at": checked_at,
            "key_verified": False,
            "verification": "not_verified",
        }
        result["last_successful_call_at"] = self.config_status().get("last_successful_call_at")
        if os.environ.get("HANGZHI_OFFLINE", "false").lower() == "true":
            return {**result, "reason_code": "offline_policy"}
        try:
            async with httpx.AsyncClient(
                proxy=model_proxy(), timeout=8, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.head("https://api.deepseek.com/models")
            result["reachable"] = response.status_code < 500
        except httpx.HTTPError:
            result.update(reachable=False, reason_code="model_connection_failed")
        return result

    @property
    def ledger(self) -> BudgetLedger:
        if self._ledger is None:
            self._ledger = BudgetLedger(BUDGET_LEDGER_PATH, require_existing=True)
        return self._ledger

    def usage(self, run_id=None, purpose=None):
        return self.ledger.usage(run_id=run_id, purpose=purpose)

    def budget(self):
        return self.ledger.budget(get_settings()["budget_limit_cny"])

    def config_status(self):
        import importlib.metadata

        try:
            last_success = next(
                (item["created_at"] for item in self.ledger.recent() if item["state"] == "settled"), None
            )
        except RuntimeError:
            last_success = None
        return {
            "model": MODEL_ID,
            "key_configured": bool(get_api_key()),
            "last_successful_call_at": last_success,
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
        max_tokens=MAX_OUTPUT_TOKENS,
        json_mode=False,
        images=None,
        evidence=None,
        output_schema=None,
    ):
        run_id = _authorize_egress(run_id=run_id, evidence=evidence)
        api_key = get_api_key()
        if not api_key:
            raise ModelCallError(
                "model_not_configured", "未配置模型密钥，请在Windows用户环境变量中设置后重试"
            )
        if len(str(prompt).encode("utf-8")) + len(system.encode("utf-8")) > 200_000:
            raise ModelCallError(
                "model_input_too_large", "模型评审或执行上下文超过200KB，请缩小资料或交付范围"
            )
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
                max_tokens=min(max(1, max_tokens), get_settings()["max_output_tokens"], MAX_OUTPUT_TOKENS),
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
                output_schema=output_schema,
                use_json_mode=bool(output_schema),
            )
            if json_mode:
                prompt = str(prompt) + "\n请仅输出一个合法JSON对象。"
            try:
                output = await agent.arun(
                    str(prompt), images=[Image(url=value) for value in (images or [])] or None
                )
            except (BudgetExceeded, ModelRequestCancelled, PermissionError):
                raise
            except Exception as exc:  # noqa: BLE001 - SDK errors must not expose credentials or request contents.
                if transport.policy_failure is not None:
                    raise transport.policy_failure from None
                # Some SDK versions reject truncated JSON before returning an Agent output.
                # Preserve the provider's exact failure reason instead of hiding it in a generic SDK error.
                _require_complete_output(transport.result)
                # Never propagate SDK request dumps, input data or credentials.
                raise RuntimeError(
                    f"模型调用未完成（{type(exc).__name__}），请检查连接、余额与模型配置；未确认费用仍计入预留"
                ) from None
            _validated_run(output, transport)
            content = output.content.model_dump() if hasattr(output.content, "model_dump") else output.content
            text = (
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            )
            return {
                "text": text,
                **transport.summary(),
                "model": transport.result.get("model", MODEL_ID),
                "request_id": transport.result.get("request_id"),
                "finish_reason": (transport.result.get("finish_reasons") or [None])[0],
            }

    @staticmethod
    def _bind_tool(name: str, binding, tool_calls: list):
        """把受控 callable 包成 Agno 工具并记录 Skill/Tool 分层调用。"""
        import functools
        import inspect

        @functools.wraps(binding)
        async def wrapped(*args, **kwargs):
            _authorize_egress()
            result = binding(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict):
                _authorize_egress(evidence=result.get("evidence"), context=result)
            trace_calls = result.get("trace", {}).get("tool_calls", []) if isinstance(result, dict) else []
            skill_id = getattr(binding, "__skill_id__", None)
            tool_calls.append(
                {
                    "kind": "skill" if skill_id else "tool",
                    "skill_id": skill_id,
                    "tool_id": trace_calls[0].get("tool_id", name) if trace_calls else name,
                    "status": result.get("status") if isinstance(result, dict) else "completed",
                    "arguments": sorted(kwargs),
                    "duration_ms": trace_calls[0].get("duration_ms", 0) if trace_calls else 0,
                }
            )
            return result

        wrapped.__name__ = name.replace(".", "_")
        return wrapped

    async def run_agent(
        self,
        agent_spec: dict,
        messages,
        tool_bindings: dict,
        context=None,
        max_rounds: int = 4,
        timeout_seconds: float = 180,
        output_schema=None,
    ):
        """带真实 tools= 的受控模型执行；所有请求仍经由计量传输与预算账本。"""
        run_id = context.get("run_id") if isinstance(context, dict) else getattr(context, "run_id", None)
        run_id = _authorize_egress(run_id=run_id, context=context)
        api_key = get_api_key()
        if not api_key:
            raise ModelCallError(
                "model_not_configured", "未配置模型密钥，请在Windows用户环境变量中设置后重试"
            )
        from agno.agent import Agent
        from agno.models.deepseek import DeepSeek
        from openai import AsyncOpenAI

        transport = MeteredTransport(self.ledger, "agent_run", run_id)
        tool_calls: list[dict] = []
        tools = [
            self._bind_tool(name, binding, tool_calls) for name, binding in (tool_bindings or {}).items()
        ]
        async with (
            self.semaphore,
            httpx.AsyncClient(transport=transport, timeout=90, follow_redirects=False) as http_client,
        ):
            client = AsyncOpenAI(
                api_key=api_key, base_url="https://api.deepseek.com", max_retries=0, http_client=http_client
            )
            model = DeepSeek(
                id=MODEL_ID,
                api_key=api_key,
                async_client=client,
                max_tokens=min(get_settings()["max_output_tokens"], MAX_OUTPUT_TOKENS),
                use_thinking=False,
                retries=0,
                max_retries=0,
                request_params={"response_format": {"type": "json_object"}} if output_schema else None,
            )
            agent = Agent(
                name=f"workbench-agent-{agent_spec.get('name', 'run')}",
                model=model,
                system_message=str(agent_spec.get("instructions") or "你是严谨的研究助手。"),
                tools=tools or None,
                tool_call_limit=max(1, int(max_rounds)),
                telemetry=False,
                markdown=False,
                retries=0,
                add_history_to_context=False,
                output_schema=output_schema,
                use_json_mode=bool(output_schema),
            )
            try:
                output = await asyncio.wait_for(
                    agent.arun(str(messages)), timeout=max(1.0, float(timeout_seconds))
                )
            except (BudgetExceeded, ModelRequestCancelled, PermissionError):
                raise
            except TimeoutError:
                raise ModelCallError(
                    "model_timeout", "工具型智能体超过本轮执行时限；未确认费用继续保留预留"
                ) from None
            except Exception as exc:  # noqa: BLE001 - SDK errors must not expose credentials or request contents.
                if transport.policy_failure is not None:
                    raise transport.policy_failure from None
                _require_complete_output(transport.result)
                raise RuntimeError(
                    f"模型调用未完成（{type(exc).__name__}），请检查连接、余额与模型配置；未确认费用仍计入预留"
                ) from None
            _validated_run(output, transport)
            content = output.content.model_dump() if hasattr(output.content, "model_dump") else output.content
            text = (
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            )
            return {
                "text": text,
                **transport.summary(),
                "model": transport.result.get("model", MODEL_ID),
                "request_id": transport.result.get("request_id"),
                "finish_reason": (transport.result.get("finish_reasons") or [None])[0],
                "tool_calls": tool_calls,
            }


model_gateway = ModelGateway()
