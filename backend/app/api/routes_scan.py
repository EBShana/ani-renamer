from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_media_root, load_settings
from ..core.identify import apply_identification
from ..core.scan_store import build_samples, get_scan, put_scan
from ..core.scanner import ScanError, scan_groups
from ..store import get_group, list_assignments, list_rules

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
    # 第 3 步：扫描后立刻做“作品识别”，并套用用户记住的指派映射
    apply_identification(result, list_assignments(), settings)
    put_scan(result)
    return result


@router.get("/scans/{scan_id}")
def read_scan(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


@router.get("/samples")
def samples() -> dict:
    result = build_samples(load_settings(), list_rules(), list_assignments())
    # 示例也要放进扫描会话，未归属区和预览才能读取
    put_scan(result)
    return result
