"""后台执行任务：分批跑完、进度、暂停 / 取消、中断续跑。

设计要点：
- 开工前整份计划和每一条目已经落库（见 routes_execute），这里只按批取“还没完成”的条目；
- 每完成一个动作立刻更新该条状态并落库；
- 同一时间只允许一个任务（排队中 / 执行中 / 已暂停都算占用）；
- 文件夹合并会先展开成“每个文件一行”，再逐条执行。
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..config import get_media_root
from ..store import (
    create_pending_operation,
    finalize_operation,
    get_operation,
    operation_done_items,
    operation_item_counts,
    pending_operation_items,
    set_operation_status,
    update_operation_item,
    update_operation_progress,
)
from .executor import build_group_map, cleanup_done_sources, execute_item

BATCH_SIZE = 100
ACTIVE_STATUSES = ("queued", "running", "paused")
RESUMABLE_STATUSES = ("interrupted", "partial")


def build_plan(rows: list[dict]) -> list[dict]:
    """把预览行展开成执行计划：文件夹细到每个文件一行，不能执行的行不写入计划。"""
    items: list[dict] = []
    for row in rows:
        if row.get("action") != "rename":
            continue
        target_text = str(row.get("new_path") or "")
        if not target_text:
            continue
        source = Path(str(row.get("original_path") or ""))
        target = Path(target_text)
        if row.get("category") == "folder" and source.is_dir():
            # 扫描器留下的 .ignore/.nomedia 等隐藏标记文件不算作品内容，不参与移动
            files = sorted(
                path
                for path in source.rglob("*")
                if path.is_file() and not path.name.startswith(".")
            )
            if not files:
                items.append(
                    {
                        "group_id": row.get("group_id"),
                        "file_id": row.get("file_id"),
                        "category": row.get("category"),
                        "original_path": str(source),
                        "target_path": "",
                        "status": "skipped",
                        "message": "文件夹是空的，无需移动",
                    }
                )
                continue
            for file_path in files:
                try:
                    relative = file_path.relative_to(source)
                except ValueError:
                    continue
                items.append(
                    {
                        "group_id": row.get("group_id"),
                        "file_id": row.get("file_id"),
                        "category": row.get("category"),
                        "original_path": str(file_path),
                        "target_path": str(target / relative),
                        "status": "planned",
                        "message": "",
                    }
                )
            continue
        conflict = row.get("status") == "error"
        items.append(
            {
                "group_id": row.get("group_id"),
                "file_id": row.get("file_id"),
                "category": row.get("category"),
                "original_path": str(source),
                "target_path": str(target),
                "status": "conflict" if conflict else "planned",
                "message": "多个文件映射到同一目标路径，需要先处理冲突" if conflict else "",
            }
        )
    return items


def create_operation(
    scan_id: str | None,
    preset_id: str | None,
    preset_name: str,
    items: list[dict],
    groups: list[dict],
    policy: str,
) -> tuple[str, int]:
    """先把整份执行计划写入数据库，返回（任务编号, 批次总数）。"""
    actionable = sum(1 for item in items if item.get("status") == "planned")
    batch_count = max(1, (actionable + BATCH_SIZE - 1) // BATCH_SIZE) if actionable else 0
    summary = {
        "conflict_policy": policy,
        "groups": groups,
        "total": len(items),
        "done": 0,
        "skipped": 0,
        "failed": 0,
        "conflict": sum(1 for item in items if item.get("status") == "conflict"),
    }
    operation_id = create_pending_operation(
        scan_id=scan_id,
        preset_id=preset_id,
        preset_name=preset_name,
        summary=summary,
        items=items,
        status="queued",
        batch_count=batch_count,
    )
    return operation_id, batch_count


class OperationManager:
    """单例后台任务管理器（单进程 Uvicorn）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._operation_id: str | None = None
        self._pause = threading.Event()
        self._cancel = threading.Event()

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def active_id(self) -> str | None:
        with self._lock:
            return self._operation_id if self.running() else None

    def start(self, operation_id: str, policy: str, keep_empty_dirs: bool) -> bool:
        with self._lock:
            if self.running():
                return False
            self._operation_id = operation_id
            self._pause.clear()
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(operation_id, policy, keep_empty_dirs),
                name=f"execute-{operation_id}",
                daemon=True,
            )
            self._thread.start()
            return True

    def pause(self, operation_id: str) -> bool:
        """请求暂停：当前这个文件处理完才停。"""
        with self._lock:
            if self._operation_id != operation_id or not self.running():
                return False
            self._pause.set()
            return True

    def cancel(self, operation_id: str) -> bool:
        """请求取消：当前这个文件处理完才停，已完成的不会自动回滚。"""
        with self._lock:
            if self._operation_id != operation_id or not self.running():
                return False
            self._cancel.set()
            return True

    def _summary(self, operation_id: str, policy: str, groups: list[dict]) -> dict:
        counts = operation_item_counts(operation_id)
        return {
            "conflict_policy": policy,
            "groups": groups,
            "total": counts["total"],
            "done": counts["done"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "conflict": counts["conflict"],
        }

    def _finish(self, operation_id: str, policy: str, groups: list[dict], keep_empty_dirs: bool) -> None:
        if not keep_empty_dirs:
            try:
                group_map = build_group_map(groups, get_media_root())
                cleanup_done_sources(operation_done_items(operation_id), group_map, get_media_root())
            except Exception:
                # 清理空目录失败不影响执行结果
                pass
        counts = operation_item_counts(operation_id)
        status = "conflict" if counts["conflict"] else ("partial" if counts["failed"] else "done")
        finalize_operation(operation_id, status, self._summary(operation_id, policy, groups))

    def _run(self, operation_id: str, policy: str, keep_empty_dirs: bool) -> None:
        operation = get_operation(operation_id)
        groups = list(((operation or {}).get("summary") or {}).get("groups") or [])
        group_map = build_group_map(groups, get_media_root())
        media_root = get_media_root()
        processed = operation_item_counts(operation_id)["processed"]
        batch_index = int((operation or {}).get("batch_index") or 0)

        try:
            set_operation_status(operation_id, "running")
            while True:
                if self._cancel.is_set():
                    set_operation_status(operation_id, "cancelled")
                    return
                if self._pause.is_set():
                    set_operation_status(operation_id, "paused")
                    return
                batch = pending_operation_items(operation_id)[:BATCH_SIZE]
                if not batch:
                    break
                batch_index += 1
                update_operation_progress(operation_id, processed, "", batch_index)
                for item in batch:
                    if self._cancel.is_set():
                        set_operation_status(operation_id, "cancelled")
                        return
                    update_operation_progress(
                        operation_id, processed, Path(item["original_path"]).name, batch_index
                    )
                    status, target, text = execute_item(item, group_map, media_root, policy)
                    update_operation_item(
                        operation_id,
                        int(item["seq"]),
                        status,
                        target or item.get("target_path", ""),
                        text,
                    )
                    processed = operation_item_counts(operation_id)["processed"]
                    update_operation_progress(
                        operation_id, processed, Path(item["original_path"]).name, batch_index
                    )
                    if self._pause.is_set():
                        set_operation_status(operation_id, "paused")
                        return
            self._finish(operation_id, policy, groups, keep_empty_dirs)
        except Exception as exc:  # 任务意外中断：保留现场，历史里可以续跑
            try:
                update_operation_progress(operation_id, processed, f"中断：{exc}", batch_index)
            finally:
                set_operation_status(operation_id, "interrupted")
        finally:
            with self._lock:
                self._thread = None
                self._operation_id = None
                self._pause.clear()
                self._cancel.clear()


manager = OperationManager()
