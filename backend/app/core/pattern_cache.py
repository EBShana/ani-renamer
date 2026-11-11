"""命名规律缓存：指纹完全一致才自动套用，且必须先通过 5 项自检。

规则：
- 只有「目标子目录里每个视频的指纹都能在缓存里找到、且规律完全一致」才自动套用；
  任何一个文件指纹不同，都只把命中的条目作为候选展示（pattern_candidate），不套用。
- 自检不过就不套用，原因写进日志与返回值，识别字段保持原样。
- 与内置识别真正冲突（内置已经有值且不同）时按「内置优先」处理，不自动覆盖，
  只把两边依据留给用户选择。
- 套用不改动文件名，只按规律写入扫描结果里的识别字段，并在候选上标记
  「缓存命中 / 已自动套用」，不会静默生效。
"""

from __future__ import annotations

import json
import logging

from ..store import list_pattern_entries, mark_pattern_hit
from .pattern import (
    PatternError,
    annotate_pattern,
    compile_structure,
    fingerprint,
    prepare_patterns,
)
from .pattern_check import _apply_values, _rel_dir, evaluate_structure, target_rows

logger = logging.getLogger("anime_renamer")

CACHE_SOURCE = "cache_hit"
CACHE_SOURCE_LABEL = "AI 规律（缓存命中）"

# 内置侧这些值表示「没识别出来」，不算与 AI 规律冲突
_EMPTY_BUILTIN = (None, "", "unknown")


def _auto_enabled(settings: dict) -> bool:
    return bool(settings.get("providers.ai_pattern_cache_auto_apply", True))


def _base_result(group_id: str, dir_rel: str, rows: list[dict]) -> dict:
    return {
        "status": "skipped",
        "applied": False,
        "source": CACHE_SOURCE,
        "source_label": CACHE_SOURCE_LABEL,
        "group_id": str(group_id or ""),
        "dir_rel": str(dir_rel or ""),
        "target_count": len(rows),
        "reason": "",
        "checks": [],
        "sample": [],
        "mismatched": [],
        "unmatched": [],
        "conflicts": {},
        "pattern_ids": [],
    }


def _candidate(row: dict) -> dict | None:
    value = row.get("pattern_candidate")
    return value if isinstance(value, dict) else None


def _mark_candidate(row: dict, auto_applied: bool) -> None:
    candidate = _candidate(row)
    if candidate is None:
        return
    candidate["source"] = CACHE_SOURCE
    candidate["source_label"] = CACHE_SOURCE_LABEL
    candidate["auto_applied"] = bool(auto_applied)


def _annotate(rows: list[dict], prepared: list[dict]) -> None:
    for row in rows:
        annotate_pattern(row, prepared)
        _mark_candidate(row, False)


def _blocking_conflicts(row: dict) -> dict:
    """取「内置已经有值且与 AI 不同」的冲突；内置空值不算冲突。"""
    conflict = row.get("pattern_conflict")
    if not isinstance(conflict, dict):
        return {}
    fields = conflict.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {
        key: value
        for key, value in fields.items()
        if value.get("builtin") not in _EMPTY_BUILTIN
    }


def _normalize_structure(structure: dict) -> str:
    return json.dumps(structure, sort_keys=True, ensure_ascii=False)


def auto_apply(
    scan: dict,
    group_id: str,
    dir_rel: str,
    preset: dict | None,
    settings: dict,
) -> dict:
    """尝试按缓存自动套用命名规律；返回结果里一定带中文说明。"""
    rows = target_rows(scan, group_id, dir_rel)
    result = _base_result(group_id, dir_rel, rows)
    if not rows:
        result["reason"] = "这个目录没有视频文件，无法套用命名规律"
        return result
    movies = [row for row in rows if str(row.get("kind") or "") == "movie"]
    if movies:
        result["reason"] = "目录里含剧场版，暂不自动套用缓存规律（避免把剧场版改成正片）"
        return result
    if not _auto_enabled(settings):
        result["reason"] = "设置里已关闭「AI 规律缓存自动套用」，全部按内置规则处理"
        logger.info("规律缓存：%s/%s 自动套用已关闭，未套用", group_id, dir_rel)
        return result

    entries = [item for item in list_pattern_entries() if item.get("enabled", True)]
    if not entries:
        result["reason"] = "缓存里还没有可用的命名规律"
        return result

    codes: dict[str, str] = {}
    for row in rows:
        codes[str(row.get("id") or "")] = fingerprint(
            str(row.get("group_id") or ""),
            _rel_dir(row),
            str(row.get("filename") or ""),
        )
    by_code = {str(item.get("fingerprint") or ""): item for item in entries}
    missing = [
        str(row.get("filename") or "")
        for row in rows
        if codes[str(row.get("id") or "")] not in by_code
    ]

    used_codes = {code for code in codes.values() if code in by_code}
    prepared = []
    invalid: list[str] = []
    for code in sorted(used_codes):
        try:
            prepared.extend(prepare_patterns([by_code[code]]))
        except PatternError as exc:
            invalid.append(f"{by_code[code].get('dir_rel') or '未知目录'}：{exc}")
    if invalid:
        result["status"] = "rejected"
        result["reason"] = "缓存里的规律结构不合法，没有套用：" + "；".join(invalid[:3])
        logger.warning("规律缓存：%s/%s %s", group_id, dir_rel, result["reason"])
        return result

    if missing:
        _annotate(rows, prepared)
        result["status"] = "candidate_only"
        result["unmatched"] = missing[:20]
        result["reason"] = (
            f"有 {len(missing)} 个文件的指纹在缓存里没有完全一致的记录，"
            "只能作为候选展示，不会自动套用"
        )
        logger.info("规律缓存：%s/%s 指纹不完全一致，仅作候选", group_id, dir_rel)
        return result

    structures = {
        _normalize_structure(by_code[code].get("structure") or {}) for code in used_codes
    }
    if len(structures) != 1:
        _annotate(rows, prepared)
        result["status"] = "candidate_only"
        result["reason"] = (
            f"缓存里这个目录对应 {len(structures)} 种不同规律，无法确定用哪一个，"
            "只能作为候选展示，不会自动套用"
        )
        logger.info("规律缓存：%s/%s 缓存规律不一致，仅作候选", group_id, dir_rel)
        return result

    structure = by_code[sorted(used_codes)[0]].get("structure") or {}
    try:
        compiled = compile_structure(structure)
    except PatternError as exc:
        result["reason"] = f"缓存里的规律结构不合法，没有套用：{exc}"
        logger.warning("规律缓存：%s/%s %s", group_id, dir_rel, result["reason"])
        return result

    report = evaluate_structure(scan, compiled, preset, settings, group_id, dir_rel)
    result["checks"] = report["checks"]
    result["sample"] = report["sample"]
    result["mismatched"] = report["mismatched"]
    if not report["passed"]:
        failed = "；".join(
            f"{item['label']}：{item['detail']}" for item in report["checks"] if not item["ok"]
        )
        result["status"] = "rejected"
        result["reason"] = f"缓存规律没有通过 5 项自检，已放弃套用：{failed}"
        logger.warning("规律缓存：%s/%s %s", group_id, dir_rel, result["reason"])
        return result

    _annotate(rows, prepared)
    conflicts: dict[str, dict] = {}
    for row in rows:
        blocking = _blocking_conflicts(row)
        if blocking:
            conflicts[str(row.get("filename") or "")] = {
                key: {"builtin": value.get("builtin"), "ai": value.get("ai")}
                for key, value in blocking.items()
            }
    if conflicts:
        result["status"] = "candidate_only"
        result["conflicts"] = conflicts
        result["reason"] = (
            f"有 {len(conflicts)} 个文件与内置识别冲突，按内置优先留给用户选择，"
            "不会自动套用"
        )
        logger.info("规律缓存：%s/%s 与内置冲突，仅作候选", group_id, dir_rel)
        return result

    applied = 0
    for row in rows:
        candidate = _candidate(row)
        if candidate is None:
            continue
        _apply_values(row, candidate.get("values") or {})
        _mark_candidate(row, True)
        applied += 1
        mark_pattern_hit(codes[str(row.get("id") or "")])

    result["status"] = "applied"
    result["applied"] = True
    result["pattern_ids"] = sorted(
        {str((_candidate(row) or {}).get("pattern_id") or "") for row in rows} - {""}
    )
    result["reason"] = (
        "指纹完全一致，5 项自检全部通过，已自动套用缓存规律；"
        f"来源标明为「{CACHE_SOURCE_LABEL}」，共 {applied} 个文件"
    )
    logger.info("规律缓存：%s/%s 已自动套用 %s 个文件", group_id, dir_rel, applied)
    return result


def auto_apply_scan(scan: dict, preset: dict | None, settings: dict) -> list[dict]:
    """扫描/重扫结束后，按「目录组 + 子目录」逐个尝试缓存自动套用。

    设计约束：只有缓存里已有规律、且设置里允许自动套用时才动手；缓存为空直接返回，
    保证不传规律或缓存为空时扫描结果与之前完全一致。每个目录的结果都会带上中文说明，
    供日志和界面状态使用。
    """
    if not _auto_enabled(settings):
        return []
    if not list_pattern_entries():
        return []

    seen: set[tuple[str, str]] = set()
    results: list[dict] = []
    for row in scan.get("files") or []:
        if str(row.get("category") or "") != "video":
            continue
        key = (str(row.get("group_id") or ""), _rel_dir(row))
        if key in seen:
            continue
        seen.add(key)
        results.append(auto_apply(scan, key[0], key[1], preset, settings))
    return results
