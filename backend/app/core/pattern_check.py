"""命名规律的自检、样例预览与「失败重问一次」。

自检在本地对「这个目录的全部视频」跑 5 项检查，只有全部通过才形成待确认方案：

1. 匹配率 100%：每个视频都能被结构描述解释；
2. 编号数量等于视频数：每个视频都被归入正片 / 特典 / 合集之一，不重复计数；
3. 无缺号无重号：集号按真实数值比较，24 和 24.5 是两集；
4. 特典/合集未被误当正片；
5. 套模板后目标路径无重复。

不通过时只把「结构描述 + 对不上的文件」重问一次；仍不通过就整批放弃。
本模块只读扫描结果，模拟改名时用的是深拷贝，绝不改动扫描里的任何字段，
因此不存在「一部分用 AI 规律、一部分用旧规则」的半套状态。
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path

from .episode_no import number_key
from .pattern import PatternError, apply_structure, compile_structure
from .pattern_sample import sample_filenames
from .preview import build_preview

CHECK_LABELS = (
    ("match", "匹配率 100%"),
    ("count", "编号数量等于视频数"),
    ("numbers", "无缺号无重号"),
    ("special", "特典/合集未被误当正片"),
    ("paths", "套模板后目标路径无重复"),
)

_SPECIAL_CLUE = re.compile(
    r"(?i)(?<![A-Za-z])(special|ncoped|ncop|nced|ova|oad|teaser|preview|menu|sp|pv|cm|yokoku|bonus)"
)
_OP_ED = re.compile(r"(?i)【\s*(OP|ED)(?:[-\s]?\d{1,2})?\s*】")
_RANGE_CLUE = re.compile(r"\d{1,4}\s*[-~～—]\s*\d{1,4}")


def _rel_dir(row: dict) -> str:
    parts = [part for part in str(row.get("rel_path") or "").replace("\\", "/").split("/") if part]
    return "/".join(parts[:-1])


def target_rows(scan: dict, group_id: str, dir_rel: str) -> list[dict]:
    wanted = str(dir_rel or "").replace("\\", "/").strip("/")
    rows = []
    for row in scan.get("files") or []:
        if row.get("category") != "video":
            continue
        if group_id and str(row.get("group_id") or "") != str(group_id):
            continue
        if _rel_dir(row) != wanted:
            continue
        rows.append(row)
    return rows


def _classify(values: dict) -> str:
    if "range" in values:
        return "collection"
    if "episode" in values:
        return "episode"
    if "special_no" in values or "special_type" in values:
        return "special"
    return ""


def _special_clue(row: dict) -> bool:
    if row.get("special_folder"):
        return True
    if str(row.get("kind") or "") == "special":
        return True
    name = str(row.get("filename") or "")
    return bool(_SPECIAL_CLUE.search(name) or _OP_ED.search(name))


def _collection_clue(row: dict) -> bool:
    return bool(row.get("collection")) or bool(
        _RANGE_CLUE.search(str(row.get("filename") or ""))
    )


def _apply_values(row: dict, values: dict) -> None:
    """把规律取值写进扫描行。

    自检模拟时只作用于深拷贝；只有缓存自动套用在 5 项自检通过后，
    才会对真实扫描行调用它。
    """
    fields = dict(row.get("fields") or {})
    if "episode" in values:
        row["kind"] = "episode"
        row["episode"] = values["episode"]
        row["episode_exact"] = str(values.get("episode_text") or "")
        fields["episode"] = values.get("episode_text", values["episode"])
    if "season" in values:
        row["season"] = values["season"]
        fields["season"] = values["season"]
    if "version" in values:
        fields["version"] = values["version"]
    if "title" in values:
        fields["series"] = values["title"]
    if "special_type" in values:
        row["kind"] = "special"
        row["season"] = 0
        row["special_type"] = values["special_type"]
        fields["special_type"] = values["special_type"]
        fields["season"] = 0
    if "special_no" in values:
        row["kind"] = "special"
        row["season"] = 0
        row["special_no"] = values["special_no"]
        row["special_no_raw"] = str(values.get("special_no_text") or "")
        fields["special_no"] = values.get("special_no_text", values["special_no"])
        fields["special_no_raw"] = str(values.get("special_no_text") or "")
        fields["season"] = 0
    if "range" in values:
        row["collection"] = True
        row["collection_range"] = values["range"]
    row["fields"] = fields


def _simulate_scan(scan: dict, results: dict, preset: dict, settings: dict) -> dict:
    files = []
    for row in scan.get("files") or []:
        file_id = str(row.get("id") or "")
        if file_id in results:
            item = copy.deepcopy(row)
            _apply_values(item, results[file_id]["values"])
            files.append(item)
        else:
            files.append(row)
    return dict(scan, files=files)


def _check(key: str, ok: bool, detail: str = "", examples: list[str] | None = None) -> dict:
    label = dict(CHECK_LABELS)[key]
    return {
        "key": key,
        "label": label,
        "ok": bool(ok),
        "detail": detail if detail else ("通过" if ok else "没通过"),
        "examples": list(examples or [])[:6],
    }


def _numbers_check(values: list[float | int]) -> tuple[bool, str]:
    if not values:
        return True, "没有集号，跳过缺号/重号检查"
    keys = [number_key(value) for value in values]
    duplicated = sorted({key for key, count in Counter(keys).items() if count > 1})
    if duplicated:
        return False, f"有重复集号：{'、'.join(duplicated[:8])}"
    if all(isinstance(value, int) for value in values):
        numbers = sorted({int(value) for value in values})
        missing = [number for number in range(numbers[0], numbers[-1] + 1) if number not in set(numbers)]
        if missing:
            return False, f"中间缺号：{'、'.join(str(item) for item in missing[:8])}"
        return True, f"集号 {numbers[0]}～{numbers[-1]} 连续且不重复"
    return True, "含小数集号，已按真实数值判重，不做连续号检查"


def evaluate_structure(
    scan: dict,
    compiled: dict,
    preset: dict | None,
    settings: dict,
    group_id: str,
    dir_rel: str,
) -> dict:
    """对目标目录跑 5 项自检，返回 checks / passed / mismatched / sample。"""
    rows = target_rows(scan, group_id, dir_rel)
    if not rows:
        return {
            "passed": False,
            "checks": [_check("match", False, "这个目录没有视频文件")],
            "mismatched": [],
            "mismatched_total": 0,
            "sample": [],
            "target_count": 0,
        }

    results = {}
    for row in rows:
        results[str(row.get("id"))] = apply_structure(
            compiled, Path(str(row.get("filename") or "")).stem
        )

    explained = []
    unexplained = []
    for row in rows:
        value = results[str(row.get("id"))]
        if _classify(value["values"]) or str(row.get("kind") or "") == "movie":
            explained.append(row)
        else:
            unexplained.append(str(row.get("filename") or ""))

    checks = [
        _check(
            "match",
            not unexplained,
            "全部视频都能被结构描述解释" if not unexplained else "有文件解释不了",
            unexplained,
        )
    ]

    classes = []
    for row in rows:
        if str(row.get("kind") or "") == "movie":
            continue
        classes.append(_classify(results[str(row.get("id"))]["values"]))
    classified = [item for item in classes if item]
    checks.append(
        _check(
            "count",
            len(classified) == len(classes),
            f"已归类 {len(classified)} / 视频 {len(classes)}（剧场版不参与）",
            [str(row.get("filename") or "") for row in rows if not _classify(results[str(row.get("id"))]["values"])][:6],
        )
    )

    episode_values = []
    for row in rows:
        values = results[str(row.get("id"))]["values"]
        if "episode" in values:
            episode_values.append(values["episode"])
    numbers_ok, numbers_detail = _numbers_check(episode_values)
    checks.append(_check("numbers", numbers_ok, numbers_detail))

    misclassified = []
    for row in rows:
        values = results[str(row.get("id"))]["values"]
        if _classify(values) != "episode":
            continue
        if _special_clue(row) or _collection_clue(row):
            misclassified.append(str(row.get("filename") or ""))
    checks.append(
        _check(
            "special",
            not misclassified,
            "特典/合集都没有被当成正片" if not misclassified else "有特典或合集被当成了正片",
            misclassified,
        )
    )

    if preset is None:
        checks.append(_check("paths", False, "还没有可用的命名预设，无法检查目标路径"))
        preview_rows = []
    else:
        simulated = _simulate_scan(scan, results, preset, settings)
        preview = build_preview(simulated, preset, settings)
        preview_rows = preview.get("rows") or []
        target_ids = {str(row.get("id")) for row in rows}
        paths = [
            str(item.get("new_path") or "")
            for item in preview_rows
            if str(item.get("file_id") or "") in target_ids
            and item.get("action") == "rename"
            and item.get("category") != "folder"
            and item.get("new_path")
        ]
        duplicated = sorted({path for path, count in Counter(paths).items() if count > 1})
        checks.append(
            _check(
                "paths",
                not duplicated,
                "所有目标路径都不重复" if not duplicated else "多个文件会落到同一目标路径",
                duplicated,
            )
        )

    mismatched: list[str] = list(unexplained)
    for check in checks:
        if check["key"] in ("special",):
            mismatched.extend(check["examples"])
    if not numbers_ok:
        for key, count in Counter(
            number_key(value)
            for value in episode_values
        ).items():
            if count > 1:
                for row in rows:
                    values = results[str(row.get("id"))]["values"]
                    if "episode" in values and number_key(values["episode"]) == key:
                        mismatched.append(str(row.get("filename") or ""))
    seen = set()
    mismatched = [name for name in mismatched if name and not (name in seen or seen.add(name))]
    mismatched_total = len(mismatched)

    sample = _sample(rows, results, preview_rows)
    return {
        "passed": all(check["ok"] for check in checks),
        "checks": checks,
        "mismatched": mismatched[:20],
        "mismatched_total": mismatched_total,
        "sample": sample,
        "target_count": len(rows),
    }


def _sample(rows: list[dict], results: dict, preview_rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: str(row.get("rel_path") or ""))
    if len(ordered) > 6:
        picked = ordered[:3] + ordered[-3:]
    else:
        picked = ordered
    by_id = {str(item.get("file_id") or ""): item for item in preview_rows}
    sample = []
    total = len(ordered)
    for index, row in enumerate(picked, 1):
        file_id = str(row.get("id") or "")
        item = by_id.get(file_id) or {}
        value = results[file_id]
        builtin = {
            "kind": row.get("kind"),
            "episode": row.get("episode"),
            "season": row.get("season"),
            "special_no": row.get("special_no"),
            "version": (row.get("fields") or {}).get("version", ""),
        }
        ai = {
            "kind": _classify(value["values"]),
            "episode": value["values"].get("episode"),
            "season": value["values"].get("season"),
            "special_no": value["values"].get("special_no"),
            "version": value["values"].get("version", ""),
        }
        sample.append(
            {
                "position": f"第 {index} 条 / 共 {total} 条",
                "file_id": file_id,
                "original": str(row.get("filename") or ""),
                "new_name": str(item.get("new_name") or ""),
                "new_path": str(item.get("new_path") or ""),
                "status": item.get("status") or "",
                "messages": item.get("messages") or [],
                "evidence": [entry.get("note", "") for entry in value["evidence"] if entry.get("note")],
                "suspects": value["suspects"],
                "builtin": builtin,
                "ai": ai,
                # 只比较 AI 明确给出的字段；AI 没提到的字段不算「改过」
                "changed": any(
                    ai[key] not in (None, "") and str(builtin.get(key)) != str(ai[key])
                    for key in ("kind", "episode", "season", "special_no", "version")
                ),
            }
        )
    return sample


def propose_with_retry(
    scan: dict,
    group_id: str,
    dir_rel: str,
    call_ai,
    preset: dict | None,
    settings: dict,
) -> dict:
    """第一次问 AI，自检不过只把对不上的文件再问一次；仍不过整批放弃。

    `call_ai(filenames, context)`：context 为 None 表示第一次；
    第二次会带上上一次的结构描述、对不上的文件名和错误说明。
    """
    rows = target_rows(scan, group_id, dir_rel)
    if not rows:
        return {
            "status": "abandoned",
            "attempts": 0,
            "message": "这个目录没有视频文件，无法分析命名规律",
            "checks": [],
            "sample": [],
            "sampling": None,
            "recheck": None,
        }
    try:
        limit = int(settings.get("providers.ai_batch_limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    all_filenames = [str(row.get("filename") or "") for row in rows]
    sampling = sample_filenames(all_filenames, limit)
    filenames = list(sampling["filenames"])
    target_count = len(rows)

    def recheck_stats(report: dict | None, retry_sent: int) -> dict:
        failed = int((report or {}).get("mismatched_total") or 0)
        # 结构本身非法时没有“对不上的文件”，通过率要按 0 算，不能误报 100%。
        if not failed and report is not None and not report.get("passed"):
            failed = target_count
        rate = 1.0 if not target_count else max(0.0, round((target_count - failed) / target_count, 4))
        return {
            "total": target_count,
            "mismatched_total": failed,
            "match_rate": rate,
            "retry_sent": retry_sent,
        }

    structure = None
    report = None
    error = ""
    retry_sent = 0
    for attempt in (1, 2):
        context = None
        send = list(filenames)
        if attempt == 2:
            mismatched = []
            for name in (report or {}).get("mismatched") or []:
                if name and name not in mismatched:
                    mismatched.append(name)
            # 只补问对不上的文件；如果没列出具体文件（例如结构本身非法），再发一次样本。
            send = mismatched[:limit] or list(filenames)
            retry_sent = len(send)
            context = {
                "structure": structure,
                "mismatched": (report or {}).get("mismatched") or [],
                "error": error,
            }
        try:
            structure = call_ai(send, context)
            compiled = compile_structure(structure)
            report = evaluate_structure(scan, compiled, preset, settings, group_id, dir_rel)
            error = ""
        except PatternError as exc:
            error = str(exc)
            report = {
                "passed": False,
                "checks": [
                    _check("match", False, f"AI 返回的结构描述不合法：{error}")
                ],
                "mismatched": [],
                "mismatched_total": 0,
                "sample": [],
                "target_count": len(rows),
            }
        if report.get("passed"):
            return {
                "status": "proposal",
                "attempts": attempt,
                "message": (
                    f"自检在全部 {target_count} 条上通过；{sampling['note']}；"
                    "确认后才会生效，现在还没有改动任何文件"
                ),
                "structure": structure,
                "checks": report["checks"],
                "mismatched": report["mismatched"],
                "sample": report["sample"],
                "sampling": sampling,
                "recheck": recheck_stats(report, retry_sent),
                "target_count": report["target_count"],
            }
    return {
        "status": "abandoned",
        "attempts": 2,
        "message": (
            f"AI 规律连续两次没有通过全量复核，已整批放弃（全量 {target_count} 条，"
            f"第一次发送 {sampling['sent']} 条）：缓存没有写入，"
            "识别字段没有改动，全部继续按内置规则处理"
        ),
        "checks": (report or {}).get("checks") or [],
        "mismatched": (report or {}).get("mismatched") or [],
        "sample": (report or {}).get("sample") or [],
        "sampling": sampling,
        "recheck": recheck_stats(report, retry_sent),
        "target_count": target_count,
    }
