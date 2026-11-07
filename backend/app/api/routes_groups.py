"""目录组接口：多组「源目录 + 目标目录」的增删改查与边界校验。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_media_root
from ..core.groups import validate_groups
from ..store import (
    active_operation,
    create_group,
    delete_group,
    list_groups,
    update_group,
    validate_group_payload,
)

router = APIRouter()

LOCK_MESSAGE = "有执行任务正在进行或已暂停，完成后才能增删改目录组（避免组快照和实际文件对不上）。"


class GroupPayload(BaseModel):
    name: str = ""
    source: str = ""
    target: str = ""


def _locked() -> bool:
    return active_operation() is not None


@router.get("/groups")
def read_groups() -> dict:
    return {
        "groups": list_groups(),
        "media_root": str(get_media_root()),
        "locked": _locked(),
    }


@router.post("/groups")
def add_group(payload: GroupPayload) -> dict:
    if _locked():
        raise HTTPException(status_code=409, detail=LOCK_MESSAGE)
    errors = validate_group_payload(payload.model_dump())
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    return create_group(payload.model_dump())


@router.put("/groups/{group_id}")
def modify_group(group_id: str, payload: GroupPayload) -> dict:
    if _locked():
        raise HTTPException(status_code=409, detail=LOCK_MESSAGE)
    errors = validate_group_payload(payload.model_dump(), ignore_id=group_id)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    group = update_group(group_id, payload.model_dump())
    if group is None:
        raise HTTPException(status_code=404, detail="目录组不存在")
    return group


@router.delete("/groups/{group_id}")
def remove_group(group_id: str) -> dict:
    if _locked():
        raise HTTPException(status_code=409, detail=LOCK_MESSAGE)
    if not delete_group(group_id):
        raise HTTPException(status_code=404, detail="目录组不存在")
    return {"ok": True}


@router.get("/groups/check")
def check_groups() -> dict:
    """检查现有目录组是否仍然合法（比如目录被删了、组之间有重叠）。"""
    groups = list_groups()
    return {
        "groups": groups,
        "errors": validate_groups(groups, get_media_root()) if groups else [],
        "media_root": str(get_media_root()),
        "locked": _locked(),
    }
