"""Source-governed memory and explicitly configured knowledge adapters."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from .contracts import ConnectorInput, KnowledgeQuery, MemoryInput
from .db import now, repo
from .quality import text_of
from .service import DomainError, audit, fail, project_access, required, timestamp, usable_asset


def create_connector(body, user):
    body = ConnectorInput.model_validate(body).model_dump()
    if body["kind"] == "http_knowledge":
        if user["role"] != "admin":
            fail("permission_denied", "只有管理员可以配置网络连接器", 403)
        parsed = urlsplit(body["base_url"])
        allowed = set(filter(None, os.environ.get("HANGZHI_KNOWLEDGE_HOSTS", "").split(",")))
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in allowed
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            fail("connector_not_authorized", "连接器地址必须位于部署时设置的知识服务主机白名单，且不包含凭据")
    with repo.transaction() as tx:
        project_access(body["project_id"], user, True, tx)
        record = tx.put(
            "connectors", {**body, "status": "ready" if body["kind"] == "local_assets" else "unverified"}
        )
        audit(tx, body["project_id"], "connector.created", record["id"], user)
        return record


def create_memory(body, user):
    body = MemoryInput.model_validate(body).model_dump()
    with repo.transaction() as tx:
        project_access(body["project_id"], user, True, tx)
        expiry = datetime.now(UTC) + timedelta(days=body["retention_days"])
        sources, lineage = [], []
        for source_id in body["source_refs"]:
            asset = required("data_assets", source_id, user, repository=tx)
            if asset["project_id"] != body["project_id"]:
                fail("permission_denied", "不能跨项目引用来源", 403)
            usable_asset(asset)
            if asset.get("modality") not in {"text", "structured"} or asset.get("content") is None:
                fail("unverified_memory", "不能从未经解析的媒体或空载荷创建事实记忆")
            sources.append(text_of(asset))
            lineage.append(
                {
                    "asset_id": asset["id"],
                    "checksum": asset["checksum"],
                    "source_version": asset["source_version"],
                    "classification": asset["classification"],
                }
            )
            expiry = min(expiry, timestamp(asset["expires_at"]))
        if not any(body["content"] in text for text in sources):
            fail("unverified_memory", "当前记忆写入仅接受来源中的精确事实片段，不能保存未经验证的推断")
        memory = tx.put(
            "memories",
            {
                **body,
                "status": "active",
                "expires_at": expiry.isoformat(),
                "quality_status": "source_exact",
                "lineage": lineage,
                "classification": "local"
                if any(a["classification"] == "local" for a in lineage)
                else "public",
            },
        )
        audit(tx, body["project_id"], "memory.created", memory["id"], user)
        return memory


async def search(body, user):
    body = KnowledgeQuery.model_validate(body).model_dump()
    project_access(body["project_id"], user, repository=repo)
    result = []
    query = body["query"].casefold()
    for asset in repo.list("data_assets", body["project_id"]):
        try:
            usable_asset(asset)
        except DomainError:
            continue
        if asset.get("content") is None:
            continue
        content = text_of(asset)
        offset = content.casefold().find(query)
        if offset >= 0:
            result.append(
                {
                    "id": asset["id"],
                    "source_id": asset["source_id"],
                    "source_version": asset["source_version"],
                    "name": asset["name"],
                    "text": content[max(0, offset - 80) : offset + 400],
                    "location": {"offset": offset},
                    "provenance": asset["provenance"],
                    "retrieved_at": now(),
                }
            )
    # Network adapters are never auto-discovered or enabled by a model.
    for connector in repo.list("connectors", body["project_id"]):
        if connector["kind"] != "http_knowledge":
            continue
        host = urlsplit(connector["base_url"]).hostname
        if host not in os.environ.get("HANGZHI_KNOWLEDGE_HOSTS", "").split(","):
            continue
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:  # noqa: SIM117
                async with client.stream(
                    "POST",
                    connector["base_url"].rstrip("/") + "/search",
                    json={"query": body["query"], "limit": body["limit"], "project_id": body["project_id"]},
                ) as response:
                    response.raise_for_status()
                    raw = b""
                    async for part in response.aiter_bytes():
                        raw += part
                        if len(raw) > 1_000_000:
                            fail("connector_response_too_large", "知识服务响应超过1MB")
                    import json

                    payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise TypeError("Expected items array")
            for item in payload["items"][: body["limit"]]:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(k), str)
                    for k in ("id", "text", "source_id", "source_version", "provenance")
                ):
                    raise ValueError("Missing source contract fields")
                if item.get("project_id", body["project_id"]) != body["project_id"]:
                    raise ValueError("Upstream returned a different project")
                result.append(
                    {k: item[k] for k in ("id", "text", "source_id", "source_version", "provenance")}
                    | {"connector_id": connector["id"], "retrieved_at": now()}
                )
            _connector_status(connector, "ready", None)
        except (httpx.HTTPError, ValueError, TypeError, DomainError):
            _connector_status(connector, "unavailable", "upstream_contract_or_connection_failed")
            result.append(
                {
                    "connector_id": connector["id"],
                    "error_code": "upstream_contract_or_connection_failed",
                    "text": "",
                    "name": connector["name"],
                }
            )
    # A slow upstream response must not expose local data revoked while waiting.
    project_access(body["project_id"], user, repository=repo)
    filtered = []
    for item in result:
        if not item.get("connector_id"):
            current = repo.get("data_assets", item["id"])
            try:
                if not current:
                    continue
                usable_asset(current)
            except DomainError:
                continue
        filtered.append(item)
    return filtered[: body["limit"]]


def _connector_status(connector, status, error):
    with repo.transaction() as tx:
        current = tx.get("connectors", connector["id"], for_update=True)
        if current and current.get("base_url") == connector.get("base_url"):
            tx.put("connectors", {**current, "status": status, "last_checked_at": now(), "error_code": error})
