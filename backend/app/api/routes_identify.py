"""作品识别接口：未归属区、批量指派、记住的指派规则。

识别本身在扫描时完成（见 core/identify.py），这里负责展示与人工确认。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.identify import (
    apply_special_memory,
    assign_rows,
    assign_special_folder,
    build_overview,
    clear_special_folder,
    confirm_work,
    ignore_segment,
    special_suggestions,
    confirm_all,
)
from ..core.duplicates import apply_decision
from ..core.matcher import build_groups
from ..core.queue import refresh_queue
from ..core.scan_store import get_scan, save_scan
from ..store import (
    create_alias,
    create_assignment,
    delete_alias,
    delete_assignment,
    delete_special_memory,
    list_aliases,
    list_assignments,
    list_special_memory,
    remember_special_folder,
    update_alias,
    update_assignment,
)

router = APIRouter()


def _scan_or_404(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


def _collection_items(scan: dict) -> list[dict]:
    """合集文件列表（含当前处理选择），供「匹配作品」页的合集区展示。"""
    decisions = scan.get("collection_decisions")
    decisions = decisions if isinstance(decisions, dict) else {}
    rows: list[dict] = []
    for row in scan.get("files", []):
        if row.get("category") != "video" or not row.get("collection"):
            continue
        ident = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        rows.append(
            {
                "file_id": row.get("id"),
                "filename": row.get("filename"),
                "rel_path": row.get("rel_path"),
                "group_id": row.get("group_id"),
                "collection_range": row.get("collection_range") or "",
                "work_id": ident.get("work_id") or "",
                "title": ident.get("title") or fields.get("series") or "",
                "season": row.get("season") or 1,
                "decision": decisions.get(str(row.get("id"))),
            }
        )
    return rows


def _overview(scan: dict) -> dict:
    aliases = list_aliases()
    memory = list_special_memory()
    # 老扫描记录 / 刚记住的目录：打开页面时也直接套用，避免又要点一次确认
    apply_special_memory(scan, memory)
    data = build_overview(scan)
    data["assignments"] = list_assignments()
    data["aliases"] = aliases
    data["special_memory"] = memory
    data["special_suggestions"] = special_suggestions(scan, aliases)
    data["collections"] = _collection_items(scan)
    return data


class IdentifyRequest(BaseModel):
    scan_id: str


@router.post("/identify")
def identify(payload: IdentifyRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    apply_special_memory(scan, list_special_memory())
    save_scan(scan)
    return _overview(scan)


class ConfirmAllRequest(BaseModel):
    scan_id: str


@router.post("/identify/confirm-all")
def identify_confirm_all(payload: ConfirmAllRequest) -> dict:
    """一键确认「已指派待确认」里的全部作品，一次写回。"""
    scan = _scan_or_404(payload.scan_id)
    count = confirm_all(scan)
    refresh_queue(scan)
    save_scan(scan)
    data = _overview(scan)
    data["confirmed"] = count
    return data


class AssignRequest(BaseModel):
    scan_id: str
    file_ids: list[str] = []
    work_id: str | None = None
    title: str | None = None
    year: int | None = None
    remember: bool = False
    remember_alias: bool = False
    group_id: str | None = None
    segment: str | None = None


@router.post("/identify/assign")
def identify_assign(payload: AssignRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    file_ids = [str(item) for item in payload.file_ids if str(item)]
    if not file_ids:
        raise HTTPException(status_code=400, detail="请先勾选要指派的文件")

    title = (payload.title or "").strip()
    year = payload.year
    if payload.work_id:
        works = build_groups(scan.get("files", []))
        work = next((item for item in works if item["group_id"] == payload.work_id), None)
        if work is None:
            raise HTTPException(status_code=404, detail="所选作品不存在，请重新扫描后再试")
        title = str(work.get("title") or "").strip()
        year = work.get("year")
    if not title:
        raise HTTPException(status_code=400, detail="请填写作品名称")

    assign_rows(scan, file_ids, title, year)
    # 指派后条目的作品信息要跟着更新，清单才能按新作品折叠
    refresh_queue(scan)
    save_scan(scan)

    # 记住映射：同一「目录组 + 名称段」下次扫描直接复用
    if payload.remember and payload.group_id and payload.segment:
        existing = next(
            (
                item
                for item in list_assignments()
                if item.get("group_id") == payload.group_id
                and item.get("segment") == payload.segment
            ),
            None,
        )
        if existing:
            update_assignment(existing["id"], {"title": title, "year": year})
        else:
            create_assignment(
                {
                    "group_id": payload.group_id,
                    "segment": payload.segment,
                    "title": title,
                    "year": year,
                }
            )

    # 别名：同一部作品的另一种写法，下次扫描直接复用（只做精确匹配）
    if payload.remember_alias and payload.segment:
        alias_text = payload.segment.strip()
        existing_alias = next(
            (
                item
                for item in list_aliases()
                if str(item.get("alias") or "").strip().lower() == alias_text.lower()
                and str(item.get("title") or "") == title
                and item.get("year") == year
            ),
            None,
        )
        if existing_alias:
            update_alias(existing_alias["id"], {"title": title, "year": year, "alias": alias_text})
        else:
            create_alias({"title": title, "year": year, "alias": alias_text})

    return _overview(scan)


class IgnoreRequest(BaseModel):
    scan_id: str
    group_id: str
    segment: str


@router.post("/identify/ignore")
def identify_ignore(payload: IgnoreRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    if not payload.segment.strip():
        raise HTTPException(status_code=400, detail="缺少名称段")
    ignore_segment(scan, payload.group_id, payload.segment)
    refresh_queue(scan)
    save_scan(scan)
    return _overview(scan)


class ConfirmRequest(BaseModel):
    scan_id: str
    work_id: str


@router.post("/identify/confirm")
def identify_confirm(payload: ConfirmRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    if confirm_work(scan, payload.work_id) == 0:
        raise HTTPException(status_code=404, detail="作品不存在或已被修改")
    refresh_queue(scan)
    save_scan(scan)
    return _overview(scan)


@router.get("/identify/assignments")
def assignments() -> dict:
    return {"assignments": list_assignments()}


class AssignmentPayload(BaseModel):
    group_id: str
    segment: str
    title: str
    year: int | None = None


@router.post("/identify/assignments")
def create_assignment_route(payload: AssignmentPayload) -> dict:
    group_id = payload.group_id.strip()
    segment = payload.segment.strip()
    title = payload.title.strip()
    if not group_id or not segment or not title:
        raise HTTPException(status_code=400, detail="目录组、名称段、作品名都不能为空")
    existing = next(
        (
            item
            for item in list_assignments()
            if item.get("group_id") == group_id and item.get("segment") == segment
        ),
        None,
    )
    if existing:
        updated = update_assignment(existing["id"], {"title": title, "year": payload.year})
        return {"assignment": updated, "assignments": list_assignments()}
    created = create_assignment(
        {"group_id": group_id, "segment": segment, "title": title, "year": payload.year}
    )
    return {"assignment": created, "assignments": list_assignments()}


@router.put("/identify/assignments/{assignment_id}")
def update_assignment_route(assignment_id: str, payload: AssignmentPayload) -> dict:
    updated = update_assignment(
        assignment_id,
        {
            "group_id": payload.group_id.strip(),
            "segment": payload.segment.strip(),
            "title": payload.title.strip(),
            "year": payload.year,
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="映射不存在")
    return {"assignment": updated, "assignments": list_assignments()}


@router.delete("/identify/assignments/{assignment_id}")
def delete_assignment_route(assignment_id: str) -> dict:
    if not delete_assignment(assignment_id):
        raise HTTPException(status_code=404, detail="映射不存在")
    return {"assignments": list_assignments()}


class AliasPayload(BaseModel):
    alias: str
    title: str
    year: int | None = None


@router.get("/identify/aliases")
def aliases_route() -> dict:
    return {"aliases": list_aliases()}


@router.post("/identify/aliases")
def create_alias_route(payload: AliasPayload) -> dict:
    alias = payload.alias.strip()
    title = payload.title.strip()
    if not alias or not title:
        raise HTTPException(status_code=400, detail="写法和作品名都不能为空")
    created = create_alias({"alias": alias, "title": title, "year": payload.year})
    return {"alias": created, "aliases": list_aliases()}


@router.put("/identify/aliases/{alias_id}")
def update_alias_route(alias_id: str, payload: AliasPayload) -> dict:
    alias = payload.alias.strip()
    title = payload.title.strip()
    if not alias or not title:
        raise HTTPException(status_code=400, detail="写法和作品名都不能为空")
    updated = update_alias(
        alias_id, {"alias": alias, "title": title, "year": payload.year}
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="别名不存在")
    return {"alias": updated, "aliases": list_aliases()}


@router.delete("/identify/aliases/{alias_id}")
def delete_alias_route(alias_id: str) -> dict:
    if not delete_alias(alias_id):
        raise HTTPException(status_code=404, detail="别名不存在")
    return {"aliases": list_aliases()}


class SpecialRequest(BaseModel):
    scan_id: str
    folder_key: str
    action: str = "keep"
    work_id: str | None = None


@router.post("/identify/special")
def identify_special(payload: SpecialRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    folder_key = payload.folder_key.strip()
    if not folder_key:
        raise HTTPException(status_code=400, detail="缺少特典目录标识")

    group_id, _, rel_path = folder_key.partition(":")
    if payload.action == "keep":
        clear_special_folder(scan, folder_key)
        # 用户明确选择“保持原地”，把记住的归属也删掉，避免下次扫描又被套用
        for entry in list_special_memory():
            if entry.get("group_id") == group_id and entry.get("rel_path") == rel_path:
                delete_special_memory(str(entry.get("id") or ""))
    elif payload.action == "assign":
        works = build_groups(scan.get("files", []))
        work = next(
            (item for item in works if str(item.get("group_id") or "") == payload.work_id),
            None,
        )
        if work is None:
            raise HTTPException(status_code=404, detail="所选作品不存在，请先在上方匹配作品")
        work_title = str(work.get("title") or "")
        work_year = work.get("year") if isinstance(work.get("year"), int) else None
        assign_special_folder(
            scan,
            folder_key,
            str(work.get("group_id") or ""),
            work_title,
            work_year,
        )
        if rel_path:
            # 确认即记住：同一目录组 + 同一目录结构，下次扫描 / 重扫直接复用
            remember_special_folder(
                {
                    "group_id": group_id,
                    "rel_path": rel_path,
                    "work_id": str(work.get("group_id") or ""),
                    "title": work_title,
                    "year": work_year,
                }
            )
    else:
        raise HTTPException(status_code=400, detail="未知的特典处理方式")

    refresh_queue(scan)
    save_scan(scan)
    return _overview(scan)


@router.get("/identify/special-memory")
def special_memory_route() -> dict:
    return {"special_memory": list_special_memory()}


@router.delete("/identify/special-memory/{memory_id}")
def delete_special_memory_route(memory_id: str) -> dict:
    if not delete_special_memory(memory_id):
        raise HTTPException(status_code=404, detail="这条特典目录记录不存在")
    return {"special_memory": list_special_memory()}


class CollectionDecisionRequest(BaseModel):
    scan_id: str
    file_id: str
    action: str = "keep"
    season: int | None = None


@router.post("/identify/collection")
def identify_collection(payload: CollectionDecisionRequest) -> dict:
    """合集处理方式：原样搬运 / 指定季存放 / 清除选择（清除后不自动执行）。"""
    scan = _scan_or_404(payload.scan_id)
    file_id = payload.file_id.strip()
    row = next(
        (
            item
            for item in scan.get("files", [])
            if str(item.get("id") or "") == file_id and item.get("collection")
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="不是合集文件，或文件已不存在")
    decisions = scan.get("collection_decisions")
    if not isinstance(decisions, dict):
        decisions = {}
    if payload.action == "clear":
        decisions.pop(file_id, None)
    elif payload.action == "keep":
        decisions[file_id] = {"action": "keep"}
    elif payload.action == "season":
        season = payload.season
        if season is None:
            season = row.get("season") or 1
        if not isinstance(season, int) or season < 0 or season > 99:
            raise HTTPException(status_code=400, detail="季数必须是 0~99 的整数")
        decisions[file_id] = {"action": "season", "season": season}
    else:
        raise HTTPException(status_code=400, detail="未知的合集处理方式")
    scan["collection_decisions"] = decisions
    refresh_queue(scan)
    save_scan(scan)
    return _overview(scan)


class DuplicateDecisionRequest(BaseModel):
    scan_id: str
    key: str
    action: str
    file_ids: list[str] = []
    target_file_id: str | None = None


@router.post("/identify/duplicates")
def identify_duplicates(payload: DuplicateDecisionRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    try:
        decision = apply_decision(
            scan,
            payload.key,
            payload.action,
            payload.file_ids,
            payload.target_file_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_scan(scan)
    return {"decision": decision}
