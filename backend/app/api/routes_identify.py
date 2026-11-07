"""作品识别接口：未归属区、批量指派、记住的指派规则。

识别本身在扫描时完成（见 core/identify.py），这里负责展示与人工确认。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.identify import assign_rows, build_overview, confirm_work, ignore_segment
from ..core.matcher import build_groups
from ..core.scan_store import get_scan
from ..store import (
    create_assignment,
    delete_assignment,
    list_assignments,
    update_assignment,
)

router = APIRouter()


def _scan_or_404(scan_id: str) -> dict:
    payload = get_scan(scan_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return payload


def _overview(scan: dict) -> dict:
    data = build_overview(scan)
    data["assignments"] = list_assignments()
    return data


class IdentifyRequest(BaseModel):
    scan_id: str


@router.post("/identify")
def identify(payload: IdentifyRequest) -> dict:
    return _overview(_scan_or_404(payload.scan_id))


class AssignRequest(BaseModel):
    scan_id: str
    file_ids: list[str] = []
    work_id: str | None = None
    title: str | None = None
    year: int | None = None
    remember: bool = False
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
    return _overview(scan)


class ConfirmRequest(BaseModel):
    scan_id: str
    work_id: str


@router.post("/identify/confirm")
def identify_confirm(payload: ConfirmRequest) -> dict:
    scan = _scan_or_404(payload.scan_id)
    if confirm_work(scan, payload.work_id) == 0:
        raise HTTPException(status_code=404, detail="作品不存在或已被修改")
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
