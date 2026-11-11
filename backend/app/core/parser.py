"""目录 + 文件名两级解析。

目录层负责作品标题、季、特典目录；文件层负责集号、特典序号与技术标签；
两级合并后再生成模板字段，避免“每个文件各自猜一部动画”。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .fields import FIELD_REGISTRY
from .lexicon import OVA_DIR_NAMES, SPECIAL_DIR_TYPES
from .rules import Rule, chinese_to_int, match_rules
from .techname import BRACKET_RE, extract_group, extract_tech, extract_year, is_tag_content

PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
# 全角方括号：和半角一样参与清洗，但只丢“整段都是技术标签”的内容
CN_BRACKET_RE = re.compile(r"【([^【】]*)】")
# 只有“整串就是一个方括号组”才算标题，避免 [组名] 标题 [标签] 这种混合目录被误判
FULL_BRACKET_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")
MOVIE_RE = re.compile(r"(?i)(剧场版|劇場版|剧场|劇場|movie)")
# 合集线索：01-12 / EP01-12 / 1-12话 / 合集 / Complete / 全12话
COLLECTION_PATTERNS = (
    re.compile(r"(?i)\b(?:ep|e)?0?\d{1,3}\s*[-~～~]\s*0?\d{1,3}\b"),
    re.compile(r"\d{1,3}\s*[-~～~]\s*\d{1,3}\s*[话話集]"),
    re.compile(r"全\s*\d{1,3}\s*[话話集]"),
    re.compile(r"(?i)\bcomplete\b"),
    re.compile(r"合集|全集"),
)


def detect_collection(text: str) -> str:
    """返回命中的合集区间写法；没命中返回空字符串。"""
    for pattern in COLLECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(0).strip()
    return ""


# 作品名里不应残留的发布 / 合集标记（方括号整段是标签时由 is_tag_content 处理，这里处理裸露写法）
RELEASE_NOISE_RE = re.compile(
    r"(?i)bd[\s-]*box|blu-?ray[\s-]*box"
    r"|(?<![A-Za-z0-9])[0-9]{1,3}\s*[-~～]\s*[0-9]{1,3}(?![0-9])"
    r"|\b(?:fin|complete)\b"
    r"|全\s*[0-9]{1,3}\s*[话話集]|完结|全集|合集"
    r"|\b(?:bd|bdrip|bdremux|blu-?ray|hdtv|dvdrip|web-?dl|web-?rip)\b"
)
# 目录名末尾的媒体形态后缀（如 “- TV + OVA”“- Movie”），属于发布信息
MEDIA_SUFFIX_RE = re.compile(
    r"(?i)\s*[-–—]\s*(?:TV|OVA|OAD|MOVIE|WEB|SP)(?:\s*[+＋]\s*(?:TV|OVA|OAD|MOVIE|WEB|SP))*\s*$"
)


def strip_release_noise(text: str) -> str:
    """去掉区间 / 完结 / BD 等发布标记，避免它们留在作品名里。"""
    cleaned = RELEASE_NOISE_RE.sub(" ", text or "")
    cleaned = MEDIA_SUFFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -_.·:：@")


# 开头单独占一个方括号的“机构名”候选（半角 / 全角都认）
_LEADING_BRACKET_RE = re.compile(r"^\s*[\[【]([^\[\]【】]*)[\]】]")
# 字幕组 / 发布组常见的整段结尾；只在“整段前缀”里匹配，不做句子内替换
_GROUP_MARKER_RE = re.compile(
    r"(?i)(?:字幕组|字幕社|汉化组|制作组|压制组|发布组|译制组|工作室|字幕|汉化|压制|发布|译制|制作"
    r"|fansub|sub(?:s)?|raws?|studio|project|team|group|encodes?)$"
)
_GROUP_SEASON_RE = re.compile(
    r"(?i)^(?:s\d{1,2}|season\s*\d{1,2}|第[0-9一二三四五六七八九十百零两]+季)$"
)
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def _is_group_segment(content: str) -> bool:
    """整段方括号内容是否“明显是字幕组 / 发布组”，而不是作品名。"""
    text = content.strip()
    if not text or len(text) > 8:
        return False
    if _KANA_RE.search(text) or re.search(r"\d", text):
        return False
    return bool(_GROUP_MARKER_RE.search(text))


def strip_group_prefix(name: str) -> str:
    """去掉名称开头“整段明显是字幕组 / 发布组”的方括号前缀。

    边界（保守）：
    - 只处理名称最开头的第一个方括号，且整段不超过 8 个字、不含假名和数字；
    - 整段必须以字幕组 / 汉化组 / 制作 / 工作室 / Raws / Subs 这类机构词结尾；
    - 后面必须还有另一个像作品名的方括号内容，或方括号外还有有效文字；
    - 后面紧跟的是季号 / 技术标签时不动；单个方括号就是作品名时不动。
    """
    text = str(name or "").strip()
    first = _LEADING_BRACKET_RE.match(text)
    if not first or not _is_group_segment(first.group(1)):
        return text
    rest = text[first.end():].strip()
    if not rest:
        return text
    nxt = _LEADING_BRACKET_RE.match(rest)
    if nxt:
        content = nxt.group(1).strip()
        if not content or is_tag_content(content) or _GROUP_SEASON_RE.match(content):
            return text
        return rest
    outside = strip_release_noise(_LEADING_BRACKET_RE.sub("", rest)).strip(" -_.·:：@")
    return rest if len(outside) >= 2 else text


def title_looks_grouped(title: str) -> bool:
    """预览提示：标题里是否还留着“字幕组名 + 空格 + 作品名”的形态。"""
    text = str(title or "").strip()
    if not text or " " not in text:
        return False
    head = text.split(" ", 1)[0].strip()
    return bool(head) and len(head) <= 8 and bool(_GROUP_MARKER_RE.search(head))


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
    "yokoku": "Yokoku",
}
# 特典文件名里更具体的类型词（优先于 SPxx 这种碟片序号）
SPECIAL_LABEL_PATTERNS = (
    (re.compile(r"(?i)\bmenu\b"), "Menu"),
    (re.compile(r"(?i)\bncop(?:ed)?\b"), "NCOP"),
    (re.compile(r"(?i)\bnced\b"), "NCED"),
    (re.compile(r"(?i)\byokoku\b"), "Yokoku"),
    (re.compile(r"(?i)\b(?:pv|preview|teaser|trailer)\b"), "PV"),
    (re.compile(r"(?i)\bcm\b"), "CM"),
    (re.compile(r"(?i)\b(?:ova|oad)\b"), "OVA"),
    (re.compile(r"(?i)\bspecial\b"), "SP"),
)


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
    text = strip_group_prefix(name.strip())
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
    text = CN_BRACKET_RE.sub(bracket_repl, text)
    text = PAREN_RE.sub(paren_repl, text)
    text = re.sub(r"(?i)\bS\d{1,2}\b", " ", text)
    text = re.sub(r"(?i)\bSeason\s*\d{1,2}\b", " ", text)
    text = re.sub(r"第[0-9一二三四五六七八九十百零两]+季", " ", text)
    text = re.sub(r"(?i)\b(19|20)\d{2}\b", " ", text)
    text = strip_release_noise(text)
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
        if part.strip().lower() in SPECIAL_DIR_TYPES:
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


def parse_file(
    media_root: Path,
    path: Path,
    settings: dict,
    rules: list[Rule],
    size: int | None = None,
) -> dict:
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
        special_type = SPECIAL_DIR_TYPES.get(special_dir.strip().lower(), "SP")

    special_no = _digital(file_result.get("special_no"))
    if kind == "special" and special_no is None and episode is not None:
        special_no = episode
    # 特典自己的序号（Menu - 01 / OVA - 24.5）：只给模板变量用，
    # special_no 仍保留原来的碟片盒号逻辑，保证默认命名不变
    typed_no = typed_special_number(stem) if kind == "special" else None
    special_no_raw = typed_no if typed_no is not None else special_no

    version = _digital(file_result.get("version")) or _digital(tech.get("version"))

    title = _pick_title(dirs, dir_results, settings, media_root)

    # 合集（一个视频含多集）：单独标记，不参与 SxxExx 命名，也不参与重复集判定
    collection_range = detect_collection(stem)
    collection = bool(collection_range) and not movie_hint and kind in ("episode", "unknown")

    warnings: list[str] = []
    if collection:
        warnings.append(f"识别为合集（{collection_range}），不做拆集，处理方式由你决定")
    if kind == "unknown":
        warnings.append("未能识别剧集 / 特典 / 剧场版")
    if not title:
        warnings.append("未能提取作品标题")
    if kind == "episode" and episode is None:
        warnings.append("未能识别集号")

    if size is None:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

    # 来源季 / 来源目录：特典模板用它区分「同一作品、同一类型、不同季」的文件。
    # 先看文件名里的 S2 / Season 2；没有就退回来源目录里识别到的季号。
    source_season = ""
    season_match = re.search(
        r"(?i)(?<![A-Za-z0-9])(?:s|season\s*)(\d{1,2})(?![0-9A-Za-z])", stem
    )
    if season_match:
        source_season = f"S{int(season_match.group(1))}"
    elif dir_season is not None:
        source_season = f"S{dir_season}"
    source_dir = title

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
        "special_no_raw": special_no_raw if special_no_raw is not None else "",
        "source_season": source_season,
        "source_dir": source_dir,
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
        "special_no_raw": special_no_raw,
        "group": group or "",
        "resolution": tech.get("resolution", ""),
        "codec": tech.get("codec", ""),
        "source": tech.get("source", ""),
        "status": status,
        "warnings": warnings,
        "collection": collection,
        "collection_range": collection_range,
        "fields": fields,
    }


_TYPED_INDEX_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:special|ncoped|ncop|nced|ova|oad|teaser|preview|menu|sp|pv|cm|yokoku|bonus)"
    r"[\s_.\-]*(?P<num>\d{1,3}(?:\.\d{1,2})?)"
)


def typed_special_number(stem: str) -> int | str | None:
    """取特典文件名里“同类特典自己的序号”（Menu - 01 / OVA - 24.5），忽略 [SPxx] 碟片盒号。"""
    text = BRACKET_RE.sub(" ", stem or "")
    match = _TYPED_INDEX_RE.search(text)
    if not match:
        return None
    raw = match.group("num")
    return raw if "." in raw else int(raw)


def special_label(stem: str) -> str | None:
    """从特典文件名里找更具体的类型词（Menu/NCOP/CM…），忽略 [SPxx] 这类碟片序号。"""
    text = BRACKET_RE.sub(" ", stem or "")
    for pattern, label in SPECIAL_LABEL_PATTERNS:
        if pattern.search(text):
            return label
    return None


_DISC_MARK_RE = re.compile(
    r"(?i)(?:disc|disk|dvd|bd|blu-?ray|vol(?:ume)?|part|pt)[\s._-]*0*(\d{1,3})(?![0-9])"
)
_SEASON_MARK_RE = re.compile(r"(?i)^s(\d{1,2})$")


def _special_season(row: dict) -> int:
    """特典归属的来源季：文件名 / 目录识别出的 Sxx 优先，未知按 0 算。"""
    fields = row.get("fields") or {}
    match = _SEASON_MARK_RE.match(str(fields.get("source_season") or "").strip())
    if match:
        return int(match.group(1))
    season = row.get("season")
    if isinstance(season, int) and season > 0:
        return season
    return 0


def _disc_order(row: dict) -> int | None:
    """从相对路径 / 文件名里取碟号（Disc-09 / DVD 3 / Vol.2），用于稳定排序。"""
    text = f"{row.get('rel_path') or ''} {row.get('filename') or ''}"
    match = _DISC_MARK_RE.search(text)
    return int(match.group(1)) if match else None


def _typed_order(row: dict) -> float | None:
    """文件自带的同类序号（OP02 的 2、OVA 24.5），只作为排序依据。"""
    value = row.get("special_no_raw")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _natural_key(text: str) -> tuple:
    """自然排序：Disc-9 排在 Disc-10 前面，而不是按字母序倒过来。"""
    parts = re.split(r"(\d+)", str(text or "").lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def _is_fractional(row: dict) -> bool:
    """24.5 这类小数序号：是作品语义的一部分，不参与连续编号。"""
    typed = _typed_order(row)
    return typed is not None and not float(typed).is_integer()


def _special_order(row: dict) -> tuple:
    disc = _disc_order(row)
    typed = _typed_order(row)
    return (
        0 if disc is not None else 1,
        disc or 0,
        0 if typed is None else 1,
        typed or 0.0,
        _natural_key(str(row.get("rel_path") or "")),
    )


def _renumber_kept(rows: list[dict]) -> None:
    """整目录保留、不改名的特典：沿用旧编号，避免无意义的元数据变化。"""
    buckets: dict[tuple[object, ...], list[dict]] = {}
    for row in rows:
        title = str(row.get("title") or "").strip()
        year = row.get("year") if isinstance(row.get("year"), int) else ""
        stem = str((row.get("fields") or {}).get("original_stem") or row.get("filename") or "")
        type_name = special_label(stem) or str(row.get("special_type") or "SP")
        key: tuple[object, ...] = (title.lower(), year, type_name)
        if not title:
            key = (str(row.get("group_id") or ""), "", type_name)
        buckets.setdefault(key, []).append(row)

    for key, bucket in buckets.items():
        type_name = str(key[2])
        numbers = [
            item.get("special_no") if isinstance(item.get("special_no"), int) else None
            for item in bucket
        ]
        duplicate = any(numbers.count(value) > 1 for value in numbers) if len(bucket) > 1 else False
        bucket.sort(key=lambda item: str(item.get("rel_path") or ""))
        for number, row in enumerate(bucket, 1):
            row["special_type"] = type_name
            fields = row.setdefault("fields", {})
            fields["special_type"] = type_name
            if not duplicate:
                continue
            row["special_no"] = number
            fields["special_no"] = number


def renumber_specials(rows: list[dict], settings: dict | None = None) -> None:
    """按「作品 + 来源季 + 特典类型」统一编号，保证同季同类型内唯一、连续。

    规则：
    - 不同类型（OP / ED / NCOP / NCED / SP / CM …）各自一份计数器，互不消耗；
    - 排序先按碟号（Disc-XX / DVD 3 / Vol.2），再按文件自带的同类序号（OP02 → 2），
      最后才按路径自然序，避免路径字母序变化导致换号；
    - 只有一条的特典保持原样（原本没号就不硬加），避免把孤立的 OVA / NCOP 改成 XX1；
    - 特典目录整目录保留（默认策略）时不改名，沿用旧编号；改成进 Season 00 时才生效。
    """
    policy = str((settings or {}).get("execution.special_video_policy") or "root")
    kept: list[dict] = []
    active: list[dict] = []
    fractional: list[dict] = []
    for row in rows:
        if row.get("category") != "video" or row.get("kind") != "special":
            continue
        if row.get("special_folder") and policy != "season":
            kept.append(row)
        elif _is_fractional(row):
            # 24.5 这类小数是作品语义的一部分，不参与连续编号
            fractional.append(row)
        else:
            active.append(row)
    _renumber_kept(kept)
    for row in fractional:
        stem = str((row.get("fields") or {}).get("original_stem") or row.get("filename") or "")
        type_name = special_label(stem) or str(row.get("special_type") or "SP")
        row["special_type"] = type_name
        row.setdefault("fields", {})["special_type"] = type_name

    buckets: dict[tuple[object, ...], list[dict]] = {}
    for row in active:
        title = str(row.get("title") or "").strip()
        year = row.get("year") if isinstance(row.get("year"), int) else ""
        stem = str((row.get("fields") or {}).get("original_stem") or row.get("filename") or "")
        type_name = special_label(stem) or str(row.get("special_type") or "SP")
        key: tuple[object, ...] = (
            str(row.get("group_id") or ""),
            title.lower(),
            year,
            _special_season(row),
            type_name,
        )
        buckets.setdefault(key, []).append(row)

    for key, bucket in buckets.items():
        type_name = str(key[4])
        bucket.sort(key=_special_order)
        for number, row in enumerate(bucket, 1):
            row["special_type"] = type_name
            fields = row.setdefault("fields", {})
            fields["special_type"] = type_name
            if len(bucket) == 1:
                # 单条特典不强行加号：没有号就保持没有号，有号也保留原号。
                continue
            row["special_no"] = number
            fields["special_no"] = number


def parse_asset(
    media_root: Path,
    path: Path,
    settings: dict,
    rules: list[Rule],
    category: str,
    size: int | None = None,
) -> dict:
    """非视频文件（字幕、音频、图片等）只需要标题与基础信息，不做集号解析。"""
    rel = path.relative_to(media_root)
    dirs = list(rel.parts[:-1])
    dir_results = [match_rules(part, "dirname", rules) for part in dirs]
    filename = path.name
    stem, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower()
    title = _pick_title(dirs, dir_results, settings, media_root)

    if size is None:
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
