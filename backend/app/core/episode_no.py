"""集号的统一取值：整数（12）和小数（24.5）都要能表达。

AI 命名规律可能识别出 `24.5` 这种小数集号。它必须按真实数值参与排序和去重：
第 24 集和 24.5 是两个不同的集，不能被判成重复集。

约定：
- `row["episode"]` 存数值（int 或 float）；
- `row["episode_exact"]` 存文件名里的原文（如 "24.5"），命名和显示优先用它；
- 本模块的 `episode_value` / `number_key` 负责统一比较，避免各处各写一套。
"""

from __future__ import annotations

from typing import Any


def _parse_number(text: str) -> int | float | None:
    value = text.strip()
    if not value:
        return None
    try:
        number: int | float = float(value) if "." in value else int(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def episode_value(row: dict) -> int | float | None:
    """取可用于排序、去重的集号数值；没有集号返回 None。"""
    exact = row.get("episode_exact")
    if isinstance(exact, str) and exact.strip():
        number = _parse_number(exact)
        if number is not None:
            return number
    value = row.get("episode")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if value >= 0 else None
    if isinstance(value, str):
        return _parse_number(value)
    return None


def episode_text(row: dict) -> str:
    """取集号原文，用于显示和命名；没有集号返回空字符串。"""
    exact = row.get("episode_exact")
    if isinstance(exact, str) and exact.strip():
        return exact.strip()
    value = row.get("episode")
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def number_key(value: Any) -> str:
    """把数值转成稳定的比较键：24 和 24.0 都是 "24"，24.5 是 "24.5"。"""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)
