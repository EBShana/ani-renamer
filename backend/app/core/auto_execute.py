"""自动执行前的硬性检查：开关、门槛、本次授权，缺一不可。

自动执行默认关闭。开关只是前置条件，不能替代授权；
每次真正放行都必须有一条绑定本次运行、尚未使用的授权记录。
"""

from __future__ import annotations

from typing import Any

# 风险文案一旦改动就升版本；旧版本授权会被拒绝，必须重新确认。
AUTO_EXECUTE_RISK_VERSION = "v1"
AUTO_EXECUTE_RISK_TEXT = (
    "无人看守时程序可能改错文件：命名模板、目录组或目标路径判断有误，都会直接改动真实文件。"
    "撤销依赖执行记录，如果文件随后被移动、覆盖，或原目标已不存在，撤销未必能完全恢复。"
    "请确认本次扫描、匹配与预览结果正确后再授权。"
)
# 自动放行使用的规则编号，写进决策流水。
AUTO_EXECUTE_RULE_ID = "auto-execute-gate"


def risk_payload() -> dict[str, str]:
    return {"version": AUTO_EXECUTE_RISK_VERSION, "text": AUTO_EXECUTE_RISK_TEXT}


def auto_execute_refusal(
    settings: dict[str, Any],
    gate: dict[str, Any] | None,
    authorization: dict[str, Any] | None,
    run_id: str = "",
) -> str:
    """返回拒绝原因；空字符串表示允许放行。调用方必须把它当成硬门槛。"""
    if not settings.get("execution.auto_execute_enabled"):
        return "自动执行开关未开启（默认关闭）"
    if not isinstance(gate, dict) or not gate.get("passed"):
        summary = str((gate or {}).get("summary") or "还没有门槛检查结果")
        return f"自动放行门槛未通过：{summary}"
    if not authorization:
        return "没有找到本次运行的自动执行授权，已拒绝执行"
    if str(authorization.get("status") or "") != "granted":
        return "这条授权已经使用过，每次自动执行都需要重新授权"
    if run_id and str(authorization.get("run_id") or "") != str(run_id):
        return "授权不属于本次运行，已拒绝执行"
    if str(authorization.get("risk_version") or "") != AUTO_EXECUTE_RISK_VERSION:
        return "风险提示已更新，请重新授权"
    return ""
