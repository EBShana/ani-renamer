"""把匹配到同一部 TMDB 作品的作品组合并 / 撤销合并。

合并只改视频行上的 work_id 与标题，不重建文件、不改路径；
每行已有的季号字段原样保留，因此合并不会丢掉季号选择。
"""

from __future__ import annotations

import copy
import uuid

from .matcher import build_groups, find_group
from .queue import refresh_queue
from .scan_store import save_scan


def season_state(scan: dict) -> dict:
    state = scan.get("season_state")
    if not isinstance(state, dict):
        state = {}
        scan["season_state"] = state
    return state


def merge_same_work(scan: dict) -> list[dict]:
    """把 tmdb_id + media_type 都相同的作品组合并成一个组。"""
    groups = build_groups(scan.get("files") or [])
    state = season_state(scan)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for group in groups:
        match = group.get("match") or {}
        tmdb_id = str(match.get("tmdb_id") or "").strip()
        if not tmdb_id:
            continue
        media_type = str(match.get("media_type") or "").strip()
        buckets.setdefault((tmdb_id, media_type), []).append(group)

    merged: list[dict] = []
    for (tmdb_id, media_type), members in buckets.items():
        if len(members) < 2:
            continue

        def rank(group: dict) -> tuple[int, int, str]:
            info = state.get(group["group_id"])
            manual = bool(
                isinstance(info, dict)
                and str(info.get("mode") or "") == "manual"
                and isinstance(info.get("choice"), int)
            )
            return (1 if manual else 0, int(group.get("video_count") or 0), group["group_id"])

        members.sort(key=rank, reverse=True)
        primary = members[0]
        primary_id = str(primary["group_id"])
        primary_rows = find_group(scan.get("files") or [], primary_id)
        primary_title = next(
            (
                str((row.get("fields") or {}).get("series") or "").strip()
                for row in primary_rows
                if str((row.get("fields") or {}).get("series") or "").strip()
            ),
            str(primary.get("title") or ""),
        )
        record = {
            "id": f"merge-{uuid.uuid4().hex[:8]}",
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "primary": primary_id,
            "primary_title": primary_title,
            "primary_state": copy.deepcopy(state.get(primary_id)),
            "members": [],
        }
        for group in members[1:]:
            group_id = str(group["group_id"])
            rows = find_group(scan.get("files") or [], group_id)
            row_backup: dict[str, dict] = {}
            for row in rows:
                identify = row.get("identify")
                if not isinstance(identify, dict):
                    identify = {}
                    row["identify"] = identify
                row_backup[str(row.get("id"))] = {
                    "work_id": identify.get("work_id"),
                    "title": identify.get("title"),
                }
                identify["work_id"] = primary_id
                if primary_title:
                    identify["title"] = primary_title
            member_state = copy.deepcopy(state.pop(group_id, None))
            if member_state and not isinstance(state.get(primary_id), dict):
                # 主组自己没有季号状态时借用这一组的，避免合并后季号显示丢失
                state[primary_id] = copy.deepcopy(member_state)
            record["members"].append(
                {
                    "group_id": group_id,
                    "title": str(group.get("title") or ""),
                    "season_state": member_state,
                    "rows": row_backup,
                }
            )
        scan.setdefault("merges", []).append(record)
        merged.append(
            {
                "id": record["id"],
                "primary": primary_id,
                "title": primary_title,
                "merged_group_ids": [item["group_id"] for item in record["members"]],
                "count": len(members),
            }
        )

    if merged:
        refresh_queue(scan)
        save_scan(scan)
    return merged


def undo_merge(scan: dict, merge_id: str | None = None) -> str | None:
    """撤销最近一次合并；返回被撤销的合并 id，没有可撤销项时返回 None。"""
    merges = scan.get("merges") if isinstance(scan.get("merges"), list) else []
    if not merges:
        return None
    record = merges[-1]
    if merge_id and merge_id != record["id"]:
        return None
    state = season_state(scan)
    primary_id = str(record.get("primary") or "")
    state.pop(primary_id, None)
    if isinstance(record.get("primary_state"), dict):
        state[primary_id] = copy.deepcopy(record["primary_state"])
    rows_by_id = {str(row.get("id")): row for row in scan.get("files") or []}
    for member in record.get("members") or []:
        for file_id, backup in (member.get("rows") or {}).items():
            row = rows_by_id.get(file_id)
            if row is None:
                continue
            identify = row.get("identify")
            if not isinstance(identify, dict):
                identify = {}
                row["identify"] = identify
            identify["work_id"] = backup.get("work_id")
            identify["title"] = backup.get("title")
        if isinstance(member.get("season_state"), dict):
            state[str(member.get("group_id"))] = copy.deepcopy(member["season_state"])
    merges.pop()
    scan["merges"] = merges
    refresh_queue(scan)
    save_scan(scan)
    return str(record["id"])
