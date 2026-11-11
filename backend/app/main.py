from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    routes_ai,
    routes_auth,
    routes_auto,
    routes_execute,
    routes_groups,
    routes_identify,
    routes_match,
    routes_meta,
    routes_presets,
    routes_queue,
    routes_runs,
    routes_scan,
)
from .config import BASE_DIR
from .core import auth
from .core.operations import refresh_interrupted_summaries
from .store import init_db, mark_interrupted_operations, clear_run_locks

STATIC_DIR = BASE_DIR / "app" / "static"
logger = logging.getLogger("anime_renamer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 容器重启后，内存里的后台任务已经消失：把未完成记录标成“已中断”，历史里可以续跑
    mark_interrupted_operations()
    # 后台推进者已经随进程消失，清掉遗留租约，界面上不会一直显示“被占用”
    clear_run_locks()
    # 进程被杀时来不及刷新摘要，启动时补齐，避免历史页“结果”显示 0
    refresh_interrupted_summaries()
    yield


app = FastAPI(title="Anime Renamer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_login(request, call_next):
    """设置了访问密码后，除健康检查和登录接口外，所有 /api 接口都要已登录。"""
    path = request.url.path
    # 只有状态查询和登录接口豁免；设置/修改/关闭密码必须已登录才能调用
    exempt = (
        path == "/api/health"
        or path in ("/api/auth/status", "/api/auth/login")
        or not path.startswith("/api/")
        or request.method == "OPTIONS"
    )
    if not exempt and auth.password_enabled():
        if not auth.is_authenticated(request.cookies.get(auth.COOKIE_NAME)):
            return JSONResponse(
                status_code=401,
                content={"detail": "访问密码已开启，请先登录"},
            )
    return await call_next(request)


app.include_router(routes_auth.router, prefix="/api", tags=["auth"])
app.include_router(routes_meta.router, prefix="/api", tags=["meta"])
app.include_router(routes_groups.router, prefix="/api", tags=["groups"])
app.include_router(routes_scan.router, prefix="/api", tags=["scan"])
app.include_router(routes_queue.router, prefix="/api", tags=["queue"])
app.include_router(routes_identify.router, prefix="/api", tags=["identify"])
app.include_router(routes_ai.router, prefix="/api", tags=["ai"])
app.include_router(routes_presets.router, prefix="/api", tags=["presets"])
app.include_router(routes_match.router, prefix="/api", tags=["match"])
app.include_router(routes_execute.router, prefix="/api", tags=["execute"])
app.include_router(routes_runs.router, prefix="/api", tags=["runs"])
app.include_router(routes_auto.router, prefix="/api", tags=["auto"])


@app.exception_handler(Exception)
async def unhandled_error(request, exc: Exception) -> JSONResponse:
    """兜住未预料的异常：日志里留完整堆栈，界面只看到一句可读的中文提示。"""
    logger.exception("接口出错：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=400,
        content={
            "detail": (
                f"操作失败：服务内部出错了（{exc.__class__.__name__}）。"
                "可以再试一次；如果一直失败，请把容器日志发给开发者。"
            )
        },
    )


if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

else:

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "app": "Anime Renamer",
            "message": "前端尚未构建：开发时请使用 vite dev server，或执行 npm run build",
        }
