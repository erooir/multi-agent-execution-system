from __future__ import annotations

import os
from types import SimpleNamespace
from .storage import DATA_DIR, store

MODEL_ID = "deepseek-flash"
BUDGET_LIMIT_CNY = 300.0
settings = SimpleNamespace(DATA_DIR=DATA_DIR, data_dir=DATA_DIR, MODEL_ID=MODEL_ID,
                           BUDGET_LIMIT_CNY=BUDGET_LIMIT_CNY, model_id=MODEL_ID)


def get_api_key() -> str | None:
    value = os.environ.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")
    if value:
        return value.strip()
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return str(winreg.QueryValueEx(key, "deepseek_api_key")[0]).strip()
        except FileNotFoundError:
            pass
    return None


def get_settings() -> dict:
    saved = store.get("settings", "main") or {}
    return {"model": MODEL_ID, "base_url": "https://api.deepseek.com", "budget_limit_cny": min(float(saved.get("budget_limit_cny", BUDGET_LIMIT_CNY)), BUDGET_LIMIT_CNY),
            "default_mode": saved.get("default_mode", "rehearsal"), "max_concurrent_runs": 2,
            "max_output_tokens": min(int(saved.get("max_output_tokens", 3000)), 6000),
            "request_timeout_seconds": 90, "key_configured": bool(get_api_key()),
            "deployment": "localhost", "telemetry_enabled": False,
            "pricing_note": "按官方高峰单价的2倍保守计账（输入4元/百万token，输出16元/百万token），含安全余量；非账单实扣。"}


def save_settings(data: dict) -> dict:
    old = get_settings()
    limit = float(data.get("budget_limit_cny", old["budget_limit_cny"]))
    if not 0 < limit <= BUDGET_LIMIT_CNY:
        raise ValueError("累计预算必须大于0且不超过300元，不能通过设置扩大授权额度")
    mode = data.get("default_mode", old["default_mode"])
    if mode not in ("rehearsal", "live"):
        raise ValueError("执行模式不合法")
    output = int(data.get("max_output_tokens", old["max_output_tokens"]))
    if not 256 <= output <= 6000:
        raise ValueError("单次输出上限为256至6000 tokens")
    store.save("settings", {"id": "main", "budget_limit_cny": limit, "default_mode": mode, "max_output_tokens": output})
    return get_settings()
