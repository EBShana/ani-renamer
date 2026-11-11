"""三页清单：扫描 → 匹配 → 预览 的条目分区。

“条目”是用户眼里的一个可勾选单位：
- 视频 + 同名字幕 = 一个条目（字幕强制跟随视频，不能单独取消）；
- 散落的音频 / 图片 / 压缩包等文件各自一个条目；
- 素材文件夹整夹算一个条目，内部文件不单独列。

条目在扫描时生成并保存在扫描会话里；页面之间是“移动”，不会复制。
执行成功的条目移到 done，失败 / 跳过 / 冲突的留在 preview 并带上原因。
"""

from __future__ import annotations

import os
from typing import Any

STAGES = ("scan", "match", "preview", "done")
STAGE_LABELS = {"scan": "扫描", "match": "匹配", "preview": "预览", "done": "已完成"}


def _norm(path: object) -> str:
    return os.path.normpath(str(path or "")).replace("\\", "/")


def _rel_parts(rel: str) -> list[str]:
    return [part for part in rel.replace("\\", "/").split("/") if part not in ("", ".")]


def _first_segment(rel: str) -> str:
    parts = _rel_parts(rel)
    return parts[0] if parts else ""


def _work_of(row: dict) -> tuple[str, str, int | None]:
    identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
    work_id = str(identify.get("work_id") or "").strip()
    title = str(
        identify.get("title")
        or (row.get("fields") or {}).get("series")
        or row.get("title")
        or ""
    ).strip()
    year = identify.get("year")
    return work_id, title, year


def _entry(
    entry_id: str,
    row: dict,
    category: str,
    file_ids: list[str],
    work_id: str,
    title: str,
    year: int | None,
    is_folder: bool = False,
) -> dict[str, Any]:
    rel = str(row.get("rel_path") or "")
    group_id = str(row.get("group_id") or "")
    segment = _first_segment(rel)
    work_key = work_id or (f"folder:{group_id}:{segment}" if segment else f"folder:{group_id}")
    return {
        "entry_id": entry_id,
        "stage": "scan",
        "group_id": group_id,
        "work_id": work_id or None,
        "work_key": work_key,
        "title": title or str(row.get("filename") or ""),
        "year": year,
        "kind": row.get("kind"),
        "category": category,
        "file_ids": file_ids,
        "filename": str(row.get("filename") or ""),
        "rel_path": rel,
        "parent": str(row.get("parent") or ""),
        "season": row.get("season"),
        "episode": row.get("episode"),
        "is_folder": is_folder,
        "needs_assign": bool(category == "video" and not work_id),
        "folder_key": f"{group_id}:{rel}" if is_folder else None,
        "result": None,
    }


def build_entries(scan: dict) -> list[dict]:
    files: list[dict] = list(scan.get("files") or [])
    video_rows = [row for row in files if str(row.get("id") or "") and row.get("category") == "video"]

    # 字幕归属：同一目录 + 同一目录组下，最长前缀匹配的视频
    videos_by_parent: dict[tuple[str, str], list[dict]] = {}
    for row in video_rows:
        key = (str(row.get("group_id") or ""), str(row.get("parent") or ""))
        videos_by_parent.setdefault(key, []).append(row)

    subtitle_owner: dict[str, str] = {}
    for row in files:
        if row.get("category") != "subtitle" or not row.get("id"):
            continue
        gid = str(row.get("group_id") or "")
        stem = str((row.get("fields") or {}).get("original_stem") or "")
        best: tuple[int, dict] | None = None
        for video in videos_by_parent.get((gid, str(row.get("parent") or "")), []):
            video_stem = str((video.get("fields") or {}).get("original_stem") or "")
            if not video_stem or not stem.startswith(video_stem):
                continue
            if best is None or len(video_stem) > best[0]:
                best = (len(video_stem), video)
        if best is not None:
            subtitle_owner[str(row["id"])] = str(best[1]["id"])

    # 同一目录里视频的作品信息，供散落素材 / 文件夹跟随
    sibling_work: dict[tuple[str, str], tuple[str, str, int | None]] = {}
    segment_work: dict[tuple[str, str], tuple[str, str, int | None]] = {}
    for row in video_rows:
        work = _work_of(row)
        if not work[0]:
            continue
        gid = str(row.get("group_id") or "")
        parent_key = (gid, str(row.get("parent") or ""))
        sibling_work.setdefault(parent_key, work)
        seg = _first_segment(str(row.get("rel_path") or ""))
        if seg:
            segment_work.setdefault((gid, seg), work)

    entries: list[dict] = []
    attached: set[str] = set()
    for row in video_rows:
        work_id, title, year = _work_of(row)
        file_ids = [str(row["id"])]
        for subtitle_id, video_id in subtitle_owner.items():
            if video_id == str(row["id"]):
                file_ids.append(subtitle_id)
                attached.add(subtitle_id)
        entries.append(_entry(f"f:{row['id']}", row, "video", file_ids, work_id, title, year))

    for row in files:
        if row.get("category") == "video" or not row.get("id"):
            continue
        rid = str(row["id"])
        if rid in attached:
            continue
        gid = str(row.get("group_id") or "")
        work = sibling_work.get((gid, str(row.get("parent") or "")))
        if not work:
            work = segment_work.get((gid, _first_segment(str(row.get("rel_path") or ""))))
        work_id, title, year = work if work else ("", "", None)
        entries.append(
            _entry(
                f"f:{rid}",
                row,
                str(row.get("category") or "other"),
                [rid],
                work_id,
                title,
                year,
            )
        )

    # 素材文件夹：整夹一个条目，只保留最外层
    ignored = [item for item in (scan.get("ignored_dirs") or []) if isinstance(item, dict)]
    ignored_keys = [
        (str(item.get("group_id") or ""), str(item.get("path") or "").strip("/")) for item in ignored
    ]
    seen: set[str] = set()
    for item in ignored:
        gid = str(item.get("group_id") or "")
        rel = str(item.get("path") or "").strip("/")
        if not rel:
            continue
        folder_key = f"{gid}:{rel}"
        if folder_key in seen:
            continue
        seen.add(folder_key)
        if any(
            other_gid == gid and other_rel and rel.startswith(other_rel + "/")
            for other_gid, other_rel in ignored_keys
        ):
            continue
        decision = (scan.get("special_decisions") or {}).get(folder_key)
        if isinstance(decision, dict) and decision.get("title"):
            work = (
                str(decision.get("work_id") or ""),
                str(decision.get("title") or ""),
                decision.get("year") if isinstance(decision.get("year"), int) else None,
            )
        else:
            work = segment_work.get((gid, _first_segment(rel)))
        work_id, title, year = work if work else ("", "", None)
        row = {
            "rel_path": rel,
            "group_id": gid,
            "filename": rel.split("/")[-1],
            "parent": "",
            "kind": "asset",
            "category": "folder",
        }
        entry = _entry(f"d:{folder_key}", row, "folder", [], work_id, title, year, True)
        entry["folder_key"] = folder_key
        entries.append(entry)

    return entries


def refresh_queue(scan: dict) -> list[dict]:
    """重建条目，但保留原有的阶段与执行结果（识别 / 匹配改动后调用）。"""
    previous = {
        str(item.get("entry_id")): item
        for item in ((scan.get("queue") or {}).get("entries") or [])
        if isinstance(item, dict)
    }
    entries = build_entries(scan)
    for entry in entries:
        old = previous.get(entry["entry_id"])
        if old:
            entry["stage"] = old.get("stage") or "scan"
            entry["result"] = old.get("result")
    scan["queue"] = {"entries": entries}
    return entries


def init_queue(scan: dict) -> None:
    if "queue" not in scan:
        refresh_queue(scan)


def get_entries(scan: dict) -> list[dict]:
    if "queue" not in scan:
        refresh_queue(scan)
    return list((scan.get("queue") or {}).get("entries") or [])


def counts(scan: dict) -> dict[str, int]:
    result = {stage: 0 for stage in STAGES}
    for entry in get_entries(scan):
        stage = str(entry.get("stage") or "scan")
        result[stage] = result.get(stage, 0) + 1
    return result


def move(scan: dict, entry_ids: list[str], to_stage: str) -> int:
    """把勾选的条目移动到下一页；只允许向前移动，已完成的不会再参与。"""
    if to_stage not in ("match", "preview"):
        raise ValueError("只能移动到「匹配」或「预览」")
    allowed_from = {"match": ("scan",), "preview": ("scan", "match")}[to_stage]
    wanted = {str(item) for item in entry_ids if str(item)}
    moved = 0
    for entry in get_entries(scan):
        if entry["entry_id"] not in wanted:
            continue
        if entry.get("stage") not in allowed_from:
            continue
        entry["stage"] = to_stage
        entry["result"] = None
        moved += 1
    return moved


def preview_file_ids(scan: dict) -> list[str]:
    ids: list[str] = []
    for entry in get_entries(scan):
        if entry.get("stage") != "preview" or entry.get("is_folder"):
            continue
        for file_id in entry.get("file_ids") or []:
            ids.append(str(file_id))
    return ids


def preview_folder_keys(scan: dict) -> list[str]:
    return [
        str(entry.get("folder_key") or "")
        for entry in get_entries(scan)
        if entry.get("stage") == "preview" and entry.get("is_folder")
    ]


def _entry_items(entry: dict, items: list[dict]) -> list[dict]:
    group_id = str(entry.get("group_id") or "")
    if entry.get("is_folder"):
        # 文件夹条目的原始路径由扫描时的源目录推出，这里按组 + 相对路径匹配
        rel = str(entry.get("rel_path") or "")
        return [
            item
            for item in items
            if str(item.get("group_id") or "") == group_id
            and _folder_path_matches(rel, str(item.get("original_path") or ""))
        ]
    ids = {str(file_id) for file_id in (entry.get("file_ids") or [])}
    return [item for item in items if str(item.get("file_id") or "") in ids]


def _folder_path_matches(rel: str, original_path: str) -> bool:
    normalized = _norm(original_path)
    target = "/" + rel.strip("/") + "/"
    return normalized.lower().find(target.lower()) >= 0


def apply_operation_result(scan: dict, operation: dict) -> None:
    """执行结束后回写清单：成功的条目移到 done，其余留在 preview 并记原因。"""
    items = list(operation.get("items") or [])
    if not items:
        return
    for entry in get_entries(scan):
        if entry.get("stage") != "preview":
            continue
        relevant = _entry_items(entry, items)
        if not relevant:
            continue
        statuses = [str(item.get("status") or "") for item in relevant]
        if all(status in ("done", "merged") for status in statuses):
            entry["stage"] = "done"
            entry["result"] = {"status": "done", "message": "执行成功，已移出清单"}
            continue
        failed = [item for item in relevant if str(item.get("status")) == "failed"]
        conflicts = [item for item in relevant if str(item.get("status")) == "conflict"]
        skipped = [item for item in relevant if str(item.get("status")) == "skipped"]
        if failed:
            status = "failed"
        elif conflicts:
            status = "conflict"
        else:
            status = "skipped"
        messages: list[str] = []
        for item in failed + conflicts + skipped:
            text = str(item.get("message") or "").strip()
            if text and text not in messages:
                messages.append(text)
        entry["result"] = {
            "status": status,
            "message": "；".join(messages) or "这一步没有完成，请查看执行记录",
        }


def overview(scan: dict) -> dict:
    entries = get_entries(scan)
    return {
        "entries": entries,
        "counts": counts(scan),
        "groups": scan.get("groups") or [],
        "total": len(entries),
    }
