from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import config_schema, get_media_root, load_settings, save_settings
from ..core.fields import field_payload
from ..core.metadata import test_connectivity
from ..scanner_helpers import is_under
from ..store import list_rules

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/fields")
def fields() -> dict:
    return field_payload()


@router.get("/config")
def read_config() -> dict:
    return {"values": load_settings(), "media_root": str(get_media_root())}


@router.get("/config/schema")
def read_config_schema() -> dict:
    return config_schema()


class ConfigPayload(BaseModel):
    values: dict


@router.put("/config")
def write_config(payload: ConfigPayload) -> dict:
    return {"values": save_settings(payload.values)}


class ProviderTestPayload(BaseModel):
    proxy_url: str | None = None
    tmdb_api_key: str | None = None


@router.post("/providers/test")
def test_provider(payload: ProviderTestPayload | None = None) -> dict:
    """测试 TMDB 连通性；不返回代理地址和密钥原文。"""
    settings = load_settings()
    proxy = (
        payload.proxy_url
        if payload is not None and payload.proxy_url is not None
        else str(settings.get("providers.proxy_url") or "")
    )
    key = (
        payload.tmdb_api_key
        if payload is not None and payload.tmdb_api_key is not None
        else str(settings.get("providers.tmdb_api_key") or "")
    )
    return test_connectivity(proxy, key)


@router.get("/rules")
def read_rules() -> dict:
    return {"rules": [rule.to_dict() for rule in list_rules()]}


@router.get("/browse")
def browse(path: str | None = Query(default=None)) -> dict:
    root = get_media_root()
    if not root.exists():
        return {
            "root": str(root),
            "path": str(root),
            "parent": None,
            "entries": [],
            "error": f"媒体根目录不存在：{root}",
        }
    current = root
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / path
        current = candidate.resolve()
        if not is_under(current, root):
            raise HTTPException(status_code=400, detail="路径不在媒体根目录内")
        if not current.exists():
            raise HTTPException(status_code=404, detail="目录不存在")
    entries = []
    try:
        for entry in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            entries.append({"name": entry.name, "path": str(entry)})
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"目录无权限：{exc}") from exc
    parent = str(current.parent) if current != root and is_under(current.parent, root) else None
    return {"root": str(root), "path": str(current), "parent": parent, "entries": entries}
