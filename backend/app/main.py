from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    routes_execute,
    routes_groups,
    routes_identify,
    routes_match,
    routes_meta,
    routes_presets,
    routes_scan,
)
from .config import BASE_DIR
from .store import init_db, mark_interrupted_operations

STATIC_DIR = BASE_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 容器重启后，内存里的后台任务已经消失：把未完成记录标成“已中断”，历史里可以续跑
    mark_interrupted_operations()
    yield


app = FastAPI(title="Anime Renamer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_meta.router, prefix="/api", tags=["meta"])
app.include_router(routes_groups.router, prefix="/api", tags=["groups"])
app.include_router(routes_scan.router, prefix="/api", tags=["scan"])
app.include_router(routes_identify.router, prefix="/api", tags=["identify"])
app.include_router(routes_presets.router, prefix="/api", tags=["presets"])
app.include_router(routes_match.router, prefix="/api", tags=["match"])
app.include_router(routes_execute.router, prefix="/api", tags=["execute"])

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
