"""扫描结果按“作品组”聚合，并把选定的元数据写回字段。"""

from __future__ import annotations

import re
import unicodedata


def normalize_title(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
    return value


def group_id_for(title: str, year: int | None) -> str:
    return f"{normalize_title(title)}|{year or ''}"


def build_groups(files: list[dict]) -> list[dict]:
    """把视频文件按“作品”聚合。

    第 3 步起优先使用识别结果里的 work_id：同一部作品可以分布在多个文件夹（多季、特典外置），
    未归属（没有 work_id）的文件不进入作品列表，交给“未归属区”处理。
    """
    groups: dict[str, dict] = {}
    for row in files:
        if row.get("category") != "video":
            continue
        identify = row.get("identify") if isinstance(row.get("identify"), dict) else None
        if identify is not None:
            key = str(identify.get("work_id") or "")
            if not key:
                continue
            title = str(identify.get("title") or row.get("title") or "").strip()
            raw_year = identify.get("year")
            confidence = str(identify.get("confidence") or "")
            needs_confirm = bool(identify.get("needs_confirm"))
            source = str(identify.get("source") or "")
        else:
            # 兼容没有识别信息的旧扫描结果
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            raw_year = row.get("year")
            key = group_id_for(title, raw_year if isinstance(raw_year, int) else None)
            confidence = ""
            needs_confirm = False
            source = ""

        year = raw_year if isinstance(raw_year, int) else None
        group = groups.setdefault(
            key,
            {
                "group_id": key,
                "title": title,
                "year": year,
                "seasons": set(),
                "video_count": 0,
                "episode_count": 0,
                "special_count": 0,
                "movie_count": 0,
                "episode_min": None,
                "episode_max": None,
                "tmdb_ids": set(),
                "matched_name": "",
                "name_source": "",
                "confidence": confidence,
                "needs_confirm": needs_confirm,
                "source": source,
                "file_ids": [],
            },
        )
        group["video_count"] += 1
        group["file_ids"].append(row.get("id"))
        kind = row.get("kind")
        if kind == "episode":
            group["episode_count"] += 1
            number = row.get("episode")
            if isinstance(number, int):
                current_min = group.get("episode_min")
                current_max = group.get("episode_max")
                group["episode_min"] = number if current_min is None else min(current_min, number)
                group["episode_max"] = number if current_max is None else max(current_max, number)
        elif kind == "special":
            group["special_count"] += 1
        elif kind == "movie":
            group["movie_count"] += 1
        season = row.get("season")
        if isinstance(season, int):
            group["seasons"].add(season)
        fields = row.get("fields") or {}
        if str(fields.get("tmdb_id") or "").strip():
            group["tmdb_ids"].add(str(fields.get("tmdb_id")))
            group["matched_name"] = str(fields.get("series") or "")
            if fields.get("name_source"):
                group["name_source"] = str(fields.get("name_source"))

    result = []
    for group in groups.values():
        tmdb_ids = sorted(group.pop("tmdb_ids"))
        matched_name = group.pop("matched_name")
        name_source = group.pop("name_source")
        group["seasons"] = sorted(group["seasons"])
        group["match"] = (
            {
                "tmdb_id": int(tmdb_ids[0]) if tmdb_ids[0].isdigit() else tmdb_ids[0],
                "name": matched_name,
                "name_source": name_source,
            }
            if tmdb_ids
            else None
        )
        group["mixed_match"] = len(tmdb_ids) > 1
        result.append(group)
    result.sort(key=lambda item: (item["title"], item["year"] or 0))
    return result


def find_group(files: list[dict], group_id: str) -> list[dict]:
    rows = []
    for row in files:
        if row.get("category") != "video":
            continue
        identify = row.get("identify") if isinstance(row.get("identify"), dict) else None
        if identify is not None:
            if str(identify.get("work_id") or "") != group_id:
                continue
            rows.append(row)
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        year = row.get("year")
        if group_id_for(title, year if isinstance(year, int) else None) == group_id:
            rows.append(row)
    return rows


def score_candidate(group: dict, candidate: dict) -> dict:
    """给一个 TMDB 候选打分：标题是否一致、年份是否吻合、类型是否合理。"""
    title = normalize_title(str(group.get("title") or ""))
    name = normalize_title(str(candidate.get("name") or ""))
    original = normalize_title(str(candidate.get("original_name") or ""))
    exact = bool(title) and title in {name, original}
    contained = False
    if not exact and title:
        contained = any(value and (title in value or value in title) for value in (name, original))

    year = group.get("year")
    candidate_year = candidate.get("year")
    year_match = bool(year and candidate_year and int(year) == int(candidate_year))
    year_unknown = year is None
    movie_group = bool(group.get("movie_count")) and not group.get("episode_count") and not group.get("special_count")
    kind_ok = candidate.get("media_type") == ("movie" if movie_group else "tv")

    score = 4 if exact else (2 if contained else 0)
    if year_match:
        score += 2
    elif year_unknown:
        score += 1
    elif candidate_year:
        score -= 1
    if kind_ok:
        score += 1

    reasons: list[str] = []
    if exact:
        reasons.append("标题完全一致")
    elif contained:
        reasons.append("标题部分一致")
    if year_match:
        reasons.append("年份一致")
    if kind_ok:
        reasons.append("类型匹配")

    return {
        "candidate": candidate,
        "score": score,
        "exact": exact,
        "contained": contained,
        "year_match": year_match,
        "kind_ok": kind_ok,
        "reasons": reasons,
    }


def auto_pick(group: dict, candidates: list[dict]) -> tuple[str, dict | None, list[dict]]:
    """返回 (auto/choose/missing, 选中的候选, 排序后的评分列表)。

    只有“标题足够确定 + 没有同分竞争”才自动套用，其余交给用户选择。
    """
    scored = [score_candidate(group, candidate) for candidate in candidates]
    scored.sort(
        key=lambda item: (
            -item["score"],
            -float(item["candidate"].get("popularity") or 0),
        )
    )
    if not scored:
        return "missing", None, scored

    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    unique = (
        second is None
        or second["score"] < top["score"]
        or second["candidate"].get("id") == top["candidate"].get("id")
    )
    confident = (
        top["score"] >= 6
        and unique
        and (top["exact"] or (top["contained"] and top["year_match"]))
    )
    # 只搜到一个结果、且类型对得上时也直接套用，避免“只有一个选项还要手动选”
    confident = confident or (len(scored) == 1 and (top["kind_ok"] or top["score"] >= 5))
    return ("auto" if confident else "choose"), top["candidate"], scored


def build_updates(
    rows: list[dict],
    meta: dict,
    season_override: int | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """根据 TMDB 详情生成每个文件的字段更新；返回（更新表, 缺失集列表）。

    season_override 用于用户指定季号后重新抓取该季的单集信息，只影响正片，特典季号仍为 0。
    """
    base = {
        "tmdb_id": str(meta.get("tmdb_id") or ""),
        "original_title": str(meta.get("original_name") or ""),
        "year": meta.get("year") or "",
    }
    if meta.get("name"):
        base["series"] = str(meta["name"])
    if meta.get("name_source"):
        # 只做标记用：这个作品名是从简体 / 繁体 / 原名 / 英文里哪一档取来的
        base["name_source"] = str(meta["name_source"])
    if meta.get("media_type") == "movie":
        base["air_date"] = str(meta.get("release_date") or "")
        base["ep_title"] = ""
        base["season"] = ""
        base["episode"] = ""

    episodes: dict[str, dict] = meta.get("episodes") or {}
    updates: dict[str, dict] = {}
    missing: list[str] = []

    for row in rows:
        fields = dict(base)
        kind = row.get("kind")
        if kind != "movie" and episodes:
            season = row.get("season")
            number = row.get("episode")
            if kind == "special":
                number = row.get("special_no") or row.get("episode")
                if season != 0:
                    season = row.get("season")
            elif kind == "episode" and isinstance(season_override, int):
                season = season_override
            if isinstance(season, int) and isinstance(number, int):
                episode_meta = episodes.get(f"{season}:{number}")
                if episode_meta:
                    fields["ep_title"] = episode_meta.get("name") or ""
                    fields["air_date"] = episode_meta.get("air_date") or ""
                else:
                    if season_override is not None and kind == "episode":
                        # 切换季号后，原来那一季的单集标题不能继续沿用
                        fields["ep_title"] = ""
                        fields["air_date"] = ""
                    # 特典在 TMDB 里本来就不一定有对应集，不算“缺集”警告
                    if not (kind == "special" and season == 0):
                        label = f"S{season:02d}E{number:02d}"
                        if label not in missing:
                            missing.append(label)
        updates[str(row["id"])] = fields

    return updates, missing
