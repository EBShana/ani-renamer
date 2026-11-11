"""执行层：真实移动 / 重命名、冲突处理与空目录清理。

v3 起每个动作都带 group_id；源路径必须属于该组的源目录，目标路径必须属于该组的目标目录，
以此保证永远不跨组移动。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

POLICIES = ("skip", "overwrite", "suffix")
CONFLICT_FILE_ACTIONS = ("skip", "overwrite", "suffix")
CONFLICT_FOLDER_ACTIONS = (
    "skip",
    "overwrite",
    "suffix",
    "merge-skip",
    "merge-overwrite",
    "merge-suffix",
)
# 这些方式会永久删除目标内容，撤销也恢复不了，必须有显式确认
DESTRUCTIVE_ACTIONS = ("overwrite", "merge-overwrite")


def validate_conflict_choices(
    choices: list[dict], current: dict[int, dict], confirm_overwrite: bool
) -> None:
    """校验冲突处理方式；覆盖类缺少确认时抛 ValueError（中文说明）。"""
    destructive = False
    for choice in choices:
        seq = int(choice["seq"])
        action = str(choice["action"])
        item = current.get(seq)
        if item is None:
            continue
        allowed = (
            CONFLICT_FOLDER_ACTIONS
            if item.get("category") == "folder"
            else CONFLICT_FILE_ACTIONS
        )
        if action not in allowed:
            raise ValueError(f"第 {seq} 条冲突不支持「{action}」这种处理方式")
        if action in DESTRUCTIVE_ACTIONS:
            destructive = True
    if destructive and not confirm_overwrite:
        raise ValueError(
            "覆盖会永久删除目标内容，且撤销无法恢复。请先勾选“我确认覆盖”再提交。"
        )


def normalize_policy(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in POLICIES else "skip"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _resolved(path: Path) -> Path:
    """解析真实路径；解析失败就退回原路径（后续比较仍能给出结果）。"""
    try:
        return path.resolve()
    except OSError:
        return path


def _within(path: Path, root: Path) -> bool:
    """两个路径都已经 resolve 过时直接比较，省掉重复 realpath。

    在 Windows 挂载盘上每次 realpath 实测约 11ms，执行时每个文件原本要做十几次。
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _text_within(path: Path, root: Path) -> bool:
    """纯字符串前缀判断（不访问磁盘），仅用于“根目录本身没有软链”的场景。"""
    try:
        relative = os.path.relpath(os.path.normpath(str(path)), os.path.normpath(str(root)))
    except ValueError:
        return False
    return relative != os.pardir and not relative.startswith(os.pardir + os.sep)


class DirIndex:
    """一次执行内的目录名单缓存。

    目标目录里已经有几百个文件时，每个文件都重新列一遍目录会越来越慢；
    这里按目录缓存“小写文件名 → 真实路径”，移动后同步更新，结果与 find_existing 一致。
    """

    def __init__(self, low_io: bool = False) -> None:
        self._names: dict[str, dict[str, Path]] = {}
        # 网盘模式：同一目标目录的 mkdir 只做一次，避免每个文件一次重复系统调用。
        self.low_io = low_io
        self._ensured: set[str] = set()

    def ensure_dir(self, directory: Path) -> None:
        """等价于 mkdir(parents=True, exist_ok=True)；网盘模式下同目录只调用一次。"""
        if self.low_io:
            key = str(directory)
            if key in self._ensured:
                return
            directory.mkdir(parents=True, exist_ok=True)
            self._ensured.add(key)
            return
        directory.mkdir(parents=True, exist_ok=True)

    def _load(self, directory: Path) -> dict[str, Path]:
        key = str(directory)
        names = self._names.get(key)
        if names is None:
            names = {}
            try:
                for child in directory.iterdir():
                    names[child.name.lower()] = child
            except OSError:
                pass
            self._names[key] = names
        return names

    def find(self, path: Path) -> Path | None:
        parent = path.parent
        cached = self._names.get(str(parent))
        if cached is not None:
            # 目录已经列过（或本次执行里写过）：直接查缓存，不再 stat
            return cached.get(path.name.lower())
        try:
            if path.exists():
                return path
            if not parent.exists():
                return None
        except OSError:
            return None
        return self._load(parent).get(path.name.lower())

    def remember(self, path: Path) -> None:
        names = self._names.get(str(path.parent))
        if names is not None:
            names[path.name.lower()] = path

    def forget(self, path: Path) -> None:
        names = self._names.get(str(path.parent))
        if names is not None:
            names.pop(path.name.lower(), None)


def find_existing(path: Path) -> Path | None:
    """按 Windows 习惯不区分大小写地找目标是否已存在，返回真实路径。"""
    if path.exists():
        return path
    parent = path.parent
    if not parent.exists():
        return None
    wanted = path.name.lower()
    try:
        for child in parent.iterdir():
            if child.name.lower() == wanted:
                return child
    except OSError:
        return None
    return None


def _suffix_target(target: Path, index: DirIndex | None = None) -> Path:
    finder = index.find if index is not None else find_existing
    stem, dot, ext = target.name.rpartition(".")
    if not dot or len(ext) > 12:
        stem, ext = target.name, ""
    index_no = 1
    while True:
        name = f"{stem} ({index_no})" + (f".{ext}" if ext else "")
        candidate = target.with_name(name)
        if finder(candidate) is None:
            return candidate
        index_no += 1


def suffix_target(target: Path) -> Path:
    """目标已存在时生成「名字 (1)」这样的副本路径（不存在才返回，探测从 1 开始；Windows 下按不区分大小写判断）。"""
    return _suffix_target(target)


def delete_target(path: Path) -> str:
    """按用户确认的覆盖策略永久删除目标内容，返回错误说明（空字符串表示成功）。

    第 6 步的“覆盖 / 合并并覆盖”会删除目标文件或整个目标文件夹，
    界面上已经二次确认过“永久删除、撤销无法恢复”，这里才允许删目录。
    """
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        return f"无法删除目标内容：{exc}"
    return ""


def _group_map(groups: list[dict], media_root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("id") or "")
        source = Path(str(item.get("source") or media_root))
        target = Path(str(item.get("target") or source))
        # 源 / 目标根目录的真实路径只在这里解析一次：执行时每个文件都要做越界检查，
        # 在 Windows 挂载盘上重复 realpath 每次约 11ms，是执行慢的大头。
        # plain 表示根目录本身没有软链，此时可以直接用字符串前缀判断，完全不用访问磁盘。
        result[group_id] = {
            "id": group_id,
            "source": source,
            "target": target,
            "source_r": _resolved(source),
            "target_r": _resolved(target),
        }
        result[group_id]["source_plain"] = result[group_id]["source_r"] == source
        result[group_id]["target_plain"] = result[group_id]["target_r"] == target
    if not result:
        root_r = _resolved(media_root)
        result[""] = {
            "id": "",
            "source": media_root,
            "target": media_root,
            "source_r": root_r,
            "target_r": root_r,
            "source_plain": root_r == media_root,
            "target_plain": root_r == media_root,
        }
    return result


def _group_for(row: dict, groups: dict[str, dict], path: Path, media_root: Path) -> dict:
    group = groups.get(str(row.get("group_id") or ""))
    if group is not None:
        return group
    # 旧数据没有 group_id：按路径找所属组，找不到就用媒体根目录
    resolved = _resolved(path)
    for candidate in groups.values():
        if _within(resolved, candidate.get("target_r") or _resolved(candidate["target"])) or _within(
            resolved, candidate.get("source_r") or _resolved(candidate["source"])
        ):
            return candidate
    root_r = _resolved(media_root)
    return {"id": "", "source": media_root, "target": media_root, "source_r": root_r, "target_r": root_r}


def move_one(
    source: Path,
    target: Path,
    policy: str,
    source_root: Path,
    target_root: Path | None = None,
    treat_conflict: bool = False,
    index: DirIndex | None = None,
    resolved: tuple[Path | None, Path | None, Path, Path] | None = None,
) -> tuple[str, Path, str]:
    """移动单个文件或目录，返回 (done/skipped/failed/conflict, 实际目标, 说明)。

    treat_conflict=True 时（真实执行），目标已存在且策略为跳过会返回 conflict，
    让执行记录能单独统计“待处理冲突”；撤销时保持 skipped，避免把还原失败算成冲突。
    resolved 传入已经解析好的（源, 目标, 源根目录, 目标根目录）可省掉重复 realpath；
    其中“源 / 目标”可以为 None，表示该侧根目录没有软链、直接用字符串前缀判断即可。
    """
    if target_root is None:
        target_root = source_root
    if resolved is not None:
        source_r, target_r, source_root_r, target_root_r = resolved
        source_inside = (
            _text_within(source, source_root) if source_r is None else _within(source_r, source_root_r)
        )
        target_inside = (
            _text_within(target, target_root) if target_r is None else _within(target_r, target_root_r)
        )
        if source_r is None and target_r is None:
            same = os.path.normpath(str(source)) == os.path.normpath(str(target))
        else:
            same = (source_r or _resolved(source)) == (target_r or _resolved(target))
    else:
        source_r, target_r = _resolved(source), _resolved(target)
        source_root_r, target_root_r = _resolved(source_root), _resolved(target_root)
        source_inside = _within(source_r, source_root_r)
        target_inside = _within(target_r, target_root_r)
        same = source_r == target_r
    if not source.exists():
        return "failed", target, "源文件不存在"
    if not source_inside or not target_inside:
        return "failed", target, "路径超出允许范围"
    if same:
        return "skipped", target, "目标与源路径相同"

    finder = index.find if index is not None else find_existing
    existing = finder(target)
    if existing is not None:
        if policy == "skip":
            return ("conflict" if treat_conflict else "skipped"), target, "目标已存在，按策略跳过"
        if policy == "overwrite":
            if source.is_dir() or existing.is_dir():
                return "failed", target, "目录不能自动覆盖，请手动处理"
            try:
                existing.unlink()
            except OSError as exc:
                return "failed", target, f"无法覆盖目标：{exc}"
            if index is not None:
                index.forget(existing)
        else:
            target = _suffix_target(target, index)

    try:
        if index is not None:
            index.ensure_dir(target.parent)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError as exc:
        return "failed", target, f"移动失败：{exc}"
    if index is not None:
        index.forget(source)
        index.remember(target)
    return "done", target, ""


def remove_empty_parents(
    file_path: Path,
    root: Path,
    root_r: Path | None = None,
    plain: bool = False,
    checked: set[str] | None = None,
) -> None:
    """从文件所在目录逐级向上删除已清空的目录，遇到非空或根目录即停止。

    plain=True 表示根目录没有软链：直接用字符串前缀判断，省掉每个文件一次 realpath
    （Windows 挂载盘上每次约 11ms，200 个文件就是 2 秒多）。
    checked 传入本次执行内已判断过的目录，避免同一个目录被反复列一遍。
    """
    current = file_path.parent
    if plain:
        while current != root and _text_within(current, root):
            key = str(current)
            if checked is not None and key in checked:
                break
            try:
                if any(current.iterdir()):
                    if checked is not None:
                        checked.add(key)
                    break
                current.rmdir()
            except OSError:
                if checked is not None:
                    checked.add(key)
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        return
    if root_r is None:
        root_r = _resolved(root)
    current_r = _resolved(current)
    while current != root and _within(current_r, root_r):
        key = str(current)
        if checked is not None and key in checked:
            break
        try:
            if any(current.iterdir()):
                if checked is not None:
                    checked.add(key)
                break
            current.rmdir()
        except OSError:
            if checked is not None:
                checked.add(key)
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
        current_r = current_r.parent


def cleanup_written_markers(
    markers: list[str], groups: list[dict], media_root: Path
) -> list[str]:
    """撤销时清掉执行阶段写下的标记文件，并删掉因此变空的目录。"""
    group_map = _group_map(groups, media_root)
    removed: list[str] = []
    checked: set[str] = set()
    for text in markers:
        marker = Path(str(text or ""))
        # 只处理隐藏标记文件，防止摘要里的脏数据误删真实文件
        if not marker.name.startswith("."):
            continue
        group = _group_for({}, group_map, marker, media_root)
        group_target = group["target"]
        if not is_within(marker, group_target) or not marker.is_file():
            continue
        try:
            marker.unlink()
        except OSError:
            continue
        removed.append(str(marker))
        remove_empty_parents(marker, group_target, checked=checked)
    return removed


def prune_empty_tree(path: Path, root: Path, root_r: Path | None = None, plain: bool = False) -> None:
    """清空一个作品源目录：先删掉内部空子目录，最后删掉它自己（不删扫描根目录）。"""
    if path == root:
        return
    if plain:
        if not _text_within(path, root):
            return
    elif not _within(_resolved(path), root_r or _resolved(root)):
        return
    if not path.is_dir():
        return
    # os.walk 自底向上，只用 scandir，不逐个创建 Path / stat，比 rglob 快很多
    for current, _dirs, _files in os.walk(path, topdown=False):
        try:
            scan_entries = os.scandir(current)
            try:
                has_child = next(scan_entries, None) is not None
            finally:
                scan_entries.close()
            if not has_child:
                os.rmdir(current)
        except OSError:
            continue


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
    index: DirIndex | None = None,
) -> tuple[str, str, str]:
    """执行一条已经落库的执行计划（一个文件或一个目录），返回 (状态, 最终目标, 说明)。"""
    source = Path(str(item.get("original_path") or ""))
    target_text = str(item.get("target_path") or "")
    if not target_text:
        return "failed", "", "没有目标路径"
    target = Path(target_text)
    group = _group_for(item, group_map, source, media_root)
    source_root_r = group.get("source_r") or _resolved(group["source"])
    target_root_r = group.get("target_r") or _resolved(group["target"])
    source_plain = bool(group.get("source_plain"))
    target_plain = bool(group.get("target_plain"))
    # 根目录没有软链时直接用字符串前缀判断，省掉每个文件两次 realpath（约 20ms）
    source_r = None if source_plain else _resolved(source)
    target_r = None if target_plain else _resolved(target)
    source_inside = (
        _text_within(source, group["source"]) if source_r is None else _within(source_r, source_root_r)
    )
    target_inside = (
        _text_within(target, group["target"]) if target_r is None else _within(target_r, target_root_r)
    )
    if not source_inside or not target_inside:
        return "failed", str(target), "路径超出该目录组的源/目标范围（禁止跨组移动）"
    choice = str(item.get("choice") or "")
    existing = index.find(target) if index is not None else find_existing(target)
    if choice == "overwrite" and existing is not None:
        # 用户在冲突面板确认过覆盖：先永久删除目标，再按普通移动处理
        problem = delete_target(existing)
        if problem:
            return "failed", str(target), problem
        if index is not None:
            index.forget(existing)
    status, final_target, message = move_one(
        source,
        target,
        "skip" if choice == "overwrite" else policy,
        group["source"],
        group["target"],
        treat_conflict=True,
        index=index,
        resolved=(source_r, target_r, source_root_r, target_root_r),
    )
    if choice == "overwrite" and status == "done":
        message = "已覆盖目标（原内容已永久删除）"
    return status, str(final_target), message


def cleanup_done_sources(
    done_items: list[dict],
    group_map: dict[str, dict],
    media_root: Path,
    low_io: bool = False,
) -> None:
    """全部批次结束后，清理已经清空的源目录树（只删空目录）。"""
    group_dirs: dict[str, tuple[Path, Path, Path | None, bool]] = {}
    # 网盘模式：同一父目录只判断一次，避免每个文件都把同一个目录列一遍。
    checked: set[str] | None = set() if low_io else None
    for item in done_items:
        source = Path(str(item.get("original_path") or ""))
        group = _group_for(item, group_map, source, media_root)
        group_source = group["source"]
        plain = bool(group.get("source_plain"))
        group_source_r = group.get("source_r") or _resolved(group_source)
        remove_empty_parents(source, group_source, group_source_r, plain, checked)
        try:
            relative = source.relative_to(group_source)
        except ValueError:
            continue
        if len(relative.parts) > 1:
            top = group_source / relative.parts[0]
            group_dirs[f"{group_source}|{top}"] = (group_source, top, group_source_r, plain)
    for group_source, group_dir, group_source_r, plain in group_dirs.values():
        prune_empty_tree(group_dir, group_source, group_source_r, plain)


def undo_items(
    items: list[dict],
    groups: list[dict],
    media_root: Path,
    keep_empty_dirs: bool = True,
    low_io: bool = False,
) -> list[dict]:
    """按逆序把已执行的文件移回原路径。"""
    group_map = _group_map(groups, media_root)
    # 还原时也复用目录名单：同一条目标目录里的多个文件不再逐个 stat / 列目录。
    # 空目录清理仍逐条判断：目录可能后面才变空，不能像结束时那样提前缓存“非空”。
    index = DirIndex(low_io=low_io)
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
        status, final_target, message = move_one(
            target, original, "skip", group_target, group_source, index=index
        )
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
