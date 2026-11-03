"""Administrator-allowlisted egress for declarative tools and remote MCP."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from urllib.parse import urlsplit

import httpx


def _local_http_allowed():
    return os.environ.get("HANGZHI_ALLOW_LOCAL_HTTP", "false").lower() == "true"


def _allowed_host(host, variable):
    allowed = {item.strip().lower() for item in os.environ.get(variable, "").split(",") if item.strip()}
    return host in allowed or any(item.startswith(".") and host.endswith(item) for item in allowed)


def validate_target(url, variable):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"https", "http"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.scheme == "http" and not (local and _local_http_allowed()))
        or not _allowed_host(host, variable)
    ):
        raise ValueError("external_target_not_allowed")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global and not (local and _local_http_allowed()):
                raise ValueError("external_address_not_allowed")
    except socket.gaierror:
        raise ValueError("external_target_unavailable") from None
    return url


def secret_value(reference):
    if reference is None:
        return None
    if not reference or not reference.replace("_", "a").isalnum() or reference != reference.upper():
        raise ValueError("secret_ref_invalid")
    value = os.environ.get(reference)
    if not value:
        raise ValueError("secret_unavailable")
    return value


def auth_headers(reference):
    value = secret_value(reference)
    return {"Authorization": f"Bearer {value}"} if value else {}


async def post_json(url, arguments, *, secret_ref=None, timeout_seconds=15, max_result_bytes=80000):
    validate_target(url, "HANGZHI_TOOL_HOSTS")
    timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
    try:
        async with (
            httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                headers=auth_headers(secret_ref),
            ) as client,
            client.stream("POST", url, json=arguments) as response,
        ):
            if response.status_code < 200 or response.status_code >= 300:
                raise ValueError("external_tool_http_error")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > max_result_bytes:
                    raise ValueError("tool_result_too_large")
        value = json.loads(chunks)
    except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("external_tool_invalid_response") from None
    return value if isinstance(value, dict) else {"value": value}
