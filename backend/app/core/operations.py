"""后台执行任务：分批跑完、进度、暂停 / 取消、中断续跑。

设计要点：
- 开工前整份计划和每一条目已经落库（见 routes_execute），这里只按批取“还没完成”的条目；
- 每完成一个动作立刻更新该条状态并落库；
- 同一时间只允许一个任务（排队中 / 执行中 / 已暂停都算占用）；
- 文件夹合并会先展开成“每个文件一行”，再逐条执行。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ..config import get_media_root, load_settings
from .rescan import light_recheck
from .scan_store import get_scan, save_scan
from .queue import apply_operation_result
from ..store import (
    open_connection,
    create_pending_operation,
    finalize_operation,
    get_operation,
    operation_done_items,
    operation_item_counts,
    pending_operation_items,
    set_operation_status,
    list_operations,
    update_operation_item_progress,
    update_operation_progress,
    update_operation_summary,
)
from .executor import DirIndex, build_group_map, cleanup_done_sources, execute_item, find_existing
from .lexicon import special_dir_name_set

BATCH_SIZE = 100
ACTIVE_STATUSES = ("queued", "running", "paused")
RESUMABLE_STATUSES = ("interrupted", "partial")


def _batch_settings() -> tuple[int, int, int, int]:
    """（每批数量, 批间等待秒数, 重试次数, 重试间隔秒数）；用户可在设置页调整。"""
    settings = load_settings()

    def number(key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(settings.get(key))
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    return (
        number("execution.batch_size", BATCH_SIZE, 1, 1000),
        number("execution.batch_delay_seconds", 0, 0, 600),
        number("execution.retry_times", 0, 0, 5),
        number("execution.retry_backoff_seconds", 3, 0, 60),
    )


def configured_batch_size() -> int:
    """继续 / 续跑 / 冲突处理重新分批时，读设置页的批大小，避免和实际执行用的批大小不一致。"""
    return _batch_settings()[0]


def _write_move_markers(operation_id: str) -> None:
    """移动完成后再写 .ignore / .nomedia，避免在原特典目录里留下空标记文件。

    设置项选 B（特典视频进 Season 00 并改名）时不写标记。
    """
    settings = load_settings()
    if not bool(settings.get("parse.write_ignore_markers", True)):
        return
    if str(settings.get("execution.special_video_policy") or "root") != "root":
        return
    names = special_dir_name_set(settings.get("parse.special_dir_names") or "")
    markers = [
        item.strip()
        for item in str(settings.get("parse.ignore_marker_files") or ".ignore,.nomedia").split(",")
        if item.strip()
    ]
    written: set[str] = set()
    created: list[str] = []
    for item in operation_done_items(operation_id):
        target = Path(str(item.get("target_path") or ""))
        if not target.name:
            continue
        # 特典文件夹是整体搬过去的，标记要写在这个“搬过去之后的特典文件夹”里。
        # 从目标文件向上走，取最外层连续的“特典目录名”层级：
        # 例如 作品/EXTRA/CDs/x.zip -> 写在 作品/EXTRA；作品/Scans/x.jpg -> 写在 作品/Scans。
        # 文件夹整体移动（覆盖 / 冲突后生成副本）时目标本身就是最终文件夹，
        # 从目标自己开始判断，不能像普通文件那样从上一级开始。
        is_folder_move = item.get("category") == "folder" and target.is_dir()
        current = target if is_folder_move else target.parent
        folder = None
        while current != current.parent and current.name.lower() in names:
            folder = current
            current = current.parent
        if folder is None and is_folder_move:
            # “生成副本”会把特典文件夹改名成「EXTRA (1)」，名字不再等于特典目录名，
            # 但内容仍来自特典目录，按源文件夹名补写标记。
            origin = Path(str(item.get("original_path") or ""))
            if origin.name.lower() in names:
                folder = target
        if folder is None:
            continue
        if str(folder) in written or not folder.is_dir():
            continue
        written.add(str(folder))
        for name in markers:
            marker = folder / name
            if marker.exists():
                continue
            try:
                marker.write_text("", encoding="utf-8")
                created.append(str(marker))
            except OSError:
                pass
    if created:
        # 记下这次执行实际写出的标记，撤销时只清这些，避免误删用户原有标记。
        operation = get_operation(operation_id) or {}
        known = list((operation.get("summary") or {}).get("written_markers") or [])
        for text in created:
            if text not in known:
                known.append(text)
        update_operation_summary(operation_id, {"written_markers": known})


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
            if find_existing(target) is not None:
                # 第 6 步：素材文件夹整体撞名才算文件夹冲突，结束第一轮后统一在冲突面板处理
                items.append(
                    {
                        "group_id": row.get("group_id"),
                        "file_id": row.get("file_id"),
                        "category": "folder",
                        "original_path": str(source),
                        "target_path": str(target),
                        "status": "conflict",
                        "message": "目标文件夹已存在，需要选择整体处理或合并",
                    }
                )
                continue
            # 扫描器留下的 .ignore/.nomedia 等隐藏标记文件不算作品内容，不参与移动；
            # 选 A 时特典目录里的视频已经单独搬走，这里跳过它们，避免重复移动。
            extracted = {str(Path(item)) for item in row.get("extracted_paths") or []}
            files = sorted(
                path
                for path in source.rglob("*")
                if path.is_file()
                and not path.name.startswith(".")
                and str(path) not in extracted
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
        if row.get("status") == "error":
            # 多个文件算到同一个目标路径属于“命名方案问题”，不是目标已存在冲突；
            # 直接记为失败，提示用户调整模板，避免在冲突面板里误覆盖已移入的文件。
            items.append(
                {
                    "group_id": row.get("group_id"),
                    "file_id": row.get("file_id"),
                    "category": row.get("category"),
                    "original_path": str(source),
                    "target_path": str(target),
                    "status": "failed",
                    "message": "多个文件映射到同一目标路径，请调整命名模板后重新预览",
                }
            )
            continue
        items.append(
            {
                "group_id": row.get("group_id"),
                "file_id": row.get("file_id"),
                "category": row.get("category"),
                "original_path": str(source),
                "target_path": str(target),
                "status": "planned",
                "message": "",
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
    batch_size, _, _, _ = _batch_settings()
    actionable = sum(1 for item in items if item.get("status") == "planned")
    batch_count = max(1, (actionable + batch_size - 1) // batch_size) if actionable else 0
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


def build_summary(operation_id: str, policy: str, groups: list[dict]) -> dict:
    counts = operation_item_counts(operation_id)
    operation = get_operation(operation_id) or {}
    summary = dict(operation.get("summary") or {})
    summary.update(
        {
            "conflict_policy": policy,
            "groups": groups,
            "total": counts["total"],
            "done": counts["done"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "conflict": counts["conflict"],
            "merged": counts["merged"],
        }
    )
    # 冲突解决后统计“源目录还剩多少个文件”（合并时被跳过的同名项会留在源目录）
    items = operation.get("items") or []
    merged_roots = [
        Path(str(item.get("original_path") or ""))
        for item in items
        if item.get("status") == "merged"
    ]
    count_cache: dict[str, int] = {}

    def count_files(path: Path) -> int:
        # 同一次摘要计算里，合并目录会被多条明细反复统计；按路径缓存结果。
        key = str(path)
        cached = count_cache.get(key)
        if cached is not None:
            return cached
        if path.is_dir():
            total = sum(
                1
                for child in path.rglob("*")
                if child.is_file() and not child.name.startswith(".")
            )
            count_cache[key] = total
            return total
        return 1 if path.is_file() else 0

    def inside_merged(path: Path) -> bool:
        return any(path == root or root in path.parents for root in merged_roots)

    remaining = 0
    for item in items:
        choice = str(item.get("choice") or "")
        status = str(item.get("status") or "")
        if status == "merged":
            remaining += count_files(Path(str(item.get("original_path") or "")))
        elif status == "skipped" and choice in ("skip", "merge-skip"):
            source = Path(str(item.get("original_path") or ""))
            if inside_merged(source):
                continue
            remaining += count_files(source)
    summary["remaining_source"] = remaining
    return summary


def refresh_summary(operation_id: str) -> None:
    """暂停 / 取消 / 中断时也要刷新摘要，保证历史页的“结果”和明细一致。"""
    operation = get_operation(operation_id) or {}
    summary = operation.get("summary") or {}
    policy = str(summary.get("conflict_policy") or "skip")
    groups = list(summary.get("groups") or [])
    update_operation_summary(operation_id, build_summary(operation_id, policy, groups))


def refresh_interrupted_summaries() -> None:
    """容器重启后补齐“已中断”记录的摘要（进程被杀时来不及刷新）。"""
    for operation in list_operations(200):
        if str(operation.get("status") or "") != "interrupted":
            continue
        try:
            refresh_summary(str(operation.get("id") or ""))
        except Exception:
            pass


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

    def _needs_light_check(self, operation: dict | None, status: str, counts: dict) -> bool:
        """正常执行完不自动重扫；失败 / 冲突 / 合并 / 覆盖 / 取消才轻量核对。"""
        if status != "done" or counts.get("merged"):
            return True
        choices = {str(item.get("choice") or "") for item in (operation or {}).get("items") or []}
        return bool(choices & {"overwrite", "merge-skip", "merge-overwrite", "merge-suffix"})

    def _mark_cancelled(self, operation_id: str) -> None:
        set_operation_status(operation_id, "cancelled")
        refresh_summary(operation_id)
        operation = get_operation(operation_id)
        scan_id = str((operation or {}).get("scan_id") or "")
        scan = get_scan(scan_id) if scan_id else None
        if scan is not None:
            light_recheck(scan, operation or {})
            save_scan(scan)

    def _finish(self, operation_id: str, policy: str, groups: list[dict], keep_empty_dirs: bool) -> None:
        low_io = bool(load_settings().get("execution.network_drive_mode"))
        try:
            _write_move_markers(operation_id)
        except Exception:
            # 标记文件写失败不影响执行结果
            pass
        if not keep_empty_dirs:
            try:
                group_map = build_group_map(groups, get_media_root())
                cleanup_done_sources(
                    operation_done_items(operation_id), group_map, get_media_root(), low_io
                )
            except Exception:
                # 清理空目录失败不影响执行结果
                pass
        counts = operation_item_counts(operation_id)
        status = "conflict" if counts["conflict"] else ("partial" if counts["failed"] else "done")
        finalize_operation(operation_id, status, build_summary(operation_id, policy, groups))
        # 第 5 步：成功的条目移出预览清单，失败 / 跳过 / 冲突的留在原处并带上原因
        operation = get_operation(operation_id)
        scan_id = str((operation or {}).get("scan_id") or "")
        if scan_id:
            scan = get_scan(scan_id)
            if scan is not None:
                apply_operation_result(scan, operation or {})
                if self._needs_light_check(operation, status, counts):
                    light_recheck(scan, operation or {})
                save_scan(scan)

    def _run(self, operation_id: str, policy: str, keep_empty_dirs: bool) -> None:
        operation = get_operation(operation_id)
        groups = list(((operation or {}).get("summary") or {}).get("groups") or [])
        group_map = build_group_map(groups, get_media_root())
        media_root = get_media_root()
        processed = operation_item_counts(operation_id)["processed"]
        batch_index = int((operation or {}).get("batch_index") or 0)
        batch_size, batch_delay, retry_times, retry_backoff = _batch_settings()
        low_io = bool(load_settings().get("execution.network_drive_mode"))
        # 整个任务共用一个目录名单缓存，避免每个文件都重新列一遍目标目录；
        # 网盘模式下这个缓存还会跳过重复的 mkdir。
        index = DirIndex(low_io=low_io)
        # 整批写库共用一条连接：每个文件都新建连接（还要跑几条 PRAGMA）在 Windows
        # 挂载盘上实测要几十毫秒，是执行慢的最大一笔。
        write_conn = open_connection()

        try:
            set_operation_status(operation_id, "running")
            while True:
                if self._cancel.is_set():
                    self._mark_cancelled(operation_id)
                    return
                if self._pause.is_set():
                    set_operation_status(operation_id, "paused")
                    refresh_summary(operation_id)
                    return
                batch = pending_operation_items(operation_id)[:batch_size]
                if not batch:
                    break
                batch_index += 1
                update_operation_progress(operation_id, processed, "", batch_index)
                for item in batch:
                    if self._cancel.is_set():
                        self._mark_cancelled(operation_id)
                        return
                    status, target, text = _execute_with_retry(
                        item,
                        group_map,
                        media_root,
                        policy,
                        retry_times,
                        retry_backoff,
                        index,
                    )
                    processed += 1
                    # 条目状态 + 整体进度只提交一次；在 Windows 挂载盘上每次提交都要 fsync，
                    # 之前每个文件提交三次，是执行慢的主因之一。
                    update_operation_item_progress(
                        operation_id,
                        int(item["seq"]),
                        status,
                        target or item.get("target_path", ""),
                        text,
                        processed,
                        Path(item["original_path"]).name,
                        batch_index,
                        write_conn,
                    )
                    if self._pause.is_set():
                        set_operation_status(operation_id, "paused")
                        refresh_summary(operation_id)
                        return
                # 批与批之间等待：网盘场景可减少同时进行的请求量
                if batch_delay > 0 and pending_operation_items(operation_id):
                    time.sleep(batch_delay)
            write_conn.close()
            self._finish(operation_id, policy, groups, keep_empty_dirs)
        except Exception as exc:  # 任务意外中断：保留现场，历史里可以续跑
            try:
                update_operation_progress(operation_id, processed, f"中断：{exc}", batch_index)
            finally:
                set_operation_status(operation_id, "interrupted")
                refresh_summary(operation_id)
        finally:
            try:
                write_conn.close()
            except Exception:
                pass
            with self._lock:
                self._thread = None
                self._operation_id = None
                self._pause.clear()
                self._cancel.clear()


def _execute_with_retry(
    item: dict,
    group_map: dict,
    media_root: Path,
    policy: str,
    retry_times: int,
    retry_backoff: int,
    index: DirIndex | None = None,
) -> tuple[str, str, str]:
    """移动失败时按设置重试；文件名 / 路径类永久错误不重试。"""
    status, target, text = execute_item(item, group_map, media_root, policy, index)
    attempt = 0
    while status == "failed" and text.startswith("移动失败") and attempt < retry_times:
        attempt += 1
        if retry_backoff > 0:
            time.sleep(retry_backoff * attempt)
        status, target, text = execute_item(item, group_map, media_root, policy, index)
    return status, target, text


manager = OperationManager()
