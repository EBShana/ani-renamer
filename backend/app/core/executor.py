"""执行层：真实移动 / 重命名、冲突处理与空目录清理。

v3 起每个动作都带 group_id；源路径必须属于该组的源目录，目标路径必须属于该组的目标目录，
以此保证永远不跨组移动。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

POLICIES = ("skip", "overwrite", "suffix")


def normalize_policy(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in POLICIES else "skip"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _suffix_target(target: Path) -> Path:
    stem, dot, ext = target.name.rpartition(".")
    if not dot or len(ext) > 12:
        stem, ext = target.name, ""
    index = 1
    while True:
        name = f"{stem} ({index})" + (f".{ext}" if ext else "")
        candidate = target.with_name(name)
        if not candidate.exists():
            return candidate
        index += 1


def _group_map(groups: list[dict], media_root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("id") or "")
        source = Path(str(item.get("source") or media_root))
        target = Path(str(item.get("target") or source))
        result[group_id] = {"id": group_id, "source": source, "target": target}
    if not result:
        result[""] = {"id": "", "source": media_root, "target": media_root}
    return result


def _group_for(row: dict, groups: dict[str, dict], path: Path, media_root: Path) -> dict:
    group = groups.get(str(row.get("group_id") or ""))
    if group is not None:
        return group
    # 旧数据没有 group_id：按路径找所属组，找不到就用媒体根目录
    for candidate in groups.values():
        if is_within(path, candidate["target"]) or is_within(path, candidate["source"]):
            return candidate
    return {"id": "", "source": media_root, "target": media_root}


def move_one(
    source: Path,
    target: Path,
    policy: str,
    source_root: Path,
    target_root: Path | None = None,
    treat_conflict: bool = False,
) -> tuple[str, Path, str]:
    """移动单个文件或目录，返回 (done/skipped/failed/conflict, 实际目标, 说明)。

    treat_conflict=True 时（真实执行），目标已存在且策略为跳过会返回 conflict，
    让执行记录能单独统计“待处理冲突”；撤销时保持 skipped，避免把还原失败算成冲突。
    """
    if target_root is None:
        target_root = source_root
    if not source.exists():
        return "failed", target, "源文件不存在"
    if not is_within(source, source_root) or not is_within(target, target_root):
        return "failed", target, "路径超出允许范围"
    if source.resolve() == target.resolve():
        return "skipped", target, "目标与源路径相同"

    if target.exists():
        if policy == "skip":
            return ("conflict" if treat_conflict else "skipped"), target, "目标已存在，按策略跳过"
        if policy == "overwrite":
            if source.is_dir() or target.is_dir():
                return "failed", target, "目录不能自动覆盖，请手动处理"
            try:
                target.unlink()
            except OSError as exc:
                return "failed", target, f"无法覆盖目标：{exc}"
        else:
            target = _suffix_target(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError as exc:
        return "failed", target, f"移动失败：{exc}"
    return "done", target, ""


def remove_empty_parents(file_path: Path, root: Path) -> None:
    """从文件所在目录逐级向上删除已清空的目录，遇到非空或根目录即停止。"""
    current = file_path.parent
    while current != root and is_within(current, root):
        try:
            if any(current.iterdir()):
                break
            current.rmdir()
        except OSError:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent


def prune_empty_tree(path: Path, root: Path) -> None:
    """清空一个作品源目录：先删掉内部空子目录，最后删掉它自己（不删扫描根目录）。"""
    if path == root or not is_within(path, root) or not path.exists():
        return
    if not path.is_dir():
        return
    children = sorted(
        (item for item in path.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for child in children:
        try:
            if not any(child.iterdir()):
                child.rmdir()
        except OSError:
            continue
    try:
        if not any(path.iterdir()):
            path.rmdir()
    except OSError:
        return


def execute_rows(
    rows: list[dict],
    groups: list[dict],
    media_root: Path,
    policy: str,
    keep_empty_dirs: bool,
    dry_run: bool = False,
    progress_cb: Callable[[int, dict], None] | None = None,
) -> list[dict]:
    """按预览行执行改名 / 移动。只处理 action=rename 的行。"""
    group_map = _group_map(groups, media_root)
    results: list[dict] = []
    done_sources: list[tuple[Path, Path]] = []

    for index, row in enumerate(rows, start=1):
        item = {
            "seq": int(row.get("seq") or index),
            "file_id": row.get("file_id"),
            "group_id": row.get("group_id"),
            "category": row.get("category"),
            "original_path": row.get("original_path", ""),
            "target_path": row.get("new_path", ""),
            "status": "skipped",
            "message": "",
        }
        if row.get("action") != "rename":
            item["message"] = "无需改名"
            results.append(item)
            if progress_cb:
                progress_cb(item["seq"], item)
            continue
        if not item["target_path"]:
            item["status"] = "failed"
            item["message"] = "没有目标路径"
            results.append(item)
            if progress_cb:
                progress_cb(item["seq"], item)
            continue
        if row.get("status") == "error":
            item["message"] = "预览存在冲突，未执行"
            results.append(item)
            if progress_cb:
                progress_cb(item["seq"], item)
            continue

        source = Path(item["original_path"])
        target = Path(item["target_path"])
        group = _group_for(row, group_map, source, media_root)
        group_source = group["source"]
        group_target = group["target"]
        if not is_within(source, group_source) or not is_within(target, group_target):
            item["status"] = "failed"
            item["message"] = "路径超出该目录组的源/目标范围（禁止跨组移动）"
            results.append(item)
            if progress_cb:
                progress_cb(item["seq"], item)
            continue

        if dry_run:
            if target.exists() and policy == "skip":
                item["status"] = "skipped"
                item["message"] = "目标已存在（试运行不会移动）"
            else:
                if target.exists() and policy == "suffix":
                    item["target_path"] = str(_suffix_target(target))
                item["status"] = "planned"
                item["message"] = "试运行，未真正移动"
            results.append(item)
            if progress_cb:
                progress_cb(item["seq"], item)
            continue

        status, final_target, message = move_one(source, target, policy, group_source, group_target)
        item["status"] = status
        item["target_path"] = str(final_target)
        item["message"] = message
        if status == "done":
            done_sources.append((source, group_source))
        results.append(item)
        if progress_cb:
            progress_cb(item["seq"], item)

    if not keep_empty_dirs:
        for source, group_source in done_sources:
            remove_empty_parents(source, group_source)
        group_dirs: set[tuple[str, Path]] = set()
        for source, group_source in done_sources:
            try:
                relative = source.relative_to(group_source)
            except ValueError:
                continue
            if len(relative.parts) > 1:
                group_dirs.add((str(group_source), group_source / relative.parts[0]))
        for group_source_text, group_dir in group_dirs:
            prune_empty_tree(group_dir, Path(group_source_text))
    return results


def build_group_map(groups: list[dict], media_root: Path) -> dict[str, dict]:
    """后台任务开始时构建一次组映射，避免每条都重新解析。"""
    return _group_map(groups, media_root)


def execute_item(
    item: dict,
    group_map: dict[str, dict],
    media_root: Path,
    policy: str,
) -> tuple[str, str, str]:
    """执行一条已经落库的执行计划（一个文件或一个目录），返回 (状态, 最终目标, 说明)。"""
    source = Path(str(item.get("original_path") or ""))
    target_text = str(item.get("target_path") or "")
    if not target_text:
        return "failed", "", "没有目标路径"
    target = Path(target_text)
    group = _group_for(item, group_map, source, media_root)
    status, final_target, message = move_one(
        source,
        target,
        policy,
        group["source"],
        group["target"],
        treat_conflict=True,
    )
    return status, str(final_target), message


def cleanup_done_sources(
    done_items: list[dict],
    group_map: dict[str, dict],
    media_root: Path,
) -> None:
    """全部批次结束后，清理已经清空的源目录树（只删空目录）。"""
    group_dirs: set[tuple[str, Path]] = set()
    for item in done_items:
        source = Path(str(item.get("original_path") or ""))
        group = _group_for(item, group_map, source, media_root)
        group_source = group["source"]
        remove_empty_parents(source, group_source)
        try:
            relative = source.relative_to(group_source)
        except ValueError:
            continue
        if len(relative.parts) > 1:
            group_dirs.add((str(group_source), group_source / relative.parts[0]))
    for group_source_text, group_dir in group_dirs:
        prune_empty_tree(group_dir, Path(group_source_text))


def undo_items(
    items: list[dict],
    groups: list[dict],
    media_root: Path,
    keep_empty_dirs: bool = True,
) -> list[dict]:
    """按逆序把已执行的文件移回原路径。"""
    group_map = _group_map(groups, media_root)
    results: list[dict] = []
    ordered = sorted(items, key=lambda item: int(item.get("seq", 0)), reverse=True)
    for item in ordered:
        if item.get("status") != "done":
            continue
        target = Path(item["target_path"])
        original = Path(item["original_path"])
        group = _group_for(item, group_map, target, media_root)
        group_source = group["source"]
        group_target = group["target"]
        if not is_within(target, group_target) or not is_within(original, group_source):
            results.append(
                {
                    "file_id": item.get("file_id"),
                    "category": item.get("category"),
                    "original_path": str(target),
                    "target_path": str(original),
                    "status": "failed",
                    "message": "路径超出该目录组的源/目标范围，无法撤销",
                }
            )
            continue
        status, final_target, message = move_one(target, original, "skip", group_target, group_source)
        results.append(
            {
                "file_id": item.get("file_id"),
                "category": item.get("category"),
                "original_path": str(target),
                "target_path": str(final_target),
                "status": status,
                "message": message or ("已还原" if status == "done" else ""),
            }
        )
        if status == "done" and not keep_empty_dirs:
            remove_empty_parents(target, group_target)
    return results
