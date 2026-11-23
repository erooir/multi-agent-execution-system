"""Source-governed memory and explicitly configured knowledge adapters."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import httpx

from .contracts import ConnectorInput, KnowledgeQuery, MemoryInput, is_file_index
from .db import now, repo
from .quality import text_of
from .service import DomainError, audit, fail, project_access, required, timestamp, usable_asset


def project_connectors(project_id, user, repository):
    """Project connector identities, shared by knowledge and capability navigation."""
    from .tool_registry import catalog

    project_access(project_id, user, repository=repository)
    result = [
        {
            "id": server["id"],
            "name": "项目表格服务",
            "kind": "mcp",
            "status": "published",
            "transport": server["transport"],
            "current_version": server["version"],
            "builtin": True,
        }
        for server in catalog(include_readiness=False)["mcp_servers"]
    ]
    for row in repository.list("capability_definitions", project_id):
        if row["kind"] != "mcp_server":
            continue
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row.get("description", ""),
                "kind": "mcp",
                "status": row["status"],
                "current_version": row.get("current_version"),
                "transport": row.get("spec", {}).get("transport"),
            }
        )
    for row in repository.list("connectors", project_id):
        if row["kind"] != "http_knowledge":
            continue
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row.get("description", ""),
                "kind": "http_knowledge",
                "status": row["status"],
                "transport": "http",
                "automatic_intake": row.get("automatic_intake", False),
            }
        )
    return result


def offline_host_allowed(host):
    if os.environ.get("HANGZHI_OFFLINE", "").lower() != "true":
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or any(
        address in ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    )


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
            or not offline_host_allowed(parsed.hostname)
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
            if asset.get("task_case_id"):
                fail("task_scoped_source", "任务专用资料不能直接写入项目共享记忆", 403)
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
        if asset.get("task_case_id"):
            continue
        try:
            usable_asset(asset)
        except DomainError:
            continue
        content = text_of(asset) if asset.get("content") is not None else ""
        offset = content.casefold().find(query)
        metadata_hit = is_file_index(asset)
        original = asset.get("original_file")
        if offset < 0 and original:
            content = "\n".join((asset["name"], asset["source_id"], original["filename"]))
            offset = content.casefold().find(query)
            metadata_hit = True
        if offset >= 0:
            result.append(
                {
                    "id": asset["id"],
                    "source_id": asset["source_id"],
                    "source_version": asset["source_version"],
                    "name": asset["name"],
                    "text": content[max(0, offset - 80) : offset + 400],
                    "location": {"offset": offset, "field": "metadata"}
                    if metadata_hit
                    else {"offset": offset},
                    "provenance": asset["provenance"],
                    "retrieved_at": now(),
                }
            )
    # Network adapters are never auto-discovered or enabled by a model.
    for connector in repo.list("connectors", body["project_id"]):
        if connector["kind"] != "http_knowledge":
            continue
        result.extend(await connector_results(connector, body, repo))
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


async def connector_results(connector, body, repository, *, task_intake=False):
    """Call only a configured origin; a task import also requires explicit project attribution."""
    items = []
    host = urlsplit(connector["base_url"]).hostname
    if host not in os.environ.get("HANGZHI_KNOWLEDGE_HOSTS", "").split(",") or not offline_host_allowed(host):
        return (
            [{"connector_id": connector["id"], "error_code": "connector_not_authorized"}]
            if task_intake
            else []
        )
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:  # noqa: SIM117
            async with client.stream(
                "POST",
                connector["base_url"].rstrip("/") + "/search",
                json={
                    "query": body["query"],
                    "limit": body["limit"],
                    "project_id": body["project_id"],
                    **({"source_ids": body["source_ids"]} if task_intake and body.get("source_ids") else {}),
                },
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
            if item.get("project_id", None if task_intake else body["project_id"]) != body["project_id"]:
                raise ValueError("Upstream returned a different project")
            items.append(
                {k: item[k] for k in ("id", "text", "source_id", "source_version", "provenance")}
                | {
                    "connector_id": connector["id"],
                    "retrieved_at": now(),
                    "media_type": item.get("media_type", "text/plain"),
                    "classification": "public"
                    if connector.get("allow_public_results") and item.get("classification") == "public"
                    else "local",
                }
            )
        _connector_status(repository, connector, "ready", None)
    except (httpx.HTTPError, ValueError, TypeError, DomainError):
        items.clear()
        _connector_status(repository, connector, "unavailable", "upstream_contract_or_connection_failed")
        items.append(
            {
                "connector_id": connector["id"],
                "error_code": "upstream_contract_or_connection_failed",
                "text": "",
                "name": connector["name"],
            }
        )
    return items


def _connector_status(repository, connector, status, error):
    with repository.transaction() as tx:
        current = tx.get("connectors", connector["id"], for_update=True)
        if current and current.get("base_url") == connector.get("base_url"):
            tx.put("connectors", {**current, "status": status, "last_checked_at": now(), "error_code": error})
