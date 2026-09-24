"""OurAirports 离线快照工具：机场查询与附近机场（network: none，drill 下真实执行）。

快照位于 .local/ourairports/（airports.csv、SNAPSHOT_DATE 等，gitignore 不入库）。
缺失时明确报 provider_unavailable 并说明补齐方式。惰性加载并缓存。
"""

from __future__ import annotations

import csv
import math
import os
import threading
from pathlib import Path

from ..errors import PROVIDER_UNAVAILABLE, CapabilityError

_lock = threading.Lock()
_cache: dict[str, tuple[str, list[dict]]] = {}

_MISSING_HINT = (
    "OurAirports 数据快照缺失：请运行 `uv run python scripts/download_ourairports.py`，"
    "或手动下载 https://ourairports.com/data/airports.csv 等到 .local/ourairports/"
)


def _snapshot_dir() -> Path:
    override = os.environ.get("OURAIRPORTS_DIR")
    if override:
        return Path(override)
    data_dir = os.environ.get("WORKBENCH_DATA_DIR")
    root = Path(data_dir) if data_dir else Path(__file__).resolve().parents[4] / ".local"
    return root / "ourairports"


def _load() -> tuple[str, list[dict]]:
    directory = _snapshot_dir()
    with _lock:
        cached = _cache.get(str(directory))
        if cached is not None:
            return cached
        path = directory / "airports.csv"
        if not path.is_file():
            raise CapabilityError(PROVIDER_UNAVAILABLE, _MISSING_HINT)
        snapshot_date = (
            (directory / "SNAPSHOT_DATE").read_text(encoding="utf-8").strip()
            if (directory / "SNAPSHOT_DATE").is_file()
            else "未知"
        )
        with open(path, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        _cache[str(directory)] = (snapshot_date, rows)
        return snapshot_date, rows


def reset_cache() -> None:
    """测试辅助：清空快照缓存。"""
    with _lock:
        _cache.clear()


def _airport_summary(row: dict) -> dict:
    return {
        "ident": row.get("ident"),
        "icao": row.get("icao_code") or row.get("ident"),
        "iata": row.get("iata_code"),
        "name": row.get("name"),
        "type": row.get("type"),
        "municipality": row.get("municipality"),
        "iso_country": row.get("iso_country"),
        "latitude": _float(row.get("latitude_deg")),
        "longitude": _float(row.get("longitude_deg")),
        "elevation_ft": _float(row.get("elevation_ft")),
    }


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _evidence(snapshot_date: str, row: dict, extra: str = "") -> dict:
    ident = row.get("ident", "")
    name = row.get("name", "")
    return {
        "id": f"ourairports:{ident}",
        "origin": "external",
        "visibility": "external",
        "source_uri": row.get("wikipedia_link") or f"https://ourairports.com/airports/{ident}/",
        "source_title": name,
        "retrieved_at": f"OurAirports 快照 {snapshot_date}",
        "location": f"{row.get('municipality') or ''}, {row.get('iso_country') or ''}",
        "text": f"{name}（{ident}，{row.get('type')}，{row.get('municipality') or '-'}）{extra}".strip(),
    }


def lookup_airport(query: str = "", limit: int | None = None) -> dict:
    query = str(query or "").strip()
    if not query:
        raise ValueError("查询词不能为空")
    limit = min(max(int(8 if limit is None else limit), 1), 25)
    snapshot_date, rows = _load()
    needle = query.casefold()
    code = query.upper()
    exact, fuzzy = [], []
    for row in rows:
        if row.get("type") == "closed":
            continue
        codes = {
            row.get("ident", "").upper(),
            (row.get("icao_code") or "").upper(),
            (row.get("iata_code") or "").upper(),
        }
        if code in codes:
            exact.append(row)
        elif (
            needle in (row.get("name") or "").casefold()
            or needle in (row.get("municipality") or "").casefold()
        ):
            fuzzy.append(row)
    matched = (exact + fuzzy)[:limit]
    return {
        "query": query,
        "snapshot_date": snapshot_date,
        "airports": [_airport_summary(row) for row in matched],
        "count": len(matched),
        "evidence": [_evidence(snapshot_date, row) for row in matched],
    }


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radians = math.radians
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(radians(lat1)) * math.cos(radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearby_airports(
    latitude: float, longitude: float, radius_km: float | None = None, limit: int | None = None
) -> dict:
    latitude, longitude = float(latitude), float(longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("经纬度超出有效范围")
    radius_km = min(max(float(100.0 if radius_km is None else radius_km), 1.0), 2000.0)
    limit = min(max(int(20 if limit is None else limit), 1), 50)
    snapshot_date, rows = _load()
    found = []
    for row in rows:
        if row.get("type") == "closed":
            continue
        lat, lon = _float(row.get("latitude_deg")), _float(row.get("longitude_deg"))
        if lat is None or lon is None:
            continue
        distance = _distance_km(latitude, longitude, lat, lon)
        if distance <= radius_km:
            found.append((distance, row))
    found.sort(key=lambda pair: pair[0])
    matched = found[:limit]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "snapshot_date": snapshot_date,
        "airports": [
            {**_airport_summary(row), "distance_km": round(distance, 1)} for distance, row in matched
        ],
        "count": len(matched),
        "evidence": [
            _evidence(snapshot_date, row, f"，距离 {round(distance, 1)} km") for distance, row in matched
        ],
    }
