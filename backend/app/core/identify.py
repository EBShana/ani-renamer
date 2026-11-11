"""作品识别：以「视频所在上一级普通文件夹名」为准 + 文件名辅助。

识别结果直接写回扫描记录里的每个视频文件：

    row["identify"] = {
        "work_id": "w:<组>:<标题>|<年份>",  # 空字符串表示还没归属
        "title": "...", "year": 2022,
        "confidence": "high" | "medium" | "low" | "none",
        "source": "folder" | "filename" | "assignment" | "manual" | "alias" | "none",
        "segment": "用于记住映射的名称段",
        "needs_confirm": bool,
        "ignored": bool,
        "candidates": [{"title", "source", "weight"}],
    }

约定：
- 作品名优先取上一级普通文件夹；`Season xx / SPs / Specials / CDs / Scans / Subs / Anime / TV`
  等收纳目录会跳过，继续往上找；
- 文件名只作为辅助信息：吻合时提高可信度，对不上时降级为「需要你确认」；
- 只有 high（唯一确定）才自动生成 work_id，并标记为「待确认」；
- medium / low 不自动归属，进入未归属区交给用户指派；
- 未归属的文件不能在预览 / 执行里处理。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from .lexicon import (
    BUILTIN_GENERIC_DIRS,
    EPISODE_MARKER_PATTERNS,
    OVA_DIR_NAMES,
    SEASON_DIR_NAME_RE,
    VERSION_MARK_RE,
    split_dir_names,
    special_dir_name_set,
)
from .parser import clean_dir_title, strip_release_noise
from .techname import BRACKET_RE, extract_year, is_tag_content

GENERIC_TITLE_WORDS = {
    "movie", "ova", "oad", "complete", "合集", "全集", "tv", "sp", "season",
    "special", "specials", "bonus", "extras", "vol", "volume", "part",
}

_YEAR_RE = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])(?![xX]\d)(?![pP])")
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
    for pattern in EPISODE_MARKER_PATTERNS:
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

    for pattern in EPISODE_MARKER_PATTERNS:
        text = pattern.sub(" ", text)

    # 去掉“恰好等于已识别集号”的尾部数字（避免把作品名里的 100 当集号删掉）
    if isinstance(episode, int) and episode >= 0:
        text = re.sub(rf"(?<=\s|[-_.])0*{episode}(?![0-9])", " ", text)

    text = VERSION_MARK_RE.sub(" ", text)
    text = _YEAR_RE.sub(" ", text)
    text = strip_release_noise(text)
    text = text.replace("_", " ")
    text = _SPLIT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.·:：@")

    # 还有分隔符时取最长的一段（通常是作品名，后面是副标题 / 标签）
    pieces = [piece.strip(" -_.·:：@") for piece in re.split(r"\s+-\s+", text) if piece.strip()]
    if len(pieces) > 1:
        text = max(pieces, key=len)
    return text.strip(" -_.·:：@")


def _generic_dirs(settings: dict) -> set[str]:
    names = split_dir_names(settings.get("parse.generic_dir_names", ""))
    names |= special_dir_name_set(settings.get("parse.special_dir_names", ""))
    return names | BUILTIN_GENERIC_DIRS | OVA_DIR_NAMES


def _is_organizer_dir(name: str, generic: set[str]) -> bool:
    lowered = name.strip().lower()
    return lowered in generic or bool(SEASON_DIR_NAME_RE.fullmatch(lowered))


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


def _alias_index(aliases: list[dict]) -> dict[str, list[dict]]:
    """别名只做“规范化后完全相同”的匹配，绝不按相似度合并作品。"""
    index: dict[str, list[dict]] = {}
    for item in aliases:
        alias = _norm(str(item.get("alias") or ""))
        title = str(item.get("title") or "").strip()
        if not alias or not title:
            continue
        bucket = index.setdefault(alias, [])
        if not any(
            _norm(str(existing.get("title") or "")) == _norm(title)
            and existing.get("year") == item.get("year")
            for existing in bucket
        ):
            bucket.append(item)
    return index


def _corroborating_folder(segment: str, folders: list[str]) -> str:
    """文件名和目录名是否互相印证（完全相同，或一方完整包含另一方）。"""
    target = _norm(segment)
    if len(target) < 2:
        return ""
    for name in folders:
        other = _norm(name)
        if not other:
            continue
        if other == target or (len(other) >= 2 and other in target) or target in other:
            return name
    return ""


def apply_identification(
    scan: dict,
    assignments: list[dict],
    settings: dict,
    aliases: list[dict] | None = None,
) -> dict:
    """扫描完成后调用：给每个视频行写入 identify 信息，并同步 fields.series。"""
    files = scan.get("files") or []
    videos = [row for row in files if row.get("category") == "video"]
    min_length = int(settings.get("parse.min_title_length", 2) or 2)
    index = _assignment_index(assignments)
    alias_index = _alias_index(aliases or [])

    folder_cache: dict[str, list[str]] = {}
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
        # 特典文件名通常只有 SP/NCOP/Menu 这类标记，不能当作作品名去和目录名“对不上”
        if str(row.get("kind")) == "special" and folders:
            segment = ""
        prepared.append((row, group_id, parent_rel, folders, segment))

    for row, group_id, parent_rel, folders, segment in prepared:
        fields = row.setdefault("fields", {})

        folder_title = folders[0] if folders else ""
        candidates: list[dict] = []
        for depth, name in enumerate(folders):
            candidates.append(
                {"title": name, "source": "folder", "weight": max(1.0, 2.6 - depth * 0.6)}
            )
        if segment:
            candidates.append({"title": segment, "source": "filename", "weight": 2.2})

        year = row.get("year")
        if not isinstance(year, int):
            year = extract_year(str(fields.get("original_stem") or ""))
        if not isinstance(year, int):
            year = None

        seg_norm = _norm(segment)
        folder_norm = _norm(folder_title)
        assignment = None
        assignment_key = ""
        if seg_norm and index.get((group_id, seg_norm)):
            assignment = index[(group_id, seg_norm)]
            assignment_key = segment
        elif folder_norm and index.get((group_id, folder_norm)):
            assignment = index[(group_id, folder_norm)]
            assignment_key = folder_title

        alias_matches = None
        alias_key = ""
        if seg_norm and alias_index.get(seg_norm):
            alias_matches = alias_index[seg_norm]
            alias_key = segment
        elif folder_norm and alias_index.get(folder_norm):
            alias_matches = alias_index[folder_norm]
            alias_key = folder_title

        reason = ""
        if assignment:
            title = str(assignment.get("title") or assignment_key)
            assigned_year = assignment.get("year")
            if isinstance(assigned_year, int):
                year = assigned_year
            confidence = "high"
            source = "assignment"
            reason = (
                f"记住的规则：名称「{assignment_key}」在目录组「{group_id}」里对应「{title}」"
            )
        elif alias_matches and len(alias_matches) == 1:
            alias = alias_matches[0]
            title = str(alias.get("title") or alias_key)
            alias_year = alias.get("year")
            if isinstance(alias_year, int):
                year = alias_year
            confidence = "high"
            source = "alias"
            reason = f"别名「{alias_key}」对应作品「{title}」（你之前确认过的写法）"
        elif folder_title:
            # 本轮的主要判定依据：上一级普通文件夹名
            title = folder_title
            source = "folder"
            corroborated = _corroborating_folder(segment, [folder_title]) if segment else ""
            if segment and not corroborated:
                confidence = "medium"
                reason = (
                    f"以上一级文件夹「{folder_title}」为准；文件名看起来是「{segment}」，"
                    "两者对不上，需要你确认后才能改名"
                )
            else:
                confidence = "high"
                reason = (
                    f"以上一级文件夹「{folder_title}」为准，文件名「{segment}」也与它一致"
                    if segment
                    else f"以上一级文件夹「{folder_title}」为准（文件名里没有可用的作品名）"
                )
        elif segment:
            # 平铺在根目录，或者上级只有收纳目录：文件名降级为辅助信息
            title = segment
            confidence = "medium"
            source = "filename"
            reason = f"上级没有可用的作品文件夹，只能从文件名「{segment}」推断，需要你确认"
        else:
            title = ""
            confidence = "none"
            source = "none"
            reason = "上级文件夹和文件名都没有足够信息"

        row["identify"] = {
            "work_id": "",
            "title": title,
            "year": year,
            "confidence": confidence,
            "source": source,
            "segment": folder_title or segment,
            "needs_confirm": confidence == "high" and source not in ("assignment", "manual", "alias"),
            "ignored": False,
            "candidates": candidates,
            "reason": reason,
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
                    "reason": str(ident.get("reason") or ""),
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
                    "reason": str(ident.get("reason") or ""),
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
                "segment": str(ident.get("segment") or ""),
                "reason": str(ident.get("reason") or "文件名和目录名都没有足够信息"),
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
                        "segment": str(
                            (_row_identify(row) or {}).get("segment") or ""
                        ),
                        "reason": str(
                            (_row_identify(row) or {}).get("reason")
                            or "只识别出候选名称，需要确认"
                        ),
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
        ident = _row_identify(row)
        if str(ident.get("work_id") or "") != work_id:
            continue
        ident["needs_confirm"] = False
        count += 1
    return count


def confirm_all(scan: dict) -> int:
    """把「已指派待确认」里的作品一次全部确认；返回确认的文件条数。"""
    count = 0
    for row in scan.get("files") or []:
        if str(row.get("category")) != "video":
            continue
        ident = _row_identify(row)
        if not ident.get("work_id") or not ident.get("needs_confirm"):
            continue
        ident["needs_confirm"] = False
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


def _folder_match_score(folder_name: str, work_title: str) -> tuple[int, str]:
    """特典目录名与作品名的匹配强度：完全相同优先，其次是一方包含另一方。"""
    folder = _norm(folder_name)
    title = _norm(work_title)
    if not folder or not title:
        return 0, ""
    if folder == title:
        return 3, "目录名与作品名完全相同"
    if title in folder:
        return 2, f"目录名「{folder_name}」包含作品名「{work_title}」"
    if folder in title:
        return 2, f"作品名「{work_title}」包含目录名「{folder_name}」"
    return 0, ""


def special_suggestions(
    scan: dict,
    aliases: list[dict] | None = None,
    min_length: int = 2,
) -> list[dict]:
    """为扫描时跳过的特典目录给出作品候选；只推荐，不自动套用。"""
    works: dict[str, dict] = {}
    # 目录结构索引：某个上级目录（含更上层）下出现过哪些作品。
    subtree_works: dict[str, dict[str, dict]] = {}
    for row in scan.get("files") or []:
        if row.get("category") != "video":
            continue
        ident = _row_identify(row)
        work_id = str(ident.get("work_id") or "")
        title = str(ident.get("title") or "").strip()
        if not work_id or not title:
            continue
        work = works.setdefault(
            work_id,
            {
                "work_id": work_id,
                "title": title,
                "year": ident.get("year") if isinstance(ident.get("year"), int) else None,
            },
        )
        parts = [part for part in str(row.get("rel_path") or "").split("/")[:-1] if part]
        for depth in range(len(parts)):
            subtree_works.setdefault("/".join(parts[:depth]), {}).setdefault(work_id, work)

    alias_titles = {
        (_norm(str(item.get("title") or "")), item.get("year")): str(item.get("alias") or "")
        for item in (aliases or [])
        if str(item.get("alias") or "").strip() and str(item.get("title") or "").strip()
    }
    decisions = scan.get("special_decisions") or {}
    result: list[dict] = []

    for item in scan.get("ignored_dirs") or []:
        if not isinstance(item, dict) or str(item.get("reason") or "") != "special":
            continue
        gid = str(item.get("group_id") or "")
        rel = str(item.get("path") or "").strip("/")
        if not rel:
            continue
        folder_key = f"{gid}:{rel}"
        names = [part for part in rel.split("/") if part]
        candidates: list[dict] = []
        for work in works.values():
            best_score = 0
            best_reason = ""
            for name in names:
                score, reason = _folder_match_score(name, work["title"])
                if score > best_score:
                    best_score, best_reason = score, reason
            alias_text = alias_titles.get(
                (_norm(work["title"]), work["year"])
            ) or ""
            if alias_text:
                for name in names:
                    score, reason = _folder_match_score(name, alias_text)
                    if score >= 2 and score >= best_score:
                        best_score = score
                        best_reason = f"{reason}（这是作品「{work['title']}」的别名）"
            if best_score:
                candidates.append(
                    {
                        **work,
                        "score": best_score,
                        "reason": best_reason,
                    }
                )
        # 目录结构兜底：特典目录所在的上级目录里如果只有一个作品，结构上只能是它
        parent = "/".join(names[:-1])
        siblings = subtree_works.get(parent) or {}
        if len(siblings) == 1:
            only = next(iter(siblings.values()))
            if not any(entry["work_id"] == only["work_id"] for entry in candidates):
                candidates.append(
                    {
                        **only,
                        "score": 1,
                        "reason": (
                            f"特典目录的上级目录「{parent or '扫描根目录'}」里只有作品"
                            f"「{only['title']}」，按目录结构归属"
                        ),
                    }
                )
        candidates.sort(key=lambda entry: (-int(entry["score"]), entry["title"]))
        decision = decisions.get(folder_key) if isinstance(decisions, dict) else None
        top = candidates[0] if candidates else None
        if decision:
            reason = f"你已经确认这个特典目录归属「{decision.get('title')}」"
        elif top:
            reason = f"{top['reason']}，请确认是否归到「{top['title']}」；不确认就保持原地"
        else:
            reason = "目录名对不上扫描到的任何作品，默认原地不动"
        result.append(
            {
                "folder_key": folder_key,
                "group_id": gid,
                "rel_path": rel,
                "name": rel.split("/")[-1],
                "candidates": candidates,
                "confirmed": decision,
                "reason": reason,
            }
        )
    result.sort(key=lambda entry: (entry["group_id"], entry["rel_path"]))
    return result


def apply_special_memory(scan: dict, entries: list[dict] | None) -> int:
    """把记住的特典目录归属直接套用到本次扫描，不用再点一次确认。

    只对本次扫描里确实存在、且用户还没手动改过的特典目录生效。
    """
    if not entries:
        return 0
    decisions = scan.setdefault("special_decisions", {})
    known = {
        (str(item.get("group_id") or ""), str(item.get("path") or "").strip("/"))
        for item in scan.get("ignored_dirs") or []
        if isinstance(item, dict) and str(item.get("reason") or "") == "special"
    }
    count = 0
    for entry in entries:
        key = (
            str(entry.get("group_id") or ""),
            str(entry.get("rel_path") or "").strip("/"),
        )
        if key not in known:
            continue
        folder_key = f"{key[0]}:{key[1]}"
        if folder_key in decisions:
            continue
        decisions[folder_key] = {
            "work_id": str(entry.get("work_id") or ""),
            "title": str(entry.get("title") or ""),
            "year": entry.get("year") if isinstance(entry.get("year"), int) else None,
            "remembered": True,
        }
        count += 1
    return count


def assign_special_folder(
    scan: dict, folder_key: str, work_id: str, title: str, year: int | None
) -> None:
    decisions = scan.setdefault("special_decisions", {})
    decisions[folder_key] = {
        "work_id": work_id,
        "title": title,
        "year": year,
    }


def clear_special_folder(scan: dict, folder_key: str) -> None:
    decisions = scan.get("special_decisions")
    if isinstance(decisions, dict):
        decisions.pop(folder_key, None)
