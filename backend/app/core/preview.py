"""预览构建：按目录组 + 自动目录规范算目标路径，供预览与执行共用。

v3 起目录结构不由预设模板决定，而是统一按规范生成：
  正片  -> 目标目录/作品 (年份)/Season xx/文件名
  特典  -> 目标目录/作品 (年份)/特典季目录/文件名
  剧场版 -> 目标目录/作品 (年份)/文件名（或按设置放进特典季目录）
勾选“只重命名文件”后，改为 目标目录/源目录相对结构/新文件名。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import get_media_root
from ..store import get_preset, list_presets
from .template import SPECIALS_SEGMENTS, build_filename, sanitize_component

KINDS = ("episode", "special", "movie")
SKIP_MESSAGE = "该类别暂不参与重命名（后续版本可扩展）"
SEASON_SEGMENT_RE = re.compile(r"^(season\s*0?\d+|s\d{2,})$", re.IGNORECASE)


def _split_rel(rel_path: str) -> tuple[str, str]:
    """把组内相对路径拆成（第一层目录, 其余相对路径）。"""
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    if len(parts) <= 1:
        return "", parts[-1] if parts else ""
    return parts[0], "/".join(parts[1:])


def _rel_parts(rel_path: str) -> list[str]:
    return [part for part in PurePosixPath(rel_path.replace("\\", "/")).parts if part not in ("", ".")]


def _is_organizer_segment(name: str, specials_name: str) -> bool:
    lowered = name.strip().lower()
    return (
        lowered in SPECIALS_SEGMENTS
        or bool(SEASON_SEGMENT_RE.fullmatch(lowered))
        or lowered == specials_name.strip().lower()
    )


def _show_root(target: Path, specials_name: str) -> Path:
    """从目标文件路径向上找作品根目录（跳过 Season xx / Specials 层级）。"""
    folder = target.parent
    while folder.parent != folder and _is_organizer_segment(folder.name, specials_name):
        folder = folder.parent
    return folder


def _asset_row(
    original: Path,
    target: Path,
    category: str,
    enabled: bool,
    file_id: str | None = None,
    group_id: str | None = None,
) -> dict:
    item: dict = {
        "file_id": file_id,
        "group_id": group_id,
        "original_path": str(original),
        "new_path": "",
        "new_name": target.name,
        "kind": "asset",
        "category": category,
        "action": "skip",
        "status": "skip",
        "messages": [],
    }
    if not enabled:
        item["messages"] = ["按设置保留在原处（可在设置页把“特典等其余文件处理”改为移动）"]
        return item
    if str(target) == str(original):
        item["messages"] = ["已经在目标位置，无需移动"]
        return item
    item["new_path"] = str(target)
    item["action"] = "rename"
    item["status"] = "warning" if target.exists() else "ok"
    if target.exists():
        item["messages"] = ["目标已存在"]
    return item


def load_preset(preset_id: str | None) -> dict | None:
    """按 id 取预设；未指定或找不到时退回默认预设。"""
    preset = get_preset(preset_id) if preset_id else None
    if preset is not None:
        return preset
    presets = list_presets()
    if not presets:
        return None
    return next((item for item in presets if item.get("is_default")), presets[0])


def _compose_template(spec: dict) -> dict:
    """v3：预设只保存文件名规则，目录由系统自动生成。"""
    return {"pattern": str(spec.get("pattern", "")), "output_mode": "filename"}


def merge_templates(preset: dict, overrides: dict | None) -> dict[str, dict]:
    templates = {
        kind: _compose_template(value)
        for kind, value in (preset.get("templates") or {}).items()
        if kind in KINDS
    }
    if overrides:
        for kind, spec in overrides.items():
            if kind not in KINDS:
                continue
            templates[kind] = _compose_template(spec)
    return templates


def _safe_component(text: object, settings: dict) -> str:
    forbidden = str(settings.get("naming.forbidden_chars", ""))
    max_bytes = int(settings.get("naming.max_filename_bytes", 240))
    value, _ = sanitize_component(str(text or "").strip(), forbidden, max_bytes)
    return value


def _show_folder(fields: dict, settings: dict) -> str:
    """作品文件夹名：作品名 (年份)。"""
    series = _safe_component(fields.get("series") or "", settings)
    year_text = str(fields.get("year") or "").strip()
    year = _safe_component(year_text, settings) if year_text else ""
    if series and year:
        return f"{series} ({year})"
    return series or year


def _season_number(row: dict, kind: str, settings: dict) -> int:
    if kind == "special":
        return 0
    value = row.get("season")
    if isinstance(value, int) and value >= 0:
        return value
    text = str((row.get("fields") or {}).get("season") or "").strip()
    if text.isdigit():
        return int(text)
    try:
        return int(settings.get("parse.default_season", 1))
    except (TypeError, ValueError):
        return 1


def _groups_of(scan_payload: dict, settings: dict) -> dict[str, dict]:
    """扫描时保存的目录组快照。"""
    base = Path(scan_payload.get("root") or get_media_root())
    groups = scan_payload.get("groups") or []
    result: dict[str, dict] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("id") or "")
        source = Path(str(item.get("source") or base))
        target = Path(str(item.get("target") or source))
        result[group_id] = {
            "id": group_id,
            "name": str(item.get("name") or ""),
            "source": str(source),
            "target": str(target),
        }
    if not result:
        result[""] = {
            "id": "",
            "name": "",
            "source": str(base),
            "target": str(base),
        }
    return result


def _group_for(row: dict, groups: dict[str, dict]) -> dict:
    group = groups.get(str(row.get("group_id") or ""))
    if group is not None:
        return group
    if "" in groups:
        return groups[""]
    return next(iter(groups.values()))


def _build_video(
    row: dict,
    kind: str,
    template: dict,
    settings: dict,
    group: dict,
) -> dict:
    original = Path(row["path"])
    fields = dict(row.get("fields") or {})
    if kind == "special":
        # 特典统一放进特典季目录，文件名里的季号也固定为 0，避免 S01 与 Season 00 不一致
        fields["season"] = 0
    render_row = {**row, "fields": fields}
    name, errors, warnings = build_filename(render_row, template, settings)

    item: dict[str, Any] = {
        "file_id": row.get("id"),
        "group_id": group.get("id") or row.get("group_id"),
        "original_path": str(original),
        "new_path": "",
        "new_name": name,
        "kind": kind,
        "category": "video",
        "action": "rename",
        "status": "ok",
        "messages": [],
    }

    identify = row.get("identify") if isinstance(row.get("identify"), dict) else None
    if identify is not None and not str(identify.get("work_id") or "").strip():
        # 第 3 步：未归属到作品的文件不能在预览 / 执行里改名
        item["status"] = "error"
        item["action"] = "skip"
        item["messages"] = ["尚未归属到作品：请先在「匹配作品」页的未归属区指派"]
        return item

    target: Path | None = None
    out_dir: Path | None = None
    target_root = Path(group["target"])

    preserve = bool(settings.get("naming.preserve_structure", False))
    if preserve:
        parts = _rel_parts(str(row.get("rel_path") or ""))[:-1]
        out_dir = target_root.joinpath(*parts) if parts else target_root
    elif errors:
        out_dir = None
    else:
        folder = _show_folder(fields, settings)
        if not folder:
            errors.append("未能生成作品文件夹（作品名和年份都为空）")
        else:
            specials_name = _safe_component(
                settings.get("naming.specials_dir") or "Season 00", settings
            ) or "Season 00"
            if kind == "special":
                out_dir = target_root / folder / specials_name
            elif kind == "movie":
                if str(settings.get("naming.movie_layout") or "folder") == "special":
                    out_dir = target_root / folder / specials_name
                else:
                    out_dir = target_root / folder
            else:
                season = _season_number(row, kind, settings)
                out_dir = target_root / folder / f"Season {season:02d}"

    if not errors and out_dir is not None and name:
        target = out_dir / name

    if errors:
        item["status"] = "error"
        item["messages"] = errors + warnings
    elif target is None:
        item["status"] = "error"
        item["messages"] = warnings + ["无法计算目标路径"]
    elif target == original:
        item["status"] = "warning"
        item["messages"] = warnings + ["目标与源路径相同"]
    elif target.exists():
        item["status"] = "warning"
        item["messages"] = warnings + ["目标文件已存在"]
    else:
        item["messages"] = warnings
    if target is not None:
        item["new_path"] = str(target)
    return item


def _build_subtitle(
    row: dict,
    videos_by_parent: dict[str, list[dict]],
    built: dict[str, dict],
    settings: dict,
    mode: str,
) -> dict:
    """字幕跟随对应视频改名，保留语言后缀（如 .Chs&Jap）。"""
    original = Path(row["path"])
    stem = str(row.get("fields", {}).get("original_stem", ""))
    ext = str(row.get("fields", {}).get("ext", ""))
    item: dict = {
        "file_id": row.get("id"),
        "group_id": row.get("group_id"),
        "original_path": str(original),
        "new_path": "",
        "new_name": row["filename"],
        "kind": row.get("kind"),
        "category": "subtitle",
        "action": "skip",
        "status": "skip",
        "messages": [],
    }
    if mode != "follow":
        item["messages"] = ["字幕保持原名（可在设置里改为跟随视频）"]
        return item

    matched: tuple[str, dict] | None = None
    for video in videos_by_parent.get(row.get("parent", ""), []):
        if str(video.get("group_id") or "") != str(row.get("group_id") or ""):
            continue
        video_stem = str(video.get("fields", {}).get("original_stem", ""))
        if not video_stem or not stem.startswith(video_stem):
            continue
        if matched is None or len(video_stem) > len(matched[0]):
            matched = (video_stem, video)
    if matched is None:
        item["status"] = "warning"
        item["messages"] = ["未找到同名视频，字幕将跳过"]
        return item

    video_stem, video = matched
    target_video = built.get(video["id"], {})
    if not target_video.get("new_path") or target_video.get("status") == "error":
        item["status"] = "warning"
        item["messages"] = ["对应视频未能生成有效目标名，字幕将跳过"]
        return item

    suffix = stem[len(video_stem):]
    new_stem = Path(target_video["new_path"]).stem
    candidate = f"{new_stem}{suffix}.{ext}" if ext else f"{new_stem}{suffix}"
    forbidden = str(settings.get("naming.forbidden_chars", ""))
    max_bytes = int(settings.get("naming.max_filename_bytes", 240))
    name, truncated = sanitize_component(candidate, forbidden, max_bytes)
    if not name:
        item["status"] = "error"
        item["messages"] = ["无法生成字幕文件名"]
        return item

    target = Path(target_video["new_path"]).parent / name
    item["new_path"] = str(target)
    item["new_name"] = name
    item["action"] = "rename"
    item["status"] = "warning" if target.exists() else "ok"
    if truncated:
        item["messages"].append("字幕文件名过长，已截断")
    if target.exists():
        item["messages"].append("目标文件已存在")
    return item


def build_preview(
    scan_payload: dict,
    preset: dict,
    settings: dict,
    templates_override: dict | None = None,
    file_ids: list[str] | None = None,
    limit: int = 0,
    subtitle_mode: str | None = None,
) -> dict:
    templates = merge_templates(preset, templates_override)
    groups = _groups_of(scan_payload, settings)
    rows = scan_payload.get("files", [])
    if file_ids:
        selected = set(file_ids)
        rows = [row for row in rows if row["id"] in selected]
    if limit and limit > 0:
        # 默认不限制：预览和执行都以扫描到的全部条目为准
        rows = rows[:limit]

    if subtitle_mode not in ("follow", "keep"):
        subtitle_mode = str(settings.get("naming.subtitle_mode", "follow"))
    preserve = bool(settings.get("naming.preserve_structure", False))
    specials_name = str(settings.get("naming.specials_dir") or "Season 00")

    video_rows = [row for row in rows if row.get("category") == "video"]
    videos_by_parent: dict[str, list[dict]] = {}
    for row in video_rows:
        videos_by_parent.setdefault(row.get("parent", ""), []).append(row)

    built: dict[str, dict] = {}
    for row in video_rows:
        kind = row["kind"] if row["kind"] in templates else "episode"
        template = templates.get(kind) or templates.get("episode")
        group = _group_for(row, groups)
        built[row["id"]] = _build_video(row, kind, template, settings, group)

    asset_enabled = str(settings.get("execution.asset_policy") or "move") == "move"
    include_ignored = file_ids is None

    # 非“只重命名”模式：杂项文件跟随同组的作品文件夹
    work_roots: dict[tuple[str, str], Counter[str]] = {}
    parent_roots: dict[tuple[str, str], Counter[str]] = {}
    for row in video_rows:
        item = built.get(row["id"])
        if not item or item.get("status") == "error" or not item.get("new_path"):
            continue
        group_id = str(row.get("group_id") or "")
        key, _ = _split_rel(str(row.get("rel_path") or ""))
        root = _show_root(Path(item["new_path"]), specials_name)
        work_roots.setdefault((group_id, key), Counter())[str(root)] += 1
        parent_roots.setdefault((group_id, str(row.get("parent") or "")), Counter())[str(root)] += 1

    show_roots: dict[tuple[str, str], Path] = {
        key: Path(votes.most_common(1)[0][0]) for key, votes in work_roots.items()
    }
    sibling_roots: dict[tuple[str, str], Path] = {
        key: Path(votes.most_common(1)[0][0]) for key, votes in parent_roots.items()
    }

    def asset_target(row: dict) -> tuple[Path | None, str]:
        group = _group_for(row, groups)
        target_root = Path(group["target"])
        rel = str(row.get("rel_path") or "")
        if preserve:
            parts = _rel_parts(rel)
            return (target_root.joinpath(*parts) if parts else target_root), ""
        group_id = str(row.get("group_id") or "")
        parent = str(row.get("parent") or "")
        # 优先跟着同一目录里的视频走：一个文件夹里有多部作品（合集）时也能各归各位
        sibling = sibling_roots.get((group_id, parent))
        if sibling is not None and parent:
            parent_parts = _rel_parts(parent)
            rel_parts = _rel_parts(rel)
            if rel_parts[: len(parent_parts)] == parent_parts and len(rel_parts) > len(parent_parts):
                inner_parts = rel_parts[len(parent_parts):]
            else:
                inner_parts = [rel_parts[-1]] if rel_parts else []
            return sibling.joinpath(*inner_parts), ""
        key, inner = _split_rel(rel)
        root = show_roots.get((group_id, key))
        if root is None:
            return None, "未找到对应作品目录，无法移动"
        return root / inner, ""

    results: list[dict] = []
    for row in rows:
        category = row.get("category", "video")
        if category == "video":
            results.append(built[row["id"]])
        elif category == "subtitle":
            results.append(_build_subtitle(row, videos_by_parent, built, settings, subtitle_mode))
        else:
            target, note = asset_target(row)
            if target is None:
                results.append(
                    {
                        "file_id": row.get("id"),
                        "group_id": row.get("group_id"),
                        "original_path": row["path"],
                        "new_path": "",
                        "new_name": row["filename"],
                        "kind": row.get("kind"),
                        "category": category,
                        "action": "skip",
                        "status": "skip",
                        "messages": [note or SKIP_MESSAGE],
                    }
                )
            else:
                item = _asset_row(Path(row["path"]), target, category, asset_enabled, row.get("id"), row.get("group_id"))
                if note:
                    item["messages"].append(note)
                results.append(item)

    if include_ignored:
        ignored = scan_payload.get("ignored_dirs") or []
        ignored_paths = [
            (str(entry.get("group_id") or ""), str(entry.get("path") or "").strip("/"))
            for entry in ignored
        ]
        seen_ignored: set[tuple[str, str]] = set()
        for entry in ignored:
            group_id = str(entry.get("group_id") or "")
            rel = str(entry.get("path") or "").strip("/")
            if not rel or (group_id, rel) in seen_ignored:
                continue
            seen_ignored.add((group_id, rel))
            # 目录套目录时只移动最外层
            if any(
                other and other_gid == group_id and rel.startswith(other + "/")
                for other_gid, other in ignored_paths
            ):
                continue
            group = groups.get(group_id)
            base = Path(group["source"]) if group else Path(scan_payload.get("root") or get_media_root())
            original = base / rel
            if entry.get("reason") == "ova":
                results.append(
                    {
                        "file_id": None,
                        "group_id": group_id,
                        "original_path": str(original),
                        "new_path": "",
                        "new_name": PurePosixPath(rel).name,
                        "kind": "asset",
                        "category": "folder",
                        "action": "skip",
                        "status": "skip",
                        "messages": ["OVA 目录按设置保持原处"],
                    }
                )
                continue
            pseudo = {"rel_path": rel, "group_id": group_id}
            target, note = asset_target(pseudo)
            if target is None:
                results.append(
                    {
                        "file_id": None,
                        "group_id": group_id,
                        "original_path": str(original),
                        "new_path": "",
                        "new_name": PurePosixPath(rel).name,
                        "kind": "asset",
                        "category": "folder",
                        "action": "skip",
                        "status": "skip",
                        "messages": [note],
                    }
                )
                continue
            item = _asset_row(original, target, "folder", asset_enabled, None, group_id)
            if note:
                item["messages"].append(note)
            results.append(item)

    duplicate_paths = Counter(
        item["new_path"]
        for item in results
        if item.get("new_path") and item.get("action") == "rename"
    )
    for item in results:
        if item.get("new_path") and duplicate_paths[item["new_path"]] > 1:
            item["status"] = "error"
            item["messages"] = list(item["messages"]) + ["多个文件映射到同一目标路径"]

    summary = {"ok": 0, "warning": 0, "error": 0, "skip": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1

    return {
        "rows": results,
        "summary": summary,
        "subtitle_mode": subtitle_mode,
        "is_sample": bool(scan_payload.get("is_sample")),
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "base_root": str(scan_payload.get("root") or get_media_root()),
        "groups": list(groups.values()),
    }
