"""执行前风控预检：只做检查，不改动任何文件。

网盘挂载的媒体目录在密集读写时容易触发风控，执行前先把明显有风险的路径挑出来，
由用户决定「继续」还是「取消」。这里不做任何写入探测（不会创建临时文件），
只使用 stat / 列目录 / 权限位判断，因此预检本身也是低 IO 的。
"""

from __future__ import annotations

import os
from pathlib import Path

# Windows 文件名里不允许出现的字符（路径分隔符单独判断）
INVALID_CHARS = set('<>:"|?*')
# Windows 保留设备名（不分大小写，带扩展名也算）
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MAX_NAME_LENGTH = 255
MAX_PATH_LENGTH = 259
# 目标目录文件数超过这个值就提示：执行时列目录和冲突判断都会明显变慢
LARGE_DIR_FILE_COUNT = 5000
MAX_LISTED_RISKS = 50


def _normalized(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))


def _inside(path: Path, root: Path) -> bool:
    """纯字符串判断，不访问磁盘。"""
    child = _normalized(path)
    parent = _normalized(root)
    if child == parent:
        return True
    return child.startswith(parent.rstrip("\\/") + os.sep)


def _group_map(groups: list[dict], media_root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "")
        source = Path(str(group.get("source") or media_root))
        target = Path(str(group.get("target") or source))
        result[group_id] = {"id": group_id, "name": str(group.get("name") or group_id), "source": source, "target": target}
    if not result:
        result[""] = {"id": "", "name": "媒体根目录", "source": media_root, "target": media_root}
    return result


def _name_problem(name: str) -> str:
    if not name:
        return "文件名为空"
    if len(name) > MAX_NAME_LENGTH:
        return f"文件名过长（{len(name)} 个字符，超过 {MAX_NAME_LENGTH}）"
    bad = sorted({char for char in name if char in INVALID_CHARS or ord(char) < 32})
    if bad:
        shown = " ".join(repr(char) for char in bad[:5])
        return f"文件名包含非法字符：{shown}"
    stem = name.split(".")[0].strip().upper()
    if stem in RESERVED_NAMES:
        return f"文件名使用了 Windows 保留名「{stem}」，可能无法创建"
    if name.endswith(" ") or name.endswith("."):
        return "文件名以空格或点结尾，Windows 上可能无法创建"
    return ""


def _count_files(directory: Path) -> int:
    """最多数到阈值 + 1，避免在超大目录上白扫一遍。"""
    count = 0
    try:
        with os.scandir(directory) as entries:
            for _entry in entries:
                count += 1
                if count > LARGE_DIR_FILE_COUNT:
                    break
    except OSError:
        return 0
    return count


def check_execution(items: list[dict], groups: list[dict], media_root: Path) -> list[dict]:
    """返回风险清单；空列表表示没有发现需要提示的问题。"""
    group_map = _group_map(groups, media_root)
    risks: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, message: str, path: object = "") -> None:
        key = (code, _normalized(path))
        if key in seen:
            return
        seen.add(key)
        risks.append({"code": code, "level": "warning", "message": message, "path": str(path or "")})

    # 1. 目标目录本身是否可达 / 是否可写
    checked_targets: set[str] = set()
    for group in group_map.values():
        target = group["target"]
        key = _normalized(target)
        if key in checked_targets:
            continue
        checked_targets.add(key)
        name = group["name"]
        if target.exists():
            if not target.is_dir():
                add("target-not-dir", f"目录组「{name}」的目标路径不是文件夹：{target}", target)
            elif not os.access(target, os.W_OK):
                add("target-readonly", f"目录组「{name}」的目标目录可能不可写：{target}", target)
        elif not target.parent.exists():
            add(
                "target-unreachable",
                f"目录组「{name}」的目标目录不存在，上级目录也不存在，可能是网盘未挂载：{target}",
                target,
            )

    # 2. 每条目标路径：是否在所属目录组内、文件名是否合法、完整路径是否过长
    large_dirs: dict[str, int] = {}
    for item in items:
        if item.get("status") != "planned":
            continue
        target_text = str(item.get("target_path") or "")
        if not target_text:
            continue
        target = Path(target_text)
        group = group_map.get(str(item.get("group_id") or ""))
        if group is not None and not _inside(target, group["target"]):
            add(
                "target-outside-group",
                f"目标路径超出目录组「{group['name']}」的范围：{target}",
                target,
            )
        problem = _name_problem(target.name)
        if problem:
            add("name-invalid", f"{problem}：{target.name}", target)
        if len(str(target)) > MAX_PATH_LENGTH:
            add(
                "path-too-long",
                f"完整路径超过 {MAX_PATH_LENGTH} 个字符，部分系统可能无法写入：{target}",
                target,
            )
        # 3. 目标目录现有文件数特别大：只统计一次，最多数到阈值 + 1
        parent_key = _normalized(target.parent)
        if parent_key not in large_dirs:
            large_dirs[parent_key] = _count_files(target.parent) if target.parent.is_dir() else 0
        count = large_dirs[parent_key]
        if count > LARGE_DIR_FILE_COUNT:
            add(
                "target-dir-large",
                f"目标目录已有超过 {LARGE_DIR_FILE_COUNT} 个文件，执行时列目录和冲突判断会明显变慢：{target.parent}",
                target.parent,
            )

    if len(risks) > MAX_LISTED_RISKS:
        extra = len(risks) - MAX_LISTED_RISKS
        risks = risks[:MAX_LISTED_RISKS]
        risks.append(
            {
                "code": "more-risks",
                "level": "warning",
                "message": f"另有 {extra} 条同类提示未展开",
                "path": "",
            }
        )
    return risks
