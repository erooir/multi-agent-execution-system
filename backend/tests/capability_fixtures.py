"""能力内核测试用的本地 entrypoint 桩。"""

from __future__ import annotations

import time


def slow(seconds: float = 5) -> dict:
    time.sleep(seconds)
    return {"slept": seconds}


def ping() -> dict:
    return {"ok": True}
