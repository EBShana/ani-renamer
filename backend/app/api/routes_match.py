"""作品匹配接口：按作品组搜索 TMDB / Bangumi，并把结果写回扫描字段。"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_settings
from ..core.ai import AiError, ai_status, ask_query, pick_candidate
from ..core.metadata import (
    MetadataError,
    fetch_movie,
    fetch_tv,
    name_language_order,
    name_source_label,
    resolve_display_name,
    search_bangumi,
    search_tmdb,
)
from ..core.matcher import auto_pick, build_groups, build_updates, find_group
from ..core.season_plan import build_season_plan
from ..core.scan_store import apply_field_updates, get_scan, save_scan
from ..store import create_alias, list_aliases, update_alias

router = APIRouter()


def _scan_or_404(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


def _settings_for(scan: dict) -> dict:
    # 从 v2 起设置页是唯一权威，扫描记录里保存的 preset_id 只用于显示
    return load_settings()


def _group_or_404(scan: dict, group_id: str) -> tuple[dict, list[dict]]:
    groups = build_groups(scan.get("files", []))
    group = next((item for item in groups if item["group_id"] == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="作品组不存在，请重新扫描")
    return group, find_group(scan.get("files", []), group_id)


def _official_seasons(meta: dict) -> list[dict]:
    """TMDB 的正式季（排除 S00 特典季），只保留界面上要展示的三个字段。"""
    seasons: list[dict] = []
    for item in meta.get("seasons") or []:
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


def _record_season_state(scan: dict, group_id: str, meta: dict) -> None:
    """匹配成功后记下季列表：单季直接定为该季；多季留待用户确认，换作品则作废旧选择。"""
    official = _official_seasons(meta)
    state = scan.get("season_state")
    if not isinstance(state, dict):
        state = {}
        scan["season_state"] = state
    tmdb_id = str(meta.get("tmdb_id") or "")
    if not official:
        state.pop(group_id, None)
        return
    old = state.get(group_id) if isinstance(state.get(group_id), dict) else {}
    if len(official) == 1:
        state[group_id] = {
            "tmdb_id": tmdb_id,
            "seasons": official,
            "choice": official[0]["season_number"],
            "mode": "auto",
        }
        return
    same_work = str(old.get("tmdb_id") or "") == tmdb_id
    old_choice = old.get("choice")
    valid = isinstance(old_choice, int) and any(
        item["season_number"] == old_choice for item in official
    )
    choice = old_choice if same_work and valid else None
    state[group_id] = {
        "tmdb_id": tmdb_id,
        "seasons": official,
        "choice": choice,
        "mode": "manual" if choice is not None else "pending",
    }


def _season_recommendation(official: list[dict], group: dict) -> int | None:
    """按当前集号范围给个推荐季：在所有能容纳最大集号的季里，选集数最小的那一季。"""
    max_episode = group.get("episode_max")
    if not isinstance(max_episode, int) or max_episode < 1:
        return None
    fits = [
        item
        for item in official
        if item["episode_count"] and item["episode_count"] >= max_episode
    ]
    if not fits:
        return None
    return min(fits, key=lambda item: (item["episode_count"], item["season_number"]))["season_number"]


def _decorate_groups(scan: dict, groups: list[dict]) -> list[dict]:
    """给作品组附上季号相关的展示字段，前端据此决定是否显示季号下拉。"""
    state = scan.get("season_state") if isinstance(scan.get("season_state"), dict) else {}
    anchors = _anchor_index()
    for group in groups:
        match = group.get("match") or {}
        info = state.get(group["group_id"])
        anchor = anchors.get(str(group.get("title") or "").strip().lower())
        group["anchor"] = (
            {
                "tmdb_id": anchor.get("tmdb_id"),
                "media_type": str(anchor.get("media_type") or "tv"),
                "name": str(anchor.get("title") or ""),
            }
            if anchor
            else None
        )
        official = (
            [item for item in info.get("seasons") or [] if isinstance(item, dict)]
            if isinstance(info, dict)
            and str(info.get("tmdb_id") or "") == str(match.get("tmdb_id") or "")
            else []
        )
        group["season_options"] = official
        group["season_choice"] = info.get("choice") if official else None
        if official:
            group["season_mode"] = str(info.get("mode") or "manual")
        else:
            group["season_mode"] = "default" if not match else "unknown"
        group["season_pending"] = bool(official and group["season_mode"] == "pending")
        group["season_recommended"] = (
            _season_recommendation(official, group)
            if len(official) >= 2 and not group["season_choice"]
            else None
        )
    return groups


class GroupsRequest(BaseModel):
    scan_id: str


@router.post("/match/groups")
def match_groups(payload: GroupsRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    groups = _decorate_groups(scan, build_groups(scan.get("files", [])))
    matched = sum(1 for item in groups if item.get("match"))
    return {"groups": groups, "total": len(groups), "matched": matched}


class SearchRequest(BaseModel):
    scan_id: str | None = None
    group_id: str | None = None
    query: str | None = None
    source: str = "tmdb"


@router.post("/match/search")
def match_search(payload: SearchRequest) -> dict:
    settings = load_settings()
    query = (payload.query or "").strip()
    if payload.scan_id and payload.group_id:
        scan = _scan_or_404(payload.scan_id)
        settings = _settings_for(scan)
        group, _ = _group_or_404(scan, payload.group_id)
        if not query:
            query = str(group["title"])
    if not query:
        raise HTTPException(status_code=400, detail="请填写搜索关键词")

    language = str(settings.get("providers.tmdb_lang") or "zh-CN")
    try:
        if payload.source == "bangumi":
            token = str(settings.get("providers.bangumi_token") or "")
            candidates = search_bangumi(query, token)
        else:
            api_key = str(settings.get("providers.tmdb_api_key") or "")
            candidates = search_tmdb(query, api_key, language)
    except MetadataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fallback: list[dict] = []
    # 第 10 步：TMDB 搜不到时，用同一关键词去 Bangumi 找中文名 / 日文原名作为备用候选。
    # 没配置 Token、请求失败或超时都静默跳过，不打断 TMDB 流程。
    if payload.source != "bangumi" and not candidates:
        token = str(settings.get("providers.bangumi_token") or "")
        if token:
            try:
                fallback = search_bangumi(query, token)
            except Exception:
                fallback = []
    return {
        "query": query,
        "source": payload.source,
        "candidates": candidates,
        "fallback": fallback,
    }


class ApplyRequest(BaseModel):
    scan_id: str
    group_id: str
    tmdb_id: int
    media_type: str = "tv"


def _anchor_index() -> dict[str, dict]:
    """作品锚点：名称写法 → TMDB 作品。只有带 tmdb_id 的别名才算锚点。"""
    index: dict[str, dict] = {}
    for item in list_aliases():
        alias = str(item.get("alias") or "").strip().lower()
        if not alias or not item.get("tmdb_id"):
            continue
        index[alias] = item
    return index


def _anchor_for_group(group: dict, rows: list[dict], index: dict[str, dict]) -> dict | None:
    """按作品组的各种写法找锚点；多种写法指向不同作品时视为冲突，不自动使用。"""
    names: list[str] = []
    for row in rows:
        segment = str((row.get("identify") or {}).get("segment") or "").strip().lower()
        if segment and segment not in names:
            names.append(segment)
    title = str(group.get("title") or "").strip().lower()
    if title and title not in names:
        names.append(title)

    hits: list[dict] = []
    for name in names:
        item = index.get(name)
        if item is None:
            continue
        if all(str(existing.get("tmdb_id")) != str(item.get("tmdb_id")) for existing in hits):
            hits.append(item)
    return hits[0] if len(hits) == 1 else None


def _remember_anchor(rows: list[dict], group: dict, meta: dict) -> None:
    """匹配成功后记下「名称写法 → TMDB 作品」，下次扫描直接复用同一个编号。"""
    try:
        tmdb_id = int(meta.get("tmdb_id"))
    except (TypeError, ValueError):
        return
    title = str(meta.get("name") or "").strip()
    if not tmdb_id or not title:
        return
    media_type = str(meta.get("media_type") or "tv").strip().lower()
    year = meta.get("year") if isinstance(meta.get("year"), int) else None

    names: list[str] = []
    for row in rows:
        segment = str((row.get("identify") or {}).get("segment") or "").strip()
        if segment and segment.lower() not in [item.lower() for item in names]:
            names.append(segment)
    group_title = str(group.get("title") or "").strip()
    if group_title and group_title.lower() not in [item.lower() for item in names]:
        names.append(group_title)

    for name in names[:5]:
        existing = next(
            (
                item
                for item in list_aliases()
                if str(item.get("alias") or "").strip().lower() == name.lower()
            ),
            None,
        )
        payload = {
            "title": title,
            "year": year,
            "alias": name,
            "tmdb_id": tmdb_id,
            "media_type": media_type,
        }
        if existing:
            update_alias(existing["id"], payload)
        else:
            create_alias(payload)


def _meta_brief(meta: dict) -> dict:
    """接口里返回给前端的作品信息；带上「名称来源」供界面标注。"""
    source = str(meta.get("name_source") or "")
    return {
        "tmdb_id": meta.get("tmdb_id"),
        "media_type": meta.get("media_type"),
        "name": meta.get("name"),
        "name_source": source,
        "name_source_label": name_source_label(source),
        "original_name": meta.get("original_name"),
        "year": meta.get("year"),
        "overview": meta.get("overview"),
        "poster": meta.get("poster"),
    }


def _apply_meta(
    scan: dict,
    group: dict,
    rows: list[dict],
    tmdb_id: int,
    media_type: str,
) -> tuple[dict, int, list[str]]:
    settings = _settings_for(scan)
    api_key = str(settings.get("providers.tmdb_api_key") or "")
    language = str(settings.get("providers.tmdb_lang") or "zh-CN")
    if media_type == "movie":
        meta = fetch_movie(tmdb_id, api_key, language)
    else:
        seasons = [int(item) for item in group.get("seasons") or []]
        if group.get("special_count"):
            seasons.append(0)
        if not seasons:
            seasons = [1]
        meta = fetch_tv(tmdb_id, api_key, language, seasons)
    # 作品名语言回退：简体 → 繁体 → 日文原名 → 英文（顺序来自设置）。
    # 名字一律取 TMDB 返回值，AI 不参与生成。
    meta = resolve_display_name(meta, api_key, name_language_order(settings), language)

    updates, missing = build_updates(rows, meta)
    updated = apply_field_updates(scan["scan_id"], updates)
    # 作品锚点：把「名称写法 → TMDB 编号」记下来，下次扫描不必重新搜索
    _remember_anchor(rows, group, meta)
    # 季号规则：单季自动定季，多季等用户确认，换 TMDB 作品时作废旧选择
    _record_season_state(scan, group["group_id"], meta)
    save_scan(scan)
    return meta, updated, missing


def _retryable(exc: MetadataError) -> bool:
    text = str(exc)
    return any(key in text for key in ("频繁", "无法连接", "超时", "读取元数据"))


def _search_tmdb_with_retry(query: str, api_key: str, language: str) -> list[dict]:
    for attempt in range(2):
        try:
            return search_tmdb(query, api_key, language)
        except MetadataError as exc:
            if attempt == 0 and _retryable(exc):
                time.sleep(1.0)
                continue
            raise


def _apply_meta_with_retry(
    scan: dict,
    group: dict,
    rows: list[dict],
    tmdb_id: int,
    media_type: str,
) -> tuple[dict, int, list[str]]:
    """自动匹配套用失败时重试一次，主要应对 TMDB 临时限流 / 网络抖动。"""
    for attempt in range(2):
        try:
            return _apply_meta(scan, group, rows, tmdb_id, media_type)
        except MetadataError as exc:
            if attempt == 0 and _retryable(exc):
                time.sleep(1.0)
                continue
            raise


@router.post("/match/apply")
def match_apply(payload: ApplyRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    group, rows = _group_or_404(scan, payload.group_id)
    try:
        meta, updated, missing = _apply_meta(
            scan, group, rows, payload.tmdb_id, payload.media_type
        )
    except MetadataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    groups = _decorate_groups(scan, build_groups(scan.get("files", [])))
    return {
        "updated": updated,
        "missing": missing,
        "meta": _meta_brief(meta),
        "groups": groups,
    }


class RenameRequest(BaseModel):
    scan_id: str
    group_id: str
    name: str


@router.post("/match/rename")
def match_rename(payload: RenameRequest) -> dict:
    """用户手动改名：只改作品名，TMDB 编号和季号保持不变。"""
    scan = _scan_or_404(payload.scan_id)
    group, rows = _group_or_404(scan, payload.group_id)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="作品名不能为空")
    if not group.get("match"):
        raise HTTPException(status_code=400, detail="这个作品还没有匹配到 TMDB，请先匹配再改名")
    updates = {str(row["id"]): {"series": name, "name_source": "manual"} for row in rows}
    updated = apply_field_updates(scan["scan_id"], updates)
    save_scan(scan)
    groups = _decorate_groups(scan, build_groups(scan.get("files", [])))
    return {"updated": updated, "name": name, "groups": groups}


class SeasonPlanRequest(BaseModel):
    scan_id: str


@router.post("/match/season-plan")
def match_season_plan(payload: SeasonPlanRequest) -> dict:
    """多个作品组指向同一部 TMDB 作品时，给出季号分配方案（只读，不改数据）。"""
    scan = _scan_or_404(payload.scan_id)
    return {"clusters": build_season_plan(scan)}


class SeasonRequest(BaseModel):
    scan_id: str
    group_id: str
    season: int


@router.post("/match/season")
def match_season(payload: SeasonRequest) -> dict:
    """用户确认季号：写回扫描记录，并按所选季重新抓取单集信息。"""
    scan = _scan_or_404(payload.scan_id)
    group, rows = _group_or_404(scan, payload.group_id)
    match = group.get("match") or {}
    if not match:
        raise HTTPException(
            status_code=400, detail="这个作品还没有匹配到 TMDB，季号保持扫描默认值"
        )
    state = scan.get("season_state") if isinstance(scan.get("season_state"), dict) else {}
    info = state.get(payload.group_id) if isinstance(state, dict) else None
    if not isinstance(info, dict) or str(info.get("tmdb_id") or "") != str(match.get("tmdb_id") or ""):
        raise HTTPException(
            status_code=400, detail="还没有读取到 TMDB 季列表，请重新匹配这个作品后再选季号"
        )
    official = [item for item in info.get("seasons") or [] if isinstance(item, dict)]
    if not any(int(item.get("season_number") or 0) == payload.season for item in official):
        raise HTTPException(status_code=400, detail="所选季号不在 TMDB 的季列表里")

    settings = _settings_for(scan)
    api_key = str(settings.get("providers.tmdb_api_key") or "")
    language = str(settings.get("providers.tmdb_lang") or "zh-CN")
    try:
        meta = fetch_tv(int(match["tmdb_id"]), api_key, language, [payload.season])
    except MetadataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if match.get("name_source") == "manual" and match.get("name"):
        # 用户手动改过的名字，换季时保持不动
        meta["name"] = str(match["name"])
        meta["name_source"] = "manual"
    else:
        meta = resolve_display_name(meta, api_key, name_language_order(settings), language)

    updates, missing = build_updates(rows, meta, season_override=payload.season)
    updated = apply_field_updates(scan["scan_id"], updates)
    info["choice"] = payload.season
    info["mode"] = "manual"
    save_scan(scan)
    groups = _decorate_groups(scan, build_groups(scan.get("files", [])))
    return {
        "season": payload.season,
        "updated": updated,
        "missing": missing,
        "groups": groups,
    }


MAX_AUTO_GROUPS = 40


def _candidate_brief(scored: dict) -> dict:
    data = dict(scored["candidate"])
    data["score"] = scored["score"]
    data["reasons"] = scored["reasons"]
    return data


def _work_payload(group: dict, rows: list[dict]) -> dict:
    """问 AI 时只用标题、年份和少量文件名，不发送其它内容。"""
    return {
        "id": str(group.get("group_id") or ""),
        "title": str(group.get("title") or ""),
        "year": group.get("year") if isinstance(group.get("year"), int) else None,
        "seasons": [int(item) for item in group.get("seasons") or [] if isinstance(item, int)],
        "sample_files": [str(row.get("filename") or "") for row in rows[:4]],
    }


def _ai_retry_search(
    group: dict,
    rows: list[dict],
    first_query: str,
    api_key: str,
    language: str,
    settings: dict,
) -> tuple[list[dict], str, str]:
    """本地关键词没搜到时，让 AI 换关键词重试最多 2 次。

    返回（候选, 最后用的关键词, 失败说明）。AI 只给关键词，标题仍取 TMDB 返回值。
    """
    previous = first_query
    for _ in range(2):
        try:
            suggestion = ask_query(
                {
                    **_work_payload(group, rows),
                    "previous_query": previous,
                    "note": "上一次的关键词在 TMDB 没有搜到结果",
                },
                settings,
            )
        except AiError as exc:
            return [], previous, str(exc)
        query = str(suggestion.get("query") or "").strip()
        if not query or query.lower() == previous.strip().lower():
            return [], previous, "AI 没能给出新的关键词"
        try:
            found = _search_tmdb_with_retry(query, api_key, language)
        except MetadataError as exc:
            return [], query, f"用 AI 关键词搜索失败：{exc}"
        if found:
            return found, query, ""
        previous = query
    return [], previous, "AI 换了关键词仍然没搜到"


class AutoRequest(BaseModel):
    scan_id: str
    limit: int = 20
    include_matched: bool = False


@router.post("/match/auto")
def match_auto(payload: AutoRequest) -> dict:
    """批量搜索 TMDB：本地能确定的自动套用，卡住时才问 AI，仍不确定就交给用户。"""
    scan = _scan_or_404(payload.scan_id)
    settings = _settings_for(scan)
    api_key = str(settings.get("providers.tmdb_api_key") or "")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先在设置页填写 TMDB API Key")
    language = str(settings.get("providers.tmdb_lang") or "zh-CN")
    ai = ai_status(settings)
    # 一次请求里最多问 AI 多少个作品，避免一次自动匹配把额度用光
    ai_budget = int(ai.get("batch_limit") or 0) if ai.get("configured") else 0

    groups = build_groups(scan.get("files", []))
    targets = [
        group for group in groups if payload.include_matched or not group.get("match")
    ]
    targets = targets[: max(1, min(payload.limit, MAX_AUTO_GROUPS))]

    results: list[dict] = []
    anchors = _anchor_index()
    for group in targets:
        query = str(group.get("title") or "")
        entry: dict = {
            "group_id": group["group_id"],
            "title": group["title"],
            "query": query,
            "status": "missing",
            "candidates": [],
            "meta": None,
            "updated": 0,
            "missing": [],
            "message": "",
        }
        if not query:
            entry["message"] = "标题为空，请手动搜索"
            results.append(entry)
            continue

        rows = find_group(scan.get("files", []), group["group_id"])
        anchor = _anchor_for_group(group, rows, anchors)
        if anchor is not None:
            try:
                meta, updated, missing = _apply_meta_with_retry(
                    scan,
                    group,
                    rows,
                    int(anchor["tmdb_id"]),
                    str(anchor.get("media_type") or "tv"),
                )
            except MetadataError as exc:
                entry["message"] = f"已记住的作品读取失败：{exc}，改为重新搜索"
            else:
                entry["status"] = "anchor"
                entry["query"] = f"已记住的作品 #{anchor['tmdb_id']}"
                entry["meta"] = _meta_brief(meta)
                entry["updated"] = updated
                entry["missing"] = missing
                entry["message"] = "这是你之前确认过的作品，已直接用它的 TMDB 编号，没有重新搜索"
                results.append(entry)
                continue

        def apply_candidate(candidate: dict) -> bool:
            try:
                meta, updated, missing = _apply_meta_with_retry(
                    scan, group, rows, int(candidate["id"]), str(candidate["media_type"])
                )
            except MetadataError as exc:
                entry["message"] = f"自动套用失败：{exc}，请在下方候选中手动选择"
                return False
            entry["meta"] = _meta_brief(meta)
            entry["updated"] = updated
            entry["missing"] = missing
            return True

        try:
            candidates = _search_tmdb_with_retry(query, api_key, language)
        except MetadataError as exc:
            entry["message"] = f"搜索失败：{exc}，请稍后重试或手动搜索"
            results.append(entry)
            continue

        ai_note = ""
        if not candidates and ai_budget > 0:
            # 本地搜不到才问 AI 换关键词，最多 2 次
            ai_budget -= 1
            candidates, ai_query, error = _ai_retry_search(
                group, rows, query, api_key, language, settings
            )
            if candidates:
                entry["query"] = ai_query
                ai_note = f"本地关键词没搜到，AI 换用「{ai_query}」后找到了结果"
            else:
                entry["message"] = f"没有搜到结果，AI 换关键词也没找到（{error}），请手动搜索"
                results.append(entry)
                continue
        if not candidates:
            entry["message"] = "没有搜到结果，请手动搜索"
            results.append(entry)
            continue

        status, chosen, scored = auto_pick(group, candidates)
        entry["status"] = status
        entry["candidates"] = [_candidate_brief(item) for item in scored[:6]]

        if status == "auto" and chosen is not None:
            if apply_candidate(chosen):
                entry["status"] = "auto"
                if ai_note:
                    entry["message"] = ai_note
            else:
                entry["status"] = "choose"
        elif status == "choose" and len(scored) >= 2:
            if ai_budget > 0:
                # 本地分不清多个候选：把候选列表交给 AI，它只能选编号
                ai_budget -= 1
                try:
                    picked = pick_candidate(
                        _work_payload(group, rows),
                        [item["candidate"] for item in scored[:6]],
                        settings,
                    )
                except AiError as exc:
                    picked = {
                        "id": None,
                        "valid": True,
                        "message": f"AI 挑选失败：{exc}，请手动选择",
                    }
                chosen_candidate = None
                if picked.get("id") is not None:
                    chosen_candidate = next(
                        (
                            item["candidate"]
                            for item in scored
                            if str(item["candidate"].get("id")) == str(picked["id"])
                        ),
                        None,
                    )
                if picked.get("valid") and chosen_candidate is not None:
                    if apply_candidate(chosen_candidate):
                        entry["status"] = "ai_pick"
                        entry["message"] = (
                            f"有多个分数接近的候选，AI 选了「{chosen_candidate.get('name')}」"
                            "（名称仍取自 TMDB），请核对"
                        )
                        if ai_note:
                            entry["message"] = f"{ai_note}；{entry['message']}"
                    else:
                        entry["status"] = "choose"
                elif not picked.get("valid"):
                    # AI 选了不存在的编号：整条丢弃，不猜
                    entry["status"] = "missing"
                    entry["candidates"] = []
                    entry["message"] = str(
                        picked.get("message")
                        or "AI 选了不存在的编号，已丢弃这条结果，请手动搜索"
                    )
                else:
                    entry["status"] = "choose"
                    entry["message"] = str(
                        picked.get("message")
                        or "有多个分数接近的候选，AI 也说不确定，请手动选择"
                    )
            else:
                entry["message"] = "有多个相似结果，请手动选择"
        else:
            entry["message"] = "有多个相似结果，请手动选择"

        results.append(entry)

    groups = _decorate_groups(scan, build_groups(scan.get("files", [])))
    summary = {"auto": 0, "choose": 0, "missing": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {
        "results": results,
        "groups": groups,
        "summary": summary,
        "total": len(results),
    }
