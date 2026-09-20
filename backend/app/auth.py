"""Persisted local identities, salted password hashes, sessions and role enforcement."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from .storage import now, store

PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 43_200
RATE_WINDOW_SECONDS = 600
LEGACY_USERS = (
    {"id": "admin", "username": "admin", "name": "平台管理员", "role": "admin"},
    {"id": "operator", "username": "operator", "name": "研究人员", "role": "operator"},
    {"id": "reviewer", "username": "reviewer", "name": "审核专家", "role": "reviewer"},
)
PUBLIC_USER_FIELDS = ("id", "username", "name", "role")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        rounds = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= rounds <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (AttributeError, TypeError, ValueError):
        return False


def _insert_record(conn, kind: str, record: dict, *, ignore_existing: bool = False) -> None:
    record.setdefault("created_at", now())
    record["updated_at"] = now()
    statement = "INSERT OR IGNORE" if ignore_existing else "INSERT"
    conn.execute(
        f"{statement} INTO records(kind,id,data,updated_at) VALUES(?,?,?,?)",
        (kind, record["id"], json.dumps(record, ensure_ascii=False), record["updated_at"]),
    )


def ensure_users() -> None:
    """One-time, atomic migration; existing local accounts and sessions retain their identity."""
    if store.get("auth_meta", "persisted_users_v1"):
        return
    legacy_password = os.environ.get("WORKBENCH_DEMO_PASSWORD", "demo12345")
    migrated = [
        {**user, "enabled": True, "password_hash": _hash_password(legacy_password)} for user in LEGACY_USERS
    ]
    with store.lock, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM records WHERE kind='auth_meta' AND id='persisted_users_v1'"
        ).fetchone():
            return
        for user in migrated:
            _insert_record(conn, "users", user, ignore_existing=True)
        _insert_record(conn, "auth_meta", {"id": "persisted_users_v1", "version": 1})


def _username(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{3,32}", value):
        raise HTTPException(422, "用户名须为3至32位英文字母、数字或下划线")
    return value.lower()


def check_origin(request: Request | None) -> None:
    if request is None:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站修改请求")
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (8000, 5173)}
    base_url = str(request.base_url).rstrip("/")
    if urlsplit(base_url).hostname in ("localhost", "127.0.0.1", "testserver"):
        allowed.add(base_url)
    if origin not in allowed:
        raise HTTPException(403, "不允许跨站修改请求")


def _rate_limit(request: Request | None, action: str, username: str = "") -> None:
    if request is None:
        return
    address = request.client.host if request.client else "local"
    keys = [(f"{action}:ip:{address}", 30 if action == "login" else 10)]
    if action == "login":
        keys.append((f"login:username:{username}", 10))
    current_time = time.time()
    with store.lock, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for raw_key, limit in keys:
            key = hashlib.sha256(raw_key.encode()).hexdigest()
            row = conn.execute(
                "SELECT data FROM records WHERE kind='auth_limits' AND id=?", (key,)
            ).fetchone()
            record = json.loads(row[0]) if row else {"id": key, "started_at": current_time, "count": 0}
            if current_time - record["started_at"] >= RATE_WINDOW_SECONDS:
                record.update(started_at=current_time, count=0)
            if record["count"] >= limit:
                retry_after = max(1, int(RATE_WINDOW_SECONDS - (current_time - record["started_at"])))
                raise HTTPException(
                    429, "尝试次数过多，请稍后再试", headers={"Retry-After": str(retry_after)}
                )
            record["count"] += 1
            conn.execute("DELETE FROM records WHERE kind='auth_limits' AND id=?", (key,))
            _insert_record(conn, "auth_limits", record)


def _public_user(user: dict) -> dict:
    return {key: user[key] for key in PUBLIC_USER_FIELDS}


def _start_session(user: dict, response: Response, request: Request | None = None) -> dict:
    if request:
        previous_token = request.cookies.get("workbench_session")
        if previous_token:
            store.delete("sessions", hashlib.sha256(previous_token.encode()).hexdigest())
    token = secrets.token_urlsafe(32)
    store.save(
        "sessions",
        {
            "id": hashlib.sha256(token.encode()).hexdigest(),
            "username": user["username"],
            "expires_at": (datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS)).isoformat(),
        },
    )
    response.set_cookie(
        "workbench_session",
        token,
        httponly=True,
        samesite="strict",
        max_age=SESSION_SECONDS,
        secure=bool(request and request.url.scheme == "https"),
        path="/",
    )
    return {"user": _public_user(user)}


def register(body: dict, response: Response, request: Request | None = None) -> dict:
    check_origin(request)
    _rate_limit(request, "register")
    if set(body) - {"username", "password", "name"}:
        raise HTTPException(422, "注册仅支持用户名、密码和显示名，不能指定角色或权限")
    username = _username(body.get("username"))
    password = body.get("password")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise HTTPException(422, "密码须为8至128个字符")
    name = body.get("name", "")
    if not isinstance(name, str):
        raise HTTPException(422, "显示名须为文字")
    name = name.strip() or username
    if len(name) > 40 or not name.isprintable():
        raise HTTPException(422, "显示名须为1至40个可打印字符")
    ensure_users()
    user = {
        "id": username,
        "username": username,
        "name": name,
        "role": "operator",
        "enabled": True,
        "password_hash": _hash_password(password),
    }
    try:
        with store.lock, store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # The records primary key enforces uniqueness even across server processes.
            _insert_record(conn, "users", user)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "用户名已被使用") from None
    store.audit("register", username, {"role": "operator"}, username)
    return _start_session(user, response, request)


def login(username: str, password: str, response: Response, request: Request | None = None) -> dict:
    check_origin(request)
    username = _username(username)
    _rate_limit(request, "login", username)
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise HTTPException(401, "账号或密码不正确")
    ensure_users()
    user = store.get("users", username)
    # Perform an equally expensive hash for unknown identities to limit timing disclosure.
    encoded = (
        user.get("password_hash", "")
        if user
        else f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'00' * 32}${'00' * 32}"
    )
    if not _verify_password(password, encoded) or not user or not user.get("enabled", True):
        raise HTTPException(401, "账号或密码不正确")
    store.audit("login", username, "用户登录", username)
    return _start_session(user, response, request)


async def get_current_user(request: Request) -> dict:
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
    token = request.cookies.get("workbench_session", "")
    record = store.get("sessions", hashlib.sha256(token.encode()).hexdigest()) if token else None
    try:
        valid = record and datetime.fromisoformat(record["expires_at"]) > datetime.now(UTC)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise HTTPException(401, "请先登录")
    await asyncio.to_thread(ensure_users)
    user = store.get("users", record.get("username", ""))
    if not user or not user.get("enabled", True):
        raise HTTPException(401, "账号不可用，请重新登录")
    return _public_user(user)


def require_roles(*roles: str):
    async def dependency(request: Request):
        user = await get_current_user(request)
        if user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(403, "当前身份没有执行该操作的权限")
        return user

    return dependency


def logout(request: Request, response: Response) -> dict:
    check_origin(request)
    token = request.cookies.get("workbench_session", "")
    if token:
        store.delete("sessions", hashlib.sha256(token.encode()).hexdigest())
    response.delete_cookie("workbench_session", path="/")
    return {"ok": True}
