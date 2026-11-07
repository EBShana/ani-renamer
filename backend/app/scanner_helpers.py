"""媒体根目录校验的共享辅助函数。"""

from __future__ import annotations

from pathlib import Path


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
