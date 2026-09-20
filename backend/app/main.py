"""Local product server with a private, genuinely used AgentOS runtime."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

os.environ.setdefault("AGNO_TELEMETRY", "false")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    from agno.os import AgentOS

    from .seeds import seed_all

    seed_all()
    engine.recover_interrupted()
    # The registered Workflow is the same object engine._drive actually executes.
    # Never mount private_app: native model/registry execution must not bypass RBAC/budget.
    runtime = AgentOS(
        id="research-workbench-private",
        name="研究智能体平台内部运行时",
        workflows=[engine.runtime_workflow()],
        telemetry=False,
    )
    app.state.agent_os = runtime
    app.state.private_agentos_app = runtime.get_app()
    yield
    import asyncio

    tasks = list(engine.TASKS.values()) + list(engine.EVALUATION_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="研究智能体平台", version="0.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
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


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)[:1200]})


@app.exception_handler(KeyError)
async def key_error(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": "请求的记录不存在"})


@app.exception_handler(PermissionError)
async def permission_error(request: Request, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)[:1200]})


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    return JSONResponse(status_code=503, content={"detail": str(exc)[:1200]})


from .api import router

app.include_router(router)

frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "接口不存在"})
        candidate = (frontend_dist / path).resolve()
        if candidate.is_relative_to(frontend_dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
