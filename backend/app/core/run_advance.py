"""运行推进的纯规则：阶段只前进一格，自动推进必须带规则 id。"""

from __future__ import annotations

RUN_STAGE_ORDER: tuple[str, ...] = ("scan", "match", "preview", "execute", "done")

# 只有这些执行状态才允许把运行标记为完成。
TERMINAL_OPERATION_STATUSES = ("done", "partial", "cancelled", "interrupted", "undone")


class AdvanceError(ValueError):
    """推进被拒绝。http_status 由接口层翻译成 HTTP 状态码。"""

    def __init__(self, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


def validate_advance(current_stage: str, source: str = "human", rule_id: str = "") -> str:
    """校验来源并返回下一个阶段；不满足条件直接抛 AdvanceError。"""
    if source not in ("human", "rule"):
        raise AdvanceError("来源只能是人工确认或规则自动")
    if source == "rule" and not str(rule_id or "").strip():
        raise AdvanceError("规则自动推进必须带上规则 id")
    if current_stage not in RUN_STAGE_ORDER:
        raise AdvanceError("运行阶段异常，无法推进", http_status=409)
    index = RUN_STAGE_ORDER.index(current_stage)
    if index == len(RUN_STAGE_ORDER) - 1:
        raise AdvanceError("这次运行已经完成，不能再推进", http_status=409)
    return RUN_STAGE_ORDER[index + 1]


def can_mark_done(operation_status: str) -> bool:
    return str(operation_status or "") in TERMINAL_OPERATION_STATUSES
