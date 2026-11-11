from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_media_root, load_settings
from ..core.identify import apply_identification, apply_special_memory
from ..core.pattern_cache import auto_apply_scan
from ..core.preview import load_preset
from ..core.queue import init_queue, overview
from ..core.rescan import light_recheck
from ..core.scan_store import (
    build_samples,
    delete_scan,
    get_scan,
    list_scans,
    put_scan,
    save_scan,
)
from ..core.scanner import ScanError, scan_groups
from ..store import (
    get_group,
    latest_operation_for_scan,
    list_aliases,
    list_assignments,
    list_rules,
    list_special_memory,
)

router = APIRouter()


class ScanRequest(BaseModel):
    group_ids: list[str]


@router.post("/scans")
def create_scan(payload: ScanRequest) -> dict:
    # 设置页是唯一权威；目录组决定“从哪里扫、输出到哪里”
    settings = load_settings()
    rules = list_rules()
    groups = []
    for group_id in payload.group_ids:
        group = get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=f"目录组不存在：{group_id}")
        groups.append(group)
    if not groups:
        raise HTTPException(status_code=400, detail="请至少选择一个目录组")

    try:
        result = scan_groups(groups, get_media_root(), settings, rules)
    except ScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 第 3 步：扫描后立刻做“作品识别”，并套用用户记住的指派映射与别名
    apply_identification(result, list_assignments(), settings, list_aliases())
    # 记住过的特典目录归属直接复用，不用再次确认
    apply_special_memory(result, list_special_memory())
    # 第 7 步：识别完成后，再按「目录组 + 子目录」尝试缓存自动套用；
    # 缓存为空或设置里关闭自动套用时直接跳过，现有扫描结果不变。
    auto_apply_scan(result, load_preset(None), settings)
    init_queue(result)
    put_scan(result)
    return result


@router.get("/scans")
def read_scans() -> dict:
    """最近的扫描记录；容器重启后可以从这里恢复，也可以手动删除。"""
    return {"scans": list_scans()}


@router.get("/scans/{scan_id}")
def read_scan(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


@router.delete("/scans/{scan_id}")
def remove_scan(scan_id: str) -> dict:
    if not delete_scan(scan_id):
        raise HTTPException(status_code=404, detail="扫描记录不存在或已清理")
    return {"ok": True}


@router.post("/scans/{scan_id}/recheck")
def recheck_scan(scan_id: str) -> dict:
    """轻量核对：只看当前清单里的文件还在不在，不重新读取目录。"""
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    check = light_recheck(payload, latest_operation_for_scan(scan_id))
    # 用户已经主动核对过，之后不再提示“这是上次的扫描”
    payload["restored"] = False
    save_scan(payload)
    return {"check": check, "queue": overview(payload)}


class RescanRequest(BaseModel):
    group_ids: list[str] = []


@router.post("/scans/{scan_id}/rescan")
def rescan_scan(scan_id: str, payload: RescanRequest) -> dict:
    """完整重扫：重新读取目录，清单回到扫描页（等同于重新扫描一次）。"""
    old = get_scan(scan_id)
    if old is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    group_ids = payload.group_ids or [
        str(item.get("id") or "")
        for item in old.get("groups") or []
        if str(item.get("id") or "")
    ]
    groups = []
    missing: list[str] = []
    for group_id in group_ids:
        group = get_group(group_id)
        if group is None:
            missing.append(group_id)
        else:
            groups.append(group)
    if missing:
        raise HTTPException(
            status_code=400,
            detail="这些目录组已经不存在，可能被删除了：" + "、".join(missing),
        )
    if not groups:
        raise HTTPException(status_code=400, detail="没有可以重扫的目录组")

    settings = load_settings()
    try:
        result = scan_groups(groups, get_media_root(), settings, list_rules())
    except ScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    apply_identification(result, list_assignments(), settings, list_aliases())
    apply_special_memory(result, list_special_memory())
    # 重扫同样要尝试缓存自动套用，规则与首次扫描一致
    auto_apply_scan(result, load_preset(None), settings)
    init_queue(result)
    put_scan(result)
    return result


@router.get("/samples")
def samples() -> dict:
    result = build_samples(load_settings(), list_rules(), list_assignments())
    # 示例也要放进扫描会话，未归属区、清单和预览才能读取
    init_queue(result)
    put_scan(result)
    return result
