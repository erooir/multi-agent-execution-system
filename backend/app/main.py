"""Hangzhi product API over a private, metered Agno runtime."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

os.environ["AGNO_TELEMETRY"] = "false"

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .platform.db import ConcurrentWrite
from .platform.service import DomainError


class RequestBodyLimitMiddleware:
    """Bound streamed bodies before JSON/multipart parsers allocate or spool them."""

    def __init__(self, app, max_bytes=12_000_000):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = -1
        if declared < 0 or declared > self.max_bytes:
            return await self.reject(scope, receive, send, 400 if declared < 0 else 413)
        messages, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                return await self.reject(scope, receive, send, 413)
            messages.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(messages)

        async def replay():
            return next(iterator, None) or await receive()

        return await self.app(scope, replay, send)

    @staticmethod
    async def reject(scope, receive, send, status):
        response = JSONResponse(
            status_code=status,
            content={
                "detail": {
                    "code": "invalid_body" if status == 400 else "input_too_large",
                    "message": "请求体不合法或超过12MB限制",
                }
            },
        )
        await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    from agno.os import AgentOS

    from .auth import ensure_users
    from .platform.runtime import runtime_workflow

    ensure_users()
    runtime = AgentOS(
        id="hangzhi-private-v2",
        name="航智智能体管理平台内部运行时",
        workflows=[runtime_workflow()],
        telemetry=False,
    )
    app.state.agent_os = runtime
    # Never mount this native application: product RBAC and budget are authoritative.
    app.state.private_agentos_app = runtime.get_app()
    yield


app = FastAPI(title="航智智能体管理平台", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=422, content={"detail": {"code": "invalid_input", "message": str(exc)[:500]}}
    )


@app.exception_handler(ConcurrentWrite)
async def concurrent_write(request: Request, exc: ConcurrentWrite):
    return JSONResponse(
        status_code=409, content={"detail": {"code": "concurrent_write", "message": str(exc)}}
    )


@app.exception_handler(PermissionError)
async def permission_error(request: Request, exc: PermissionError):
    return JSONResponse(
        status_code=403, content={"detail": {"code": "permission_denied", "message": str(exc)[:500]}}
    )


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    return JSONResponse(
        status_code=503, content={"detail": {"code": "service_unavailable", "message": str(exc)[:500]}}
    )


from .platform.api import identity, router

app.include_router(identity)
app.include_router(router)
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    if (frontend_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.startswith(("api/", "agents/", "teams/", "workflows/")):
            return JSONResponse(status_code=404, content={"detail": "接口不存在"})
        candidate = (frontend_dist / path).resolve()
        if candidate.is_relative_to(frontend_dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
