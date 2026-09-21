"""下载/更新 OurAirports 离线数据快照到 .local/ourairports/（该目录不入库）。

用法：uv run python scripts/download_ourairports.py
"""

from __future__ import annotations

import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

FILES = ["airports.csv", "airport-frequencies.csv", "runways.csv", "navaids.csv"]
BASE = "https://ourairports.com/data"
TARGET = Path(__file__).resolve().parents[1] / ".local" / "ourairports"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        destination = TARGET / name
        print(f"下载 {BASE}/{name} -> {destination}")
        request = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "workbench-demo"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - 固定公开数据源
            destination.write_bytes(response.read())
    (TARGET / "SNAPSHOT_DATE").write_text(datetime.now(UTC).date().isoformat(), encoding="utf-8")
    print(f"快照日期已记录：{(TARGET / 'SNAPSHOT_DATE').read_text(encoding='utf-8')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
