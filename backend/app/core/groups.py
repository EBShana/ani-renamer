"""目录组：一组“源目录 + 目标目录”。

组内可以自由移动，组间必须相互独立（源目录、目标目录都不能嵌套或重叠），
避免一次任务里把别的组的文件卷进来。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..scanner_helpers import is_under


def normalize_group(item: dict) -> dict:
    source = str(item.get("source") or "").strip()
    target = str(item.get("target") or "").strip() or source
    return {
        "id": str(item.get("id") or f"group-{uuid.uuid4().hex[:10]}"),
        "name": str(item.get("name") or "未命名目录组")[:120],
        "source": source,
        "target": target,
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def snapshot(group: dict) -> dict:
    """扫描 / 执行时保存的组快照；之后改目录组不影响已有任务。"""
    return {
        "id": group["id"],
        "name": group.get("name") or "",
        "source": str(group.get("source") or ""),
        "target": str(group.get("target") or group.get("source") or ""),
    }


def _resolve(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def validate_groups(groups: list[dict], media_root: Path) -> list[str]:
    """检查目录组是否可用；返回给用户看的中文错误列表。"""
    errors: list[str] = []
    if not groups:
        return ["请至少添加一个目录组"]

    roots: list[tuple[str, str, str, Path]] = []
    for index, group in enumerate(groups, start=1):
        name = group.get("name") or f"第 {index} 组"
        group_id = str(group.get("id") or f"index-{index}")
        source_text = str(group.get("source") or "").strip()
        target_text = str(group.get("target") or "").strip() or source_text
        if not source_text:
            errors.append(f"「{name}」没有填写源目录")
            continue
        source = _resolve(source_text)
        target = _resolve(target_text)
        if not is_under(source, media_root):
            errors.append(f"「{name}」的源目录必须位于媒体根目录 {media_root} 内")
        elif not source.exists():
            errors.append(f"「{name}」的源目录不存在：{source_text}")
        elif not source.is_dir():
            errors.append(f"「{name}」的源目录不是文件夹：{source_text}")
        if not is_under(target, media_root):
            errors.append(f"「{name}」的目标目录必须位于媒体根目录 {media_root} 内")
        roots.append((group_id, name, "源目录", source))
        roots.append((group_id, name, "目标目录", target))

    for source_index in range(len(roots)):
        for target_index in range(source_index + 1, len(roots)):
            left_id, left_name, left_kind, left = roots[source_index]
            right_id, right_name, right_kind, right = roots[target_index]
            if left_id == right_id:
                # 同一组里的源目录和目标目录允许相同或嵌套
                continue
            if is_under(left, right) or is_under(right, left):
                errors.append(
                    f"「{left_name}」的{left_kind}与「{right_name}」的{right_kind}"
                    f"重叠或嵌套，目录组之间必须相互独立"
                )
    return errors
