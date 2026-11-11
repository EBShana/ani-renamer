"""AI 辅助接口：状态查询、识别建议、确认写回、匹配关键词建议。

所有 AI 建议都只在接口响应里返回，不写进扫描会话；只有用户确认的才由
`/ai/identify/apply` 写回，并按现有别名机制记住写法。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_settings
from ..core.ai import (
    AiError,
    ai_status,
    suggest_identify,
    suggest_match,
    suggest_season,
    suggest_special,
)
from ..core.identify import assign_rows
from ..core.pattern_ai import apply_pattern, propose, record_decision
from ..core.preview import load_preset
from ..core.queue import refresh_queue
from ..core.scan_store import get_scan, save_scan
from ..store import (
    clear_pattern_cache,
    create_alias,
    delete_pattern_entry,
    list_aliases,
    list_pattern_decisions,
    list_pattern_entries,
    set_pattern_enabled,
    update_alias,
)

router = APIRouter()

_AI_KINDS = ("episode", "special", "movie")


def _scan_or_404(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    if payload.get("is_sample"):
        raise HTTPException(status_code=400, detail="内置示例数据不需要 AI 辅助")
    return payload


@router.get("/ai/status")
def read_ai_status() -> dict:
    """返回 AI 是否可用；不包含地址和 Key。"""
    return ai_status()


class IdentifySuggestRequest(BaseModel):
    scan_id: str
    group_id: str | None = None
    limit: int | None = None


@router.post("/ai/identify/suggest")
def ai_identify_suggest(payload: IdentifySuggestRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    try:
        return suggest_identify(scan, load_settings(), payload.group_id, payload.limit)
    except AiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class MatchSuggestRequest(BaseModel):
    scan_id: str
    limit: int | None = None
    include_matched: bool = False


@router.post("/ai/match/suggest")
def ai_match_suggest(payload: MatchSuggestRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    try:
        return suggest_match(
            scan, load_settings(), payload.limit, payload.include_matched
        )
    except AiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SeasonSuggestRequest(BaseModel):
    scan_id: str
    work_id: str
    title: str | None = None
    year: int | None = None
    seasons: list[dict] = []


@router.post("/ai/season/suggest")
def ai_season_suggest(payload: SeasonSuggestRequest) -> dict:
    """AI 推荐季号：只返回建议，不写回；季号必须落在 TMDB 季列表里。"""
    scan = _scan_or_404(payload.scan_id)
    rows = []
    for row in scan.get("files") or []:
        if str(row.get("category")) != "video":
            continue
        identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        if str(identify.get("work_id") or "") == payload.work_id:
            rows.append(row)
    if not rows:
        raise HTTPException(status_code=400, detail="扫描结果里找不到这个作品，请重新扫描")

    folders: list[str] = []
    files: list[str] = []
    identify = rows[0].get("identify") if isinstance(rows[0].get("identify"), dict) else {}
    for row in rows[:8]:
        rel = str(row.get("rel_path") or "").replace("\\", "/")
        parts = [part for part in rel.split("/") if part][:-1]
        if parts and parts[-1] not in folders:
            folders.append(parts[-1])
        files.append(str(row.get("filename") or ""))
    context = {
        "title": (payload.title or str(identify.get("title") or "")).strip(),
        "year": payload.year if isinstance(payload.year, int) else identify.get("year"),
        "seasons": payload.seasons,
        "folders": folders,
        "files": files,
        "current_season": rows[0].get("season"),
    }
    try:
        result = suggest_season(context, load_settings())
    except AiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"work_id": payload.work_id, **result}


class SpecialSuggestRequest(BaseModel):
    scan_id: str
    limit: int | None = None


@router.post("/ai/special/suggest")
def ai_special_suggest(payload: SpecialSuggestRequest) -> dict:
    """AI 判断特典目录归属：只返回建议，确认仍走 /identify/special。"""
    scan = _scan_or_404(payload.scan_id)
    try:
        return suggest_special(scan, load_settings(), payload.limit)
    except AiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ApplyItem(BaseModel):
    file_ids: list[str] = []
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    kind: str | None = None
    segment: str | None = None
    remember_alias: bool = True


class ApplyRequest(BaseModel):
    scan_id: str
    items: list[ApplyItem] = []
    remember_alias: bool = True


def _remember_alias(segment: str, title: str, year: int | None) -> None:
    """按现有别名机制记住写法；同一写法 + 同一作品去重。"""
    text = segment.strip()
    if not text:
        return
    existing = next(
        (
            item
            for item in list_aliases()
            if str(item.get("alias") or "").strip().lower() == text.lower()
            and str(item.get("title") or "") == title
            and item.get("year") == year
        ),
        None,
    )
    if existing:
        update_alias(existing["id"], {"alias": text, "title": title, "year": year})
    else:
        create_alias({"alias": text, "title": title, "year": year})


@router.post("/ai/identify/apply")
def ai_identify_apply(payload: ApplyRequest) -> dict:
    """把用户确认过的 AI 建议写回扫描会话；未确认的不会到这里。"""
    scan = _scan_or_404(payload.scan_id)
    if not payload.items:
        raise HTTPException(status_code=400, detail="没有确认任何建议")

    applied = 0
    remembered = 0
    for item in payload.items:
        title = (item.title or "").strip()
        file_ids = [str(value) for value in item.file_ids if str(value)]
        if not title or not file_ids:
            continue
        count = assign_rows(scan, file_ids, title, item.year)
        applied += count

        selected = {str(value) for value in file_ids}
        kind = str(item.kind or "").strip().lower()
        matched: list[dict] = []
        for row in scan.get("files") or []:
            if str(row.get("id")) not in selected or row.get("category") != "video":
                continue
            matched.append(row)
            fields = row.setdefault("fields", {})
            if kind in _AI_KINDS:
                row["kind"] = kind
                fields["kind"] = kind
            if kind == "special":
                row["season"] = 0
                fields["season"] = 0
            elif kind in ("episode", "movie"):
                if item.season is not None:
                    row["season"] = item.season
                    fields["season"] = item.season
                if item.episode is not None:
                    if kind == "episode":
                        row["episode"] = item.episode
                        fields["episode"] = item.episode
                    elif kind == "special":
                        row["special_no"] = item.episode
                        fields["special_no"] = item.episode

        segment = (item.segment or "").strip()
        if not segment and matched:
            identify = matched[0].get("identify") if isinstance(matched[0].get("identify"), dict) else {}
            segment = str(identify.get("segment") or "").strip()
        if count and payload.remember_alias and item.remember_alias and segment:
            _remember_alias(segment, title, item.year)
            remembered += 1

    refresh_queue(scan)
    return {
        "applied": applied,
        "remembered": remembered,
        "aliases": list_aliases(),
    }


# ---- 命名规律缓存管理（前端界面留到后续步骤，这里先提供后端接口） ----


@router.get("/ai/patterns")
def list_patterns() -> dict:
    """缓存列表：内容、来源、命中次数都在这里；不返回任何文件内容。"""
    entries = list_pattern_entries()
    return {
        "auto_apply": bool(load_settings().get("providers.ai_pattern_cache_auto_apply", True)),
        "total": len(entries),
        "entries": entries,
        # 取舍记录只用来在目录列表里显示「已放弃」，不参与套用
        "decisions": list_pattern_decisions(),
    }


class PatternToggleRequest(BaseModel):
    enabled: bool


@router.patch("/ai/patterns/{pattern_id}")
def toggle_pattern(pattern_id: str, payload: PatternToggleRequest) -> dict:
    """禁用或重新启用单条规律；禁用后指纹一致也不会自动套用。"""
    entry = set_pattern_enabled(pattern_id, payload.enabled)
    if entry is None:
        raise HTTPException(status_code=404, detail="这条命名规律不存在，可能已被删除")
    return entry


@router.delete("/ai/patterns/{pattern_id}")
def remove_pattern(pattern_id: str) -> dict:
    if not delete_pattern_entry(pattern_id):
        raise HTTPException(status_code=404, detail="这条命名规律不存在，可能已被删除")
    return {"deleted": pattern_id, "total": len(list_pattern_entries())}


@router.post("/ai/patterns/clear")
def clear_patterns() -> dict:
    """清空全部缓存；返回实际删掉的条数。"""
    return {"cleared": clear_pattern_cache()}


# ---- 命名规律：提规律 / 应用 / 记录取舍（手动触发，一次只处理一个子目录） ----


class PatternProposeRequest(BaseModel):
    scan_id: str
    group_id: str
    dir_rel: str = ""
    preset_id: str | None = None


@router.post("/ai/patterns/propose")
def pattern_propose(payload: PatternProposeRequest) -> dict:
    """让 AI 提命名规律；只发文件名与目录名，不发媒体内容。"""
    scan = _scan_or_404(payload.scan_id)
    try:
        return propose(
            scan,
            payload.group_id,
            payload.dir_rel,
            load_preset(payload.preset_id),
            load_settings(),
        )
    except AiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class PatternApplyRequest(BaseModel):
    scan_id: str
    group_id: str
    dir_rel: str = ""
    structure: dict
    source: str = "ai"
    preset_id: str | None = None


@router.post("/ai/patterns/apply")
def pattern_apply(payload: PatternApplyRequest) -> dict:
    """用户确认后应用；写入前会再跑一次 5 项自检。"""
    scan = _scan_or_404(payload.scan_id)
    result = apply_pattern(
        scan,
        payload.group_id,
        payload.dir_rel,
        payload.structure,
        payload.source,
        load_preset(payload.preset_id),
        load_settings(),
    )
    if result.get("written"):
        refresh_queue(scan)
        save_scan(scan)
    return result


class PatternDecisionRequest(BaseModel):
    group_id: str
    dir_rel: str = ""
    decision: str
    note: str | None = None
    structure: dict | None = None


@router.post("/ai/patterns/decision")
def pattern_decision(payload: PatternDecisionRequest) -> dict:
    """记录采用 / 不采用 / 稍后再说；不采用不会删除缓存。"""
    if not str(payload.group_id or "").strip():
        raise HTTPException(status_code=400, detail="缺少目录组，无法记录取舍")
    try:
        return record_decision(
            payload.group_id,
            payload.dir_rel,
            payload.decision,
            payload.note or "",
            payload.structure,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

