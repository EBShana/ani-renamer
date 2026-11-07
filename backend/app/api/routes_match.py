"""作品匹配接口：按作品组搜索 TMDB / Bangumi，并把结果写回扫描字段。"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_settings
from ..core.metadata import MetadataError, fetch_movie, fetch_tv, search_bangumi, search_tmdb
from ..core.matcher import auto_pick, build_groups, build_updates, find_group
from ..core.scan_store import apply_field_updates, get_scan

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


class GroupsRequest(BaseModel):
    scan_id: str


@router.post("/match/groups")
def match_groups(payload: GroupsRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    groups = build_groups(scan.get("files", []))
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
    return {"query": query, "source": payload.source, "candidates": candidates}


class ApplyRequest(BaseModel):
    scan_id: str
    group_id: str
    tmdb_id: int
    media_type: str = "tv"


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

    updates, missing = build_updates(rows, meta)
    updated = apply_field_updates(scan["scan_id"], updates)
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

    groups = build_groups(scan.get("files", []))
    return {
        "updated": updated,
        "missing": missing,
        "meta": {
            "tmdb_id": meta.get("tmdb_id"),
            "media_type": meta.get("media_type"),
            "name": meta.get("name"),
            "original_name": meta.get("original_name"),
            "year": meta.get("year"),
            "overview": meta.get("overview"),
            "poster": meta.get("poster"),
        },
        "groups": groups,
    }


MAX_AUTO_GROUPS = 40


def _candidate_brief(scored: dict) -> dict:
    data = dict(scored["candidate"])
    data["score"] = scored["score"]
    data["reasons"] = scored["reasons"]
    return data


class AutoRequest(BaseModel):
    scan_id: str
    limit: int = 20
    include_matched: bool = False


@router.post("/match/auto")
def match_auto(payload: AutoRequest) -> dict:
    """批量搜索 TMDB：唯一的自动套用，其余列出候选或标记为需手动。"""
    scan = _scan_or_404(payload.scan_id)
    settings = _settings_for(scan)
    api_key = str(settings.get("providers.tmdb_api_key") or "")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先在设置页填写 TMDB API Key")
    language = str(settings.get("providers.tmdb_lang") or "zh-CN")

    groups = build_groups(scan.get("files", []))
    targets = [
        group for group in groups if payload.include_matched or not group.get("match")
    ]
    targets = targets[: max(1, min(payload.limit, MAX_AUTO_GROUPS))]

    results: list[dict] = []
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

        try:
            candidates = _search_tmdb_with_retry(query, api_key, language)
        except MetadataError as exc:
            entry["message"] = f"搜索失败：{exc}，请稍后重试或手动搜索"
            results.append(entry)
            continue

        status, chosen, scored = auto_pick(group, candidates)
        entry["status"] = status
        entry["candidates"] = [_candidate_brief(item) for item in scored[:6]]

        if status == "auto" and chosen is not None:
            rows = find_group(scan.get("files", []), group["group_id"])
            try:
                meta, updated, missing = _apply_meta_with_retry(
                    scan, group, rows, int(chosen["id"]), str(chosen["media_type"])
                )
                entry["meta"] = {
                    "tmdb_id": meta.get("tmdb_id"),
                    "media_type": meta.get("media_type"),
                    "name": meta.get("name"),
                    "original_name": meta.get("original_name"),
                    "year": meta.get("year"),
                    "overview": meta.get("overview"),
                    "poster": meta.get("poster"),
                }
                entry["updated"] = updated
                entry["missing"] = missing
            except MetadataError as exc:
                entry["status"] = "choose"
                entry["message"] = f"自动套用失败：{exc}，请在下方候选中手动选择"
        elif status == "choose":
            entry["message"] = "有多个相似结果，请手动选择"
        else:
            entry["message"] = "没有搜到结果，请手动搜索"

        results.append(entry)

    groups = build_groups(scan.get("files", []))
    summary = {"auto": 0, "choose": 0, "missing": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {
        "results": results,
        "groups": groups,
        "summary": summary,
        "total": len(results),
    }
