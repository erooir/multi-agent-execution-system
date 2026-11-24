"""Only paid-model boundary. Durable reservations survive errors and restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
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

from . import resource_limits
from .config import (
    BUDGET_LEDGER_PATH,
    DATA_DIR,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    PRICED_MODEL_IDS,
    get_api_key,
    get_settings,
    intranet_provider,
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
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider_status: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float = 0,
    ):
        self.code = code
        self.provider_status = provider_status
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


def _provider_failure(status: int, headers: httpx.Headers, content: bytes) -> ModelCallError:
    try:
        payload = json.loads(content)
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    provider_code = error.get("code") or error.get("type")
    provider_code = str(provider_code)[:100] if provider_code else None
    transient = status == 429 or status >= 500
    code, message = {
        401: ("model_auth_failed", "模型密钥无效，请检查密钥配置"),
        403: ("model_auth_failed", "模型账户未获授权"),
        402: ("model_balance_insufficient", "模型服务账户余额不足"),
        429: ("model_rate_limited", "模型服务限流，将在任务修订额度内有限重试"),
    }.get(
        status,
        ("model_service_failed", "模型服务暂时失败，将在任务修订额度内有限重试")
        if transient
        else ("model_request_invalid", "模型请求未被服务接受，需修正请求或模型配置"),
    )
    if provider_code:
        message += f"；服务错误码：{provider_code}"
    try:
        retry_after = min(30.0, max(0.0, float(headers.get("retry-after", "0"))))
    except ValueError:
        retry_after = 0
    return ModelCallError(
        code,
        message,
        provider_status=status,
        provider_code=provider_code,
        retry_after_seconds=retry_after if transient else 0,
    )


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
    if os.environ.get("HANGZHI_OFFLINE", "").lower() == "true":
        raise ModelCallError("offline_policy", "离线部署禁止付费模型请求")
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

            db.execute(
                "CREATE TABLE IF NOT EXISTS resource_requests("
                "charge_id TEXT PRIMARY KEY, input_upper INTEGER NOT NULL, "
                "output_limit INTEGER NOT NULL, context_limit INTEGER NOT NULL, duration_ms INTEGER)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS charges_run_id ON charges(run_id)")

            db.execute(
                "CREATE TABLE IF NOT EXISTS intranet_requests("
                "id TEXT PRIMARY KEY,created_at TEXT NOT NULL,purpose TEXT NOT NULL,run_id TEXT,"
                "state TEXT NOT NULL,model TEXT NOT NULL,input_tokens INTEGER,output_tokens INTEGER)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS intranet_run_id ON intranet_requests(run_id)")
            db.execute(
                "CREATE VIEW IF NOT EXISTS model_request_usage AS "
                "SELECT id,created_at,purpose,run_id,state,model,input_tokens,output_tokens,cost,reserved,"
                "'deepseek' AS provider FROM charges UNION ALL "
                "SELECT id,created_at,purpose,run_id,state,model,input_tokens,output_tokens,0,0,"
                "'intranet' AS provider FROM intranet_requests"
            )

    def begin_internal(self, model, purpose, run_id, bounds):
        request_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO intranet_requests(id,created_at,purpose,run_id,state,model) VALUES(?,?,?,?,?,?)",
                (request_id, datetime.now(UTC).isoformat(), purpose, run_id, "reserved", model),
            )
            db.execute(
                "INSERT INTO resource_requests(charge_id,input_upper,output_limit,context_limit) VALUES(?,?,?,?)",
                (request_id, *bounds),
            )
        return request_id

    def internal_settle(self, request_id, model, usage):
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in ("prompt_tokens", "completion_tokens")
        ):
            raise ModelCallError("model_usage_missing", "内网模型未返回有效 Token 用量；本轮不计为已确认")
        with self.connect() as db:
            row = db.execute(
                "UPDATE intranet_requests SET state='settled',input_tokens=?,output_tokens=? WHERE id=? AND model=? AND state='reserved'",
                (usage["prompt_tokens"], usage["completion_tokens"], request_id, model),
            )
            if row.rowcount != 1:
                raise ModelCallError("model_identity_invalid", "内网模型响应标识与部署值不一致")

    def connect(self):
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw" if self.require_existing else self.path,
            timeout=30,
            uri=self.require_existing,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def reserve(
        self,
        amount: float,
        purpose: str,
        run_id=None,
        limit=HARD_CAP,
        *,
        task_limit=None,
        resource_bounds=None,
    ) -> str:
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
            if resource_bounds is not None:
                db.execute(
                    "INSERT INTO resource_requests(charge_id,input_upper,output_limit,context_limit) "
                    "VALUES(?,?,?,?)",
                    (request_id, *resource_bounds),
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
                "COALESCE(SUM(CASE WHEN state='uncertain' THEN 1 ELSE 0 END),0) uncertain_count, "
                "COALESCE(SUM(CASE WHEN state!='settled' THEN 1 ELSE 0 END),0) pending_count "
                "FROM model_request_usage" + where,
                parameters,
            ).fetchone()
        result = dict(row)
        result.update(
            cost_cny=round(result["cost_cny"], 6),
            reserved_cny=round(result["reserved_cny"], 6),
            total_cny=round(result["cost_cny"] + result["reserved_cny"], 6),
            accounting_complete=not result.pop("pending_count"),
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
            "project_cap_cny": HARD_CAP,
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


def _internal_usage_path() -> Path:
    return Path(os.environ.get("HANGZHI_UPSTREAM_USAGE_PATH", str(DATA_DIR / "intranet-usage.sqlite3")))


class InternalUsageLedger(BudgetLedger):
    """Token/request observations only; never creates or opens a paid budget ledger."""

    def __init__(self, path):
        self.path = Path(path)
        self.require_existing = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS intranet_requests("
                "id TEXT PRIMARY KEY,created_at TEXT NOT NULL,purpose TEXT NOT NULL,run_id TEXT,"
                "state TEXT NOT NULL,model TEXT NOT NULL,input_tokens INTEGER,output_tokens INTEGER)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS intranet_run_id ON intranet_requests(run_id)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS resource_requests("
                "charge_id TEXT PRIMARY KEY,input_upper INTEGER NOT NULL,output_limit INTEGER NOT NULL,"
                "context_limit INTEGER NOT NULL,duration_ms INTEGER)"
            )
            db.execute(
                "CREATE VIEW IF NOT EXISTS model_request_usage AS "
                "SELECT id,created_at,purpose,run_id,state,model,input_tokens,output_tokens,"
                "0 AS cost,0 AS reserved,'intranet' AS provider FROM intranet_requests"
            )

    def uncertain(self, request_id):
        with self.connect() as db:
            db.execute(
                "UPDATE intranet_requests SET state='uncertain' WHERE id=? AND state='reserved'",
                (request_id,),
            )

    def reserve(self, *args, **kwargs):
        raise RuntimeError("internal_meter_only: 自部署计量库不能预留付费调用")

    def settle(self, *args, **kwargs):
        raise RuntimeError("internal_meter_only: 自部署计量库不能结算付费调用")

    def budget(self, limit=None):
        return {
            "provider": "intranet",
            "financial_ledger_available": False,
            "limit_cny": 0,
            "remaining_cny": 0,
            "spent_cny": 0,
            "reserved_cny": 0,
            "request_count": 0,
            "accounting_violation": False,
            "pricing_note": "自部署模型只计量请求及Token；未打开或创建云付费账本。",
        }


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger: BudgetLedger, purpose: str, run_id=None, inner=None, *, upstream=None):
        self.ledger, self.purpose, self.run_id = ledger, purpose, run_id
        self.upstream = upstream
        self.required_first_tool: str | None = None
        self.required_tool_completed: Callable[[], bool] | None = None
        self.auto_tool_rounds: int | None = None
        self.model_id = upstream.model if upstream else MODEL_ID
        self.inner = inner or httpx.AsyncHTTPTransport(
            retries=0 if upstream else 2, proxy=None if upstream else model_proxy(), trust_env=False
        )
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
        except (BudgetExceeded, ModelRequestCancelled, PermissionError, ModelCallError) as exc:
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
        if self.upstream:
            expected = httpx.URL(self.upstream.base_url + "/chat/completions")
            if request.method != "POST" or request.url != expected or request.url.userinfo:
                raise PermissionError("模型请求目标与配置的内网端点不一致")
        elif (
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
        if (
            self.upstream
            and self.auto_tool_rounds is not None
            and len(self.request_ids) > self.auto_tool_rounds
        ):
            raise ModelCallError("tool_call_limit", "工具回合及最终输出机会已用尽，模型未生成完整产物")
        if self.upstream and payload.get("tools"):
            names = {tool.get("function", {}).get("name") for tool in payload["tools"]}
            if self.required_first_tool and self.required_first_tool not in names:
                raise PermissionError("冻结主工具不在本次授权工具中")
            if self.required_first_tool and not self.request_ids:
                payload["tool_choice"] = {"type": "function", "function": {"name": self.required_first_tool}}
            elif self.required_first_tool and self.required_tool_completed and self.required_tool_completed():
                # A case stage has one authoritative execution. Later rounds may
                # inspect its frozen result with auxiliary tools, never rerun it.
                auxiliary = [
                    tool for tool in payload["tools"] if tool["function"]["name"] != self.required_first_tool
                ]
                if auxiliary:
                    payload["tools"] = auxiliary
                else:
                    del payload["tools"]
                    payload["tool_choice"] = "none"
            if (
                self.request_ids
                and self.auto_tool_rounds is not None
                and len(self.request_ids) >= self.auto_tool_rounds
            ):
                payload["tool_choice"] = "none"
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers = httpx.Headers(request.headers)
            headers.pop("content-length", None)
            request = httpx.Request(
                request.method, request.url, headers=headers, content=body, extensions=request.extensions
            )
        # Qwen's tool parser emits XML tool calls. A final-response JSON grammar
        # must not constrain either automatic or explicitly selected tool calls.
        # Restore the schema when tool_choice=none ends the tool phase.
        if (
            self.upstream
            and payload.get("tools")
            and payload.get("tool_choice", "auto") != "none"
            and payload.pop("response_format", None) is not None
        ):
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers = httpx.Headers(request.headers)
            headers.pop("content-length", None)
            request = httpx.Request(
                request.method, request.url, headers=headers, content=body, extensions=request.extensions
            )
        if (
            (not self.upstream and MODEL_ID not in PRICED_MODEL_IDS)
            or payload.get("model") != self.model_id
            or payload.get("stream")
        ):
            raise RuntimeError("模型或流式请求不符合预算网关约束")
        if payload.get("n", 1) != 1 or payload.get("best_of", 1) != 1:
            raise RuntimeError("模型网关只允许每次一个输出，避免未计量候选")
        if any(tool.get("type", "function") != "function" for tool in payload.get("tools", [])):
            raise RuntimeError("模型网关不允许额外计费的内置服务工具")
        output_cap = payload.get("max_tokens", payload.get("max_completion_tokens", 0))
        output_limit = self.upstream.max_output_tokens if self.upstream else MAX_OUTPUT_TOKENS
        if type(output_cap) is not int or not 1 <= output_cap <= output_limit:
            raise RuntimeError(f"模型请求必须指定不超过{output_limit} tokens的输出上限")
        if "max_completion_tokens" in payload and payload["max_completion_tokens"] != output_cap:
            raise RuntimeError("模型请求输出上限字段不一致")
        if not self.upstream and self.ledger.budget()["accounting_violation"]:
            raise BudgetExceeded("计量异常，已停止所有模型调用")

        # Image bytes are not text tokens; reserve the verified per-image bound separately.
        def without_image_data(value):
            if isinstance(value, dict):
                return {key: without_image_data(child) for key, child in value.items() if key != "image_url"}
            if isinstance(value, list):
                return [without_image_data(child) for child in value]
            return value

        image_count = body.count(b'"image_url"')
        text_bytes = len(json.dumps(without_image_data(payload), ensure_ascii=False).encode())
        input_cap = text_bytes + 4096 + image_count * 1024
        if input_cap > 1_000_000:
            raise ValueError("输入超出模型上下文限制")
        window = resource_limits.context_window()
        # Byte bounds protect paid reservations, but are not a tokenizer.
        # The self-hosted server admits against its actual tokenized window.
        if not self.upstream and input_cap + output_cap > window:
            raise ModelCallError("model_context_limit", "输入估算上限与输出上限之和超过部署上下文窗口")
        try:
            async with resource_limits.model_slot(self.ledger.path, lambda: self._check_scope(scope)):
                return await self._dispatch(request, scope, run_id, input_cap, output_cap, window)
        except resource_limits.CapacityTimeout as exc:
            raise ModelCallError("model_rate_limited", str(exc)) from None

    async def _dispatch(self, request, scope, run_id, input_cap, output_cap, window):
        reserved = (input_cap * INPUT_RATE + output_cap * OUTPUT_RATE) / 1_000_000
        self._check_scope(scope)
        if scope and scope.before_dispatch:
            try:
                scope.before_dispatch()
            except BaseException as exc:
                self.policy_failure = exc
                raise
        bounds = (input_cap, output_cap, window)
        if self.upstream:
            request_id = self.ledger.begin_internal(self.model_id, self.purpose, run_id, bounds)
        else:
            request_id = self.ledger.reserve(
                reserved,
                self.purpose,
                run_id,
                get_settings()["budget_limit_cny"],
                task_limit=scope.max_cost_cny if scope else None,
                resource_bounds=bounds,
            )
        self.request_ids.append(request_id)
        start = time.monotonic()
        try:
            response, content = await self._send(request, scope)
            if 200 <= response.status_code < 300:
                data = json.loads(content)
                usage = data.get("usage")
                choices = data.get("choices") or []
                self.result = {
                    "request_id": request_id,
                    "model": data.get("model", self.model_id),
                    "finish_reasons": [
                        choice.get("finish_reason") for choice in choices if isinstance(choice, dict)
                    ],
                    "max_output_tokens": output_cap,
                }
                if self.upstream:
                    self.ledger.internal_settle(request_id, data.get("model"), usage)
                    self.result.update(usage=usage, cost_cny=0.0)
                elif isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage:
                    try:
                        cost = self.ledger.settle(request_id, usage, data.get("model", self.model_id))
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
                self.policy_failure = _provider_failure(response.status_code, response.headers, content)
            # aread() returns decoded bytes. Do not advertise compression twice.
            headers = httpx.Headers(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(response.status_code, headers=headers, content=content, request=request)
        except BaseException:
            self.ledger.uncertain(request_id)
            raise
        finally:
            with self.ledger.connect() as db:
                if self.upstream:
                    db.execute(
                        "UPDATE intranet_requests SET state='uncertain' WHERE id=? AND state='reserved'",
                        (request_id,),
                    )
                db.execute(
                    "UPDATE resource_requests SET duration_ms=? WHERE charge_id=?",
                    (round((time.monotonic() - start) * 1000), request_id),
                )

    async def aclose(self):
        await self.inner.aclose()


class ModelGateway:
    def __init__(self, ledger=None):
        self._ledger = ledger

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
        upstream = intranet_provider()
        try:
            async with httpx.AsyncClient(
                proxy=None if upstream else model_proxy(), timeout=8, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.head(
                    upstream.base_url + "/models" if upstream else "https://api.deepseek.com/models"
                )
            result["reachable"] = response.status_code < 500
        except httpx.HTTPError:
            result.update(reachable=False, reason_code="model_connection_failed")
        return result

    @property
    def ledger_path(self) -> Path:
        """Inspect the selected ledger without initializing or migrating its schema."""
        return (
            self._ledger.path
            if self._ledger
            else (_internal_usage_path() if intranet_provider() else Path(BUDGET_LEDGER_PATH))
        )

    @property
    def ledger(self) -> BudgetLedger:
        if self._ledger is None:
            self._ledger = (
                InternalUsageLedger(_internal_usage_path())
                if intranet_provider()
                else BudgetLedger(BUDGET_LEDGER_PATH, require_existing=True)
            )
        return self._ledger

    def usage(self, run_id=None, purpose=None):
        return self.ledger.usage(run_id=run_id, purpose=purpose)

    def budget(self):
        if isinstance(self.ledger, InternalUsageLedger):
            return self.ledger.budget()
        return self.ledger.budget(get_settings()["budget_limit_cny"])

    def config_status(self):
        import importlib.metadata

        upstream = intranet_provider()
        provider = "intranet" if upstream else "deepseek"
        try:
            with self.ledger.connect() as db:
                last_success = db.execute(
                    "SELECT MAX(created_at) FROM model_request_usage WHERE provider=? AND state='settled' "
                    "AND (? IS NULL OR model=?)",
                    (provider, upstream.model if upstream else None, upstream.model if upstream else None),
                ).fetchone()[0]
        except RuntimeError:
            last_success = None
        return {
            "model": upstream.model if upstream else MODEL_ID,
            "provider": provider,
            "key_configured": bool(get_api_key()),
            "last_successful_call_at": last_success,
            "agno_version": importlib.metadata.version("agno"),
            "mode": get_settings()["default_mode"],
            "budget_limit_cny": 300,
            "deployment": "localhost",
        }

    def _client_model(self, transport, api_key, http_client, max_tokens, json_mode):
        from agno.models.deepseek import DeepSeek
        from agno.models.openai import OpenAIChat
        from openai import AsyncOpenAI

        upstream = transport.upstream
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=upstream.base_url if upstream else "https://api.deepseek.com",
            max_retries=0,
            http_client=http_client,
        )
        params = {
            "id": transport.model_id,
            "api_key": api_key,
            "async_client": client,
            "max_tokens": min(
                max(
                    1,
                    max_tokens
                    if max_tokens is not None
                    else (upstream.max_output_tokens if upstream else MAX_OUTPUT_TOKENS),
                ),
                get_settings()["max_output_tokens"],
                upstream.max_output_tokens if upstream else MAX_OUTPUT_TOKENS,
            ),
            "retries": 0,
            "max_retries": 0,
            "request_params": {"response_format": {"type": "json_object"}} if json_mode else None,
        }
        if upstream:
            params.update(
                temperature=upstream.temperature,
                top_p=upstream.top_p,
                role_map={**OpenAIChat.default_role_map, "system": "system", "developer": "system"},
            )
            params["request_params"] = {
                **(params["request_params"] or {}),
                "extra_body": {"chat_template_kwargs": {"enable_thinking": upstream.enable_thinking}},
            }
        return OpenAIChat(**params) if upstream else DeepSeek(**params, use_thinking=False)

    async def complete(
        self,
        prompt,
        *,
        system="",
        purpose="general",
        run_id=None,
        max_tokens=None,
        json_mode=False,
        images=None,
        evidence=None,
        output_schema=None,
    ):
        run_id = _authorize_egress(run_id=run_id, evidence=evidence)
        upstream = intranet_provider()
        api_key = get_api_key()
        if not api_key:
            raise ModelCallError(
                "model_not_configured",
                "未配置内网模型独立凭据 HANGZHI_UPSTREAM_API_KEY"
                if upstream
                else "未配置模型密钥 DEEPSEEK_API_KEY_ZX",
            )
        if len(str(prompt).encode("utf-8")) + len(system.encode("utf-8")) > 200_000:
            raise ModelCallError(
                "model_input_too_large", "模型评审或执行上下文超过200KB，请缩小资料或交付范围"
            )
        from agno.agent import Agent
        from agno.media import Image

        transport = MeteredTransport(
            self.ledger, purpose, run_id, **({"upstream": upstream} if upstream else {})
        )
        async with httpx.AsyncClient(
            transport=transport,
            timeout=upstream.timeout_seconds if upstream else 90,
            follow_redirects=False,
        ) as http_client:
            # Agno's native schema format must reach the self-hosted endpoint.
            # A request_params json_object override otherwise replaces that
            # schema and permits missing/renamed fields in downstream contracts.
            model = self._client_model(
                transport, api_key, http_client, max_tokens, json_mode and not (upstream and output_schema)
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
                use_json_mode=bool(output_schema) and not upstream,
            )
            if json_mode or output_schema:
                prompt = str(prompt) + "\n请仅输出一个合法JSON对象。"
            try:
                media = [
                    Image(content=value["content"], mime_type=value["mime_type"])
                    if isinstance(value, dict)
                    else Image(url=value)
                    for value in (images or [])
                ]
                output = await agent.arun(str(prompt), images=media or None)
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
                "model": transport.result.get("model", transport.model_id),
                "provider": "intranet" if upstream else "deepseek",
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
        schema = getattr(binding, "__hangzhi_tool_schema__", None)
        if schema is not None:
            from agno.tools.function import Function

            # Dynamic, typed domain operations already own their validated schema.
            # Keep the same egress check and per-call audit wrapper as ordinary tools.
            return Function(
                name=wrapped.__name__,
                description=binding.__doc__,
                parameters=schema,
                entrypoint=wrapped,
                skip_entrypoint_processing=True,
            )
        return wrapped

    async def run_agent(
        self,
        agent_spec: dict,
        messages,
        tool_bindings: dict | None = None,
        context=None,
        max_rounds: int = 4,
        timeout_seconds: float = 180,
        output_schema=None,
        native_tools=None,
        skills=None,
        purpose="v2_tool_agent",
        max_tokens=None,
        images=None,
        required_first_tool: str | None = None,
        required_tool_completed: Callable[[], bool] | None = None,
        auto_tool_rounds: int | None = None,
    ):
        """带真实 tools= 的受控模型执行；所有请求仍经由计量传输与预算账本。"""
        run_id = context.get("run_id") if isinstance(context, dict) else getattr(context, "run_id", None)
        run_id = _authorize_egress(run_id=run_id, context=context)
        upstream = intranet_provider()
        api_key = get_api_key()
        if not api_key:
            raise ModelCallError(
                "model_not_configured",
                "未配置内网模型独立凭据 HANGZHI_UPSTREAM_API_KEY"
                if upstream
                else "未配置模型密钥 DEEPSEEK_API_KEY_ZX",
            )
        from agno.agent import Agent
        from agno.media import Image

        transport = MeteredTransport(
            self.ledger, "agent_run", run_id, **({"upstream": upstream} if upstream else {})
        )
        transport.required_first_tool = required_first_tool
        transport.required_tool_completed = required_tool_completed
        # Agno refuses tools beyond tool_call_limit but can keep asking the model
        # for more calls. Enforce the round budget on the wire, then allow one
        # schema-constrained final response. This also applies to generic agents.
        transport.auto_tool_rounds = max(
            1, int(auto_tool_rounds if auto_tool_rounds is not None else max_rounds)
        )
        tool_calls: list[dict] = []
        tools = [
            self._bind_tool(name, binding, tool_calls) for name, binding in (tool_bindings or {}).items()
        ]
        tools.extend(native_tools or [])
        async with httpx.AsyncClient(
            transport=transport,
            timeout=upstream.timeout_seconds if upstream else 90,
            follow_redirects=False,
        ) as http_client:
            model = self._client_model(
                transport, api_key, http_client, max_tokens, bool(output_schema) and not upstream
            )
            instructions = str(agent_spec.get("instructions") or "你是严谨的研究助手。")
            if upstream and output_schema:
                # Custom system_message bypasses Agno's schema instructions.
                # Tool rounds intentionally omit response_format, so make the
                # final output contract available in their model context too.
                schema = (
                    output_schema.model_json_schema()
                    if hasattr(output_schema, "model_json_schema")
                    else output_schema
                )
                instructions += (
                    "\n工具调用遵守工具参数合同；最终响应只输出符合以下JSON Schema的JSON，不含Markdown代码块：\n"
                    + json.dumps(schema, ensure_ascii=False)
                )
            agent = Agent(
                name=f"workbench-agent-{agent_spec.get('name', 'run')}",
                model=model,
                system_message=instructions,
                tools=tools or None,
                skills=skills,
                tool_call_limit=max(1, int(max_rounds)),
                telemetry=False,
                markdown=False,
                retries=0,
                add_history_to_context=False,
                output_schema=output_schema,
                use_json_mode=bool(output_schema) and not upstream,
            )
            media = [
                Image(content=value["content"], mime_type=value["mime_type"])
                if isinstance(value, dict)
                else Image(url=value)
                for value in (images or [])
            ]
            try:
                output = await asyncio.wait_for(
                    agent.arun(str(messages), images=media or None),
                    timeout=max(1.0, float(timeout_seconds)),
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
                "model": transport.result.get("model", transport.model_id),
                "provider": "intranet" if upstream else "deepseek",
                "request_id": transport.result.get("request_id"),
                "finish_reason": (transport.result.get("finish_reasons") or [None])[0],
                "tool_calls": tool_calls,
                "purpose": purpose,
            }


model_gateway = ModelGateway()
