"""文件哈希基线：给目录拍只读快照，并在之后对比出新增 / 删除 / 内容变化 / 仅时间变化。

设计约束：
- 全程只读：只 open(..., "rb") 读内容、stat 取元数据，从不写、不改名、不移动、不写标记文件；
- 输出文件（JSON / 报告）会先检查是否落在被扫描目录里，落在里面就直接拒绝，避免污染快照目标；
- 符号链接不跟随（记录为“已跳过”），权限不足或读失败的文件单独列在 errors 里，不会静默吞掉。

用法：
    拍快照：
        python backend/tools/hash_baseline.py snapshot \
            --root "C:\\Trae\\ani-rename\\media\\1" \
            --root "C:\\Trae\\ani-rename\\media\\2" \
            --output baseline.json --summary summary.md

    对比：
        python backend/tools/hash_baseline.py compare \
            --baseline baseline.json --output report.md

    换机器/换挂载点对比（标签=新路径，标签就是快照里的文件夹名）：
        python backend/tools/hash_baseline.py compare \
            --baseline baseline.json --output report.md \
            --root "1=/media/1" --root "2=/media/2"

退出码：snapshot 成功 0、失败 2；compare 完全一致 0、有差异 1、失败 2。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ALGORITHM = "sha256"
CHUNK_SIZE = 1024 * 1024
REPORT_LIMIT = 200


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mtime_iso(value: float) -> str:
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def _human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _parse_roots(values: list[str]) -> list[dict]:
    """--root 取路径；文件夹名作为标签（compare 时可写成 标签=路径 覆盖）。"""
    roots: list[dict] = []
    seen: set[str] = set()
    for text in values:
        label, sep, path_text = text.partition("=")
        if not sep or not path_text:
            label, path_text = Path(text).name, text
        label = label.strip()
        path = Path(path_text.strip())
        if not label:
            raise SystemExit(f"根目录标签为空：{text}")
        if label in seen:
            raise SystemExit(f"根目录标签重复：{label}")
        seen.add(label)
        roots.append({"label": label, "path": str(path)})
    return roots


def _ensure_output_outside(roots: list[dict], *outputs: str | None) -> None:
    """输出文件不能写进被扫描目录，否则会改变快照对象。"""
    targets = [Path(text) for text in outputs if text]
    for root in roots:
        root_path = Path(root["path"]).resolve()
        for target in targets:
            resolved = target.resolve()
            try:
                resolved.relative_to(root_path)
            except ValueError:
                continue
            raise SystemExit(f"安全中断：输出文件落在被扫描目录内：{resolved}")


def _hash_file(path: Path) -> tuple[str, int]:
    """返回 (sha256, 实际读到的字节数)。"""
    digest = hashlib.sha256()
    read = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            read += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), read


def _scan_root(root: dict, files: dict, skipped: list, errors: list, progress: dict) -> None:
    """遍历一个根目录，把普通文件写进 files；符号链接 / 特殊文件记入 skipped。"""
    label = root["label"]
    base = Path(root["path"])
    if not base.is_dir():
        errors.append({"path": str(base), "reason": "根目录不存在或不是文件夹"})
        return
    stack = [base]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            errors.append({"path": str(directory), "reason": f"无法读取目录：{exc}"})
            continue
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                if entry.is_symlink():
                    skipped.append({"path": str(entry_path), "reason": "符号链接，未跟随"})
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry_path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    skipped.append({"path": str(entry_path), "reason": "不是普通文件（设备 / 管道等）"})
                    continue
                stat = entry.stat(follow_symlinks=False)
                digest, read = _hash_file(entry_path)
                relative = entry_path.relative_to(base).as_posix()
                files[f"{label}/{relative}"] = {
                    "size": int(stat.st_size),
                    "sha256": digest,
                    "mtime_ns": int(stat.st_mtime_ns),
                    "mtime": _mtime_iso(stat.st_mtime),
                }
                progress["files"] += 1
                progress["bytes"] += int(stat.st_size)
                progress["read_bytes"] += read
                if progress["files"] % 200 == 0:
                    print(f"[snapshot] {progress['files']} files ...", flush=True)
            except OSError as exc:
                errors.append({"path": str(entry_path), "reason": f"读取失败：{exc}"})


def _scan_all(roots: list[dict]) -> tuple[dict, list, list, dict]:
    files: dict = {}
    skipped: list = []
    errors: list = []
    progress = {"files": 0, "bytes": 0, "read_bytes": 0}
    for root in roots:
        before = progress["files"]
        _scan_root(root, files, skipped, errors, progress)
        root["files"] = progress["files"] - before
    return files, skipped, errors, progress


def cmd_snapshot(args: argparse.Namespace) -> int:
    roots = _parse_roots(args.root)
    _ensure_output_outside(roots, args.output, args.summary)
    started = time.perf_counter()
    files, skipped, errors, progress = _scan_all(roots)
    elapsed = time.perf_counter() - started

    baseline = {
        "version": 1,
        "algorithm": ALGORITHM,
        "created_at": _now_iso(),
        "elapsed_seconds": round(elapsed, 3),
        "roots": roots,
        "files": files,
        "stats": {
            "files": progress["files"],
            "bytes": progress["bytes"],
            "read_bytes": progress["read_bytes"],
            "skipped": len(skipped),
            "errors": len(errors),
        },
        "skipped": skipped,
        "errors": errors,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.summary:
        summary = Path(args.summary)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(_snapshot_summary(baseline), encoding="utf-8")

    print(
        f"[snapshot] done files={progress['files']} bytes={progress['bytes']} "
        f"elapsed={elapsed:.1f}s skipped={len(skipped)} errors={len(errors)}",
        flush=True,
    )
    return 0


def _snapshot_summary(baseline: dict) -> str:
    stats = baseline["stats"]
    lines = [
        "# 媒体哈希基线（只读快照）",
        "",
        f"- 拍摄时间：{baseline['created_at']}",
        f"- 哈希算法：{baseline['algorithm']}（逐文件计算内容哈希，不只看文件名和大小）",
        f"- 耗时：{baseline['elapsed_seconds'] / 60:.2f} 分钟（{baseline['elapsed_seconds']:.1f} 秒）",
        f"- 文件总数：{stats['files']}",
        f"- 总大小：{_human_size(stats['bytes'])}（{stats['bytes']} 字节）",
        f"- 实际读取数据量：{_human_size(stats['read_bytes'])}（{stats['read_bytes']} 字节）",
        f"- 跳过：{stats['skipped']} 个；读取失败：{stats['errors']} 个",
        "",
        "## 每个文件夹",
        "",
        "| 文件夹 | 路径 | 文件数 | 大小 |",
        "| --- | --- | ---: | ---: |",
    ]
    for root in baseline["roots"]:
        prefix = f"{root['label']}/"
        selected = [value for key, value in baseline["files"].items() if key.startswith(prefix)]
        total = sum(int(item["size"]) for item in selected)
        lines.append(f"| {root['label']} | `{root['path']}` | {len(selected)} | {_human_size(total)} |")
    lines.append(f"| **合计** |  | **{stats['files']}** | **{_human_size(stats['bytes'])}** |")
    lines.extend(["", "## 跳过的文件", ""])
    if baseline["skipped"]:
        for item in baseline["skipped"]:
            lines.append(f"- `{item['path']}`：{item['reason']}")
    else:
        lines.append("无（没有符号链接或特殊文件）。")
    lines.extend(["", "## 读取失败", ""])
    if baseline["errors"]:
        for item in baseline["errors"]:
            lines.append(f"- `{item['path']}`：{item['reason']}")
    else:
        lines.append("无，所有文件都成功读取并计算了哈希。")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "本脚本全程只读：只读取文件内容与元数据，没有写入、改名、移动任何文件，也没有生成标记文件。",
            "输出文件特意放在被扫描目录之外。",
            "",
            "## 以后怎么用",
            "",
            "```powershell",
            "# 拍新快照（对比前先拍）",
            'python backend/tools/hash_baseline.py snapshot --root "C:\\Trae\\ani-rename\\media\\1" --output new.json',
            "",
            "# 与这份基线对比，输出中文报告",
            "python backend/tools/hash_baseline.py compare --baseline baseline.json --output report.md",
            "```",
            "",
            "`compare` 会列出新增、删除、内容变化（SHA256 不同）和只是修改时间变化的文件；",
            "退出码 0 表示完全一致，1 表示有差异，2 表示执行失败。",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_compare(args: argparse.Namespace) -> int:
    baseline_path = Path(args.baseline)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[compare] cannot read baseline: {exc}", flush=True)
        return 2
    if baseline.get("algorithm") != ALGORITHM or not isinstance(baseline.get("files"), dict):
        print("[compare] baseline format not recognized", flush=True)
        return 2

    overrides = _parse_roots(args.root or [])
    override_map = {item["label"]: item["path"] for item in overrides}
    roots = []
    for item in baseline.get("roots") or []:
        roots.append({"label": item["label"], "path": override_map.get(item["label"], item["path"])})
    missing = [label for label in override_map if label not in {item["label"] for item in roots}]
    if missing:
        print(f"[compare] unknown root label(s): {', '.join(missing)}", flush=True)
        return 2
    _ensure_output_outside(roots, args.output)

    started = time.perf_counter()
    current, skipped, errors, progress = _scan_all(roots)
    elapsed = time.perf_counter() - started
    old_files: dict = baseline["files"]

    added = sorted(key for key in current if key not in old_files)
    deleted = sorted(key for key in old_files if key not in current)
    content_changed = []
    time_changed = []
    for key in sorted(set(current) & set(old_files)):
        old = old_files[key]
        new = current[key]
        if int(old.get("size", -1)) != int(new["size"]) or old.get("sha256") != new["sha256"]:
            content_changed.append((key, old, new))
        elif int(old.get("mtime_ns", 0)) != int(new["mtime_ns"]):
            time_changed.append((key, old, new))

    old_bytes = int((baseline.get("stats") or {}).get("bytes") or 0)
    new_bytes = progress["bytes"]
    report = _compare_report(
        baseline,
        roots,
        current,
        added,
        deleted,
        content_changed,
        time_changed,
        skipped,
        errors,
        progress,
        elapsed,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")

    difference = len(added) + len(deleted) + len(content_changed) + len(time_changed)
    print(
        f"[compare] added={len(added)} deleted={len(deleted)} "
        f"content_changed={len(content_changed)} time_changed={len(time_changed)} "
        f"files={progress['files']} bytes={new_bytes} elapsed={elapsed:.1f}s "
        f"old_bytes={old_bytes}",
        flush=True,
    )
    print(f"[compare] differences={difference}", flush=True)
    return 0 if difference == 0 else 1


def _compare_report(
    baseline: dict,
    roots: list[dict],
    current: dict,
    added: list[str],
    deleted: list[str],
    content_changed: list[tuple[str, dict, dict]],
    time_changed: list[tuple[str, dict, dict]],
    skipped: list,
    errors: list,
    progress: dict,
    elapsed: float,
) -> str:
    difference = len(added) + len(deleted) + len(content_changed) + len(time_changed)
    old_bytes = int((baseline.get("stats") or {}).get("bytes") or 0)
    roots_text = "、".join("{}（{}）".format(item["label"], item["path"]) for item in roots)
    lines = [
        "# 媒体目录对比报告",
        "",
        f"- 对比时间：{_now_iso()}",
        f"- 基线拍摄时间：{baseline.get('created_at', '未知')}",
        f"- 哈希算法：{baseline.get('algorithm')}",
        f"- 扫描范围：{roots_text}",
        f"- 本次耗时：{elapsed:.1f} 秒",
        f"- 本次实际读取：{_human_size(progress['read_bytes'])}",
        "",
        "## 结论",
        "",
        "**完全一致：没有新增、删除、内容变化或时间变化。**" if difference == 0
        else f"**发现 {difference} 处差异。**",
        "",
        "| 项目 | 基线 | 当前 | 差异 |",
        "| --- | ---: | ---: | ---: |",
        f"| 文件数 | {len(baseline['files'])} | {len(current)} | {len(current) - len(baseline['files']):+d} |",
        f"| 总大小 | {_human_size(old_bytes)} | {_human_size(progress['bytes'])} | "
        f"{_human_size(abs(progress['bytes'] - old_bytes))} |",
        f"| 新增 | - | - | {len(added)} |",
        f"| 删除 | - | - | {len(deleted)} |",
        f"| 内容变化（SHA256） | - | - | {len(content_changed)} |",
        f"| 仅修改时间变化 | - | - | {len(time_changed)} |",
        "",
    ]

    def section(title: str, rows: list[str]) -> None:
        lines.append(f"## {title}（{len(rows)}）")
        lines.append("")
        if not rows:
            lines.append("无。")
        else:
            for row in rows[:REPORT_LIMIT]:
                lines.append(f"- `{row}`")
            if len(rows) > REPORT_LIMIT:
                lines.append(f"- ……另有 {len(rows) - REPORT_LIMIT} 条未展开")
        lines.append("")

    section("新增文件", added)
    section("删除文件", deleted)

    changed_rows = []
    for key, old, new in content_changed:
        changed_rows.append(
            f"{key}（大小 {old.get('size')} → {new['size']}，"
            f"SHA256 {str(old.get('sha256'))[:12]}… → {new['sha256'][:12]}…）"
        )
    section("内容变化的文件", changed_rows)

    time_rows = [f"{key}（{old.get('mtime')} → {new['mtime']}，内容哈希未变）" for key, old, new in time_changed]
    section("仅修改时间变化的文件", time_rows)

    lines.append(f"## 跳过的文件（{len(skipped)}）")
    lines.append("")
    if skipped:
        for item in skipped[:REPORT_LIMIT]:
            lines.append(f"- `{item['path']}`：{item['reason']}")
        if len(skipped) > REPORT_LIMIT:
            lines.append(f"- ……另有 {len(skipped) - REPORT_LIMIT} 条未展开")
    else:
        lines.append("无（没有符号链接或特殊文件）。")
    lines.append("")

    lines.append(f"## 读取失败（{len(errors)}）")
    lines.append("")
    if errors:
        for item in errors[:REPORT_LIMIT]:
            lines.append(f"- `{item['path']}`：{item['reason']}")
        if len(errors) > REPORT_LIMIT:
            lines.append(f"- ……另有 {len(errors) - REPORT_LIMIT} 条未展开")
    else:
        lines.append("无，所有文件都成功读取并计算了哈希。")
    lines.append("")

    lines.append("## 每个文件夹")
    lines.append("")
    lines.append("| 文件夹 | 文件数（基线 → 当前） | 大小（基线 → 当前） |")
    lines.append("| --- | ---: | ---: |")
    for root in roots:
        prefix = f"{root['label']}/"
        old_selected = [value for key, value in baseline["files"].items() if key.startswith(prefix)]
        new_selected = [value for key, value in current.items() if key.startswith(prefix)]
        lines.append(
            f"| {root['label']} | {len(old_selected)} → {len(new_selected)} | "
            f"{_human_size(sum(int(item['size']) for item in old_selected))} → "
            f"{_human_size(sum(int(item['size']) for item in new_selected))} |"
        )
    lines.append("")
    lines.append("说明：本脚本只读对比，未写入、改名、移动任何文件。")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文件哈希基线：snapshot 拍快照，compare 对比（全程只读）")
    sub = parser.add_subparsers(dest="mode", required=True)

    snap = sub.add_parser("snapshot", help="扫描目录并输出快照 JSON")
    snap.add_argument("--root", action="append", required=True, help="要扫描的目录，可重复")
    snap.add_argument("--output", required=True, help="快照 JSON 输出路径")
    snap.add_argument("--summary", help="可选：中文摘要 Markdown 输出路径")
    snap.set_defaults(func=cmd_snapshot)

    cmp_parser = sub.add_parser("compare", help="用旧快照对比当前状态")
    cmp_parser.add_argument("--baseline", required=True, help="基线 JSON 路径")
    cmp_parser.add_argument("--output", required=True, help="中文对比报告输出路径")
    cmp_parser.add_argument(
        "--root",
        action="append",
        help="可选：标签=路径，覆盖基线里记录的路径（例如 1=/media/1）",
    )
    cmp_parser.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("[abort] interrupted", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
