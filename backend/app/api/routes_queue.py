"""三页清单接口：读取条目、页面之间移动条目。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import queue
from ..core.scan_store import get_scan, save_scan
from ..store import ensure_run, update_run

router = APIRouter()


def _scan_or_404(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


class QueueRequest(BaseModel):
    scan_id: str


@router.post("/queue")
def read_queue(payload: QueueRequest) -> dict:
    return queue.overview(_scan_or_404(payload.scan_id))


class MoveRequest(BaseModel):
    scan_id: str
    entry_ids: list[str] = []
    to_stage: str


@router.post("/queue/move")
def move_entries(payload: MoveRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    try:
        moved = queue.move(scan, payload.entry_ids, payload.to_stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_scan(scan)
    if moved:
        run = ensure_run(payload.scan_id, scan.get("groups") or [])
        update_run(
            run["id"],
            stage=payload.to_stage,
            snapshot_patch={
                payload.to_stage: {
                    "from": payload.from_stage,
                    "moved": moved,
                }
            },
        )
    data = queue.overview(scan)
    data["moved"] = moved
    return data


class SelectionRequest(BaseModel):
    stage: str
    ids: list[str] = []


@router.put("/scans/{scan_id}/selections")
def save_selections(scan_id: str, payload: SelectionRequest) -> dict:
    """记住每一页的勾选状态，容器重启后可以恢复。"""
    if payload.stage not in queue.STAGES:
        raise HTTPException(status_code=400, detail=f"未知的页面：{payload.stage}")
    scan = _scan_or_404(scan_id)
    selections = scan.get("selections")
    if not isinstance(selections, dict):
        selections = {}
    selections[payload.stage] = [str(item) for item in payload.ids if item][:50000]
    scan["selections"] = selections
    save_scan(scan)
    return {"stage": payload.stage, "count": len(selections[payload.stage])}
