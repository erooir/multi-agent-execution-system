from __future__ import annotations

import ipaddress
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The lifetime budget belongs to this project, never to a disposable runtime directory.
BUDGET_LEDGER_PATH = Path(
    os.environ.get("HANGZHI_BUDGET_LEDGER_PATH", str(PROJECT_ROOT / ".local" / "budget.sqlite3"))
)
DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR", str(PROJECT_ROOT / ".local")))
MODEL_ID = os.environ.get("HANGZHI_MODEL_ID", "deepseek-flash")
PRICED_MODEL_IDS = frozenset({"deepseek-flash", "deepseek-v4-flash"})
BUDGET_LIMIT_CNY = 300.0
DEFAULT_OUTPUT_TOKENS = 8000
MAX_OUTPUT_TOKENS = 8000
settings = SimpleNamespace(
    DATA_DIR=DATA_DIR,
    data_dir=DATA_DIR,
    MODEL_ID=MODEL_ID,
    BUDGET_LIMIT_CNY=BUDGET_LIMIT_CNY,
    model_id=MODEL_ID,
)


@dataclass(frozen=True)
class IntranetProvider:
    base_url: str
    model: str
    timeout_seconds: float = 180
    max_output_tokens: int = 16000
    temperature: float = 0
    top_p: float = 1
    enable_thinking: bool = False
    max_task_requests: int = 60


def intranet_provider() -> IntranetProvider | None:
    """Operator-owned numeric address only. DNS and proxy lookups are unnecessary."""
    endpoint = os.environ.get("HANGZHI_UPSTREAM_MODEL_URL", "")
    model = os.environ.get("HANGZHI_UPSTREAM_MODEL_ID", "")
    billing = os.environ.get("HANGZHI_UPSTREAM_BILLING", "")
    if not (endpoint or model or billing or os.environ.get("HANGZHI_UPSTREAM_API_KEY")):
        return None
    if billing != "internal_zero" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", model):
        raise ValueError("内网模型必须配置模型标识并显式声明 internal_zero；未核价的付费供应端不予接入")
    url = urlsplit(endpoint)
    try:
        ip = ipaddress.ip_address(url.hostname or "")
        port = url.port
    except ValueError:
        raise ValueError("内网模型地址须为明确的本机或专用网络 IP") from None
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    if (
        not (ip.is_loopback or any(ip in ipaddress.ip_network(cidr) for cidr in networks))
        or "%" in str(ip)
        or url.scheme not in ({"http", "https"} if ip.is_loopback else {"https"})
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path.rstrip("/") not in {"", "/v1"}
        or port == 0
    ):
        raise ValueError("内网模型只接受本机 HTTP(S) 或私网 HTTPS 的标准 /v1 地址")
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    authority = f"{host}:{port}" if port else host
    timeout = float(os.environ.get("HANGZHI_UPSTREAM_TIMEOUT_SECONDS", "180"))
    # Self-hosted limits are deployment policy, separate from paid-cloud caps.
    from .resource_limits import context_window

    output = int(os.environ.get("HANGZHI_UPSTREAM_MAX_OUTPUT_TOKENS", "16000"))
    requests = int(os.environ.get("HANGZHI_UPSTREAM_MAX_TASK_REQUESTS", "60"))
    temperature = float(os.environ.get("HANGZHI_UPSTREAM_TEMPERATURE", "0"))
    top_p = float(os.environ.get("HANGZHI_UPSTREAM_TOP_P", "1"))
    thinking = os.environ.get("HANGZHI_UPSTREAM_ENABLE_THINKING", "false").lower()
    if (
        not math.isfinite(timeout)
        or not 1 <= timeout <= 3600
        or not 1 <= output < context_window()
        or not 0 <= requests <= 10000
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
        or not math.isfinite(top_p)
        or not 0 < top_p <= 1
        or thinking not in {"true", "false"}
    ):
        raise ValueError("内网模型超时、请求数、输出上限或生成参数无效")
    return IntranetProvider(
        f"{url.scheme}://{authority}/v1",
        model,
        timeout,
        output,
        temperature,
        top_p,
        thinking == "true",
        requests,
    )


def get_api_key() -> str | None:
    if intranet_provider():
        key = os.environ.get("HANGZHI_UPSTREAM_API_KEY", "").strip()
        if key and key == _deepseek_key():
            raise ValueError("内网端点必须使用独立凭据")
        return key or None
    return _deepseek_key()


def _deepseek_key() -> str | None:
    value = os.environ.get("DEEPSEEK_API_KEY_ZX")
    if value:
        return value.strip()
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return str(winreg.QueryValueEx(key, "DEEPSEEK_API_KEY_ZX")[0]).strip()
        except FileNotFoundError:
            pass
    return None


def get_settings() -> dict:
    # Retained for the old settings API; importing the gateway does not open legacy data.
    from .resource_limits import task_slots
    from .storage import store

    upstream = intranet_provider()
    saved = store.get("settings", "main") or {}
    limit = float(saved.get("budget_limit_cny", BUDGET_LIMIT_CNY))
    if not math.isfinite(limit) or not 0 < limit <= BUDGET_LIMIT_CNY:
        raise ValueError("预算配置无效，已停止模型调用")
    return {
        "model": upstream.model if upstream else MODEL_ID,
        "base_url": upstream.base_url if upstream else "https://api.deepseek.com",
        "provider": "intranet" if upstream else "deepseek",
        "budget_limit_cny": limit,
        "default_mode": saved.get("default_mode", "rehearsal"),
        "max_concurrent_runs": task_slots(),
        "max_output_tokens": min(
            int(
                saved.get(
                    "intranet_max_output_tokens" if upstream else "max_output_tokens",
                    upstream.max_output_tokens if upstream else DEFAULT_OUTPUT_TOKENS,
                )
            ),
            upstream.max_output_tokens if upstream else MAX_OUTPUT_TOKENS,
        ),
        "request_timeout_seconds": upstream.timeout_seconds if upstream else 90,
        "key_configured": bool(get_api_key()),
        "deployment": "localhost",
        "telemetry_enabled": False,
        "pricing_note": "自部署内网模型：仅计量请求与 Token，不按云模型费率收费。"
        if upstream
        else "按官方高峰单价的2倍保守计账（输入4元/百万token，输出16元/百万token），含安全余量；非账单实扣。",
    }


def save_settings(data: dict) -> dict:
    from .storage import store

    old = get_settings()
    limit = float(data.get("budget_limit_cny", old["budget_limit_cny"]))
    if not 0 < limit <= BUDGET_LIMIT_CNY:
        raise ValueError("累计预算必须大于0且不超过300元，不能通过设置扩大授权额度")
    mode = data.get("default_mode", old["default_mode"])
    if mode not in ("rehearsal", "live"):
        raise ValueError("执行模式不合法")
    output = int(data.get("max_output_tokens", old["max_output_tokens"]))
    upstream = intranet_provider()
    output_limit = upstream.max_output_tokens if upstream else MAX_OUTPUT_TOKENS
    if not 256 <= output <= output_limit:
        raise ValueError(f"单次输出上限为256至{output_limit} tokens")
    saved = store.get("settings", "main") or {}
    output_key = "intranet_max_output_tokens" if upstream else "max_output_tokens"
    store.save(
        "settings",
        {**saved, "id": "main", "budget_limit_cny": limit, "default_mode": mode, output_key: output},
    )
    return get_settings()
