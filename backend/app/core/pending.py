"""待人工清单：把分散在各处的“需要人处理”统一算出来，写进一张表。

清单只读扫描 / 执行记录，不改变任何识别、命名、执行行为。
处理完的问题会在下一次同步时自动关闭，不再挂在清单里。
"""

from __future__ import annotations

from typing import Any

from ..store import (
    ensure_run,
    list_aliases,
    list_pending_items,
    pending_counts,
    run_for_scan,
    sync_pending_items,
)
from .identify import build_overview, special_suggestions
from .matcher import build_groups


def _unassigned_items(scan: dict) -> list[dict[str, Any]]:
    overview = build_overview(scan)
    return [
        {
            "kind": "unassigned",
            "ref_type": "file",
            "ref_id": str(item.get("file_id") or ""),
            "title": str(item.get("filename") or ""),
            "reason": str(item.get("reason") or "文件名和目录名都没有足够信息"),
            "blocking": False,
            "meta": {
                "group_id": item.get("group_id"),
                "rel_path": item.get("rel_path"),
            },
        }
        for item in overview.get("unassigned") or []
        if item.get("file_id")
    ]


def _season_items(scan: dict) -> list[dict[str, Any]]:
    """多季作品还没选季号时列出来；单季自动确定的、已选过的不出现。"""
    state = scan.get("season_state") if isinstance(scan.get("season_state"), dict) else {}
    items: list[dict[str, Any]] = []
    for group in build_groups(scan.get("files") or []):
        info = state.get(group.get("group_id"))
        if not isinstance(info, dict) or not group.get("match"):
            continue
        official = [item for item in info.get("seasons") or [] if isinstance(item, dict)]
        if len(official) < 2 or info.get("choice"):
            continue
        items.append(
            {
                "kind": "season",
                "ref_type": "work",
                "ref_id": str(group.get("group_id") or ""),
                "title": str(group.get("title") or ""),
                "reason": "这部作品在 TMDB 上有多个季，需要你确认这一组文件属于第几季",
                "blocking": True,
                "meta": {
                    "seasons": [item.get("season_number") for item in official],
                },
            }
        )
    return items


def _special_items(scan: dict) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in special_suggestions(scan, list_aliases()):
        if entry.get("confirmed"):
            continue
        items.append(
            {
                "kind": "special",
                "ref_type": "folder",
                "ref_id": str(entry.get("folder_key") or ""),
                "title": str(entry.get("name") or ""),
                "reason": str(entry.get("reason") or "特典目录还没有确认归属"),
                "blocking": False,
                "meta": {
                    "group_id": entry.get("group_id"),
                    "rel_path": entry.get("rel_path"),
                },
            }
        )
    return [item for item in items if item["ref_id"]]


def _conflict_items(operation: dict | None) -> list[dict[str, Any]]:
    if not operation or operation.get("status") != "conflict":
        return []
    items: list[dict[str, Any]] = []
    for item in operation.get("items") or []:
        if item.get("status") != "conflict":
            continue
        items.append(
            {
                "kind": "conflict",
                "ref_type": "operation_item",
                "ref_id": f"{operation.get('id')}:{item.get('seq')}",
                "title": str(item.get("original_path") or ""),
                "reason": str(item.get("message") or "目标已存在，需要选择处理方式"),
                "blocking": True,
                "meta": {
                    "operation_id": operation.get("id"),
                    "seq": item.get("seq"),
                    "target_path": item.get("target_path"),
                },
            }
        )
    return items


def _preview_items(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    preview = (snapshot or {}).get("preview")
    if not isinstance(preview, dict):
        return []
    items: list[dict[str, Any]] = []
    for issue in preview.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        kind = "preview_error" if issue.get("status") == "error" else "preview_warning"
        ref_id = str(issue.get("ref_id") or "")
        if not ref_id:
            continue
        items.append(
            {
                "kind": kind,
                "ref_type": "preview_row",
                "ref_id": ref_id,
                "title": str(issue.get("title") or ""),
                "reason": str(issue.get("reason") or "预览时发现问题，请检查命名模板"),
                "blocking": kind == "preview_error",
                "meta": {
                    "original_path": issue.get("original_path"),
                    "target_path": issue.get("target_path"),
                },
            }
        )
    return items


def collect_pending(
    scan: dict,
    operation: dict | None = None,
    run_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """汇总当前所有需要人处理的事项。返回顺序按阻塞优先、来源分组。"""
    return (
        _conflict_items(operation)
        + _season_items(scan)
        + _preview_items(run_snapshot)
        + _unassigned_items(scan)
        + _special_items(scan)
    )


def sync_for_scan(scan: dict, operation: dict | None = None) -> dict[str, Any]:
    """确保运行存在，并按照最新扫描 / 执行状态刷新待人工清单。"""
    scan_id = str(scan.get("scan_id") or "")
    run = run_for_scan(scan_id) or ensure_run(scan_id, scan.get("groups"))
    if not run:
        return {"run": None, "items": [], "counts": {"open": 0, "resolved": 0, "blocking": 0}}
    items = collect_pending(scan, operation, run.get("snapshot"))
    open_items = sync_pending_items(run["id"], scan_id, items)
    return {
        "run": run,
        "items": open_items,
        "counts": pending_counts(run["id"]),
    }


def pending_view(scan: dict, operation: dict | None = None) -> dict[str, Any]:
    data = sync_for_scan(scan, operation)
    run = data.get("run") or {}
    return {
        "run": run,
        "items": data.get("items") or [],
        "counts": data.get("counts") or {"open": 0, "resolved": 0, "blocking": 0},
    }


def open_items(run_id: str) -> list[dict[str, Any]]:
    return list_pending_items(run_id)
