"""多组指向同一部 TMDB 作品时，推断各组的季号并给出分配方案。

规则（用户已确认）：
- 先看目录名（找不到再看文件名）里的明确季号：Season 2 / S02 / 第二季 / 2nd Season；
- 续作标记 Zoku / 续 / 完（Kan）/ Final Season 按「第 N 部」对应 TMDB 季列表的顺序；
- 没有线索的组不按目录顺序硬排，交给用户用 AI 判断或手动选择；
- 已经手动选过季号的组保持不动；
- 分配后仍撞车的，明确指出是哪几组、撞在哪一季哪一集。
"""

from __future__ import annotations

import re
from collections import Counter

from .episode_no import episode_value, number_key
from .matcher import build_groups, find_group

_EXPLICIT_PATTERNS = (
    (re.compile(r"(?i)\bseason\s*0*(\d{1,2})\b"), "「{raw}」"),
    (re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s*season\b"), "「{raw}」"),
    (re.compile(r"第\s*([0-9一二三四五六七八九十两]+)\s*[季期]"), "「{raw}」"),
    (re.compile(r"(?i)(?<![a-z0-9])s0*(\d{1,2})(?![0-9a-z])"), "「{raw}」"),
)

# 续作标记：rank 表示这是第几部作品（第 2 部 → TMDB 季列表里的第 2 个季）
_SEQUEL_PATTERNS = (
    (re.compile(r"(?i)\bzoku\b|续|續"), 2, "续作标记「Zoku」"),
    (re.compile(r"(?i)\bkan\b|完|final\s*season"), 3, "完结标记「Kan」"),
)

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}


def _to_int(text: str) -> int | None:
    value = str(text or "").strip()
    if value.isdigit():
        return int(value)
    if not value:
        return None
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + _CN_DIGITS.get(value[1:], 0)
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CN_DIGITS.get(left, 0) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(value)


def detect_clue(texts: list[str]) -> dict | None:
    """从一组文本里找季号线索；先找明确季号，再找续作标记。"""
    for text in texts:
        for pattern, label in _EXPLICIT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            number = _to_int(match.group(1))
            if number:
                return {
                    "kind": "number",
                    "season": number,
                    "text": label.format(raw=match.group(0)),
                }
        for pattern, rank, label in _SEQUEL_PATTERNS:
            match = pattern.search(text)
            if match:
                return {"kind": "sequel", "rank": rank, "text": label}
    return None


def _row_texts(row: dict) -> list[str]:
    """目录名优先（由深到浅），最后才看文件名。"""
    rel = str(row.get("rel_path") or "").replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    directories = list(reversed(parts[:-1]))
    filename = parts[-1] if parts else str(row.get("filename") or "")
    return directories + ([filename] if filename else [])


def group_clue(rows: list[dict]) -> tuple[dict | None, str]:
    """返回（线索, 冲突说明）。同一组里线索不一致时不自动分配。"""
    found: dict[str, dict] = {}
    for row in rows:
        if row.get("kind") != "episode":
            continue
        clue = detect_clue(_row_texts(row))
        if clue is None:
            continue
        key = f"{clue['kind']}:{clue.get('season') or clue.get('rank')}"
        found.setdefault(key, clue)
    if not found:
        return None, ""
    if len(found) == 1:
        return next(iter(found.values())), ""
    labels = "、".join(item["text"] for item in found.values())
    return None, f"同一个作品组里出现多个季号线索（{labels}），不敢自动分配"


def _official_seasons(state_info: dict) -> list[dict]:
    seasons = []
    for item in state_info.get("seasons") or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("season_number"))
        except (TypeError, ValueError):
            continue
        if number < 1:
            continue
        seasons.append(
            {
                "season_number": number,
                "name": str(item.get("name") or ""),
                "episode_count": int(item.get("episode_count") or 0),
            }
        )
    return sorted(seasons, key=lambda item: item["season_number"])


def _current_season(group: dict, info: dict, numbers: list[int]) -> int:
    choice = info.get("choice")
    if isinstance(choice, int) and choice in numbers:
        return choice
    parsed = [item for item in group.get("seasons") or [] if isinstance(item, int) and item > 0]
    return max(parsed) if parsed else 1


def _collisions(members: list[dict]) -> list[dict]:
    bucket: dict[tuple[int, str], dict] = {}
    for member in members:
        season = member["final_season"]
        for row in member["rows"]:
            if row.get("kind") != "episode":
                continue
            episode = episode_value(row)
            if episode is None:
                continue
            entry = bucket.setdefault(
                (season, number_key(episode)),
                {"episode": episode, "groups": set()},
            )
            entry["groups"].add(member["title"])
    result = [
        {"season": season, "episode": entry["episode"], "groups": sorted(entry["groups"])}
        for (season, _), entry in bucket.items()
        if len(entry["groups"]) > 1
    ]
    result.sort(key=lambda item: (item["season"], float(item["episode"])))
    return result


def build_season_plan(scan: dict) -> list[dict]:
    """生成「多组指向同一部作品」的季号分配方案；只读，不改任何数据。"""
    groups = build_groups(scan.get("files") or [])
    state = scan.get("season_state") if isinstance(scan.get("season_state"), dict) else {}
    buckets: dict[str, list[dict]] = {}
    for group in groups:
        match = group.get("match") or {}
        tmdb_id = str(match.get("tmdb_id") or "")
        if tmdb_id:
            buckets.setdefault(tmdb_id, []).append(group)

    clusters: list[dict] = []
    for tmdb_id, members in buckets.items():
        if len(members) < 2:
            continue
        official: list[dict] = []
        for member in members:
            info = state.get(member["group_id"])
            if (
                isinstance(info, dict)
                and str(info.get("tmdb_id") or "") == tmdb_id
                and info.get("seasons")
            ):
                official = _official_seasons(info)
                break
        numbers = [item["season_number"] for item in official]

        entries: list[dict] = []
        for group in members:
            rows = find_group(scan.get("files") or [], group["group_id"])
            info = state.get(group["group_id"])
            info = info if isinstance(info, dict) else {}
            choice = info.get("choice")
            choice = choice if isinstance(choice, int) and choice in numbers else None
            manual = str(info.get("mode") or "") == "manual" and choice is not None
            current = _current_season(group, info, numbers)
            clue, conflict = group_clue(rows)
            episodes = sorted(
                {
                    number
                    for row in rows
                    if row.get("kind") == "episode"
                    for number in (episode_value(row),)
                    if number is not None
                }
            )
            entry = {
                "group_id": group["group_id"],
                "title": str(group.get("title") or ""),
                "year": group.get("year") if isinstance(group.get("year"), int) else None,
                "current_season": current,
                "season_choice": choice,
                "manual": manual,
                "action": "unresolved",
                "proposed_season": None,
                "clue_text": (clue or {}).get("text", ""),
                "reason": "",
                "message": "",
                "episodes": episodes,
                "rows": rows,
                "final_season": choice if manual else current,
            }

            if not official:
                entry["message"] = "还没读取到 TMDB 季列表，请重新匹配这个作品后再分配"
            elif manual:
                entry["action"] = "keep_manual"
                entry["proposed_season"] = choice
                entry["reason"] = f"你已经手动设为第 {choice} 季，不会被覆盖"
            elif conflict:
                entry["message"] = conflict
            elif clue is None:
                entry["message"] = (
                    f"目录名和文件名里没有明确的季号线索，保持当前第 {current} 季；"
                    "可以用 AI 判断或手动选择"
                )
            elif clue["kind"] == "number":
                if clue["season"] in numbers:
                    entry["action"] = "assign"
                    entry["proposed_season"] = clue["season"]
                    entry["reason"] = (
                        f"按目录名/文件名里的{clue['text']}识别为第 {clue['season']} 季"
                    )
                else:
                    entry["action"] = "invalid_clue"
                    entry["message"] = (
                        f"目录名/文件名里的{clue['text']}指向第 {clue['season']} 季，"
                        f"但 TMDB 只有第 {'、'.join(str(item) for item in numbers)} 季"
                    )
            else:
                rank = int(clue.get("rank") or 0)
                if 1 <= rank <= len(numbers):
                    season = numbers[rank - 1]
                    entry["action"] = "assign"
                    entry["proposed_season"] = season
                    entry["reason"] = (
                        f"按目录名里的{clue['text']}判断为第 {rank} 部作品，"
                        f"对应 TMDB 第 {season} 季"
                    )
                else:
                    entry["action"] = "invalid_clue"
                    entry["message"] = (
                        f"目录名里的{clue['text']}看起来是第 {rank} 部作品，"
                        f"但 TMDB 只有 {len(numbers)} 季"
                    )
            entries.append(entry)

        # 两个组撞到同一个季号时，都不自动分配
        proposed = Counter(
            entry["proposed_season"]
            for entry in entries
            if entry["action"] == "assign" and entry["proposed_season"] is not None
        )
        clash = {season for season, count in proposed.items() if count > 1}
        if clash:
            titles_by_season: dict[int, list[str]] = {}
            for entry in entries:
                if entry["action"] == "assign" and entry["proposed_season"] is not None:
                    titles_by_season.setdefault(entry["proposed_season"], []).append(entry["title"])
            for entry in entries:
                if entry["action"] == "assign" and entry["proposed_season"] in clash:
                    season = entry["proposed_season"]
                    others = "、".join(
                        title for title in titles_by_season.get(season, []) if title != entry["title"]
                    )
                    entry["action"] = "conflict"
                    entry["proposed_season"] = None
                    entry["message"] = (
                        f"和「{others}」都指向第 {season} 季，无法自动分配，请手动选择"
                    )

        for entry in entries:
            if entry["action"] == "assign":
                entry["final_season"] = int(entry["proposed_season"])

        before = _collisions(
            [{**entry, "final_season": entry["current_season"]} for entry in entries]
        )
        after = _collisions(entries)

        public_entries = [
            {key: value for key, value in entry.items() if key not in ("rows", "final_season")}
            for entry in entries
        ]
        assignments = [
            {
                "group_id": entry["group_id"],
                "title": entry["title"],
                "season": entry["proposed_season"],
                "reason": entry["reason"],
            }
            for entry in entries
            if entry["action"] == "assign"
        ]
        clusters.append(
            {
                "tmdb_id": tmdb_id,
                "name": str((members[0].get("match") or {}).get("name") or ""),
                "seasons": official,
                "groups": public_entries,
                "assignments": assignments,
                "manual": [
                    {
                        "group_id": entry["group_id"],
                        "title": entry["title"],
                        "season": entry["season_choice"],
                        "message": entry["reason"],
                    }
                    for entry in entries
                    if entry["action"] == "keep_manual"
                ],
                "unresolved": [
                    {
                        "group_id": entry["group_id"],
                        "title": entry["title"],
                        "message": entry["message"],
                    }
                    for entry in entries
                    if entry["action"] in ("unresolved", "invalid_clue", "conflict")
                ],
                "looks_like": sorted({entry["final_season"] for entry in entries if entry["final_season"]}),
                "before_collisions": len(before),
                "collisions": after,
            }
        )

    clusters.sort(key=lambda item: (-item["before_collisions"], item["name"]))
    return clusters
