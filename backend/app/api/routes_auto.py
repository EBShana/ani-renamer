"""自动执行接口：开关 + 门槛 + 本次授权，三样同时满足才放行。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_settings
from ..core.auto_execute import (
    AUTO_EXECUTE_RULE_ID,
    auto_execute_refusal,
    risk_payload,
)
from ..core.gate import evaluate_gate
from ..core.scan_store import get_scan
from ..store import (
    append_run_event,
    attach_auto_authorization_operation,
    consume_auto_authorization,
    create_auto_authorization,
    get_auto_authorization,
    get_run,
    latest_granted_authorization,
    latest_operation_for_scan,
    list_auto_authorizations,
    record_decision,
    restore_auto_authorization,
    update_run,
)
from .routes_execute import ExecuteRequest, execute

router = APIRouter()


class AuthorizeRequest(BaseModel):
    confirmed_by: str = ""


class AutoExecuteRequest(ExecuteRequest):
    authorization_id: str = ""


def _run_or_404(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


def _scan_or_404(run: dict) -> dict:
    scan = get_scan(str(run.get("scan_id") or ""))
    if scan is None:
        raise HTTPException(status_code=404, detail="扫描结果已失效，请重新扫描")
    return scan


def _check_gate(run: dict, scan: dict) -> dict:
    scan_id = str(run.get("scan_id") or "")
    gate = evaluate_gate(scan, latest_operation_for_scan(scan_id), run)
    update_run(run["id"], snapshot_patch={"gate": gate})
    append_run_event(
        str(run["id"]),
        "gate",
        f"门槛检查：{'通过' if gate.get('passed') else '未通过'}",
        stage=str(run.get("stage") or ""),
        detail=gate,
        source="rule",
        rule_id="auto-release-gate",
        scan_id=scan_id,
    )
    return gate


@router.get("/runs/{run_id}/auto-execute")
def auto_execute_state(run_id: str) -> dict:
    """自动执行当前状态：开关、最近一次门槛结果、全部授权记录。"""
    run = _run_or_404(run_id)
    settings = load_settings()
    snapshot = run.get("snapshot") if isinstance(run.get("snapshot"), dict) else {}
    authorizations = list_auto_authorizations(run_id)
    return {
        "run": run,
        "enabled": bool(settings.get("execution.auto_execute_enabled")),
        "gate": snapshot.get("gate"),
        "risk": risk_payload(),
        "authorizations": authorizations,
        "latest_granted": next(
            (item for item in authorizations if item.get("status") == "granted"), None
        ),
    }


@router.post("/runs/{run_id}/auto-execute/authorize")
def authorize(run_id: str, payload: AuthorizeRequest) -> dict:
    """生成一条本次运行的授权。门槛不绿直接拒绝，不提供强制跳过。"""
    run = _run_or_404(run_id)
    settings = load_settings()
    if not settings.get("execution.auto_execute_enabled"):
        raise HTTPException(status_code=403, detail="自动执行开关未开启（默认关闭）")
    scan = _scan_or_404(run)
    gate = _check_gate(run, scan)
    if not gate.get("passed"):
        raise HTTPException(status_code=409, detail=f"自动放行门槛未通过：{gate.get('summary')}")
    name = payload.confirmed_by.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写确认人")
    risk = risk_payload()
    try:
        authorization = create_auto_authorization(
            run_id=run_id,
            scan_id=str(run.get("scan_id") or ""),
            confirmed_by=name,
            risk_version=risk["version"],
            risk_text=risk["text"],
            gate_checked_at=str(gate.get("checked_at") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_decision(
        str(run.get("scan_id") or ""),
        "auto_authorize",
        run_id,
        choice=str(authorization["id"]),
        source="human",
        note=f"确认人：{name}；风险文案版本：{risk['version']}",
        run_id=run_id,
    )
    return {
        "run": get_run(run_id) or run,
        "gate": gate,
        "authorization": authorization,
        "risk": risk,
    }


@router.post("/runs/{run_id}/auto-execute")
def auto_execute(run_id: str, payload: AutoExecuteRequest) -> dict:
    """消费一条本次授权后启动执行；任何一项不满足都拒绝，绝不静默放行。"""
    run = _run_or_404(run_id)
    settings = load_settings()
    if not settings.get("execution.auto_execute_enabled"):
        raise HTTPException(status_code=403, detail="自动执行开关未开启（默认关闭）")
    if payload.dry_run:
        raise HTTPException(status_code=400, detail="自动执行不接受演练模式")
    scan_id = str(run.get("scan_id") or "")
    if payload.scan_id and str(payload.scan_id) != scan_id:
        raise HTTPException(status_code=400, detail="自动执行只能使用本次运行对应的扫描结果")
    scan = _scan_or_404(run)
    gate = _check_gate(run, scan)
    authorization = (
        get_auto_authorization(payload.authorization_id)
        if payload.authorization_id
        else latest_granted_authorization(run_id)
    )
    reason = auto_execute_refusal(settings, gate, authorization, run_id)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    assert authorization is not None  # 上面的检查已经保证存在
    if not consume_auto_authorization(str(authorization["id"])):
        raise HTTPException(status_code=409, detail="这条授权已被使用，请重新授权")
    payload.scan_id = scan_id
    try:
        result = execute(payload)
    except HTTPException:
        restore_auto_authorization(str(authorization["id"]))
        raise
    operation_id = str((result or {}).get("operation_id") or "")
    if not operation_id:
        # 预检要求人工确认 / 没有可执行文件：授权退回，避免被白白消耗。
        restore_auto_authorization(str(authorization["id"]))
        return {
            **(result or {}),
            "auto_execute": {
                "authorization_id": authorization["id"],
                "started": False,
                "authorization_consumed": False,
                "message": "执行没有启动（可能需要人工确认预检风险），这条授权未被使用。",
            },
        }
    attach_auto_authorization_operation(str(authorization["id"]), operation_id)
    record_decision(
        scan_id,
        "auto_execute",
        run_id,
        choice=str(authorization["id"]),
        source="rule",
        rule_id=AUTO_EXECUTE_RULE_ID,
        note=f"门槛全绿，使用授权 {authorization['id']} 自动执行",
        operation_id=operation_id,
        run_id=run_id,
    )
    return {
        **(result or {}),
        "auto_execute": {
            "authorization_id": authorization["id"],
            "confirmed_by": authorization["confirmed_by"],
            "started": True,
            "authorization_consumed": True,
            "rule_id": AUTO_EXECUTE_RULE_ID,
        },
    }
