"""按文件名形状分桶 + 确定性抽样（AI 单次条数上限的本地解法）。

为什么需要：
- AI 一次只能看有限条文件（`providers.ai_batch_limit`），但一个目录可能有几百条；
- 直接把前 N 条发过去会漏掉稀有形态（例如只有一条的 SP），规律就会不完整；
- 所以先在本地按「形状」分桶，再按固定规则抽样，最后让 AI 的规律在**全量**文件上复核。

抽样规则（写死，不靠运气）：
1. 出现次数 ≤ 2 的形状全部带上（稀有的不能漏）；
2. 每个桶至少 1 条，优先取该桶最靠前和最靠后的各一条；
3. 总条数不超过上限；超了先保稀有形状，再让常见形状按「首尾 + 均匀分布」减量；
4. 所有被削掉的形状与条数都记录在 `trimmed` 里，结果可解释。

本模块只读文件名，不改扫描结果，也不发网络请求。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .pattern import shape_signature

RARE_LIMIT = 2


def _midpoint_order(count: int) -> list[int]:
    """按“先中间、再两侧”的顺序给出下标，用于常见形状的均匀补样。"""
    if count <= 0:
        return []
    order: list[int] = []
    queue = [(0, count - 1)]
    while queue:
        low, high = queue.pop(0)
        if low > high:
            continue
        mid = (low + high) // 2
        order.append(mid)
        queue.append((low, mid - 1))
        queue.append((mid + 1, high))
    return order


def bucket_by_shape(filenames: list[str]) -> "OrderedDict[str, list[tuple[int, str]]]":
    """按形状分桶；每个条目保留原始下标，保证输出顺序稳定。"""
    buckets: "OrderedDict[str, list[tuple[int, str]]]" = OrderedDict()
    for index, name in enumerate(filenames):
        text = str(name or "")
        if not text:
            continue
        signature = shape_signature(Path(text).stem)
        buckets.setdefault(signature, []).append((index, text))
    return buckets


def sample_filenames(filenames: list[str], limit: int) -> dict:
    """从全量文件名里抽样，返回 samples（保持原顺序）与可解释的报告。"""
    names = [str(item or "") for item in filenames]
    total = len(names)
    limit = max(1, int(limit or 1))
    buckets = bucket_by_shape(names)
    shapes = list(buckets.keys())
    counts = {shape: len(items) for shape, items in buckets.items()}
    selected: set[int] = set()

    def take(index: int) -> bool:
        if len(selected) >= limit or index in selected:
            return False
        selected.add(index)
        return True

    rare_shapes = [shape for shape in shapes if counts[shape] <= RARE_LIMIT]
    common_shapes = [shape for shape in shapes if counts[shape] > RARE_LIMIT]
    rare_total = sum(counts[shape] for shape in rare_shapes)
    rare_limit_hit = False

    # 1. 稀有形状全带上；如果稀有本身就超过上限，只能先保前面的，并标记。
    for shape in rare_shapes:
        for index, _ in buckets[shape]:
            if len(selected) >= limit:
                rare_limit_hit = True
                break
            take(index)
        if rare_limit_hit:
            break

    # 2. 每个常见形状至少一条：先首条，再末条。
    for position in ("first", "last"):
        for shape in common_shapes:
            if len(selected) >= limit:
                break
            items = buckets[shape]
            index = items[0][0] if position == "first" else items[-1][0]
            take(index)

    # 3. 还有余量就按中间向两侧均匀补样，轮询各个常见形状。
    fill_orders = {
        shape: [
            buckets[shape][local][0]
            for local in _midpoint_order(counts[shape])
            if buckets[shape][local][0] not in selected
        ]
        for shape in common_shapes
    }
    while len(selected) < limit and any(fill_orders.values()):
        for shape in common_shapes:
            if len(selected) >= limit:
                break
            pending = fill_orders[shape]
            while pending and pending[0] in selected:
                pending.pop(0)
            if pending:
                take(pending.pop(0))

    samples = [names[index] for index in sorted(selected)]

    bucket_reports = []
    trimmed = []
    uncovered = []
    for shape in shapes:
        items = buckets[shape]
        sent_items = [(index, name) for index, name in items if index in selected]
        sent = len(sent_items)
        bucket_reports.append(
            {
                "shape": shape,
                "count": counts[shape],
                "sent": sent,
                "rare": counts[shape] <= RARE_LIMIT,
                "examples": [name for _, name in items[:2]],
            }
        )
        if sent == 0:
            uncovered.append(shape)
        if sent < counts[shape]:
            missing = [name for index, name in items if index not in selected]
            trimmed.append(
                {
                    "shape": shape,
                    "total": counts[shape],
                    "sent": sent,
                    "dropped": counts[shape] - sent,
                    "dropped_examples": missing[:2],
                }
            )

    if total <= limit:
        note = f"全量 {total} 条没有超过上限 {limit} 条，全部发送，没有抽样"
    else:
        note = (
            f"全量 {total} 条，按文件名形状分成 {len(shapes)} 桶，"
            f"实际发送 {len(samples)} 条（上限 {limit} 条）；"
            f"稀有形状（≤{RARE_LIMIT} 条）{len(rare_shapes)} 种 / {rare_total} 条"
            + ("，因超过上限未能全部保留" if rare_limit_hit else "，已全部保留")
            + f"；常见形状按首尾 + 均匀分布减量，明细见 trimmed"
        )
    return {
        "total": total,
        "sent": len(samples),
        "limit": limit,
        "buckets": bucket_reports,
        "trimmed": trimmed,
        "uncovered": uncovered,
        "rare_shapes": len(rare_shapes),
        "rare_total": rare_total,
        "rare_limit_hit": rare_limit_hit,
        "filenames": samples,
        "note": note,
    }
