"""AI 命名规律接口：提规律、应用、记录取舍。

- 只把目录名与文件名文本发给 AI，不发媒体内容。
- 一次只处理「目录组 + 子目录」一个单位；文件数超过设置里的上限时，
  先在本地按名字形状分桶抽样，再让 AI 的规律在**全量**文件上复核，
  不再直接拒绝整个目录。
- 提规律复用第 3 步的 propose_with_retry（含 5 项自检与失败重问一次）。
- 放弃或候选状态绝不写识别字段；应用前再跑一次自检，再写缓存。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..store import (
    get_pattern_decision,
    save_pattern_decision,
    save_pattern_entry,
)
from .ai import AiError, _ask, ai_status
from .pattern import (
    PatternError,
    _conflicts,
    apply_structure,
    compile_structure,
    fingerprint,
)
from .pattern_check import _apply_values, evaluate_structure, propose_with_retry, target_rows

logger = logging.getLogger("anime_renamer")

MAX_PATTERN_FILES = 50
EMPTY_BUILTIN = (None, "", "unknown")

SYSTEM_PROMPT = """你是动画文件命名规律分析器。你只输出结构描述，不输出正则表达式，不输出任何文件名或示例。
请根据给出的目录名与文件名，判断文件名里哪一段是集号、季号、版本、特典类型、特典序号、区间等。

可用字段（只能出现这四个键）：
- role: episode（集号）/ season（季号）/ title（单集标题）/ series（作品名段）/ special_type（特典类型）/ special_no（特典序号）/ version（版本）/ range（区间）/ ignore（忽略）
- container: 【】 / [] / () / 无
- match: 纯数字 / 数字+v2 / 小数 / 区间 / 纯文字 / 技术标签 / 数字带字
- take: 第一个 / 最后一个 / 第 N 个 / 其余全部

match 的补充说明：
- 数字带字：字母和数字直接连在一起的一整段，如 ED01 / OP02 / NCOP01 / SP02 / CM03。
  配 special_type 时取文字部分（ED / OP / NCOP），配 special_no 时取数字部分（01 / 02）；
  它不能当集号使用。
- 特典类型（special_type）可以直接取括号里的原文：用 纯文字 或 技术标签 原样保留，
  也可以用 数字带字 取文字部分；不需要、也不允许编造文件名里不存在的类型名。

输出 JSON，形如：
{"rules":[{"role":"episode","container":"【】","match":"纯数字","take":"第一个"}]}

必须遵守：
1. 只能输出 JSON，不要解释，不要 Markdown。
2. 不要出现 regex、pattern、filename 等额外字段。
3. 集号只能用 纯数字 / 数字+v2 / 小数；区间不能当集号。
4. 特典文件名（NCOP、NCED、OP、ED、OVA、Menu 等）要归为 special_type 或 special_no，不要当正片集号。
   像 ED01 这种整段，用 数字带字：special_type 拿 ED，special_no 拿 01。
5. 一个视频文件只能有确定的一种归类。"""


def _limit(settings: dict) -> int:
    try:
        value = int(settings.get("providers.ai_batch_limit") or 10)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, MAX_PATTERN_FILES))


def _strip_extension(filename: str) -> str:
    return Path(str(filename or "")).stem


def _blocking(row: dict, values: dict, evidence: dict) -> dict:
    conflicts = _conflicts(row, values, evidence)
    return {
        key: value
        for key, value in conflicts.items()
        if value.get("builtin") not in EMPTY_BUILTIN
    }


def _decision_of(group_id: str, dir_rel: str) -> dict | None:
    return get_pattern_decision(group_id, dir_rel)


def _with_decision(result: dict, group_id: str, dir_rel: str) -> dict:
    decision = _decision_of(group_id, dir_rel)
    result["decision"] = decision
    # 只有「不采用」才不再打扰；「稍后再说」下次仍可提醒
    result["remind"] = decision is None or decision.get("decision") != "rejected"
    return result


def build_call_ai(settings: dict, dir_rel: str):
    """构造真正的 AI 调用；测试时可替换成桩函数。"""

    def call(filenames: list[str], context: dict | None) -> dict:
        status = ai_status(settings)
        if not status.get("configured"):
            raise AiError(str(status.get("message") or "AI 还没配置好，请先到设置页填写接口地址与模型名"))
        payload: dict = {"dir": dir_rel, "files": filenames}
        if context:
            payload["previous_structure"] = context.get("structure")
            payload["mismatched_files"] = context.get("mismatched")
            payload["previous_error"] = context.get("error")
        return _ask(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), settings)

    return call


def propose(
    scan: dict,
    group_id: str,
    dir_rel: str,
    preset: dict | None,
    settings: dict,
    call_ai=None,
) -> dict:
    """提规律：返回 proposal / abandoned / candidate_only 三种状态之一。"""
    rows = target_rows(scan, group_id, dir_rel)
    if not rows:
        return _with_decision(
            {
                "status": "abandoned",
                "applied": False,
                "source_label": "AI 规律",
                "target_count": 0,
                "message": "这个子目录没有视频文件，无法分析命名规律",
                "checks": [],
                "sample": [],
                "mismatched": [],
            },
            group_id,
            dir_rel,
        )

    limit = _limit(settings)
    if call_ai is None:
        call_ai = build_call_ai(settings, dir_rel)

    report = propose_with_retry(
        scan, group_id, dir_rel, call_ai, preset, settings
    )
    base = {
        "applied": False,
        "target_count": report.get("target_count", len(rows)),
        "attempts": report.get("attempts", 0),
        "structure": report.get("structure"),
        "checks": report.get("checks") or [],
        "sample": report.get("sample") or [],
        "mismatched": report.get("mismatched") or [],
        "limit": limit,
        "sampling": report.get("sampling"),
        "recheck": report.get("recheck"),
    }
    if report.get("status") != "proposal":
        base["status"] = "abandoned"
        base["source_label"] = "AI 规律（整批放弃）"
        base["message"] = str(report.get("message") or "AI 规律没有通过自检，已整批放弃")
        logger.warning(
            "命名规律 propose 放弃：%s/%s %s", group_id, dir_rel, base["message"]
        )
        return _with_decision(base, group_id, dir_rel)

    structure = report.get("structure") or {}
    try:
        compiled = compile_structure(structure)
    except PatternError as exc:
        base["status"] = "abandoned"
        base["source_label"] = "AI 规律（整批放弃）"
        base["message"] = f"AI 返回的规律结构不合法，已整批放弃：{exc}"
        return _with_decision(base, group_id, dir_rel)

    conflicts: dict[str, dict] = {}
    for row in rows:
        stem = _strip_extension(str(row.get("filename") or ""))
        result = apply_structure(compiled, stem)
        blocking = _blocking(row, result.get("values") or {}, result.get("evidence") or {})
        if blocking:
            conflicts[str(row.get("filename") or "")] = {
                key: {"builtin": value.get("builtin"), "ai": value.get("ai")}
                for key, value in blocking.items()
            }
    if conflicts:
        base["status"] = "candidate_only"
        base["source_label"] = "AI 规律（仅候选）"
        base["conflicts"] = conflicts
        base["message"] = (
            f"AI 规律通过了 5 项自检，但有 {len(conflicts)} 个文件与内置识别冲突，"
            "只能作为候选；请对比两边依据后逐项选择，不会自动写入"
        )
        return _with_decision(base, group_id, dir_rel)

    base["status"] = "proposal"
    base["source_label"] = "AI 规律（待确认）"
    sampling_note = str((report.get("sampling") or {}).get("note") or "")
    base["message"] = (
        f"AI 规律已通过 5 项自检，确认后才会写入；{sampling_note}"
        if sampling_note
        else "AI 规律已通过 5 项自检，确认后才会写入"
    )
    return _with_decision(base, group_id, dir_rel)


def apply_pattern(
    scan: dict,
    group_id: str,
    dir_rel: str,
    structure: dict,
    source: str,
    preset: dict | None,
    settings: dict,
) -> dict:
    """用户确认后应用规律：再跑一次自检，通过才写入识别字段与缓存。"""
    rows = target_rows(scan, group_id, dir_rel)
    if not rows:
        return {
            "status": "skipped",
            "applied": False,
            "written": 0,
            "skipped": 0,
            "cached": 0,
            "message": "这个子目录没有视频文件，没有可写入的内容",
        }

    source_value = "manual" if str(source or "").strip().lower() == "manual" else "ai"
    label = "手动编写" if source_value == "manual" else "AI 规律（已确认）"
    try:
        compiled = compile_structure(structure)
    except PatternError as exc:
        return {
            "status": "rejected",
            "applied": False,
            "written": 0,
            "skipped": len(rows),
            "cached": 0,
            "message": f"规律结构不合法，没有写入：{exc}",
        }

    report = evaluate_structure(scan, compiled, preset, settings, group_id, dir_rel)
    if not report["passed"]:
        failed = "；".join(
            f"{item['label']}：{item['detail']}" for item in report["checks"] if not item["ok"]
        )
        logger.warning("命名规律 apply 前自检未通过：%s/%s %s", group_id, dir_rel, failed)
        return {
            "status": "rejected",
            "applied": False,
            "written": 0,
            "skipped": len(rows),
            "cached": 0,
            "checks": report["checks"],
            "message": f"写入前重新自检没有通过，已取消写入：{failed}",
        }

    videos = [row for row in rows if str(row.get("kind") or "") != "movie"]
    skipped_files = [
        str(row.get("filename") or "")
        for row in rows
        if str(row.get("kind") or "") == "movie"
    ]
    conflicts: dict[str, dict] = {}
    values_by_row: dict[str, dict] = {}
    for row in videos:
        stem = _strip_extension(str(row.get("filename") or ""))
        result = apply_structure(compiled, stem)
        blocking = _blocking(row, result.get("values") or {}, result.get("evidence") or {})
        if blocking:
            conflicts[str(row.get("filename") or "")] = {
                key: {"builtin": value.get("builtin"), "ai": value.get("ai")}
                for key, value in blocking.items()
            }
        values_by_row[str(row.get("id") or "")] = result
    if conflicts:
        # 内置优先：只要有一行冲突，就整批不写，避免一半用规律一半用旧规则
        return {
            "status": "rejected",
            "applied": False,
            "written": 0,
            "skipped": len(rows),
            "cached": 0,
            "conflicts": conflicts,
            "message": (
                f"有 {len(conflicts)} 个文件与内置识别冲突，已按内置优先整批取消写入；"
                "请先在预览里对比两边依据并处理冲突后再应用"
            ),
        }

    written = 0
    for row in videos:
        result = values_by_row[str(row.get("id") or "")]
        _apply_values(row, result.get("values") or {})
        row["pattern_candidate"] = {
            "values": result.get("values") or {},
            "evidence": result.get("evidence") or [],
            "suspects": result.get("suspects") or [],
            "missing": result.get("missing") or [],
            "source": source_value,
            "source_label": label,
            "auto_applied": False,
            "confirmed": True,
        }
        written += 1

    cached = 0
    seen: set[str] = set()
    for row in videos:
        code = fingerprint(
            str(row.get("group_id") or ""),
            dir_rel,
            str(row.get("filename") or ""),
        )
        if code in seen:
            continue
        seen.add(code)
        save_pattern_entry(
            {
                "fingerprint": code,
                "group_id": group_id,
                "dir_rel": dir_rel,
                "structure": structure,
                "source": source_value,
            }
        )
        cached += 1

    logger.info(
        "命名规律 apply：%s/%s 写入 %s 行，缓存 %s 条", group_id, dir_rel, written, cached
    )
    return {
        "status": "applied",
        "applied": True,
        "written": written,
        "skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "cached": cached,
        "source": source_value,
        "source_label": label,
        "message": (
            f"已写入 {written} 行识别字段，跳过 {len(skipped_files)} 行（剧场版不参与），"
            f"缓存 {cached} 条规律（来源：{label}）"
        ),
    }


def record_decision(
    group_id: str,
    dir_rel: str,
    decision: str,
    note: str = "",
    structure: dict | None = None,
) -> dict:
    """记录采用 / 不采用 / 稍后再说；不删除缓存。"""
    value = str(decision or "").strip().lower()
    if value not in ("adopted", "rejected", "later"):
        raise ValueError("取舍只能是 adopted（采用）/ rejected（不采用）/ later（稍后再说）")
    entry = save_pattern_decision(
        {
            "group_id": group_id,
            "dir_rel": dir_rel,
            "decision": value,
            "note": note,
            "structure": structure or {},
        }
    )
    logger.info("命名规律 decision：%s/%s -> %s", group_id, dir_rel, entry["decision_label"])
    return entry
