"""执行与撤销接口：后台任务、进度、暂停 / 取消、断点续跑、历史与回滚。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_media_root, load_settings
from ..core import queue
from ..core.executor import (
    cleanup_written_markers,
    execute_rows,
    find_existing,
    normalize_policy,
    suffix_target,
    undo_items,
    validate_conflict_choices,
)
from ..core.operations import (
    build_plan,
    configured_batch_size,
    create_operation,
    manager,
    refresh_summary,
)
from ..core.preview import KINDS, build_preview, load_preset
from ..core.preflight import check_execution
from ..core.rescan import light_recheck
from ..core.scan_store import get_scan, save_scan
from ..store import (
    active_operation,
    get_operation,
    get_operation_progress,
    insert_operation_items,
    list_groups,
    list_operations,
    mark_operation_status,
    operation_conflict_items,
    operation_item_counts,
    reset_operation_items,
    save_operation,
    set_operation_batches,
    set_operation_status,
    update_operation_item,
    update_operation_summary,
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
    folder_ids: list[str] | None = None
    subtitle_mode: str | None = None
    limit: int = 0
    dry_run: bool = False
    conflict_policy: str | None = None
    keep_empty_dirs: bool | None = None
    # 预检发现风险时，前端让用户确认后再带这个标记重发一次
    confirm_risks: bool = False


def _counts(rows: list[dict]) -> dict[str, int]:
    counts = {"done": 0, "skipped": 0, "failed": 0, "conflict": 0, "planned": 0, "merged": 0}
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
    if scan_payload.get("queue"):
        # 第 5 步：只执行当前「预览」清单里的条目，前端传什么就只跑什么
        allowed_files = set(queue.preview_file_ids(scan_payload))
        allowed_folders = set(queue.preview_folder_keys(scan_payload))
        payload.file_ids = (
            sorted(allowed_files)
            if payload.file_ids is None
            else [item for item in payload.file_ids if item in allowed_files]
        )
        payload.folder_ids = (
            sorted(allowed_folders)
            if payload.folder_ids is None
            else [item for item in payload.folder_ids if item in allowed_folders]
        )

    preview_data = build_preview(
        scan_payload,
        preset,
        settings,
        templates,
        payload.file_ids,
        payload.limit,
        payload.subtitle_mode,
        payload.folder_ids,
    )

    groups = scan_payload.get("groups") or []
    media_root = get_media_root()
    # 第 6 步：第一轮执行一律不覆盖，遇到目标已存在就标记“冲突，待处理”继续往下跑；
    # 全部批次结束后由用户在冲突面板里统一选择处理方式。
    policy = "skip"
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

    # 执行前风控预检：有风险先让用户选择继续或取消，不擅自中止。
    risks = check_execution(plan, groups, media_root)
    if risks and not payload.confirm_risks:
        return {
            "preset_id": preset["id"],
            "preset_name": preset["name"],
            "dry_run": False,
            "conflict_policy": policy,
            "keep_empty_dirs": keep_empty_dirs,
            "operation_id": None,
            "needs_confirmation": True,
            "total": len(plan),
            "batch_count": 0,
            "risks": risks,
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
    batch_size = configured_batch_size()
    set_operation_batches(
        operation_id, (remaining + batch_size - 1) // batch_size if remaining else 0
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
        refresh_summary(operation_id)
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
    batch_size = configured_batch_size()
    set_operation_batches(
        operation_id, (remaining + batch_size - 1) // batch_size if remaining else 0
    )
    set_operation_status(operation_id, "queued")
    if not manager.start(operation_id, policy, keep_empty_dirs):
        set_operation_status(operation_id, "interrupted")
        raise HTTPException(status_code=409, detail="启动续跑任务失败，请稍后重试")
    return {"operation_id": operation_id, "status": "queued", "reset": reset}


class ResolveChoice(BaseModel):
    seq: int
    action: str


class ResolveRequest(BaseModel):
    choices: list[ResolveChoice] = []
    keep_empty_dirs: bool | None = None
    confirm_overwrite: bool = False


MERGE_MODES = {"merge-skip": "skip", "merge-overwrite": "overwrite", "merge-suffix": "suffix"}


def _conflict_view(operation: dict) -> dict:
    """冲突面板数据：按目录组分区，文件冲突与文件夹冲突分开。"""
    items = operation_conflict_items(operation["id"])
    snapshot = _groups_of_operation(operation)
    groups = [
        {"id": str(group.get("id") or ""), "name": str(group.get("name") or group.get("id") or "")}
        for group in snapshot
    ]
    files = [item for item in items if item.get("category") != "folder"]
    folders = [item for item in items if item.get("category") == "folder"]
    return {
        "operation_id": operation["id"],
        "status": operation["status"],
        "groups": groups,
        "files": files,
        "folders": folders,
        "total": len(items),
    }


@router.get("/history/{operation_id}/conflicts")
def conflicts(operation_id: str) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return _conflict_view(operation)


def _empty_stats() -> dict[str, int]:
    return {
        "file_skip": 0,
        "file_overwrite": 0,
        "file_suffix": 0,
        "folder_skip": 0,
        "folder_overwrite": 0,
        "folder_suffix": 0,
        "merge_folders": 0,
        "merge_skip": 0,
        "merge_overwrite": 0,
        "merge_suffix": 0,
        "merged_files": 0,
    }


def _visible_files(folder: Path) -> list[Path]:
    """素材文件夹里的真实内容；扫描器留下的隐藏标记文件不参与移动。"""
    return sorted(
        path for path in folder.rglob("*") if path.is_file() and not path.name.startswith(".")
    )


def _resolve_file(operation_id: str, item: dict, action: str, stats: dict[str, int]) -> None:
    source = Path(str(item.get("original_path") or ""))
    target = Path(str(item.get("target_path") or ""))
    if not source.exists():
        update_operation_item(operation_id, item["seq"], "failed", str(target), "源文件不存在")
        return
    if action == "skip":
        update_operation_item(
            operation_id, item["seq"], "skipped", str(target), "按选择跳过", "skip"
        )
        stats["file_skip"] += 1
        return
    if action == "overwrite":
        update_operation_item(
            operation_id, item["seq"], "planned", str(target), "将覆盖目标（原内容永久删除）", "overwrite"
        )
        stats["file_overwrite"] += 1
        return
    # 生成副本：处理前重新核对目标是否还在
    final = suffix_target(target) if find_existing(target) is not None else target
    update_operation_item(
        operation_id, item["seq"], "planned", str(final), "目标已存在，将生成副本", "suffix"
    )
    stats["file_suffix"] += 1


def _resolve_folder(operation_id: str, item: dict, action: str, stats: dict[str, int]) -> None:
    source = Path(str(item.get("original_path") or ""))
    target = Path(str(item.get("target_path") or ""))
    if not source.is_dir():
        update_operation_item(operation_id, item["seq"], "failed", str(target), "源文件夹不存在")
        return

    if action in ("skip", "overwrite", "suffix"):
        if action == "skip":
            update_operation_item(
                operation_id,
                item["seq"],
                "skipped",
                str(target),
                "整体跳过，源文件夹留在原处",
                "skip",
            )
            stats["folder_skip"] += 1
            return
        if action == "overwrite":
            update_operation_item(
                operation_id,
                item["seq"],
                "planned",
                str(target),
                "将删除整个目标文件夹后整体覆盖",
                "overwrite",
            )
            stats["folder_overwrite"] += 1
            return
        final = suffix_target(target) if find_existing(target) is not None else target
        update_operation_item(
            operation_id, item["seq"], "planned", str(final), "将整体移动到副本文件夹", "suffix"
        )
        stats["folder_suffix"] += 1
        return

    # 合并：只搬内容，目标文件夹保留；同名项按用户选的子策略处理
    mode = MERGE_MODES[action]
    existing_target = find_existing(target)
    if existing_target is not None and not existing_target.is_dir():
        update_operation_item(operation_id, item["seq"], "failed", str(target), "目标是文件，无法合并")
        return
    files = _visible_files(source)
    children: list[dict] = []
    moved = 0
    kept = 0
    for file_path in files:
        try:
            relative = file_path.relative_to(source)
        except ValueError:
            continue
        child_target = target / relative
        if find_existing(child_target) is not None:
            if mode == "skip":
                children.append(
                    {
                        "group_id": item.get("group_id"),
                        "category": "folder",
                        "original_path": str(file_path),
                        "target_path": str(child_target),
                        "status": "skipped",
                        "message": "同名项已存在，按合并跳过留在源目录",
                        "choice": "merge-skip",
                    }
                )
                kept += 1
                stats["merge_skip"] += 1
                continue
            if mode == "overwrite":
                children.append(
                    {
                        "group_id": item.get("group_id"),
                        "category": "folder",
                        "original_path": str(file_path),
                        "target_path": str(child_target),
                        "status": "planned",
                        "message": "合并并覆盖：将删除同名目标文件",
                        "choice": "overwrite",
                    }
                )
                moved += 1
                stats["merge_overwrite"] += 1
                continue
            child_target = suffix_target(child_target)
            moved += 1
            stats["merge_suffix"] += 1
        else:
            moved += 1
        children.append(
            {
                "group_id": item.get("group_id"),
                "category": "folder",
                "original_path": str(file_path),
                "target_path": str(child_target),
                "status": "planned",
                "message": "",
            }
        )
    if children:
        insert_operation_items(operation_id, children)
    stats["merge_folders"] += 1
    stats["merged_files"] += moved
    message = f"已合并：{moved} 个文件移入"
    if kept:
        message += f"，{kept} 个同名项留在源目录"
    update_operation_item(operation_id, item["seq"], "merged", str(target), message, action)


@router.post("/history/{operation_id}/resolve")
def resolve(operation_id: str, payload: ResolveRequest) -> dict:
    """第二轮：只处理用户确认的冲突项，处理前重新核对目标是否还在。"""
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if operation["kind"] != "execute":
        raise HTTPException(status_code=400, detail="只有执行记录可以处理冲突")
    if operation["status"] in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务还在进行中，等它结束再处理冲突")
    if not payload.choices:
        raise HTTPException(status_code=400, detail="没有选择任何冲突的处理方式")
    busy = _busy_message()
    if busy:
        raise HTTPException(status_code=409, detail=busy)

    current = {int(item["seq"]): item for item in operation_conflict_items(operation_id)}
    if not current:
        raise HTTPException(status_code=400, detail="这个记录已经没有待处理的冲突")

    stats = _empty_stats()
    unknown: list[str] = []
    # 先逐条校验，再执行。覆盖 = 永久删除且撤销无法恢复，必须带显式确认标记，
    # 这样即使绕过界面直接调接口，也不会在没确认的情况下删目标内容。
    try:
        validate_conflict_choices(
            [choice.model_dump() for choice in payload.choices],
            current,
            payload.confirm_overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for choice in payload.choices:
        item = current.get(int(choice.seq))
        if item is None:
            unknown.append(str(choice.seq))
            continue
        if item.get("category") == "folder":
            _resolve_folder(operation_id, item, choice.action, stats)
        else:
            _resolve_file(operation_id, item, choice.action, stats)

    update_operation_summary(operation_id, {"resolution": stats})
    planned = operation_item_counts(operation_id)["planned"]
    keep_empty_dirs = (
        payload.keep_empty_dirs
        if payload.keep_empty_dirs is not None
        else bool(load_settings().get("execution.keep_empty_dirs", False))
    )
    remaining = operation_item_counts(operation_id)["planned"]
    batch_size = configured_batch_size()
    set_operation_batches(
        operation_id, (remaining + batch_size - 1) // batch_size if remaining else 0
    )
    set_operation_status(operation_id, "queued")
    if not manager.start(operation_id, "skip", keep_empty_dirs):
        set_operation_status(operation_id, "conflict")
        raise HTTPException(status_code=409, detail="启动冲突处理任务失败，请稍后重试")
    return {
        "operation_id": operation_id,
        "status": "queued",
        "planned": planned,
        "resolution": stats,
        "skipped_unknown": unknown,
    }


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

    # 撤销前自动做一次轻量核对：确认清单里的源文件 / 目标文件当前状态
    scan_id = str(operation.get("scan_id") or "")
    scan = get_scan(scan_id) if scan_id else None
    if scan is not None:
        light_recheck(scan, operation, update_entries=False)
        save_scan(scan)

    settings = load_settings()
    keep_empty_dirs = (
        payload.keep_empty_dirs
        if payload and payload.keep_empty_dirs is not None
        else bool(settings.get("execution.keep_empty_dirs", False))
    )

    groups = _groups_of_operation(operation)
    # 撤销前先清掉执行阶段写下的标记：文件夹整体移动时标记在目录里，
    # 若等移回去再删会跟着目录跑回源目录。
    cleanup_written_markers(
        [str(item) for item in (operation.get("summary") or {}).get("written_markers") or []],
        groups,
        get_media_root(),
    )
    results = undo_items(
        operation["items"],
        groups,
        get_media_root(),
        keep_empty_dirs,
        low_io=bool(load_settings().get("execution.network_drive_mode")),
    )
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
