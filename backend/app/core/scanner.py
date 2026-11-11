"""目录扫描：按“目录组”遍历，生成带组归属的解析结果。"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path

from ..scanner_helpers import is_under
from .filetypes import ASSET_ONLY_EXTENSIONS, CATEGORY_ORDER, VIDEO_EXTENSIONS, category_for
from .lexicon import OVA_DIR_NAMES
from .parser import parse_asset, parse_file, renumber_specials
from .pattern import annotate_pattern, prepare_patterns
from .rules import Rule

IGNORED_DIRS = {
    ".git", ".svn", "__MACOSX", "@eaDir", "$RECYCLE.BIN", "System Volume Information",
}
# 媒体库用来“忽略整个目录”的标记文件，目录内已存在任一即视为已忽略
MARKER_CANDIDATES = (".ignore", ".nomedia")
MAX_FILES = 20000


class ScanError(Exception):
    pass


def resolve_entry(raw: str, media_root: Path) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = media_root / raw
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ScanError(f"路径无法访问：{raw}") from exc
    if not is_under(resolved, media_root):
        raise ScanError(f"路径不在媒体根目录内：{raw}")
    if not resolved.exists():
        raise ScanError(f"路径不存在：{raw}")
    return resolved


def _split_set(value: object, fallback: tuple[str, ...] = ()) -> set[str]:
    text = str(value or "").strip()
    items = [item.strip() for item in text.split(",") if item.strip()]
    return {item.lower() for item in items} if items else {item.lower() for item in fallback}


class _Context:
    """一次目录组扫描的上下文；rel_path 与忽略目录都相对该组的源目录。"""

    def __init__(
        self,
        settings: dict,
        root: Path,
        group_id: str,
        exclude: set[Path] | None = None,
    ) -> None:
        self.root = root
        self.group_id = group_id
        self.exclude = exclude or set()
        self.all_files = bool(settings.get("parse.scan_all_files", True))
        self.ignore_special = bool(settings.get("parse.ignore_special_dirs", True))
        self.ova_policy = str(settings.get("parse.ova_policy", "move"))
        self.special_names = _split_set(settings.get("parse.special_dir_names"))
        self.marker_names = sorted(_split_set(settings.get("parse.ignore_marker_files"), MARKER_CANDIDATES))
        self.write_markers = bool(settings.get("parse.write_ignore_markers", True))
        try:
            self.limit = int(settings.get("parse.max_files") or MAX_FILES)
        except (TypeError, ValueError):
            self.limit = MAX_FILES
        # 扫描阶段只记录，不再写 .ignore / .nomedia：
        # 之前的写法会在原特典目录里留下空标记文件，现在改到执行完成、文件移动之后再写。
        # 只认“扫描时会整体跳过的特典目录”（CDs / SPs / Scans 等）；
        # EXTRA 这类没有配置成跳过的目录仍按普通目录处理，原有重命名结果不变。
        self.found: list[tuple[Path, int]] = []
        # 只有“整目录被跳过”的特典目录里的视频才记在这里：它们走「特典视频落点」设置；
        # 没有被跳过（用户关掉了忽略特典目录）时仍按普通视频走特典模板改名。
        self.special_videos: dict[Path, str] = {}
        self.ignored: list[dict] = []
        self.markers_written = 0
        # 网盘模式：一次扫描内复用目录列表，并用列表里已有的文件名判断标记文件，
        # 避免每个目录再多做两次 stat、特典目录再列一遍。
        self.low_io = bool(settings.get("execution.network_drive_mode"))
        self.dir_cache: dict[str, list[os.DirEntry]] = {}

    def entries(self, directory: Path) -> list[os.DirEntry] | None:
        """返回排好序的目录项；网盘模式下同目录只列一次。"""
        key = str(directory)
        if self.low_io:
            cached = self.dir_cache.get(key)
            if cached is not None:
                return cached
        try:
            items = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
        except OSError:
            return None
        if self.low_io:
            self.dir_cache[key] = items
        return items

    def add_ignored(self, directory: Path, reason: str) -> None:
        try:
            rel = directory.relative_to(self.root).as_posix()
        except ValueError:
            rel = str(directory)
        self.ignored.append({"group_id": self.group_id, "path": rel, "reason": reason})


def _collect_ignored_videos(
    directory: Path,
    ctx: _Context,
    folder_name: str = "",
    entries: list[os.DirEntry] | None = None,
) -> None:
    """特典类目录整体被跳过时，仍把里面的视频文件收进来。

    这样 CDs / SPs / EXTRA 里的视频才能按「特典视频落点」设置处理，
    其余文件仍然交给目录整体移动。只做一次 scandir，不额外 stat。
    """
    if len(ctx.found) >= ctx.limit:
        return
    folder_name = folder_name or directory.name
    if entries is None:
        entries = ctx.entries(directory)
    if entries is None:
        return
    for entry in entries:
        if len(ctx.found) >= ctx.limit:
            return
        name = entry.name
        if name.startswith("."):
            continue
        suffix = os.path.splitext(name.lower())[1]
        if suffix in VIDEO_EXTENSIONS:
            # 视频才需要 stat（取大小）；大小留空交给解析阶段再取
            video = Path(entry.path)
            ctx.found.append((video, None))
            ctx.special_videos[video] = folder_name
            continue
        if suffix in ASSET_ONLY_EXTENSIONS:
            # 图片 / 音频 / 压缩包等特典素材，不可能是视频，直接跳过，避免每个文件都 stat
            continue
        if entry.is_dir(follow_symlinks=False) and name not in IGNORED_DIRS:
            sub = Path(entry.path)
            _collect_ignored_videos(sub, ctx, folder_name, ctx.entries(sub) if ctx.low_io else None)


def _walk(directory: Path, ctx: _Context, entries: list[os.DirEntry] | None = None) -> None:
    if len(ctx.found) >= ctx.limit:
        return
    if entries is None:
        entries = ctx.entries(directory)
    if entries is None:
        return
    for entry in entries:
        if len(ctx.found) >= ctx.limit:
            return
        name = entry.name
        # 每个条目只 stat 一次：拿到类型和大小，后面解析时不再重复 stat。
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat_module.S_ISDIR(info.st_mode):
            if name.startswith(".") or name in IGNORED_DIRS:
                continue
            sub = Path(entry.path)
            if ctx.exclude:
                try:
                    resolved = sub.resolve()
                except OSError:
                    resolved = sub
                if resolved in ctx.exclude:
                    continue
            lowered = name.lower()
            if lowered in OVA_DIR_NAMES:
                if ctx.ova_policy == "ignore":
                    ctx.add_ignored(sub, "ova")
                    continue
                # move：忽略历史标记文件，保证切换策略后 OVA 能被扫到
                _walk(sub, ctx, ctx.entries(sub) if ctx.low_io else None)
                continue
            if ctx.ignore_special and lowered in ctx.special_names:
                ctx.add_ignored(sub, "special")
                _collect_ignored_videos(sub, ctx, entries=ctx.entries(sub) if ctx.low_io else None)
                continue
            if ctx.low_io:
                # 目录列表已经读过，直接用文件名判断，省掉每个目录两次 stat。
                names = {child.name.lower() for child in (ctx.entries(sub) or [])}
                has_marker = any(marker in names for marker in MARKER_CANDIDATES)
            else:
                has_marker = any((sub / marker).exists() for marker in MARKER_CANDIDATES)
            if has_marker:
                ctx.add_ignored(sub, "marker")
                continue
            _walk(sub, ctx, ctx.entries(sub) if ctx.low_io else None)
        elif stat_module.S_ISREG(info.st_mode):
            if name.startswith("."):
                continue
            path = Path(entry.path)
            if ctx.all_files or path.suffix.lower() in VIDEO_EXTENSIONS:
                ctx.found.append((path, info.st_size))


def _target_excludes(group: dict, media_root: Path) -> set[Path]:
    """目标目录如果在源目录里面，扫描时跳过它，避免把自己产出的文件又扫进来。"""
    source = Path(group["source"]).expanduser().resolve()
    target = Path(group.get("target") or group["source"]).expanduser().resolve()
    if target != source and is_under(target, source):
        return {target}
    return set()


def scan_groups(
    groups: list[dict],
    media_root: Path,
    settings: dict,
    rules: list[Rule],
    patterns: list[dict] | None = None,
) -> dict:
    """按目录组扫描；结果里带 groups 快照与每个文件的 group_id。"""
    if not groups:
        raise ScanError("请至少选择一个目录组")
    try:
        limit = int(settings.get("parse.max_files") or MAX_FILES)
    except (TypeError, ValueError):
        limit = MAX_FILES
    limit = max(1, min(limit, 100000))

    files: list[dict] = []
    sources: list[str] = []
    all_ignored: list[dict] = []
    markers_written = 0
    truncated = False
    index = 0
    category_counts = {key: 0 for key in CATEGORY_ORDER}

    seen: set[Path] = set()
    for group in groups:
        source = Path(group["source"]).expanduser()
        if not source.exists() or not source.is_dir():
            raise ScanError(f"目录组的源目录不存在：{group['source']}")
        if not is_under(source.resolve(), media_root):
            raise ScanError(f"目录组的源目录不在媒体根目录内：{group['source']}")
        sources.append(str(source))
        source_resolved = source.resolve()
        ctx = _Context(settings, source_resolved, str(group["id"]), _target_excludes(group, media_root))
        ctx.limit = limit
        _walk(source_resolved, ctx)
        markers_written += ctx.markers_written
        all_ignored.extend(ctx.ignored)
        if len(ctx.found) >= limit:
            truncated = True

        for path, size in ctx.found:
            if path in seen:
                continue
            seen.add(path)
            if index >= limit:
                truncated = True
                break
            category = category_for(path.suffix)
            try:
                if category == "video":
                    row = parse_file(source_resolved, path, settings, rules, size)
                else:
                    row = parse_asset(source_resolved, path, settings, rules, category, size)
            except (OSError, ValueError):
                continue
            try:
                rel_path = path.relative_to(source_resolved).as_posix()
            except ValueError:
                rel_path = path.name
            special_folder = ctx.special_videos.get(path, "")
            if special_folder:
                row["special_folder"] = special_folder
            row["id"] = f"f{index:04d}"
            row["group_id"] = str(group["id"])
            index += 1
            files.append(row)
            category_counts[category] = category_counts.get(category, 0) + 1

    renumber_specials(files, settings)

    # 命名规律只记录计算结果与冲突，不修改任何现有字段（是否采用由用户决定）。
    if patterns:
        prepared = prepare_patterns(patterns)
        for row in files:
            annotate_pattern(row, prepared)

    stats: dict[str, int] = {
        "total": len(files),
        "episode": 0,
        "special": 0,
        "movie": 0,
        "unknown": 0,
        "collection": 0,
    }
    stats.update(category_counts)
    for row in files:
        if row["category"] != "video":
            continue
        if row.get("collection"):
            stats["collection"] += 1
        stats[row["kind"] if row["kind"] in ("episode", "special", "movie") else "unknown"] += 1

    return {
        "root": str(media_root),
        "groups": [
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or ""),
                "source": str(item["source"]),
                "target": str(item.get("target") or item["source"]),
            }
            for item in groups
        ],
        "sources": sources,
        "files": files,
        "stats": stats,
        "truncated": truncated,
        "max_files": limit,
        "ignored_dirs": all_ignored,
        "markers_written": markers_written,
    }
