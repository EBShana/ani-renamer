"""重复集检测：同一作品、同一季、同一集出现多个文件。

用户已知集号重复可能来自三种情况，这里尽量区分并在提示里说清：
- multi_version：同集多版本（同一文件夹里有 v1/v2、不同分辨率等）；
- multi_source：同作品多来源（同一集出现在多个文件夹 / 多个目录组里）；
- parse_error：集号解析可能算错（同一文件夹里没有版本差异，却都解析成同一集）。

检测只做提示，不会自动删掉任何文件；处理方式由用户在预览页选择：
排除其中一些、分别保留（靠模板变量区分）、或把高版本视作修正版替换旧的。
"""

from __future__ import annotations

from datetime import datetime

from .episode_no import episode_value, number_key
from .lexicon import VERSION_MARK_RE


def _rel_parts(rel: object) -> list[str]:
    return [part for part in str(rel or "").replace("\\", "/").split("/") if part not in ("", ".")]


def _first_segment(rel: object) -> str:
    parts = _rel_parts(rel)
    return parts[0] if parts else ""


def _parent(rel: object) -> str:
    parts = _rel_parts(rel)
    return "/".join(parts[:-1])


def _season_of(row: dict) -> int:
    value = row.get("season")
    if isinstance(value, int) and value >= 0:
        return value
    text = str((row.get("fields") or {}).get("season") or "").strip()
    if text.isdigit():
        return int(text)
    return 1


def _episode_of(row: dict) -> int | float | None:
    return episode_value(row)


def _episode_key(value: int | float) -> str:
    """整数沿用两位补零（01），小数保留真实数值（24.5），两者不会撞在一起。"""
    key = number_key(value)
    return key if "." in key else f"{int(value):02d}"


def _version_of(row: dict) -> str:
    version = str((row.get("fields") or {}).get("version") or "").strip()
    if version:
        return version.lower().lstrip("v")
    stem = str((row.get("fields") or {}).get("original_stem") or "")
    match = VERSION_MARK_RE.search(stem)
    return match.group(1).lstrip("0") if match else ""


def _resolution_of(row: dict) -> str:
    return str((row.get("fields") or {}).get("resolution") or "").strip().lower()


def group_key(row: dict) -> str:
    ident = row.get("identify") if isinstance(row.get("identify"), dict) else {}
    work_id = str(ident.get("work_id") or "")
    episode = _episode_of(row)
    if not work_id or episode is None:
        return ""
    return f"{work_id}|S{_season_of(row):02d}E{_episode_key(episode)}"


def collect_groups(files: list[dict]) -> dict[str, list[dict]]:
    """按「作品 + 季 + 集」聚合视频文件；同一集至少两个文件才算重复。"""
    groups: dict[str, list[dict]] = {}
    for row in files:
        if row.get("category") != "video" or row.get("kind") != "episode":
            continue
        if row.get("collection"):
            # 合集文件不参与重复集判定
            continue
        ident = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        if ident.get("ignored"):
            continue
        key = group_key(row)
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    return {key: rows for key, rows in groups.items() if len(rows) >= 2}


def _classify(rows: list[dict]) -> tuple[str, str, str]:
    """返回（类型, 中文标签, 为什么这么判）。"""
    parents = {_parent(row.get("rel_path")) for row in rows}
    versions = {_version_of(row) for row in rows}
    resolutions = {_resolution_of(row) for row in rows}
    non_empty_versions = {value for value in versions if value}
    non_empty_resolutions = {value for value in resolutions if value}
    names = "、".join(str(row.get("filename") or "") for row in rows[:5])
    if len(parents) > 1:
        return (
            "multi_source",
            "同作品多来源",
            f"同一个集号出现在 {len(parents)} 个不同文件夹里（{names}），可能是同一部作品有多个来源目录",
        )
    if non_empty_versions or len(non_empty_resolutions) >= 2:
        marks = "、".join(
            sorted(non_empty_versions) + sorted(non_empty_resolutions)
        ) or "版本标记"
        return (
            "multi_version",
            "同集多版本",
            f"同一个文件夹里有多份同集文件（{marks}），通常是不同版本或不同清晰度",
        )
    return (
        "parse_error",
        "解析算错集号",
        f"同一个文件夹里「{names}」都被解析成同一集，可能是集号识别有误，请确认是否真的重复",
    )


def describe_groups(files: list[dict], decisions: dict | None = None) -> list[dict]:
    """生成给界面用的重复集列表（含用户的处理选择）。"""
    decisions = decisions if isinstance(decisions, dict) else {}
    result: list[dict] = []
    for key, rows in collect_groups(files).items():
        kind, label, reason = _classify(rows)
        decision = decisions.get(key)
        excluded = {
            str(item)
            for item in (decision or {}).get("exclude") or []
            if str(item)
        }
        files_info = []
        for row in rows:
            ident = row.get("identify") if isinstance(row.get("identify"), dict) else {}
            files_info.append(
                {
                    "file_id": str(row.get("id") or ""),
                    "filename": str(row.get("filename") or ""),
                    "rel_path": str(row.get("rel_path") or ""),
                    "group_id": str(row.get("group_id") or ""),
                    "version": _version_of(row),
                    "resolution": _resolution_of(row),
                    "excluded": str(row.get("id") or "") in excluded,
                    "reason": str(ident.get("reason") or ""),
                }
            )
        first = rows[0]
        ident = first.get("identify") if isinstance(first.get("identify"), dict) else {}
        result.append(
            {
                "key": key,
                "work_id": str(ident.get("work_id") or ""),
                "title": str(ident.get("title") or first.get("title") or ""),
                "year": ident.get("year") if isinstance(ident.get("year"), int) else None,
                "season": _season_of(first),
                "episode": _episode_of(first),
                "kind": kind,
                "kind_label": label,
                "reason": reason,
                "files": files_info,
                "decision": decision,
            }
        )
    result.sort(
        key=lambda item: (
            str(item.get("title") or ""),
            int(item.get("season") or 0),
            float(item.get("episode") or 0),
        )
    )
    return result


def apply_decision(
    scan: dict,
    key: str,
    action: str,
    file_ids: list[str] | None = None,
    target_file_id: str | None = None,
) -> dict:
    """记录用户对一组重复集的处理方式。

    - exclude：排除 file_ids 里勾选的文件；
    - keep：只保留 file_ids 里勾选的文件（常见于把 v2 修正版留下、把旧版排除）；
    - keep_all：全部保留，由用户在模板里加入可区分变量；
    - replace：用 target_file_id 这个文件替换同组的其他文件（修正版替换旧版）。
    """
    groups = collect_groups(scan.get("files") or [])
    rows = groups.get(key)
    if not rows:
        raise ValueError("这组重复集已经不存在，请重新扫描")
    all_ids = [str(row.get("id") or "") for row in rows]
    chosen = [str(item) for item in (file_ids or []) if str(item) in all_ids]

    if action == "exclude":
        if not chosen:
            raise ValueError("请至少勾选一个要排除的文件")
        excluded = chosen
    elif action == "keep":
        if not chosen:
            raise ValueError("请至少勾选一个要保留的文件")
        excluded = [file_id for file_id in all_ids if file_id not in chosen]
    elif action == "replace":
        replacement = str(target_file_id or "")
        if replacement not in all_ids:
            raise ValueError("请选择要保留的修正版文件")
        excluded = [file_id for file_id in all_ids if file_id != replacement]
    elif action == "keep_all":
        excluded = []
    elif action == "reset":
        decisions = scan.get("duplicate_decisions")
        if isinstance(decisions, dict):
            decisions.pop(key, None)
        return {"action": "reset", "exclude": []}
    else:
        raise ValueError("未知的重复集处理方式")

    decision = {
        "action": action,
        "exclude": sorted(excluded),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    scan.setdefault("duplicate_decisions", {})[key] = decision
    return decision
