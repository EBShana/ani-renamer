"""目录扫描：按“目录组”遍历，生成带组归属的解析结果。"""

from __future__ import annotations

import os
from pathlib import Path

from ..scanner_helpers import is_under
from .filetypes import CATEGORY_ORDER, VIDEO_EXTENSIONS, category_for
from .parser import OVA_DIR_NAMES, parse_asset, parse_file
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
        self.found: list[Path] = []
        self.ignored: list[dict] = []
        self.markers_written = 0

    def add_ignored(self, directory: Path, reason: str) -> None:
        try:
            rel = directory.relative_to(self.root).as_posix()
        except ValueError:
            rel = str(directory)
        self.ignored.append({"group_id": self.group_id, "path": rel, "reason": reason})
        if self.write_markers:
            for name in self.marker_names:
                target = directory / name
                if target.exists():
                    continue
                try:
                    target.write_text("", encoding="utf-8")
                    self.markers_written += 1
                except OSError:
                    pass


def _walk(directory: Path, ctx: _Context) -> None:
    if len(ctx.found) >= MAX_FILES:
        return
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
    except OSError:
        return
    for entry in entries:
        if len(ctx.found) >= MAX_FILES:
            return
        name = entry.name
        if entry.is_dir(follow_symlinks=False):
            if name.startswith(".") or name in IGNORED_DIRS:
                continue
            sub = Path(entry.path)
            try:
                resolved = sub.resolve()
            except OSError:
                resolved = sub
            if resolved in ctx.exclude:
                continue
            if name.lower() in OVA_DIR_NAMES:
                if ctx.ova_policy == "ignore":
                    ctx.add_ignored(sub, "ova")
                    continue
                # move：忽略历史标记文件，保证切换策略后 OVA 能被扫到
                _walk(sub, ctx)
                continue
            if ctx.ignore_special and name.lower() in ctx.special_names:
                ctx.add_ignored(sub, "special")
                continue
            if any((sub / marker).exists() for marker in MARKER_CANDIDATES):
                ctx.add_ignored(sub, "marker")
                continue
            _walk(sub, ctx)
        elif entry.is_file(follow_symlinks=False):
            if name.startswith("."):
                continue
            path = Path(entry.path)
            if ctx.all_files or path.suffix.lower() in VIDEO_EXTENSIONS:
                ctx.found.append(path)


def _target_excludes(group: dict, media_root: Path) -> set[Path]:
    """目标目录如果在源目录里面，扫描时跳过它，避免把自己产出的文件又扫进来。"""
    source = Path(group["source"]).expanduser().resolve()
    target = Path(group.get("target") or group["source"]).expanduser().resolve()
    if target != source and is_under(target, source):
        return {target}
    return set()


def scan_groups(groups: list[dict], media_root: Path, settings: dict, rules: list[Rule]) -> dict:
    """按目录组扫描；结果里带 groups 快照与每个文件的 group_id。"""
    if not groups:
        raise ScanError("请至少选择一个目录组")

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
        ctx = _Context(settings, source.resolve(), str(group["id"]), _target_excludes(group, media_root))
        _walk(source.resolve(), ctx)
        markers_written += ctx.markers_written
        all_ignored.extend(ctx.ignored)
        if len(ctx.found) >= MAX_FILES:
            truncated = True

        for path in ctx.found:
            if path in seen:
                continue
            seen.add(path)
            if index >= MAX_FILES:
                truncated = True
                break
            if path.is_file():
                category = category_for(path.suffix)
            else:
                category = "video"
            try:
                if category == "video":
                    row = parse_file(source.resolve(), path, settings, rules)
                else:
                    row = parse_asset(source.resolve(), path, settings, rules, category)
            except (OSError, ValueError):
                continue
            row["id"] = f"f{index:04d}"
            row["group_id"] = str(group["id"])
            index += 1
            files.append(row)
            category_counts[category] = category_counts.get(category, 0) + 1

    stats: dict[str, int] = {
        "total": len(files),
        "episode": 0,
        "special": 0,
        "movie": 0,
        "unknown": 0,
    }
    stats.update(category_counts)
    for row in files:
        if row["category"] != "video":
            continue
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
        "ignored_dirs": all_ignored,
        "markers_written": markers_written,
    }
