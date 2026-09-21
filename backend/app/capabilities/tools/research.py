"""科技文献检索工具：OpenAlex（CC0）与 Crossref。均为只读、data_egress=query。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _query_limit(arguments: dict) -> tuple[str, int]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("检索词不能为空")
    limit = min(max(int(arguments.get("limit") or 5), 1), 20)
    return query, limit


def _evidence(item_id: str, source_uri: str, title: str, text: str) -> dict:
    return {
        "id": item_id,
        "origin": "external",
        "visibility": "external",
        "source_uri": source_uri,
        "source_title": title,
        "retrieved_at": _now(),
        "text": text[:800],
    }


def search_works_request(arguments: dict) -> dict:
    query, limit = _query_limit(arguments)
    return {
        "url": "https://api.openalex.org/works",
        "params": {"search": query, "per-page": limit},
    }


def search_works_parse(payload: Any, arguments: dict) -> dict:
    works = payload.get("results", []) if isinstance(payload, dict) else []
    entries = []
    for work in works:
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])
        ][:5]
        source = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        entries.append(
            {
                "title": work.get("title"),
                "doi": work.get("doi"),
                "year": work.get("publication_year"),
                "authors": authors,
                "cited_by_count": work.get("cited_by_count"),
                "source": source,
                "openalex_id": work.get("id"),
            }
        )
    evidence = [
        _evidence(
            f"openalex:{entry['openalex_id'] or index}",
            entry["doi"] or entry["openalex_id"] or "https://api.openalex.org/works",
            entry["title"] or "未命名文献",
            f"{entry['title']}（{entry['year']}，{entry['source'] or '未知来源'}，被引 {entry['cited_by_count']}，作者：{'、'.join(entry['authors'])}）",
        )
        for index, entry in enumerate(entries)
    ]
    return {"works": entries, "count": len(entries), "evidence": evidence}


def search_doi_request(arguments: dict) -> dict:
    query, limit = _query_limit(arguments)
    return {
        "url": "https://api.crossref.org/works",
        "params": {"query": query, "rows": limit},
    }


def search_doi_parse(payload: Any, arguments: dict) -> dict:
    items = (payload.get("message") or {}).get("items", []) if isinstance(payload, dict) else []
    entries = []
    for item in items:
        title = (item.get("title") or [""])[0]
        published = (item.get("published") or {}).get("date-parts", [[None]])[0][0]
        authors = [
            f"{a.get('family', '')} {a.get('given', '')}".strip() for a in item.get("author", [])
        ][:5]
        entries.append(
            {
                "title": title,
                "doi": item.get("DOI"),
                "year": published,
                "authors": authors,
                "cited_by_count": item.get("is-referenced-by-count"),
                "container": (item.get("container-title") or [""])[0],
            }
        )
    evidence = [
        _evidence(
            f"crossref:{entry['doi'] or index}",
            f"https://doi.org/{entry['doi']}" if entry["doi"] else "https://api.crossref.org/works",
            entry["title"] or "未命名文献",
            f"{entry['title']}（{entry['year']}，{entry['container'] or '未知期刊'}，被引 {entry['cited_by_count']}，作者：{'、'.join(entry['authors'])}）",
        )
        for index, entry in enumerate(entries)
    ]
    return {"works": entries, "count": len(entries), "evidence": evidence}
