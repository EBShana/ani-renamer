"""目录 + 文件名两级解析。

目录层负责作品标题、季、特典目录；文件层负责集号、特典序号与技术标签；
两级合并后再生成模板字段，避免“每个文件各自猜一部动画”。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .fields import FIELD_REGISTRY
from .rules import Rule, chinese_to_int, match_rules
from .techname import BRACKET_RE, extract_group, extract_tech, extract_year, is_tag_content

PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
# 只有“整串就是一个方括号组”才算标题，避免 [组名] 标题 [标签] 这种混合目录被误判
FULL_BRACKET_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")
MOVIE_RE = re.compile(r"(?i)(剧场版|劇場版|剧场|劇場|movie)")
SPECIAL_TYPE_MAP = {
    "special": "SP",
    "sp": "SP",
    "ova": "OVA",
    "oad": "OAD",
    "ncop": "NCOP",
    "ncoped": "NCOP",
    "nced": "NCED",
    "pv": "PV",
    "cm": "CM",
    "menu": "Menu",
    "teaser": "Teaser",
    "preview": "Preview",
}
SPECIAL_DIR_TYPE = {
    "特典": "SP",
    "映像特典": "SP",
    "特别篇": "SP",
    "花絮": "SP",
    "bonus": "SP",
    "extra": "SP",
    "extras": "SP",
    "special": "SP",
    "specials": "SP",
    "sp": "SP",
    "sps": "SP",
    "ncop": "NCOP",
    "nced": "NCED",
    "menu": "Menu",
    "preview": "PV",
    "previews": "PV",
    "ova": "OVA",
    "ovas": "OVA",
    "oad": "OAD",
    "oads": "OAD",
}
# OVA 目录单独处理：可以随路径模板移动到 Season 00，也可以原地跳过
OVA_DIR_NAMES = {"ova", "ovas", "oad", "oads"}


def _digital(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def clean_dir_title(name: str) -> str:
    """清洗目录名：去掉发布组、技术标签、季标记与年份。"""
    text = name.strip()
    full = FULL_BRACKET_RE.match(text)
    if full:
        text = full.group(1)
    else:
        group = extract_group(text)
        if group:
            text = text.replace(f"[{group}]", " ")

    def bracket_repl(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if not content or is_tag_content(content):
            return " "
        if re.fullmatch(r"(19|20)\d{2}", content):
            return " "
        return f" {content} "

    def paren_repl(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if not content or is_tag_content(content):
            return " "
        if re.fullmatch(r"(19|20)\d{2}", content):
            return " "
        if content.upper() in ("TV", "MOVIE", "OVA", "WEB"):
            return " "
        return f" {content} "

    text = BRACKET_RE.sub(bracket_repl, text)
    text = PAREN_RE.sub(paren_repl, text)
    text = re.sub(r"(?i)\bS\d{1,2}\b", " ", text)
    text = re.sub(r"(?i)\bSeason\s*\d{1,2}\b", " ", text)
    text = re.sub(r"第[0-9一二三四五六七八九十百零两]+季", " ", text)
    text = re.sub(r"(?i)\b(19|20)\d{2}\b", " ", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -_.·:：@")


def _pick_title(dirs: list[str], dir_results: list[dict], settings: dict, media_root: Path) -> str:
    """取最深的、非季号/非特典/非通用名的目录作为作品标题。"""
    generic_names = {
        item.strip().lower()
        for item in str(settings.get("parse.generic_dir_names", "")).split(",")
        if item.strip()
    }
    min_length = int(settings.get("parse.min_title_length", 2))
    for part, result in zip(reversed(dirs), reversed(dir_results)):
        if result.get("season") or result.get("season_cn") or result.get("kind") == "special":
            continue
        if part.strip().lower() in OVA_DIR_NAMES:
            continue
        if part.strip().lower() in generic_names:
            continue
        candidate = clean_dir_title(part)
        if len(candidate) >= min_length:
            return candidate
    if media_root.name.lower() not in generic_names:
        candidate = clean_dir_title(media_root.name)
        if len(candidate) >= min_length:
            return candidate
    return ""


def parse_file(media_root: Path, path: Path, settings: dict, rules: list[Rule]) -> dict:
    rel = path.relative_to(media_root)
    dirs = list(rel.parts[:-1])
    filename = path.name
    stem, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower()

    dir_results = [match_rules(part, "dirname", rules) for part in dirs]
    file_result = match_rules(stem, "filename", rules)

    file_tech = extract_tech(stem)
    dir_tech: dict[str, str] = {}
    for part in dirs:
        for key, value in extract_tech(part).items():
            dir_tech.setdefault(key, value)
    tech: dict[str, str] = {**dir_tech, **{k: v for k, v in file_tech.items() if v}}

    group = extract_group(stem)
    if not group:
        for part in reversed(dirs):
            group = extract_group(part)
            if group:
                break

    year = extract_year(stem)
    if year is None:
        for part in reversed(dirs):
            year = extract_year(part)
            if year:
                break

    special_dir: str | None = None
    ova_dir = False
    dir_season: int | None = None
    ova_policy = str(settings.get("parse.ova_policy", "move"))
    for part, result in zip(dirs, dir_results):
        is_ova = part.strip().lower() in OVA_DIR_NAMES
        if is_ova and ova_policy == "move":
            # 选择“移动”时，OVA 目录按特典处理（后面会归到第 0 季）
            ova_dir = True
            special_dir = part
        elif result.get("kind") == "special":
            special_dir = part
        season_value = _digital(result.get("season"))
        if season_value is not None:
            dir_season = season_value
        elif result.get("season_cn"):
            cn_value = chinese_to_int(result["season_cn"])
            if cn_value is not None:
                dir_season = cn_value

    movie_hint = bool(MOVIE_RE.search(stem)) or any(MOVIE_RE.search(part) for part in dirs)
    special_hint = bool(special_dir) or file_result.get("kind") == "special"

    if movie_hint:
        kind = "movie"
    elif special_hint:
        kind = "special"
    elif file_result.get("episode"):
        kind = "episode"
    else:
        kind = "unknown"

    episode = _digital(file_result.get("episode"))
    if kind == "unknown" and episode is None and year is not None and not special_hint:
        kind = "movie"
    if kind == "special" and episode is None:
        episode = _digital(file_result.get("special_no"))

    season: int | None = None
    if kind != "movie":
        if file_result.get("season"):
            season = _digital(file_result["season"])
        if season is None and dir_season is not None:
            season = dir_season
        if kind == "special" and ova_dir:
            season = 0
        elif kind == "special" and settings.get("parse.special_season_zero", True):
            season = 0
        if season is None:
            season = int(settings.get("parse.default_season", 1))

    special_type: str | None = None
    raw_type = file_result.get("special_type")
    if raw_type:
        special_type = SPECIAL_TYPE_MAP.get(raw_type.lower(), raw_type.upper())
    elif special_dir:
        special_type = SPECIAL_DIR_TYPE.get(special_dir.strip().lower(), "SP")

    special_no = _digital(file_result.get("special_no"))
    if kind == "special" and special_no is None and episode is not None:
        special_no = episode

    version = _digital(file_result.get("version")) or _digital(tech.get("version"))

    title = _pick_title(dirs, dir_results, settings, media_root)

    warnings: list[str] = []
    if kind == "unknown":
        warnings.append("未能识别剧集 / 特典 / 剧场版")
    if not title:
        warnings.append("未能提取作品标题")
    if kind == "episode" and episode is None:
        warnings.append("未能识别集号")

    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    fields: dict[str, object] = {
        "series": title,
        "original_title": "",
        "year": year or "",
        "season": season if season is not None else "",
        "episode": episode if episode is not None else "",
        "ep_title": "",
        "air_date": "",
        "group": group or "",
        "resolution": tech.get("resolution", ""),
        "source": tech.get("source", ""),
        "codec": tech.get("codec", ""),
        "audio": tech.get("audio", ""),
        "bit_depth": tech.get("bit_depth", ""),
        "version": version if version is not None else "",
        "special_type": special_type or "",
        "special_no": special_no if special_no is not None else "",
        "ext": ext,
        "original_stem": stem,
        "original_filename": filename,
        "file_size": size,
        "tmdb_id": "",
        "imdb_id": "",
        "hdr": tech.get("hdr", ""),
        "sub_lang": tech.get("sub_lang", ""),
        "crc32": "",
    }

    status = "ok" if not warnings else "warning"
    return {
        "path": str(path),
        "rel_path": rel.as_posix(),
        "filename": filename,
        "parent": str(path.parent),
        "size": size,
        "kind": kind,
        "category": "video",
        "title": title,
        "year": year,
        "season": season,
        "episode": episode,
        "special_type": special_type,
        "special_no": special_no,
        "group": group or "",
        "resolution": tech.get("resolution", ""),
        "codec": tech.get("codec", ""),
        "source": tech.get("source", ""),
        "status": status,
        "warnings": warnings,
        "fields": fields,
    }


def parse_asset(
    media_root: Path,
    path: Path,
    settings: dict,
    rules: list[Rule],
    category: str,
) -> dict:
    """非视频文件（字幕、音频、图片等）只需要标题与基础信息，不做集号解析。"""
    rel = path.relative_to(media_root)
    dirs = list(rel.parts[:-1])
    dir_results = [match_rules(part, "dirname", rules) for part in dirs]
    filename = path.name
    stem, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower()
    title = _pick_title(dirs, dir_results, settings, media_root)

    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    fields: dict[str, object] = {key: "" for key in FIELD_REGISTRY}
    fields.update({
        "series": title,
        "ext": ext,
        "original_stem": stem,
        "original_filename": filename,
        "file_size": size,
    })

    return {
        "path": str(path),
        "rel_path": rel.as_posix(),
        "filename": filename,
        "parent": str(path.parent),
        "size": size,
        "kind": "asset",
        "category": category,
        "title": title,
        "year": None,
        "season": None,
        "episode": None,
        "special_type": None,
        "special_no": None,
        "group": "",
        "resolution": "",
        "codec": "",
        "source": "",
        "status": "ok",
        "warnings": [],
        "fields": fields,
    }
