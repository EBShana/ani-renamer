"""运行记录与待人工清单接口：看上次停在哪一步、还有几条在等人处理。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_settings
from ..core.auto_execute import auto_execute_refusal
from ..core.gate import evaluate_gate
from ..core.pending import pending_view, sync_for_scan
from ..core.run_advance import AdvanceError, can_mark_done, validate_advance
from ..core.scan_store import get_scan
from ..store import (
    append_run_event,
    claim_run,
    current_run,
    get_operation,
    get_run,
    latest_granted_authorization,
    latest_operation_for_scan,
    list_run_events,
    pending_counts,
    record_decision,
    release_run,
    update_run,
)

router = APIRouter()


def _run_payload(run: dict | None) -> dict:
    if not run:
        return {"run": None, "pending": {"open": 0, "resolved": 0, "blocking": 0}}
    return {
        "run": run,
        "pending": pending_counts(run["id"]),
        "locked": bool(run.get("lock_owner")),
    }


@router.get("/runs/current")
def read_current_run() -> dict:
    return _run_payload(current_run())


@router.get("/runs/{run_id}")
def read_run(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_payload(run)


@router.post("/runs/{run_id}/release")
def release(run_id: str) -> dict:
    """手动释放占用：推进者卡死时不用等租约过期。"""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    release_run(run_id)
    return {"ok": True}


@router.get("/runs/{run_id}/gate")
def read_gate(run_id: str) -> dict:
    """读这次运行最后一次门槛检查结果，用于事后查“为什么放行 / 为什么没放行”。"""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    snapshot = run.get("snapshot") if isinstance(run.get("snapshot"), dict) else {}
    return {"run": run, "gate": snapshot.get("gate")}


@router.post("/runs/{run_id}/gate/check")
def check_gate(run_id: str) -> dict:
    """重新检查一次门槛并把结果存到本次运行上。不通过时只返回结果，不推进任何阶段。"""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    scan_id = str(run.get("scan_id") or "")
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    operation = latest_operation_for_scan(scan_id)
    gate = evaluate_gate(scan, operation, run)
    updated = update_run(run_id, snapshot_patch={"gate": gate}) or run
    append_run_event(
        run_id,
        "gate",
        f"门槛检查：{'通过' if gate.get('passed') else '未通过'}",
        stage=str(run.get("stage") or ""),
        detail=gate,
        source="rule",
        rule_id="auto-release-gate",
        scan_id=scan_id,
    )
    return {"run": updated, "gate": gate}


class AdvanceRequest(BaseModel):
    actor: str = ""
    source: str = "human"
    rule_id: str = ""
    owner: str = ""


class ContinueRequest(AdvanceRequest):
    from_stage: str | None = None


NEXT_ACTIONS = {
    "match": "去匹配作品",
    "preview": "去生成预览",
    "execute": "门槛已通过，可以开始执行",
    "done": "这次运行已结束",
}


def _advance_once(run: dict, payload: AdvanceRequest, expected_stage: str | None = None) -> dict:
    """推进一步：先占单写锁，再校验阶段 / 门槛，最后写阶段事件和决策。"""
    source = str(payload.source or "human")
    current = str(run.get("stage") or "scan")
    if expected_stage and current != expected_stage:
        raise HTTPException(
            status_code=409,
            detail=f"这次运行当前停在「{run.get('stage_label') or current}」，不是指定的「{expected_stage}」",
        )
    try:
        next_stage = validate_advance(current, source, payload.rule_id)
    except AdvanceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    owner = str(payload.owner or "").strip() or f"advance-{uuid.uuid4().hex[:8]}"
    if not claim_run(run["id"], owner, ttl_seconds=120):
        holder = str(run.get("lock_owner") or "另一处")
        raise HTTPException(status_code=409, detail=f"这次运行正被「{holder}」推进，请稍后再试")
    try:
        if next_stage == "execute":
            scan = get_scan(str(run.get("scan_id") or ""))
            if scan is None:
                raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
            gate = evaluate_gate(
                scan, latest_operation_for_scan(str(run.get("scan_id") or "")), run
            )
            update_run(run["id"], snapshot_patch={"gate": gate})
            append_run_event(
                run["id"],
                "gate",
                f"门槛检查：{'通过' if gate.get('passed') else '未通过'}",
                stage=current,
                detail=gate,
                source="rule",
                rule_id="auto-release-gate",
                scan_id=str(run.get("scan_id") or ""),
            )
            if not gate.get("passed"):
                raise HTTPException(
                    status_code=409,
                    detail=f"自动放行门槛未通过：{gate.get('summary')}",
                )
            if source == "rule":
                reason = auto_execute_refusal(
                    load_settings(),
                    gate,
                    latest_granted_authorization(run["id"]),
                    run["id"],
                )
                if reason:
                    raise HTTPException(status_code=403, detail=reason)
        if next_stage == "done":
            operation = get_operation(str(run.get("operation_id") or ""))
            if operation is None or not can_mark_done(str(operation.get("status") or "")):
                raise HTTPException(status_code=409, detail="执行还没结束，不能标记为完成")
        updated = update_run(
            run["id"],
            stage=next_stage,
            event_source=source,
            event_rule_id=str(payload.rule_id or ""),
            actor=str(payload.actor or owner),
        ) or run
        record_decision(
            str(run.get("scan_id") or ""),
            "advance",
            run["id"],
            choice=next_stage,
            source=source,
            rule_id=str(payload.rule_id or ""),
            note=str(payload.actor or owner),
            run_id=run["id"],
        )
        return {
            "run": updated,
            "from_stage": current,
            "to_stage": next_stage,
            "next_action": NEXT_ACTIONS.get(next_stage, ""),
            "lock_owner": owner,
        }
    finally:
        release_run(run["id"], owner)


@router.post("/runs/{run_id}/advance")
def advance_run(run_id: str, payload: AdvanceRequest | None = None) -> dict:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _advance_once(run, payload or AdvanceRequest())


@router.post("/runs/{run_id}/continue")
def continue_run(run_id: str, payload: ContinueRequest | None = None) -> dict:
    """从指定阶段继续推进；不传 from_stage 时等同推进一步。"""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    data = payload or ContinueRequest()
    return _advance_once(run, data, data.from_stage)


@router.get("/runs/{run_id}/events")
def read_run_events(run_id: str, limit: int = 500) -> dict:
    """按发生顺序回放这次运行：阶段切换、人工确认、规则自动都在这条流水里。"""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"run": run, "events": list_run_events(run_id, max(1, min(limit, 2000)))}


@router.get("/pending")
def read_pending(scan_id: str | None = None, include_resolved: bool = False) -> dict:
    run = current_run() if not scan_id else None
    if scan_id is None:
        if run is None:
            return {
                "run": None,
                "items": [],
                "counts": {"open": 0, "resolved": 0, "blocking": 0},
            }
        scan_id = str(run["scan_id"])
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    data = pending_view(scan, latest_operation_for_scan(scan_id))
    items = data["items"]
    if include_resolved:
        from ..store import list_pending_items

        items = list_pending_items(str(data["run"]["id"]), include_resolved=True)
    return {"run": data["run"], "items": items, "counts": data["counts"]}


@router.post("/pending/sync")
def sync_pending(scan_id: str | None = None) -> dict:
    run = current_run() if not scan_id else None
    if scan_id is None:
        if run is None:
            return {"run": None, "items": [], "counts": {"open": 0, "resolved": 0, "blocking": 0}}
        scan_id = str(run["scan_id"])
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    data = sync_for_scan(scan, latest_operation_for_scan(scan_id))
    return {
        "run": data["run"],
        "items": data["items"],
        "counts": data["counts"],
    }
