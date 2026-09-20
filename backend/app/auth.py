"""Local demonstration accounts with server-side role enforcement."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request, Response
from .storage import store

USERS = {
    "admin": {"id": "admin", "username": "admin", "name": "平台管理员", "role": "admin"},
    "operator": {"id": "operator", "username": "operator", "name": "研究人员", "role": "operator"},
    "reviewer": {"id": "reviewer", "username": "reviewer", "name": "审核专家", "role": "reviewer"},
}


def login(username: str, password: str, response: Response) -> dict:
    expected = os.environ.get("WORKBENCH_DEMO_PASSWORD", "demo12345")
    if username not in USERS or not hmac.compare_digest(password.encode(), expected.encode()):
        raise HTTPException(401, "账号或密码不正确")
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    store.save("sessions", {"id": digest, "username": username, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()})
    response.set_cookie("workbench_session", token, httponly=True, samesite="strict", max_age=43200, path="/")
    store.audit("login", username, "本机演示登录", username)
    return {"user": USERS[username]}


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("workbench_session", "")
    record = store.get("sessions", hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not record or datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(401, "请先登录")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).hostname not in ("localhost", "127.0.0.1", "testserver"):
                raise HTTPException(403, "不允许跨站修改请求")
    return dict(USERS[record["username"]])


def require_roles(*roles: str):
    async def dependency(request: Request):
        user = await get_current_user(request)
        if user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(403, "当前身份没有执行该操作的权限")
        return user
    return dependency


def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get("workbench_session", "")
    if token:
        store.delete("sessions", hashlib.sha256(token.encode()).hexdigest())
    response.delete_cookie("workbench_session", path="/")
    return {"ok": True}
