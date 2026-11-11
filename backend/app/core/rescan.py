"""重扫三档里的「轻量核对」：只核对当前清单里的文件还在不在，不重新读取目录。

用途：执行失败后、文件夹合并 / 覆盖后、取消后、撤销前，确认源文件是否还值得继续处理。
完整重扫仍然走 /api/scans，等同于重新扫描一遍。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .queue import get_entries


def _group_sources(scan: dict) -> dict[str, str]:
    return {
        str(item.get("id") or ""): str(item.get("source") or "")
        for item in scan.get("groups") or []
        if isinstance(item, dict)
    }


def _items_by_file(operation: dict | None) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in (operation or {}).get("items") or []:
        file_id = str(item.get("file_id") or "")
        if file_id:
            result[file_id] = item
    return result


def _is_moved(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("status") or "") not in ("done", "merged"):
        return False
    target = str(item.get("target_path") or "")
    return bool(target) and Path(target).exists()


def light_recheck(
    scan: dict, operation: dict | None = None, update_entries: bool = True
) -> dict:
    """逐条核对文件是否还在；已经移动到目标的条目标为完成，缺失的标为需要完整重扫。"""
    files = scan.get("files") or []
    sources = _group_sources(scan)
    items_by_file = _items_by_file(operation)

    stats = {"total": 0, "ok": 0, "moved": 0, "missing": 0}
    for row in files:
        if row.get("category") == "video" or str(row.get("id") or ""):
            stats["total"] += 1
        path = str(row.get("path") or "")
        item = items_by_file.get(str(row.get("id") or ""))
        if path and os.path.exists(path):
            row["check"] = {"state": "ok"}
            stats["ok"] += 1
        elif _is_moved(item):
            row["check"] = {
                "state": "moved",
                "target_path": str(item.get("target_path") or ""),
            }
            stats["moved"] += 1
        else:
            row["check"] = {"state": "missing"}
            stats["missing"] += 1

    entries_done = 0
    entries_problem = 0
    for entry in get_entries(scan):
        if not update_entries:
            break
        if entry.get("stage") == "done":
            continue
        if entry.get("is_folder"):
            gid = str(entry.get("group_id") or "")
            rel = str(entry.get("rel_path") or "")
            base = sources.get(gid)
            folder = Path(base) / rel if base and rel else None
            exists = bool(folder and folder.exists())
            folder_items = [
                item
                for item in (operation or {}).get("items") or []
                if str(item.get("group_id") or "") == gid
                and str(item.get("original_path") or "").startswith(str(folder))
            ]
            all_moved = bool(folder_items) and all(_is_moved(item) for item in folder_items)
            if not exists and all_moved:
                entry["stage"] = "done"
                entry["result"] = {"status": "done", "message": "轻量核对：文件夹已经移动到目标位置"}
                entries_done += 1
            elif not exists:
                entry["result"] = {
                    "status": "failed",
                    "message": "轻量核对：源文件夹已不存在，请用「完整重扫」更新清单",
                }
                entries_problem += 1
            continue

        states = []
        for file_id in entry.get("file_ids") or []:
            row = next(
                (item for item in files if str(item.get("id") or "") == str(file_id)), None
            )
            if row is not None:
                states.append(str((row.get("check") or {}).get("state") or ""))
        if not states:
            continue
        if all(state == "moved" for state in states):
            entry["stage"] = "done"
            entry["result"] = {"status": "done", "message": "轻量核对：已经移动到目标位置"}
            entries_done += 1
        elif any(state == "missing" for state in states):
            entry["result"] = {
                "status": "failed",
                "message": "轻量核对：源文件已不存在，请用「完整重扫」更新清单",
            }
            entries_problem += 1
        elif any(state == "moved" for state in states):
            entry["result"] = {
                "status": "warning",
                "message": "轻量核对：部分文件已经移动，请确认后再继续",
            }
            entries_problem += 1

    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "total": stats["total"],
        "ok": stats["ok"],
        "moved": stats["moved"],
        "missing": stats["missing"],
        "entries_done": entries_done,
        "entries_problem": entries_problem,
    }
    scan["last_check"] = summary
    return summary
