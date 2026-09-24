"""NOAA Aviation Weather 只读工具（aviationweather.gov，无需 Key，data_egress=query）。

请求构造与结果解析分离：Provider 统一执行白名单/超时/大小上限。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

API_BASE = "https://aviationweather.gov/api/data"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _evidence(item_id: str, source_uri: str, title: str, text: str, location: str = "") -> dict:
    return {
        "id": item_id,
        "origin": "external",
        "visibility": "external",
        "source_uri": source_uri,
        "source_title": title,
        "retrieved_at": _now(),
        "location": location,
        "text": text[:800],
    }


def _icao(arguments: dict) -> str:
    icao = str(arguments.get("icao") or "").strip().upper()
    if not icao or not icao.replace("-", "").isalnum() or len(icao) > 8:
        raise ValueError("icao 必须是 1-8 位字母数字的机场代码")
    return icao


def get_metar_request(arguments: dict) -> dict:
    icao = _icao(arguments)
    return {"url": f"{API_BASE}/metar", "params": {"ids": icao, "format": "json"}}


def get_metar_parse(payload: Any, arguments: dict) -> dict:
    icao = _icao(arguments)
    reports = payload if isinstance(payload, list) else []
    entries = []
    for item in reports:
        raw = item.get("rawOb") or item.get("raw_text") or json.dumps(item, ensure_ascii=False)[:400]
        entries.append(
            {
                "station": item.get("icaoId", icao),
                "observation_time": item.get("reportTime"),
                "raw": raw,
                "temp_c": item.get("temp"),
                "wind": f"{item.get('wdir', '')}°/{item.get('wspd', '')}kt"
                if item.get("wspd") is not None
                else None,
                "visibility": item.get("visib"),
                "flight_category": item.get("fltCat"),
            }
        )
    evidence = [
        _evidence(
            f"noaa:metar:{entry['station']}:{index}",
            f"{API_BASE}/metar?ids={entry['station']}",
            f"METAR {entry['station']}",
            entry["raw"],
            entry["station"],
        )
        for index, entry in enumerate(entries)
    ]
    return {"station": icao, "reports": entries, "count": len(entries), "evidence": evidence}


def get_taf_request(arguments: dict) -> dict:
    icao = _icao(arguments)
    return {"url": f"{API_BASE}/taf", "params": {"ids": icao, "format": "json"}}


def get_taf_parse(payload: Any, arguments: dict) -> dict:
    icao = _icao(arguments)
    reports = payload if isinstance(payload, list) else []
    entries = []
    for item in reports:
        raw = item.get("rawTAF") or item.get("raw_text") or json.dumps(item, ensure_ascii=False)[:400]
        entries.append(
            {
                "station": item.get("icaoId", icao),
                "issue_time": item.get("issueTime"),
                "valid_time": f"{item.get('validTimeFrom', '')}~{item.get('validTimeTo', '')}",
                "raw": raw,
            }
        )
    evidence = [
        _evidence(
            f"noaa:taf:{entry['station']}:{index}",
            f"{API_BASE}/taf?ids={entry['station']}",
            f"TAF {entry['station']}",
            entry["raw"],
            entry["station"],
        )
        for index, entry in enumerate(entries)
    ]
    return {"station": icao, "reports": entries, "count": len(entries), "evidence": evidence}


def get_sigmet_request(arguments: dict) -> dict:
    params: dict[str, Any] = {"format": "json"}
    bbox = str(arguments.get("bbox") or "").strip()
    if bbox:
        parts = bbox.split(",")
        if len(parts) != 4 or any(not _is_number(part) for part in parts):
            raise ValueError("bbox 必须是 纬度,经度,纬度,经度 四个数值")
        params["bbox"] = bbox
    return {"url": f"{API_BASE}/airsigmet", "params": params}


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def get_sigmet_parse(payload: Any, arguments: dict) -> dict:
    reports = payload if isinstance(payload, list) else []
    entries = []
    for item in reports:
        raw = item.get("rawAirSigmet") or item.get("raw") or json.dumps(item, ensure_ascii=False)[:400]
        entries.append(
            {
                "id": item.get("airSigmetId"),
                "hazard": item.get("hazard"),
                "severity": item.get("severity"),
                "valid_time": f"{item.get('validTimeFrom', '')}~{item.get('validTimeTo', '')}",
                "raw": raw,
            }
        )
    evidence = [
        _evidence(
            f"noaa:sigmet:{entry['id'] or index}",
            f"{API_BASE}/airsigmet",
            f"SIGMET {entry.get('hazard') or entry['id'] or index}",
            entry["raw"],
        )
        for index, entry in enumerate(entries)
    ]
    return {"reports": entries, "count": len(entries), "evidence": evidence}
