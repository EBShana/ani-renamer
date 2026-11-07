"""作品识别第一步：文件名优先 + 目录回退 + 疑似作品 + 未归属。

识别结果直接写回扫描记录里的每个视频文件：

    row["identify"] = {
        "work_id": "w:<组>:<标题>|<年份>",  # 空字符串表示还没归属
        "title": "...", "year": 2022,
        "confidence": "high" | "medium" | "low" | "none",
        "source": "filename" | "folder" | "assignment" | "manual" | "none",
        "segment": "用于记住映射的名称段",
        "needs_confirm": bool,
        "ignored": bool,
        "candidates": [{"title", "source", "weight"}],
    }

约定：
- 只有 high（唯一确定）才自动套用并生成 work_id；
- medium / low 不自动归属，聚合成“疑似作品”交给用户确认；
- 未归属的文件不能在预览 / 执行里处理。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from .parser import OVA_DIR_NAMES, clean_dir_title
from .techname import BRACKET_RE, extract_year, is_tag_content

SEASON_DIR_RE = re.compile(r"(?i)^(season\s*\d{1,2}|s\d{1,2}|第[0-9一二三四五六七八九十百零两]+季)$")

BUILTIN_GENERIC_DIRS = {
    "anime", "animation", "动画", "番剧", "tv", "movies", "movie", "media", "videos",
    "video", "downloads", "download", "subs", "sub", "subtitles", "字幕", "fonts",
    "cds", "scans", "sp", "sps", "specials", "special", "bonus", "extras", "menu",
    "pv", "cm", "preview", "trailer", "ova", "ovas", "oad", "oads",
}

GENERIC_TITLE_WORDS = {
    "movie", "ova", "oad", "complete", "合集", "全集", "tv", "sp", "season",
    "special", "specials", "bonus", "extras", "vol", "volume", "part",
}

# 集号 / 特典标记：命中后从文件名里去掉
_EPISODE_PATTERNS = [
    re.compile(r"(?i)\bS\d{1,2}E\d{1,4}\b"),
    re.compile(r"(?i)\bS\d{1,2}\b"),
    re.compile(r"(?i)\b(?:EP|E)\.?\s?\d{1,4}\b"),
    re.compile(r"(?i)\b(?:NCOP|NCED|PV|CM|MENU|SP|OVA|OAD)\s?\d{0,3}\b"),
    re.compile(r"第\s*[0-9一二三四五六七八九十百零两]+\s*[话話集回]"),
]
_VERSION_RE = re.compile(r"(?i)(?<![A-Za-z])v\d{1,2}(?![A-Za-z])")
_YEAR_RE = re.compile(r"(?:^|[^\d])((?:19|20)\d{2})(?:[^\d]|$)")
_SPLIT_RE = re.compile(r"[|/\\]+")


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def _meaningful(text: str, min_length: int) -> bool:
    value = (text or "").strip()
    if len(value) < min_length:
        return False
    if value.isdigit():
        return False
    tokens = [token for token in re.split(r"[\s._-]+", value.lower()) if token]
    if tokens and all(token in GENERIC_TITLE_WORDS for token in tokens):
        return False
    if value.lower() in GENERIC_TITLE_WORDS:
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", value))


def _strip_known_group(stem: str) -> str:
    """去掉开头的发布组方括号；只有当它不是唯一有效内容时才删。"""
    chunks = [match.group(1).strip() for match in BRACKET_RE.finditer(stem)]
    meaningful = [
        chunk
        for chunk in chunks
        if chunk
        and not chunk.isdigit()
        and not is_tag_content(chunk)
        and not re.fullmatch(r"(19|20)\d{2}", chunk)
    ]
    if not meaningful:
        return stem

    outside = BRACKET_RE.sub(" ", stem)
    outside = _YEAR_RE.sub(" ", outside)
    for pattern in _EPISODE_PATTERNS:
        outside = pattern.sub(" ", outside)
    outside_ok = bool(re.search(r"[A-Za-z\u4e00-\u9fff]{2,}", outside))

    if len(meaningful) >= 2 or (len(meaningful) == 1 and outside_ok):
        return stem.replace(f"[{meaningful[0]}]", " ")
    return stem


def _bracket_repl(match: re.Match[str]) -> str:
    content = match.group(1).strip()
    if not content:
        return " "
    if content.isdigit():
        return " "
    if re.fullmatch(r"(19|20)\d{2}", content):
        return " "
    if is_tag_content(content):
        return " "
    return f" {content} "


def _paren_repl(match: re.Match[str]) -> str:
    content = match.group(1).strip()
    if not content:
        return " "
    if re.fullmatch(r"(19|20)\d{2}", content):
        return " "
    if is_tag_content(content):
        return " "
    if content.upper() in ("TV", "MOVIE", "OVA", "WEB"):
        return " "
    return f" {content} "


def filename_segment(stem: str, episode: int | None = None) -> str:
    """从文件名里提取作品名候选（不含扩展名）。"""
    text = _strip_known_group(stem or "")
    text = BRACKET_RE.sub(_bracket_repl, text)
    text = re.sub(r"[（(]([^）)]*)[）)]", _paren_repl, text)

    for pattern in _EPISODE_PATTERNS:
        text = pattern.sub(" ", text)

    # 去掉“恰好等于已识别集号”的尾部数字（避免把作品名里的 100 当集号删掉）
    if isinstance(episode, int) and episode >= 0:
        text = re.sub(rf"(?<=\s|[-_.])0*{episode}(?![0-9])", " ", text)

    text = _VERSION_RE.sub(" ", text)
    text = _YEAR_RE.sub(" ", text)
    text = text.replace("_", " ")
    text = _SPLIT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.·:：@")

    # 还有分隔符时取最长的一段（通常是作品名，后面是副标题 / 标签）
    pieces = [piece.strip(" -_.·:：@") for piece in re.split(r"\s+-\s+", text) if piece.strip()]
    if len(pieces) > 1:
        text = max(pieces, key=len)
    return text.strip(" -_.·:：@")


def _generic_dirs(settings: dict) -> set[str]:
    names = {
        item.strip().lower()
        for item in str(settings.get("parse.generic_dir_names", "")).split(",")
        if item.strip()
    }
    names |= {
        item.strip().lower()
        for item in str(settings.get("parse.special_dir_names", "")).split(",")
        if item.strip()
    }
    return names | BUILTIN_GENERIC_DIRS | OVA_DIR_NAMES


def _is_organizer_dir(name: str, generic: set[str]) -> bool:
    lowered = name.strip().lower()
    return lowered in generic or bool(SEASON_DIR_RE.fullmatch(lowered))


def folder_candidates(rel_path: str, settings: dict, min_length: int) -> list[str]:
    """逐层往上收集候选文件夹名；跳过季目录 / 特典目录 / 大类目录。"""
    parts = list(PurePosixPath(rel_path.replace("\\", "/")).parts[:-1])
    generic = _generic_dirs(settings)
    result: list[str] = []
    seen: set[str] = set()
    for part in reversed(parts):
        if _is_organizer_dir(part, generic):
            continue
        candidate = clean_dir_title(part)
        key = _norm(candidate)
        if not key or key in seen:
            continue
        if not _meaningful(candidate, min_length):
            continue
        seen.add(key)
        result.append(candidate)
    return result


def work_id_for(group_id: str, title: str, year: int | None) -> str:
    return f"w:{group_id or ''}:{_norm(title)}|{year if isinstance(year, int) else ''}"


def _assignment_index(assignments: list[dict]) -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for item in assignments:
        group_id = str(item.get("group_id") or "")
        segment = _norm(str(item.get("segment") or ""))
        if not segment:
            continue
        index[(group_id, segment)] = item
    return index


def apply_identification(scan: dict, assignments: list[dict], settings: dict) -> dict:
    """扫描完成后调用：给每个视频行写入 identify 信息，并同步 fields.series。"""
    files = scan.get("files") or []
    videos = [row for row in files if row.get("category") == "video"]
    min_length = int(settings.get("parse.min_title_length", 2) or 2)
    index = _assignment_index(assignments)

    folder_cache: dict[str, list[str]] = {}
    parent_segments: dict[tuple[str, str], set[str]] = {}
    prepared: list[tuple[dict, str, str, list[str], str]] = []

    for row in videos:
        fields = row.setdefault("fields", {})
        group_id = str(row.get("group_id") or "")
        rel = str(row.get("rel_path") or "")
        parent_rel = "/".join(rel.replace("\\", "/").split("/")[:-1])
        episode = row.get("episode")
        if not isinstance(episode, int):
            episode = row.get("special_no") if isinstance(row.get("special_no"), int) else None

        segment = filename_segment(str(fields.get("original_stem") or ""), episode)
        if not _meaningful(segment, min_length):
            segment = ""
        if parent_rel not in folder_cache:
            folder_cache[parent_rel] = folder_candidates(rel, settings, min_length)
        folders = folder_cache[parent_rel]
        if segment:
            parent_segments.setdefault((group_id, parent_rel), set()).add(_norm(segment))
        prepared.append((row, group_id, parent_rel, folders, segment))

    for row, group_id, parent_rel, folders, segment in prepared:
        fields = row.setdefault("fields", {})

        candidates: list[dict] = []
        if segment:
            candidates.append({"title": segment, "source": "filename", "weight": 3})
        for depth, name in enumerate(folders):
            candidates.append(
                {"title": name, "source": "folder", "weight": max(1.0, 2.0 - depth * 0.5)}
            )

        year = row.get("year")
        if not isinstance(year, int):
            year = extract_year(str(fields.get("original_stem") or ""))
        if not isinstance(year, int):
            year = None

        assignment = index.get((group_id, _norm(segment))) if segment else None
        if assignment:
            title = str(assignment.get("title") or segment)
            assigned_year = assignment.get("year")
            if isinstance(assigned_year, int):
                year = assigned_year
            confidence = "high"
            source = "assignment"
        elif segment:
            # 同一目录里出现多个不同名称段时，说明“不确定”，必须交给用户选
            same_parent = parent_segments.get((group_id, parent_rel), set())
            confidence = "high" if folders and len(same_parent) <= 1 else "medium"
            source = "filename"
            title = segment
        elif folders:
            title = folders[0]
            confidence = "low"
            source = "folder"
        else:
            title = ""
            confidence = "none"
            source = "none"

        row["identify"] = {
            "work_id": "",
            "title": title,
            "year": year,
            "confidence": confidence,
            "source": source,
            "segment": segment or (folders[0] if folders else ""),
            "needs_confirm": confidence == "high" and source not in ("assignment", "manual"),
            "ignored": False,
            "candidates": candidates,
        }
        if title:
            fields["series"] = title
            if year is not None:
                fields["year"] = year
            row["title"] = title
            row["year"] = year

    # 同一组、同名、同年归到同一个作品；只有 high 才自动生成 work_id
    clusters: dict[tuple[str, str, int | None], list[dict]] = {}
    for row in videos:
        ident = row.get("identify") or {}
        title = str(ident.get("title") or "")
        if not title:
            continue
        key = (str(row.get("group_id") or ""), _norm(title), ident.get("year") if isinstance(ident.get("year"), int) else None)
        clusters.setdefault(key, []).append(row)

    for (group_id, _norm_title, year), rows in clusters.items():
        if not any(row["identify"].get("confidence") == "high" for row in rows):
            continue
        title = str(rows[0]["identify"].get("title") or "")
        work_id = work_id_for(group_id, title, year)
        for row in rows:
            ident = row["identify"]
            ident["work_id"] = work_id
            ident["year"] = year
            # 自动识别的作品统一标记为“待确认”，用户可以在未归属区一次确认
            if ident.get("source") not in ("assignment", "manual"):
                ident["needs_confirm"] = True
            row["fields"]["series"] = title
            if year is not None:
                row["fields"]["year"] = year

    return scan


def _row_identify(row: dict) -> dict:
    ident = row.get("identify")
    return ident if isinstance(ident, dict) else {}


def build_overview(scan: dict) -> dict:
    """读取识别结果，整理成「作品 / 疑似作品 / 单个未归属文件 / 已指派待确认」。"""
    files = scan.get("files") or []
    videos = [row for row in files if row.get("category") == "video"]

    works: dict[str, dict] = {}
    suspected: dict[str, dict] = {}
    unassigned: list[dict] = []

    for row in videos:
        ident = _row_identify(row)
        if ident.get("ignored"):
            continue
        work_id = str(ident.get("work_id") or "")
        fields = row.get("fields") or {}
        if work_id:
            work = works.setdefault(
                work_id,
                {
                    "work_id": work_id,
                    "title": str(ident.get("title") or row.get("title") or ""),
                    "year": ident.get("year") if isinstance(ident.get("year"), int) else None,
                    "confidence": str(ident.get("confidence") or ""),
                    "source": str(ident.get("source") or ""),
                    "needs_confirm": bool(ident.get("needs_confirm")),
                    "file_ids": [],
                    "files": [],
                    "seasons": set(),
                    "video_count": 0,
                    "episode_count": 0,
                    "special_count": 0,
                    "movie_count": 0,
                    "matched": None,
                },
            )
            work["file_ids"].append(row.get("id"))
            work["files"].append(row.get("filename"))
            work["video_count"] += 1
            kind = row.get("kind")
            if kind == "episode":
                work["episode_count"] += 1
            elif kind == "special":
                work["special_count"] += 1
            elif kind == "movie":
                work["movie_count"] += 1
            if isinstance(row.get("season"), int):
                work["seasons"].add(row["season"])
            if not work["needs_confirm"] and bool(ident.get("needs_confirm")):
                work["needs_confirm"] = True
            if str(fields.get("tmdb_id") or "").strip():
                work["matched"] = {
                    "tmdb_id": fields.get("tmdb_id"),
                    "name": str(fields.get("series") or ""),
                }
            continue

        title = str(ident.get("title") or "")
        segment = str(ident.get("segment") or title)
        confidence = str(ident.get("confidence") or "none")
        if title and segment:
            key = f"{row.get('group_id') or ''}|{_norm(segment)}"
            entry = suspected.setdefault(
                key,
                {
                    "key": key,
                    "group_id": str(row.get("group_id") or ""),
                    "segment": segment,
                    "title": title,
                    "year": ident.get("year") if isinstance(ident.get("year"), int) else None,
                    "file_ids": [],
                    "files": [],
                    "count": 0,
                    "confidence": confidence,
                },
            )
            entry["file_ids"].append(row.get("id"))
            entry["files"].append(row.get("filename"))
            entry["count"] += 1
            continue

        unassigned.append(
            {
                "file_id": row.get("id"),
                "filename": row.get("filename"),
                "path": row.get("path"),
                "rel_path": row.get("rel_path"),
                "group_id": str(row.get("group_id") or ""),
                "reason": "文件名和目录名都没有足够信息",
            }
        )

    # 只有 2 个以上文件的疑似作品才单独列出，单个的归到「单个未归属文件」
    suspected_list: list[dict] = []
    for entry in suspected.values():
        if entry["count"] >= 2:
            entry.pop("confidence", None)
            suspected_list.append(entry)
        else:
            row = next((item for item in videos if item.get("id") == entry["file_ids"][0]), None)
            if row is not None:
                unassigned.append(
                    {
                        "file_id": row.get("id"),
                        "filename": row.get("filename"),
                        "path": row.get("path"),
                        "rel_path": row.get("rel_path"),
                        "group_id": str(row.get("group_id") or ""),
                        "reason": "只识别出候选名称，需要确认",
                    }
                )

    works_list = []
    for work in works.values():
        work["seasons"] = sorted(work["seasons"])
        works_list.append(work)
    works_list.sort(key=lambda item: (item["title"], item["year"] or 0))
    suspected_list.sort(key=lambda item: (-item["count"], item["segment"]))
    unassigned.sort(key=lambda item: (item.get("filename") or ""))

    pending = [dict(item) for item in works_list if item["needs_confirm"]]
    return {
        "works": works_list,
        "pending": pending,
        "suspected": suspected_list,
        "unassigned": unassigned,
        "summary": {
            "works": len(works_list),
            "auto": sum(1 for item in works_list if item["source"] != "manual"),
            "pending": len(pending),
            "suspected": len(suspected_list),
            "unassigned": len(unassigned),
        },
    }


def assign_rows(scan: dict, file_ids: list[str], title: str, year: int | None) -> int:
    """把一批文件指派到指定作品（已有作品或新建作品）。"""
    selected = {str(item) for item in file_ids}
    count = 0
    for row in scan.get("files") or []:
        if str(row.get("id")) not in selected:
            continue
        if row.get("category") != "video":
            continue
        identify = row.setdefault("identify", {})
        group_id = str(row.get("group_id") or "")
        identify.update(
            {
                "work_id": work_id_for(group_id, title, year),
                "title": title,
                "year": year,
                "confidence": "high",
                "source": "manual",
                "needs_confirm": False,
                "ignored": False,
            }
        )
        fields = row.setdefault("fields", {})
        fields["series"] = title
        if year is not None:
            fields["year"] = year
        row["title"] = title
        row["year"] = year
        count += 1
    return count


def confirm_work(scan: dict, work_id: str) -> int:
    count = 0
    for row in scan.get("files") or []:
        if str(row.get("category")) != "video":
            continue
        identify = _row_identify(row)
        if str(ident.get("work_id") or "") != work_id:
            continue
        identify["needs_confirm"] = False
        count += 1
    return count


def ignore_segment(scan: dict, group_id: str, segment: str) -> int:
    """忽略一组疑似作品：只对“相同目录组 + 相同名称段”的未归属文件生效。"""
    target = _norm(segment)
    count = 0
    for row in scan.get("files") or []:
        if str(row.get("category")) != "video":
            continue
        identify = _row_identify(row)
        if identify.get("work_id"):
            continue
        if str(row.get("group_id") or "") != group_id:
            continue
        if _norm(str(identify.get("segment") or "")) != target:
            continue
        identify["ignored"] = True
        count += 1
    return count
