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
from .duplicates import describe_groups
from .lexicon import (
    DEFAULT_SPECIALS_DIR_NAME,
    SEASON_FOLDER_NAME_RE,
    SPECIAL_SEASON_FOLDER_NAMES,
)
from .parser import title_looks_grouped
from .template import build_filename, sanitize_component

KINDS = ("episode", "special", "movie")
SKIP_MESSAGE = "该类别暂不参与重命名（后续版本可扩展）"


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
        lowered in SPECIAL_SEASON_FOLDER_NAMES
        or bool(SEASON_FOLDER_NAME_RE.fullmatch(lowered))
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
    exists = target.exists()
    item["status"] = "warning" if exists else "ok"
    if exists:
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


def _special_remainder(rel_path: str, title: str) -> str:
    """特典目录相对作品目录的剩余部分；保留「Extras/CDs」这类子层级。"""
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    key = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())
    for index, part in enumerate(parts):
        value = re.sub(r"[^\w\u4e00-\u9fff]+", "", part.lower())
        if key and value and (value == key or key in value or value in key):
            rest = parts[index + 1 :]
            return "/".join(rest) if rest else parts[-1]
    return parts[-1]


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


def _season_override_for(row: dict, season_state: dict) -> int | None:
    """用户在匹配页确认过的季号；特典、电影、待确认、换作品后都不生效。"""
    if row.get("kind") != "episode":
        return None
    identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
    work_id = str(identify.get("work_id") or "")
    info = season_state.get(work_id) if work_id and isinstance(season_state, dict) else None
    if not isinstance(info, dict):
        return None
    choice = info.get("choice")
    if not isinstance(choice, int) or choice < 0:
        return None
    fields = row.get("fields") or {}
    tmdb_id = str(fields.get("tmdb_id") or "")
    if tmdb_id and str(info.get("tmdb_id") or "") != tmdb_id:
        # 重新匹配到了别的 TMDB 作品，旧选择作废
        return None
    return choice


def _special_season_order(row: dict, season_state: dict) -> int:
    """撞名编号时的排序依据：先按季号升序，季号未知的排在最后。

    季号优先取用户在匹配页为这个作品选定的季；没选时退回文件自身识别到的季。
    只影响编号顺序，不改变任何文件的季目录。
    """
    identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
    work_id = str(identify.get("work_id") or "")
    info = season_state.get(work_id) if work_id and isinstance(season_state, dict) else None
    if isinstance(info, dict):
        choice = info.get("choice")
        tmdb_id = str((row.get("fields") or {}).get("tmdb_id") or "")
        if isinstance(choice, int) and choice >= 0 and (
            not tmdb_id or str(info.get("tmdb_id") or "") == tmdb_id
        ):
            return choice
    season = row.get("season")
    if isinstance(season, int) and season > 0:
        return season
    return 10**6


FOLDER_SUFFIX_RE = re.compile(r"\{(name|season|source_season)(?::(0?2))?\}")


def _folder_entry_rel(item: dict) -> str:
    """从 folder 行的 entry_id（d:组:相对路径）取回相对路径。"""
    entry = str(item.get("entry_id") or "")
    parts = entry.split(":", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "d" else ""


def _render_folder_suffix(pattern: str, name: str, season: int) -> str:
    """素材文件夹撞名时的改名：支持 {name} / {season} / {season:02} / {source_season}。"""
    def replace(match: re.Match) -> str:
        key, spec = match.group(1), match.group(2)
        if key == "name":
            return name
        if key == "source_season":
            return f"S{season:02d}"
        value = str(season)
        return value.zfill(2) if spec else value

    text = FOLDER_SUFFIX_RE.sub(replace, str(pattern or ""))
    text = re.sub(r"\{[^{}]*\}", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.")
    return text


def _folder_source_season(
    rel: str,
    group_id: str,
    video_rows: list[dict],
    season_state: dict,
    work_id: str,
) -> tuple[int | None, str]:
    """素材文件夹属于第几季：匹配页选定季 → 同目录视频识别到的季。

    返回（季号, 依据）；都没有时季号为 None。
    """
    if work_id and isinstance(season_state, dict):
        info = season_state.get(work_id)
        if isinstance(info, dict):
            choice = info.get("choice")
            if isinstance(choice, int) and choice >= 0:
                return choice, "selected"
    parent = str(PurePosixPath(rel).parent)
    prefix = "" if parent in ("", ".") else parent + "/"
    candidates = [
        row
        for row in video_rows
        if str(row.get("group_id") or "") == group_id
        and not row.get("special_folder")
        and str(row.get("rel_path") or "").startswith(prefix)
    ]
    works = Counter(
        str((row.get("identify") or {}).get("work_id") or "")
        for row in candidates
        if isinstance(row.get("identify"), dict)
    )
    for candidate_work, _ in works.most_common():
        if not candidate_work or not isinstance(season_state, dict):
            continue
        info = season_state.get(candidate_work)
        if isinstance(info, dict):
            choice = info.get("choice")
            if isinstance(choice, int) and choice >= 0:
                return choice, "selected"
    seasons = Counter(
        row.get("season")
        for row in candidates
        if isinstance(row.get("season"), int) and row.get("season") > 0
    )
    if seasons:
        return int(seasons.most_common(1)[0][0]), "file"
    return None, "none"


def _apply_folder_conflicts(
    results: list[dict],
    settings: dict,
    scan_payload: dict,
    video_rows: list[dict],
    season_state: dict,
) -> None:
    """只处理“多个素材文件夹要搬到同一个目标”的情况。

    默认加季号区分；合并模式保持同名；没撞名的文件夹完全不动。
    """
    mode = str(settings.get("execution.special_folder_conflict") or "season")
    pattern = str(settings.get("naming.special_folder_suffix") or "{name} {source_season}")
    groups: dict[str, list[dict]] = {}
    for item in results:
        if item.get("category") != "folder" or item.get("action") != "rename":
            continue
        if not item.get("new_path"):
            continue
        groups.setdefault(str(item["new_path"]).lower(), []).append(item)

    for items in groups.values():
        if len(items) < 2:
            continue
        if mode == "merge":
            for item in items:
                item["messages"] = list(item.get("messages") or []) + [
                    "与其他季的同名素材文件夹合并到同一目录"
                ]
            continue
        used: set[str] = set()
        for item in sorted(items, key=lambda row: str(row.get("entry_id") or "")):
            target = Path(str(item["new_path"]))
            rel = _folder_entry_rel(item)
            group_id = str(item.get("group_id") or "")
            decision = (scan_payload.get("special_decisions") or {}).get(f"{group_id}:{rel}")
            work_id = str((decision or {}).get("work_id") or "") if isinstance(decision, dict) else ""
            season, _source = _folder_source_season(
                rel, group_id, video_rows, season_state, work_id
            )
            messages = list(item.get("messages") or [])
            if season is None:
                season = 0
                messages.append("没识别到季号，先用 S00 兜底，请手动确认")
            leaf = _safe_component(_render_folder_suffix(pattern, target.name, season), settings)
            if not leaf:
                leaf = _safe_component(f"{target.name} S{season:02d}", settings)
            candidate = leaf
            index = 2
            while candidate.lower() in used:
                candidate = f"{leaf} {index}"
                index += 1
            used.add(candidate.lower())
            new_target = target.parent / candidate
            item["new_path"] = str(new_target)
            item["new_name"] = candidate
            item["status"] = "warning" if new_target.exists() else "ok"
            if item["status"] == "warning":
                messages.append("目标已存在")
            messages.append("同名的素材文件夹已按季号区分")
            item["messages"] = messages


def _build_video(
    row: dict,
    kind: str,
    template: dict,
    settings: dict,
    group: dict,
    season_override: int | None = None,
    special_no_override: object | None = None,
) -> dict:
    original = Path(row["path"])
    fields = dict(row.get("fields") or {})
    if kind == "special":
        # 特典统一放进特典季目录，文件名里的季号也固定为 0，避免 S01 与 Season 00 不一致
        fields["season"] = 0
        if special_no_override is not None:
            fields["special_no"] = special_no_override
        else:
            raw = row.get("special_no_raw")
            try:
                numeric = float(str(raw))
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and not numeric.is_integer():
                # 24.5 这类小数序号不能被压成 24
                fields["special_no"] = raw
    elif isinstance(season_override, int):
        # 用户在匹配页选定的季号，用于季目录和文件名里的 {season}
        fields["season"] = season_override
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
                settings.get("naming.specials_dir") or DEFAULT_SPECIALS_DIR_NAME, settings
            ) or DEFAULT_SPECIALS_DIR_NAME
            if kind == "special":
                out_dir = target_root / folder / specials_name
            elif kind == "movie":
                if str(settings.get("naming.movie_layout") or "folder") == "special":
                    out_dir = target_root / folder / specials_name
                else:
                    out_dir = target_root / folder
            else:
                season = (
                    season_override
                    if isinstance(season_override, int)
                    else _season_number(row, kind, settings)
                )
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


def _special_inner_parts(row: dict) -> list[str]:
    """视频在特典文件夹内部的相对层级（含特典目录名本身）。"""
    parts = _rel_parts(str(row.get("rel_path") or ""))
    name = str(row.get("special_folder") or "").strip().lower()
    for index in range(len(parts) - 1, -1, -1):
        if name and parts[index].lower() == name:
            return parts[index:]
    return [parts[-1]] if parts else []


def _build_special_folder_video(
    row: dict,
    settings: dict,
    group: dict,
    decision: dict | None,
    base_root: Path | None = None,
) -> dict:
    """特典文件夹里的视频（选项 A）：跟着整个文件夹一起搬，不改名。

    这里只给出最终落点做预览，不单独移动文件——实际搬运由“特典文件夹”那一行统一完成，
    这样文件夹名、内部相对位置、视频/字幕/图片/压缩包都原样保留。
    """
    original = Path(row["path"])
    fields = dict(row.get("fields") or {})
    item: dict[str, Any] = {
        "file_id": row.get("id"),
        "group_id": group.get("id") or row.get("group_id"),
        "original_path": str(original),
        "new_path": "",
        "new_name": original.name,
        "kind": row.get("kind") if row.get("kind") in KINDS else "special",
        "category": "video",
        "action": "skip",
        "status": "skip",
        "messages": [],
    }

    identify = row.get("identify") if isinstance(row.get("identify"), dict) else None
    if identify is not None and not str(identify.get("work_id") or "").strip():
        item["status"] = "error"
        item["messages"] = ["尚未归属到作品：请先在「匹配作品」页的未归属区指派"]
        return item

    if not isinstance(decision, dict):
        # 特典目录还没确认归属：整个目录都不会搬，视频也跟着不动
        item["messages"] = [
            "随特典文件夹整体移动、不改名；先在「匹配作品」页确认这个特典目录属于哪部作品"
        ]
        return item

    # 落点优先对齐该作品正片实际去的目录，避免同一部作品被拆成两个文件夹
    if base_root is not None:
        target = Path(base_root)
    else:
        folder = _show_folder(
            {"series": decision.get("title") or "", "year": decision.get("year")}, settings
        )
        if not folder:
            item["status"] = "error"
            item["messages"] = ["未能生成作品文件夹（作品名和年份都为空）"]
            return item
        target = Path(group["target"]) / folder

    inner = _special_inner_parts(row)
    if inner:
        target = target.joinpath(*inner)
    item["new_path"] = str(target)
    item["messages"] = ["随特典文件夹整体移动，保留文件夹名和内部结构，视频/字幕不改名"]
    if target == original:
        item["status"] = "warning"
        item["messages"].append("已经在目标位置，无需移动")
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
    if (
        not target_video.get("new_path")
        or target_video.get("status") == "error"
        or target_video.get("action") != "rename"
    ):
        item["status"] = "warning"
        item["messages"] = ["对应视频不参与本次改名（可能是重复集或未归属），字幕一起跳过"]
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
    exists = target.exists()
    item["status"] = "warning" if exists else "ok"
    if truncated:
        item["messages"].append("字幕文件名过长，已截断")
    if exists:
        item["messages"].append("目标文件已存在")
    return item


def _build_collection(
    row: dict,
    settings: dict,
    group: dict,
    decision: dict | None,
) -> dict:
    """合集文件：保留原名，按用户选择原样搬运或放进指定季目录；不做拆集。"""
    original = Path(row["path"])
    fields = dict(row.get("fields") or {})
    item: dict[str, Any] = {
        "file_id": row.get("id"),
        "group_id": group.get("id") or row.get("group_id"),
        "original_path": str(original),
        "new_path": "",
        "new_name": original.name,
        "kind": "collection",
        "category": "video",
        "action": "skip",
        "status": "skip",
        "collection": True,
        "collection_range": row.get("collection_range") or "",
        "messages": [],
    }

    identify = row.get("identify") if isinstance(row.get("identify"), dict) else None
    if identify is not None and not str(identify.get("work_id") or "").strip():
        item["status"] = "error"
        item["messages"] = ["合集文件尚未归属到作品：请先在「匹配作品」页的未归属区指派"]
        return item

    if not isinstance(decision, dict) or str(decision.get("action") or "") not in ("keep", "season"):
        item["messages"] = [
            f"识别为合集（{row.get('collection_range') or '多集'}），已标记但不会自动执行；"
            "请在「匹配作品」页的合集区选择「原样搬运」或「指定季存放」。"
            "合集不做拆集（拆分需要重新编码，超出范围）。"
        ]
        return item

    target_root = Path(group["target"])
    action = str(decision.get("action"))
    if action == "keep":
        parts = _rel_parts(str(row.get("rel_path") or ""))
        target = target_root.joinpath(*parts) if parts else target_root / original.name
        item["messages"] = ["按你的选择原样搬运（保留原文件名）"]
    else:
        season = decision.get("season")
        if not isinstance(season, int) or season < 0:
            season = _season_number(row, "episode", settings)
        folder = _show_folder(fields, settings)
        if not folder:
            item["status"] = "error"
            item["messages"] = ["未能生成作品文件夹（作品名和年份都为空）"]
            return item
        target = target_root / folder / f"Season {season:02d}" / original.name
        item["messages"] = [f"按你的选择作为整季合集放进 Season {season:02d}（保留原文件名）"]

    if target == original:
        item["messages"] = item["messages"] + ["已经在目标位置，无需移动"]
        return item
    item["new_path"] = str(target)
    item["new_name"] = target.name
    item["action"] = "rename"
    exists = target.exists()
    item["status"] = "warning" if exists else "ok"
    if exists:
        item["messages"].append("目标文件已存在")
    return item


def _quality_warnings(results: list[dict], video_rows: list[dict], templates: dict) -> None:
    """预览里的可疑提示：只提醒，不改名、不改状态。"""
    by_id = {str(row.get("id")): row for row in video_rows}

    def add(item: dict, message: str) -> None:
        messages = list(item.get("messages") or [])
        if message not in messages:
            messages.append(message)
        item["messages"] = messages

    buckets: dict[tuple[str, str, str, str], list[tuple[dict, dict]]] = {}
    for item in results:
        if item.get("category") != "video":
            continue
        row = by_id.get(str(item.get("file_id")))
        if not row:
            continue
        fields = row.get("fields") or {}
        if row.get("kind") == "special" and item.get("action") == "rename":
            season = str(fields.get("source_season") or row.get("season") or "")
            key = (
                str(row.get("group_id") or ""),
                str(row.get("title") or "").strip().lower(),
                season,
                str(row.get("special_type") or ""),
            )
            buckets.setdefault(key, []).append((row, item))
        if item.get("action") != "rename":
            continue
        title = str(row.get("title") or "").strip()
        if title_looks_grouped(title):
            add(item, f"标题可能含字幕组名，请确认：「{title}」")
        version = fields.get("version")
        pattern = str((templates.get(row.get("kind")) or {}).get("pattern") or "")
        if version not in (None, "") and "{version}" not in pattern:
            add(
                item,
                f"识别到版本 v{version}，但当前模板没有使用 {{version}}，新名字会丢失版本信息",
            )

    for key, pairs in buckets.items():
        type_name = key[3] or "特典"
        if len(pairs) <= 1:
            # 单条特典本来就保留原号，不按连续编号要求提示
            continue
        numbers = [
            row.get("special_no")
            for row, _ in pairs
            if isinstance(row.get("special_no"), int) and not isinstance(row.get("special_no"), bool)
        ]
        if not numbers:
            continue
        if len(set(numbers)) != len(numbers):
            for _, item in pairs:
                add(item, f"同类特典编号重复（{type_name}），请确认后再执行")
        if len(numbers) != len(pairs) or sorted(numbers) != list(range(1, len(pairs) + 1)):
            shown = "、".join(str(value) for value in sorted(numbers))
            for _, item in pairs:
                add(item, f"同类特典编号不连续（{type_name}：{shown}），请确认后再执行")


def build_preview(
    scan_payload: dict,
    preset: dict,
    settings: dict,
    templates_override: dict | None = None,
    file_ids: list[str] | None = None,
    limit: int = 0,
    subtitle_mode: str | None = None,
    folder_ids: list[str] | None = None,
) -> dict:
    templates = merge_templates(preset, templates_override)
    groups = _groups_of(scan_payload, settings)
    rows = scan_payload.get("files", [])
    if file_ids is not None:
        # 传了清单就是“只算这些条目”，空列表也代表什么都不算
        selected = set(file_ids)
        rows = [row for row in rows if row["id"] in selected]
    if limit and limit > 0:
        # 默认不限制：预览和执行都以扫描到的全部条目为准
        rows = rows[:limit]

    if subtitle_mode not in ("follow", "keep"):
        subtitle_mode = str(settings.get("naming.subtitle_mode", "follow"))
    if scan_payload.get("queue"):
        # 第 5 步：清单模式下字幕强制跟随视频，不能单独取消
        subtitle_mode = "follow"
    preserve = bool(settings.get("naming.preserve_structure", False))
    specials_name = str(settings.get("naming.specials_dir") or DEFAULT_SPECIALS_DIR_NAME)
    # 特典文件夹：root = 整个文件夹跟作品搬走、内部不改名（默认）；season = 视频进 Season 00 按模板改名
    special_video_policy = str(settings.get("execution.special_video_policy") or "root")

    video_rows = [row for row in rows if row.get("category") == "video"]
    videos_by_parent: dict[str, list[dict]] = {}
    for row in video_rows:
        videos_by_parent.setdefault(row.get("parent", ""), []).append(row)

    built: dict[str, dict] = {}
    season_state = scan_payload.get("season_state")
    season_state = season_state if isinstance(season_state, dict) else {}
    collection_decisions = scan_payload.get("collection_decisions")
    collection_decisions = (
        collection_decisions if isinstance(collection_decisions, dict) else {}
    )
    # 特典目录确认归属后会整目录搬走；未确认时整个目录保持原地
    special_decisions = scan_payload.get("special_decisions") or {}
    special_decisions = special_decisions if isinstance(special_decisions, dict) else {}
    confirmed_dirs: dict[tuple[str, str], dict] = {}
    for entry in scan_payload.get("ignored_dirs") or []:
        if not isinstance(entry, dict) or str(entry.get("reason") or "") != "special":
            continue
        gid = str(entry.get("group_id") or "")
        rel = str(entry.get("path") or "").strip("/")
        decision = special_decisions.get(f"{gid}:{rel}")
        if isinstance(decision, dict) and str(decision.get("title") or "").strip():
            confirmed_dirs[(gid, rel)] = decision

    def special_dir_decision(row: dict) -> dict | None:
        gid = str(row.get("group_id") or "")
        rel_path = str(row.get("rel_path") or "")
        for (entry_gid, rel), decision in confirmed_dirs.items():
            if entry_gid == gid and rel and rel_path.startswith(rel + "/"):
                return decision
        return None

    for row in video_rows:
        group = _group_for(row, groups)
        if row.get("collection"):
            built[row["id"]] = _build_collection(
                row,
                settings,
                group,
                collection_decisions.get(str(row["id"])),
            )
            continue
        if row.get("special_folder") and special_video_policy == "root":
            # 选项 A：视频跟着整个特典文件夹搬，等作品根目录统计完再补预览
            continue
        kind = row["kind"] if row["kind"] in templates else "episode"
        template = templates.get(kind) or templates.get("episode")
        built[row["id"]] = _build_video(
            row,
            kind,
            template,
            settings,
            group,
            _season_override_for(row, season_state),
        )

    # 特典重名兜底：模板里的区分变量还不够用时，只把仍然撞名的那几条退回连续编号。
    special_rows = [
        row
        for row in video_rows
        if row.get("kind") == "special"
        and row["id"] in built
        and str(built[row["id"]].get("new_path") or "")
        # 选 A 时特典文件夹里的视频明确“不改名”，不参与自动编号
        and not (special_video_policy == "root" and row.get("special_folder"))
    ]
    for _ in range(3):
        name_map: dict[str, list[dict]] = {}
        for row in special_rows:
            item = built[row["id"]]
            key = str(Path(item["new_path"]))
            name_map.setdefault(key, []).append(row)
        collided = [pair for pair in name_map.values() if len(pair) > 1]
        if not collided:
            break
        for pair in collided:
            # 只处理真正撞名的这一组；没撞名的特典保持原样，不重新编号。
            # 编号顺序先按季号升序（同一季排完再进下一季），季号相同或未知时按路径。
            pair.sort(
                key=lambda item: (
                    _special_season_order(item, season_state),
                    str(item.get("rel_path") or ""),
                )
            )
            group = _group_for(pair[0], groups)
            template = templates.get("special") or templates.get("episode")
            pattern = str((template or {}).get("pattern") or "")
            pair_ids = {str(item["id"]) for item in pair}
            used = {
                str(Path(item["new_path"]))
                for key, item in built.items()
                if key not in pair_ids and item.get("new_path")
            }
            seq = 1
            for row in pair:
                while True:
                    if "{special_no}" in pattern:
                        candidate = _build_video(
                            row,
                            "special",
                            template,
                            settings,
                            group,
                            _season_override_for(row, season_state),
                            special_no_override=seq,
                        )
                    else:
                        # 模板里没有 {special_no}：直接在文件名后面追加编号
                        candidate = _build_video(
                            row,
                            "special",
                            template,
                            settings,
                            group,
                            _season_override_for(row, season_state),
                        )
                        if candidate.get("new_path"):
                            path = Path(candidate["new_path"])
                            candidate["new_name"] = f"{path.stem}{seq}{path.suffix}"
                            candidate["new_path"] = str(path.with_name(candidate["new_name"]))
                    if not candidate.get("new_path") or candidate["new_path"] not in used:
                        break
                    seq += 1
                built[row["id"]] = candidate
                if candidate.get("new_path"):
                    used.add(str(candidate["new_path"]))
                seq += 1

    # 第 7 步：重复集检测。没有处理选择时标红并阻止执行；用户选择后按选择跳过或放行。
    decisions = scan_payload.get("duplicate_decisions")
    # A 模式下特典目录里的视频不改名、不单独执行，不参与重复集判定，避免被误标成错误
    duplicate_rows = [
        row
        for row in video_rows
        if not (row.get("special_folder") and special_video_policy == "root")
    ]
    duplicate_info = describe_groups(
        duplicate_rows, decisions if isinstance(decisions, dict) else {}
    )
    for duplicate in duplicate_info:
        decision = duplicate.get("decision") or {}
        for info in duplicate.get("files") or []:
            item = built.get(str(info.get("file_id") or ""))
            if item is None:
                continue
            info["status"] = item.get("status")
            if info.get("excluded"):
                item["status"] = "skip"
                item["action"] = "skip"
                item["messages"] = ["已按你的选择排除（重复集）"]
                continue
            if not decision:
                item["status"] = "error"
                item["messages"] = list(item.get("messages") or []) + [
                    f"重复集（{duplicate.get('kind_label')}）：{duplicate.get('reason')}。"
                    "请在预览上方的「重复集」里选择处理方式；"
                    "如果这些文件是不同清晰度或版本，可以在模板里加入 {resolution} 或 {group} 区分。"
                ]
            elif str(decision.get("action") or "") == "keep_all":
                item["messages"] = list(item.get("messages") or []) + [
                    "重复集：你选择了全部保留；如果目标路径撞名，"
                    "请在模板里加入 {resolution} 或 {group} 等能区分的变量"
                ]
            else:
                item["messages"] = list(item.get("messages") or []) + [
                    "重复集：已按你的选择保留这一份"
                ]

    asset_enabled = str(settings.get("execution.asset_policy") or "move") == "move"
    # 只算清单里勾选的素材文件夹；没传清单时按旧行为包含全部
    allowed_folders = set(folder_ids or [])
    restrict_folders = file_ids is not None or folder_ids is not None

    # 非“只重命名”模式：杂项文件跟随同组的作品文件夹
    work_roots: dict[tuple[str, str], Counter[str]] = {}
    parent_roots: dict[tuple[str, str], Counter[str]] = {}
    # 按作品统计视频最终落在哪个作品目录，供已确认归属的特典目录对齐
    work_roots_by_work: dict[tuple[str, str], Counter[str]] = {}
    for row in video_rows:
        if row.get("special_folder") and special_video_policy == "root":
            # A 模式下这些视频随特典目录整体搬走，落点就在特典目录里，
            # 不能拿来推断“作品根目录”，否则会把特典目录当成作品目录再套一层。
            continue
        item = built.get(row["id"])
        if not item or item.get("status") == "error" or not item.get("new_path"):
            continue
        group_id = str(row.get("group_id") or "")
        key, _ = _split_rel(str(row.get("rel_path") or ""))
        root = _show_root(Path(item["new_path"]), specials_name)
        work_roots.setdefault((group_id, key), Counter())[str(root)] += 1
        parent_roots.setdefault((group_id, str(row.get("parent") or "")), Counter())[str(root)] += 1
        ident = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        work_id = str(ident.get("work_id") or "")
        if work_id:
            work_roots_by_work.setdefault((group_id, work_id), Counter())[str(root)] += 1

    show_roots: dict[tuple[str, str], Path] = {
        key: Path(votes.most_common(1)[0][0]) for key, votes in work_roots.items()
    }
    sibling_roots: dict[tuple[str, str], Path] = {
        key: Path(votes.most_common(1)[0][0]) for key, votes in parent_roots.items()
    }

    # 选项 A：补上特典文件夹内视频的预览落点（跟着整个文件夹搬、不改名）。
    # 作品根目录已统计完，优先对齐正片实际去的作品目录。
    if special_video_policy == "root":
        for row in video_rows:
            if not row.get("special_folder") or row["id"] in built:
                continue
            group = _group_for(row, groups)
            decision = special_dir_decision(row)
            base_root = None
            if isinstance(decision, dict):
                work_id = str(decision.get("work_id") or "")
                votes = work_roots_by_work.get((str(row.get("group_id") or ""), work_id))
                if votes:
                    base_root = Path(votes.most_common(1)[0][0])
            built[row["id"]] = _build_special_folder_video(
                row, settings, group, decision, base_root
            )

    def folder_target(rel: str, group_id: str) -> Path | None:
        """素材文件夹跟着同一作品里路径前缀最长的视频走，避免多套一层作品文件夹。"""
        parts = _rel_parts(rel)
        best: tuple[int, dict] | None = None
        for row in video_rows:
            if str(row.get("group_id") or "") != group_id:
                continue
            if row.get("special_folder") and special_video_policy == "root":
                # 特典文件夹里的视频不算“普通作品视频”，避免把素材带去特典目录
                continue
            item = built.get(row["id"])
            if not item or item.get("status") == "error" or not item.get("new_path"):
                continue
            other = _rel_parts(str(row.get("rel_path") or ""))
            common = 0
            while common < min(len(parts), len(other)) and parts[common] == other[common]:
                common += 1
            if common and (best is None or common > best[0]):
                best = (common, item)
        if best is None:
            return None
        root = _show_root(Path(best[1]["new_path"]), specials_name)
        remainder = parts[best[0]:]
        return root.joinpath(*remainder) if remainder else root

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

    # 选项 B：特典目录里的视频会单独进 Season 00 改名，整目录移动必须跳过这些文件，
    # 避免同一个文件搬两次。选项 A 是整个目录一起搬，视频跟着走，不需要跳过。
    extracted_by_folder: dict[tuple[str, str], list[str]] = {}
    for row in video_rows:
        if not row.get("special_folder") or special_video_policy != "season":
            continue
        gid = str(row.get("group_id") or "")
        rel_path = str(row.get("rel_path") or "")
        for entry in scan_payload.get("ignored_dirs") or []:
            if str(entry.get("group_id") or "") != gid:
                continue
            rel = str(entry.get("path") or "").strip("/")
            if rel and rel_path.startswith(rel + "/"):
                extracted_by_folder.setdefault((gid, rel), []).append(str(row["path"]))

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

    if scan_payload.get("ignored_dirs"):
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
            if restrict_folders and f"{group_id}:{rel}" not in allowed_folders:
                # 清单模式：没勾选的素材文件夹不参与
                continue
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
                        "entry_id": f"d:{group_id}:{rel}",
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
            decision = (scan_payload.get("special_decisions") or {}).get(f"{group_id}:{rel}")
            if entry.get("reason") == "special" and not (
                isinstance(decision, dict) and decision.get("title")
            ):
                # 特典目录不再跟着视频自动移动：先给候选，用户确认后才搬，未确认保持原地
                results.append(
                    {
                        "file_id": None,
                        "entry_id": f"d:{group_id}:{rel}",
                        "group_id": group_id,
                        "original_path": str(original),
                        "new_path": "",
                        "new_name": PurePosixPath(rel).name,
                        "kind": "asset",
                        "category": "folder",
                        "action": "skip",
                        "status": "skip",
                        "messages": [
                            "特典目录还没有确认归属，默认保持原地（可在「匹配作品」页的未归属区确认）"
                        ],
                    }
                )
                continue
            if entry.get("reason") == "special" and isinstance(decision, dict):
                # 优先对齐该作品视频实际去往的作品目录：匹配到 TMDB 后作品名可能变了，
                # 再用确认时的旧名字拼路径会把同一部作品拆成两个文件夹。
                votes = work_roots_by_work.get((group_id, str(decision.get("work_id") or "")))
                if votes:
                    base_root = Path(votes.most_common(1)[0][0])
                else:
                    base_root = Path(group["target"]) / _show_folder(
                        {"series": decision.get("title"), "year": decision.get("year")}, settings
                    )
                remainder = _special_remainder(rel, str(decision.get("title") or ""))
                target = base_root / remainder
                item = _asset_row(original, target, "folder", asset_enabled, None, group_id)
                item["entry_id"] = f"d:{group_id}:{rel}"
                item["messages"].append("按你确认的特典归属移动到作品文件夹")
                item["extracted_paths"] = extracted_by_folder.get((group_id, rel), [])
                item["marker_after_move"] = True
                results.append(item)
                continue
            target = folder_target(rel, group_id)
            note = ""
            if target is None:
                # 没有可参照的视频时，退回原来的按第一层目录推断
                pseudo = {"rel_path": rel, "group_id": group_id}
                target, note = asset_target(pseudo)
            if target is None:
                results.append(
                    {
                        "file_id": None,
                        "entry_id": f"d:{group_id}:{rel}",
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
            item["entry_id"] = f"d:{group_id}:{rel}"
            if note:
                item["messages"].append(note)
            results.append(item)

    # 多季同名素材文件夹（CDs / Scans / SPs）整目录搬时会撞到同一个作品目录：
    # 默认按季号区分，也可以选择合并；没撞名的不动。
    _apply_folder_conflicts(results, settings, scan_payload, video_rows, season_state)

    entry_by_file: dict[str, str] = {}
    for entry in (scan_payload.get("queue") or {}).get("entries") or []:
        if not isinstance(entry, dict) or entry.get("is_folder"):
            continue
        for file_id in entry.get("file_ids") or []:
            entry_by_file[str(file_id)] = str(entry.get("entry_id") or "")
    for item in results:
        if item.get("category") == "folder":
            continue
        item["entry_id"] = entry_by_file.get(str(item.get("file_id")))

    duplicate_paths = Counter(
        item["new_path"]
        for item in results
        if item.get("new_path")
        and item.get("action") == "rename"
        and item.get("category") != "folder"
    )
    for item in results:
        if item.get("category") == "folder":
            continue
        if item.get("new_path") and duplicate_paths[item["new_path"]] > 1:
            item["status"] = "error"
            item["messages"] = list(item["messages"]) + ["多个文件映射到同一目标路径"]

    _quality_warnings(results, video_rows, templates)

    summary = {"ok": 0, "warning": 0, "error": 0, "skip": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1

    return {
        "rows": results,
        "summary": summary,
        "duplicates": duplicate_info,
        "subtitle_mode": subtitle_mode,
        "is_sample": bool(scan_payload.get("is_sample")),
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "base_root": str(scan_payload.get("root") or get_media_root()),
        "groups": list(groups.values()),
    }
