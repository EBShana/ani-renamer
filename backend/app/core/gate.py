"""自动放行门槛：把「预览零警告、零冲突、零未归属、季号全部确定」做成可审计快照。

门槛只读扫描 / 执行 / 预览快照，不改变识别、命名、执行行为。
四项必须全部通过才算放行；任何一项不满足都整体不通过，不提供“强制跳过”。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .pending import collect_pending

# 检查项固定为四项，顺序即界面 / 审计报告里的展示顺序。
GATE_CHECKS: tuple[tuple[str, str], ...] = (
    ("preview_clear", "预览零警告"),
    ("conflict_clear", "零冲突"),
    ("unassigned_clear", "零未归属"),
    ("season_clear", "季号全部确定"),
)

_ISSUE_KIND_LABELS = {
    "preview_warning": "预览警告",
    "preview_error": "预览错误",
    "conflict": "冲突",
    "unassigned": "未归属文件",
    "season": "季号未定",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(
    check_id: str,
    label: str,
    passed: bool,
    issues: list[dict[str, Any]],
    reason: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "passed": passed,
        "reason": reason,
        "issues": issues,
    }


def _issue(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    return {
        "kind": kind,
        "kind_label": _ISSUE_KIND_LABELS.get(kind, kind),
        "ref_type": str(item.get("ref_type") or ""),
        "ref_id": str(item.get("ref_id") or ""),
        "title": str(item.get("title") or ""),
        "reason": str(item.get("reason") or ""),
        "blocking": bool(item.get("blocking")),
    }


def _preview_check(snapshot: dict[str, Any]) -> dict[str, Any]:
    preview = snapshot.get("preview") if isinstance(snapshot, dict) else None
    if not isinstance(preview, dict):
        return _check(
            "preview_clear",
            "预览零警告",
            False,
            [],
            "还没生成预览，无法确认没有警告",
        )
    issues = [
        _issue(
            {
                "kind": "preview_error" if row.get("status") == "error" else "preview_warning",
                "ref_type": "preview_row",
                "ref_id": row.get("ref_id"),
                "title": row.get("title"),
                "reason": row.get("reason"),
            }
        )
        for row in preview.get("issues") or []
        if isinstance(row, dict) and str(row.get("ref_id") or "")
    ]
    return _check(
        "preview_clear",
        "预览零警告",
        not issues,
        issues,
        "" if not issues else f"预览中有 {len(issues)} 条警告/错误",
    )


def evaluate_gate(
    scan: dict,
    operation: dict | None = None,
    run: dict | None = None,
) -> dict[str, Any]:
    """计算一次门槛检查结果。结果本身就是审计快照，可直接存进运行记录。"""
    snapshot = (run or {}).get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    items = collect_pending(scan, operation, snapshot)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item.get("kind") or ""), []).append(_issue(item))

    conflict_issues = grouped.get("conflict") or []
    unassigned_issues = grouped.get("unassigned") or []
    season_issues = grouped.get("season") or []

    checks = [
        _preview_check(snapshot),
        _check(
            "conflict_clear",
            "零冲突",
            not conflict_issues,
            conflict_issues,
            "" if not conflict_issues else f"{len(conflict_issues)} 个冲突还没选择处理方式",
        ),
        _check(
            "unassigned_clear",
            "零未归属",
            not unassigned_issues,
            unassigned_issues,
            "" if not unassigned_issues else f"{len(unassigned_issues)} 个文件还没归属作品",
        ),
        _check(
            "season_clear",
            "季号全部确定",
            not season_issues,
            season_issues,
            "" if not season_issues else f"{len(season_issues)} 部作品的季号还没确定",
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    passed = not failed
    return {
        "checked_at": _now(),
        "run_id": str((run or {}).get("id") or ""),
        "scan_id": str((run or {}).get("scan_id") or scan.get("scan_id") or ""),
        "passed": passed,
        "blocked": not passed,
        "summary": (
            "4 项检查全部通过，允许放行"
            if passed
            else "未通过 {} 项：{}".format(
                len(failed), "、".join(str(check["label"]) for check in failed)
            )
        ),
        "failed": [{"id": check["id"], "label": check["label"]} for check in failed],
        "checks": checks,
    }
