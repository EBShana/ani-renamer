"""执行与撤销接口：后台任务、进度、暂停 / 取消、断点续跑、历史与回滚。"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_media_root, load_settings
from ..core.executor import execute_rows, normalize_policy, undo_items
from ..core.operations import BATCH_SIZE, build_plan, create_operation, manager
from ..core.preview import KINDS, build_preview, load_preset
from ..core.scan_store import get_scan
from ..store import (
    active_operation,
    get_operation,
    get_operation_progress,
    list_groups,
    list_operations,
    mark_operation_status,
    operation_item_counts,
    reset_operation_items,
    save_operation,
    set_operation_batches,
    set_operation_status,
)

router = APIRouter()

STATUS_LABELS = {
    "queued": "排队中",
    "running": "执行中",
    "paused": "已暂停",
    "interrupted": "已中断",
    "cancelled": "已取消",
    "conflict": "待处理冲突",
    "done": "已完成",
    "partial": "部分完成",
    "undone": "已撤销",
}

UNDOABLE_STATUSES = ("done", "partial", "cancelled", "interrupted", "conflict")


class TemplateSpec(BaseModel):
    pattern: str = ""
    output_mode: str = "filename"


class ExecuteRequest(BaseModel):
    scan_id: str | None = None
    preset_id: str | None = None
    templates: dict[str, TemplateSpec] | None = None
    file_ids: list[str] | None = None
    subtitle_mode: str | None = None
    limit: int = 0
    dry_run: bool = False
    conflict_policy: str | None = None
    keep_empty_dirs: bool | None = None


def _counts(rows: list[dict]) -> dict[str, int]:
    counts = {"done": 0, "skipped": 0, "failed": 0, "conflict": 0, "planned": 0}
    for row in rows:
        status = row.get("status", "skipped")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _groups_of_operation(operation: dict) -> list[dict]:
    summary = operation.get("summary") or {}
    groups = summary.get("groups")
    if isinstance(groups, list) and groups:
        return groups
    root = str(get_media_root())
    return [{"id": "", "name": "媒体根目录", "source": root, "target": root}]


def _normalized(path: object) -> str:
    return os.path.normpath(str(path or "")).replace("\\", "/")


def _verify_groups(operation: dict) -> str | None:
    """续跑前逐个核对目录组快照；组被删除或改动就拒绝并说明是哪一组。"""
    snapshot = _groups_of_operation(operation)
    current = {str(group.get("id") or ""): group for group in list_groups()}
    for group in snapshot:
        group_id = str(group.get("id") or "")
        if not group_id:
            continue
        name = str(group.get("name") or group_id)
        latest = current.get(group_id)
        if latest is None:
            return f"目录组「{name}」已被删除，无法续跑。请先把它加回来，或重新扫描后执行。"
        if _normalized(latest.get("source")) != _normalized(group.get("source")) or _normalized(
            latest.get("target")
        ) != _normalized(group.get("target")):
            return f"目录组「{name}」的源目录或目标目录已改动，无法续跑。请恢复原设置或重新扫描后执行。"
    return None


def _busy_message() -> str:
    active = active_operation()
    if active is not None:
        label = STATUS_LABELS.get(str(active.get("status")), "处理中")
        return f"已有执行任务（{label}），请等它完成或取消后再开始新的任务。"
    if manager.running():
        return "已有执行任务正在进行，请等它完成或取消后再开始新的任务。"
    return ""


@router.post("/execute")
def execute(payload: ExecuteRequest) -> dict:
    # 设置页是唯一权威：预设只提供命名模板，目录组来自扫描快照
    settings = load_settings()
    preset = load_preset(payload.preset_id)
    if preset is None:
        raise HTTPException(status_code=500, detail="没有可用预设")

    scan_payload = get_scan(payload.scan_id) if payload.scan_id else None
    if scan_payload is None:
        raise HTTPException(status_code=400, detail="请先扫描真实文件后再执行（内置示例不能执行）")

    busy = _busy_message()
    if busy:
        raise HTTPException(status_code=409, detail=busy)

    templates = {
        kind: spec.model_dump() if hasattr(spec, "model_dump") else spec
        for kind, spec in (payload.templates or {}).items()
        if kind in KINDS
    }
    preview_data = build_preview(
        scan_payload,
        preset,
        settings,
        templates,
        payload.file_ids,
        payload.limit,
        payload.subtitle_mode,
    )

    groups = scan_payload.get("groups") or []
    media_root = get_media_root()
    policy = normalize_policy(
        payload.conflict_policy or settings.get("execution.conflict_policy")
    )
    keep_empty_dirs = (
        payload.keep_empty_dirs
        if payload.keep_empty_dirs is not None
        else bool(settings.get("execution.keep_empty_dirs", False))
    )

    if payload.dry_run:
        rows = execute_rows(
            preview_data["rows"], groups, media_root, policy, keep_empty_dirs, True
        )
        return {
            "preset_id": preset["id"],
            "preset_name": preset["name"],
            "dry_run": True,
            "conflict_policy": policy,
            "keep_empty_dirs": keep_empty_dirs,
            "counts": _counts(rows),
            "operation_id": None,
            "rows": rows,
        }

    plan = build_plan(preview_data["rows"])
    if not plan:
        return {
            "preset_id": preset["id"],
            "preset_name": preset["name"],
            "dry_run": False,
            "conflict_policy": policy,
            "keep_empty_dirs": keep_empty_dirs,
            "operation_id": None,
            "status": "done",
            "total": 0,
            "batch_count": 0,
            "message": "没有可执行的文件",
        }

    operation_id, batch_count = create_operation(
        scan_id=payload.scan_id,
        preset_id=preset["id"],
        preset_name=preset["name"],
        items=plan,
        groups=groups,
        policy=policy,
    )
    manager.start(operation_id, policy, keep_empty_dirs)
    if not manager.running():
        set_operation_status(operation_id, "interrupted")
        raise HTTPException(status_code=409, detail="启动执行任务失败，请稍后重试")
    return {
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "dry_run": False,
        "conflict_policy": policy,
        "keep_empty_dirs": keep_empty_dirs,
        "operation_id": operation_id,
        "status": "queued",
        "total": len(plan),
        "batch_count": batch_count,
    }


@router.get("/history/{operation_id}/progress")
def progress(operation_id: str) -> dict:
    data = get_operation_progress(operation_id)
    if data is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return data


@router.post("/history/{operation_id}/pause")
def pause(operation_id: str) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["status"] == "paused":
        return {"operation_id": operation_id, "status": "paused"}
    if operation["status"] != "running" or not manager.pause(operation_id):
        raise HTTPException(status_code=400, detail="这个任务当前没有在运行，无法暂停")
    return {"operation_id": operation_id, "status": "pausing", "message": "当前文件处理完就会停下"}


@router.post("/history/{operation_id}/continue")
def continue_paused(operation_id: str) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["status"] != "paused":
        raise HTTPException(status_code=400, detail="只有已暂停的任务可以继续")
    if manager.running():
        raise HTTPException(status_code=409, detail="已有执行任务正在进行，请等它完成或取消后再继续")
    summary = operation.get("summary") or {}
    policy = normalize_policy(summary.get("conflict_policy"))
    keep_empty_dirs = bool(load_settings().get("execution.keep_empty_dirs", False))
    remaining = operation_item_counts(operation_id)["planned"]
    set_operation_batches(
        operation_id, (remaining + BATCH_SIZE - 1) // BATCH_SIZE if remaining else 0
    )
    set_operation_status(operation_id, "queued")
    if not manager.start(operation_id, policy, keep_empty_dirs):
        set_operation_status(operation_id, "paused")
        raise HTTPException(status_code=409, detail="启动执行任务失败，请稍后重试")
    return {"operation_id": operation_id, "status": "queued"}


@router.post("/history/{operation_id}/cancel")
def cancel(operation_id: str) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["status"] == "cancelled":
        return {"operation_id": operation_id, "status": "cancelled"}
    if operation["status"] == "running" and manager.cancel(operation_id):
        return {"operation_id": operation_id, "status": "cancelling", "message": "当前文件处理完就会取消"}
    if operation["status"] in ("queued", "paused"):
        set_operation_status(operation_id, "cancelled")
        return {"operation_id": operation_id, "status": "cancelled"}
    raise HTTPException(status_code=400, detail="这个任务当前不能取消")


class ResumeRequest(BaseModel):
    conflict_policy: str | None = None
    keep_empty_dirs: bool | None = None


@router.post("/history/{operation_id}/resume")
def resume(operation_id: str, payload: ResumeRequest | None = None) -> dict:
    """断点续跑：只重试未完成的条目，已完成 / 已跳过的编号保持锁定。"""
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["kind"] != "execute":
        raise HTTPException(status_code=400, detail="只有执行记录可以续跑")
    if operation["status"] == "undone":
        raise HTTPException(status_code=400, detail="该记录已撤销，不能续跑")
    if operation["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="该任务已取消，不能再续跑")
    if operation["status"] not in ("interrupted", "partial"):
        raise HTTPException(status_code=400, detail="这个任务当前不需要续跑")

    busy = _busy_message()
    if busy:
        raise HTTPException(status_code=409, detail=busy)
    problem = _verify_groups(operation)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    reset = reset_operation_items(operation_id)
    if reset == 0:
        summary = operation.get("summary") or {}
        return {
            "operation_id": operation_id,
            "status": operation["status"],
            "message": "没有需要续跑的条目",
            "summary": summary,
        }

    summary = operation.get("summary") or {}
    policy = normalize_policy(
        (payload.conflict_policy if payload else None)
        or summary.get("conflict_policy")
        or load_settings().get("execution.conflict_policy")
    )
    keep_empty_dirs = (
        payload.keep_empty_dirs
        if payload and payload.keep_empty_dirs is not None
        else bool(load_settings().get("execution.keep_empty_dirs", False))
    )
    remaining = operation_item_counts(operation_id)["planned"]
    set_operation_batches(
        operation_id, (remaining + BATCH_SIZE - 1) // BATCH_SIZE if remaining else 0
    )
    set_operation_status(operation_id, "queued")
    if not manager.start(operation_id, policy, keep_empty_dirs):
        set_operation_status(operation_id, "interrupted")
        raise HTTPException(status_code=409, detail="启动续跑任务失败，请稍后重试")
    return {"operation_id": operation_id, "status": "queued", "reset": reset}


class UndoRequest(BaseModel):
    keep_empty_dirs: bool | None = None


@router.get("/history")
def history(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    return {
        "operations": list_operations(limit),
        "active_operation_id": (active_operation() or {}).get("id"),
    }


@router.get("/history/{operation_id}")
def history_detail(operation_id: str) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return operation


@router.post("/history/{operation_id}/undo")
def undo(operation_id: str, payload: UndoRequest | None = None) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["kind"] != "execute":
        raise HTTPException(status_code=400, detail="只有执行记录可以撤销")
    if operation["status"] in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务还在进行中，请先取消再撤销")
    if operation["status"] not in UNDOABLE_STATUSES:
        raise HTTPException(status_code=400, detail="该记录已撤销或不可撤销")

    settings = load_settings()
    keep_empty_dirs = (
        payload.keep_empty_dirs
        if payload and payload.keep_empty_dirs is not None
        else bool(settings.get("execution.keep_empty_dirs", False))
    )

    groups = _groups_of_operation(operation)
    results = undo_items(operation["items"], groups, get_media_root(), keep_empty_dirs)
    counts = _counts(results)

    undo_operation = None
    if counts["done"] > 0:
        undo_operation = save_operation(
            scan_id=operation.get("scan_id"),
            preset_id=operation.get("preset_id"),
            preset_name=operation.get("preset_name") or "",
            kind="undo",
            summary={
                "done": counts["done"],
                "skipped": counts["skipped"],
                "failed": counts["failed"],
                "source_operation": operation_id,
                "groups": groups,
            },
            items=results,
        )
        mark_operation_status(operation_id, "undone")

    return {
        "operation_id": operation_id,
        "undo_id": undo_operation["id"] if undo_operation else None,
        "counts": counts,
        "rows": results,
    }
