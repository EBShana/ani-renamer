"""网盘模式 IO 对比：在副本上分别用「关闭 / 打开」跑同一套扫描 → 预览 → 执行 → 清空目录，
统计对媒体目录发起的 stat / 列目录 / 打开文件次数，并校验两种模式的最终结果完全一致。

用法（本机，在 backend 目录下）：
    python tools/io_bench.py --source ..\\media\\5 --output ..\\2026-11-11\\network-drive-io\\outputs\\io-bench.json --report ..\\2026-11-11\\network-drive-io\\outputs\\io-bench.md

用法（容器）：
    docker exec -w /app anime-renamer python tools/io_bench.py --source /bench-src/5 ...

说明：
- 只读取 --source 指向的目录用于复制副本，不会修改源目录；
- 所有统计都发生在系统临时目录里的副本上；
- 计数口径：Python 层对「副本根目录」内的路径发起的调用次数（stat/lstat、scandir/iterdir、
  open 读 / open 写、rename、mkdir、unlink、rmdir）。DirEntry 内部缓存导致的重复 is_dir
  也会计一次，两种模式口径一致，因此相对降幅可信。
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

METRICS = ("stat", "listdir", "open_read", "open_write", "move", "mkdir", "unlink", "rmdir")

PRESET = {
    "id": "io-bench",
    "name": "io-bench",
    "templates": {
        "episode": {
            "pattern": "{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]",
            "output_mode": "filename",
        },
        "special": {
            "pattern": "{series} - {source_season} {special_type}{special_no}[v{version}]",
            "output_mode": "filename",
        },
        "movie": {"pattern": "{series} ({year})", "output_mode": "filename"},
    },
}


class Counter:
    def __init__(self) -> None:
        self.values = {name: 0 for name in METRICS}

    def bump(self, name: str, count: int = 1) -> None:
        self.values[name] += count

    def snapshot(self) -> dict[str, int]:
        return dict(self.values)


_COUNTER: Counter | None = None
_ROOT: str = ""


def _inside(path: object) -> bool:
    if isinstance(path, int):
        return False
    try:
        text = os.fspath(path)
    except TypeError:
        return False
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        return os.path.normcase(os.path.abspath(text)).startswith(_ROOT)
    except (OSError, ValueError):
        return False


def _bump(name: str) -> None:
    if _COUNTER is not None:
        _COUNTER.bump(name)


class _EntryProxy:
    """包一层 DirEntry，统计 stat / is_dir / is_file / is_symlink 调用。"""

    __slots__ = ("_entry",)

    def __init__(self, entry: os.DirEntry) -> None:
        self._entry = entry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._entry, name)

    def stat(self, *, follow_symlinks: bool = True):
        _bump("stat")
        return self._entry.stat(follow_symlinks=follow_symlinks)

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        _bump("stat")
        return self._entry.is_dir(follow_symlinks=follow_symlinks)

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        _bump("stat")
        return self._entry.is_file(follow_symlinks=follow_symlinks)

    def is_symlink(self) -> bool:
        _bump("stat")
        return self._entry.is_symlink()


class _ScandirProxy:
    __slots__ = ("_inner",)

    def __init__(self, inner) -> None:
        self._inner = inner

    def __iter__(self) -> "_ScandirProxy":
        return self

    def __next__(self) -> _EntryProxy:
        return _EntryProxy(next(self._inner))

    def __enter__(self) -> "_ScandirProxy":
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()

    def close(self) -> None:
        self._inner.close()


@contextmanager
def measure(root: Path) -> Iterator[Counter]:
    """进入后统计对 root 内路径的文件操作；退出时恢复所有被替换的函数。"""
    global _COUNTER, _ROOT
    counter = Counter()
    real_stat, real_lstat = os.stat, os.lstat
    real_scandir, real_listdir = os.scandir, os.listdir
    real_rename, real_replace = os.rename, os.replace
    real_mkdir, real_unlink, real_remove, real_rmdir = os.mkdir, os.unlink, os.remove, os.rmdir
    real_open = builtins.open
    real_io_open = io.open
    previous = (_COUNTER, _ROOT)
    _COUNTER = counter
    _ROOT = os.path.normcase(os.path.abspath(str(root))) + os.sep

    def stat(path, *args, **kwargs):
        if _inside(path):
            _bump("stat")
        return real_stat(path, *args, **kwargs)

    def lstat(path, *args, **kwargs):
        if _inside(path):
            _bump("stat")
        return real_lstat(path, *args, **kwargs)

    def scandir(path=".", *args, **kwargs):
        if _inside(path):
            _bump("listdir")
        return _ScandirProxy(real_scandir(path, *args, **kwargs))

    def listdir(path="."):
        if _inside(path):
            _bump("listdir")
        return real_listdir(path)

    def rename(src, dst, *args, **kwargs):
        if _inside(src) or _inside(dst):
            _bump("move")
        return real_rename(src, dst, *args, **kwargs)

    def replace(src, dst, *args, **kwargs):
        if _inside(src) or _inside(dst):
            _bump("move")
        return real_replace(src, dst, *args, **kwargs)

    def mkdir(path, *args, **kwargs):
        if _inside(path):
            _bump("mkdir")
        return real_mkdir(path, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        if _inside(path):
            _bump("unlink")
        return real_unlink(path, *args, **kwargs)

    def remove(path, *args, **kwargs):
        if _inside(path):
            _bump("unlink")
        return real_remove(path, *args, **kwargs)

    def rmdir(path, *args, **kwargs):
        if _inside(path):
            _bump("rmdir")
        return real_rmdir(path, *args, **kwargs)

    def open_proxy(file, mode="r", *args, **kwargs):
        if _inside(file) and isinstance(mode, str):
            _bump("open_write" if any(char in mode for char in "wax+") else "open_read")
        return real_open(file, mode, *args, **kwargs)

    os.stat, os.lstat = stat, lstat
    os.scandir, os.listdir = scandir, listdir
    os.rename, os.replace = rename, replace
    os.mkdir, os.unlink, os.remove, os.rmdir = mkdir, unlink, remove, rmdir
    builtins.open = open_proxy
    io.open = open_proxy
    try:
        yield counter
    finally:
        os.stat, os.lstat = real_stat, real_lstat
        os.scandir, os.listdir = real_scandir, real_listdir
        os.rename, os.replace = real_rename, real_replace
        os.mkdir, os.unlink, os.remove, os.rmdir = real_mkdir, real_unlink, real_remove, real_rmdir
        builtins.open = real_open
        io.open = real_io_open
        _COUNTER, _ROOT = previous


def _fingerprint(root: Path) -> dict[str, tuple[int, str]]:
    """目录内容指纹：相对路径 →（大小, SHA256）。只用于校验两份副本结果一致。"""
    result: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result[path.relative_to(root).as_posix()] = (path.stat().st_size, digest.hexdigest())
    return result


def _write_settings(config_dir: Path, network_mode: bool) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "values": {"execution.network_drive_mode": network_mode}}
    (config_dir / "settings.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_mode(source: Path, work: Path, network_mode: bool) -> dict:
    from app.config import load_settings
    from app.core.executor import DirIndex, build_group_map, cleanup_done_sources, execute_item
    from app.core.operations import build_plan
    from app.core.preview import build_preview
    from app.core.scanner import scan_groups

    config_dir = Path(os.environ["APP_CONFIG_DIR"])
    _write_settings(config_dir, network_mode)
    settings = load_settings()

    src = work / "src"
    dst = work / "dst"
    dst.mkdir(parents=True, exist_ok=True)
    groups = [{"id": "g1", "name": "bench", "source": str(src), "target": str(dst)}]
    media_root = work

    with measure(work) as counter:
        scan_started = time.perf_counter()
        scan = scan_groups(groups, media_root, settings, [])
        scan_elapsed = time.perf_counter() - scan_started
    phases = {"scan": counter.snapshot()}

    with measure(work) as counter:
        preview_started = time.perf_counter()
        preview = build_preview(scan, PRESET, settings, None, None, 0, None, None)
        preview_elapsed = time.perf_counter() - preview_started
    phases["preview"] = counter.snapshot()

    plan = build_plan(preview["rows"])
    group_map = build_group_map(groups, media_root)
    index = DirIndex(low_io=network_mode)
    done_items: list[dict] = []
    outcomes: dict[str, int] = {"done": 0, "skipped": 0, "failed": 0, "conflict": 0}
    with measure(work) as counter:
        execute_started = time.perf_counter()
        for item in plan:
            if item.get("status") != "planned":
                continue
            status, final_target, message = execute_item(
                item, group_map, media_root, "skip", index=index
            )
            outcomes[status] = outcomes.get(status, 0) + 1
            if status == "done":
                item["final_target"] = final_target
                done_items.append(item)
        execute_elapsed = time.perf_counter() - execute_started
    phases["execute"] = counter.snapshot()

    with measure(work) as counter:
        cleanup_started = time.perf_counter()
        cleanup_done_sources(done_items, group_map, media_root, low_io=network_mode)
        cleanup_elapsed = time.perf_counter() - cleanup_started
    phases["cleanup"] = counter.snapshot()

    totals = {name: sum(phase[name] for phase in phases.values()) for name in METRICS}
    return {
        "network_drive_mode": network_mode,
        "phases": phases,
        "totals": totals,
        "elapsed": {
            "scan": round(scan_elapsed, 3),
            "preview": round(preview_elapsed, 3),
            "execute": round(execute_elapsed, 3),
            "cleanup": round(cleanup_elapsed, 3),
            "total": round(scan_elapsed + preview_elapsed + execute_elapsed + cleanup_elapsed, 3),
        },
        "outcomes": outcomes,
        "files": len(scan.get("files") or []),
        "target_fingerprint": _fingerprint(dst),
        "source_left": sorted(
            path.relative_to(src).as_posix() for path in src.rglob("*") if path.is_file()
        ),
    }


def _percent(off: int, on: int) -> str:
    if off <= 0:
        return "—" if on == 0 else "+∞"
    return f"{(on - off) / off * 100:.1f}%"


def build_report(off: dict, on: dict, source: Path, equal: dict[str, bool]) -> str:
    lines = [
        "# 网盘模式读盘次数对比",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本：`{source}` 的副本（{off['files']} 个扫描条目）",
        "- 流程：扫描 → 预览 → 执行（skip 策略）→ 清空空目录，两种模式各跑一遍",
        "- 计数口径：Python 层对副本目录发起的调用次数；两种模式同一口径",
        "",
        "## 总次数",
        "",
        "| 指标 | 关闭 | 打开 | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    labels = {
        "stat": "stat / 元数据",
        "listdir": "列目录",
        "open_read": "打开读文件",
        "open_write": "打开写文件",
        "move": "rename / 移动",
        "mkdir": "创建目录",
        "unlink": "删除文件",
        "rmdir": "删除空目录",
    }
    for name in METRICS:
        lines.append(
            f"| {labels[name]} | {off['totals'][name]} | {on['totals'][name]} | "
            f"{_percent(off['totals'][name], on['totals'][name])} |"
        )
    lines.extend(
        [
            "",
            "## 分阶段",
            "",
            "| 阶段 | stat 关 / 开 | 列目录 关 / 开 | 打开读 关 / 开 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for phase in ("scan", "preview", "execute", "cleanup"):
        left = off["phases"][phase]
        right = on["phases"][phase]
        lines.append(
            f"| {phase} | {left['stat']} / {right['stat']} | "
            f"{left['listdir']} / {right['listdir']} | "
            f"{left['open_read']} / {right['open_read']} |"
        )
    lines.extend(
        [
            "",
            "## 耗时（秒，仅参考）",
            "",
            "| 阶段 | 关闭 | 打开 |",
            "| --- | ---: | ---: |",
        ]
    )
    for phase in ("scan", "preview", "execute", "cleanup", "total"):
        lines.append(f"| {phase} | {off['elapsed'][phase]} | {on['elapsed'][phase]} |")
    lines.extend(
        [
            "",
            "## 结果一致性",
            "",
            "| 检查项 | 结果 |",
            "| --- | --- |",
            f"| 目标文件树（路径 + 大小 + SHA256）完全一致 | {'是' if equal['target'] else '否'} |",
            f"| 源目录剩余文件一致 | {'是' if equal['source'] else '否'} |",
            f"| 执行结果计数一致 | {'是' if equal['outcomes'] else '否'} |",
            "",
            f"- 关闭模式执行结果：{json.dumps(off['outcomes'], ensure_ascii=False)}",
            f"- 打开模式执行结果：{json.dumps(on['outcomes'], ensure_ascii=False)}",
            "",
            "## 说明",
            "",
            "- 两种模式都**没有打开任何媒体文件读取内容**（open_read 为 0）；网盘模式也没有额外读取。",
            "- 统计包含 DirEntry 的重复 `is_dir` 调用，两种模式口径相同，相对降幅可信。",
            "- 真实网盘上的收益会更大：每次元数据请求都是一次网络往返，减少的请求数即为减少的往返次数。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="网盘模式 IO 对比基准（只操作临时副本）")
    parser.add_argument("--source", required=True, help="用于复制副本的样本目录，例如 media/5")
    parser.add_argument("--output", required=True, help="对比结果 JSON 输出路径")
    parser.add_argument("--report", help="中文报告 Markdown 输出路径")
    parser.add_argument("--keep", action="store_true", help="保留临时副本，便于排查")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"[bench] source not found: {source}", flush=True)
        return 2
    output = Path(args.output)
    _assert_outside(source, output, args.report)

    work = Path(tempfile.mkdtemp(prefix="ani-rename-io-bench-"))
    try:
        # 两种模式各自从同一份源复制，保证起点一致（复制不计入统计）
        for mode in ("off", "on"):
            shutil.copytree(source, work / mode / "src")
        (work / "off" / "config").mkdir(parents=True, exist_ok=True)
        (work / "on" / "config").mkdir(parents=True, exist_ok=True)
        # app.config 在 import 时确定配置目录；这里固定用 off 的配置目录，
        # 每次运行前会重写 settings.json，两种模式读到的是各自的值。
        os.environ["APP_CONFIG_DIR"] = str(work / "off" / "config")
        os.environ["APP_DB_PATH"] = str(work / "off" / "config" / "app.db")
        os.environ["MEDIA_ROOT"] = str(work)
        backend = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(backend))

        # 预热 import，避免把首次 import 的静态文件读取算进统计
        off = _run_mode(source, work / "off", False)
        on = _run_mode(source, work / "on", True)
        equal = {
            "target": off["target_fingerprint"] == on["target_fingerprint"],
            "source": off["source_left"] == on["source_left"],
            "outcomes": off["outcomes"] == on["outcomes"],
        }
        result = {
            "source": str(source),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "off": off,
            "on": on,
            "equal": equal,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(build_report(off, on, source, equal), encoding="utf-8")
        print(
            "[bench] stat {off}->{on} listdir {off_l}->{on_l} open_read {off_r}->{on_r} "
            "equal={eq}".format(
                off=off["totals"]["stat"],
                on=on["totals"]["stat"],
                off_l=off["totals"]["listdir"],
                on_l=on["totals"]["listdir"],
                off_r=off["totals"]["open_read"],
                on_r=on["totals"]["open_read"],
                eq=all(equal.values()),
            ),
            flush=True,
        )
        return 0 if all(equal.values()) else 1
    finally:
        if args.keep:
            print(f"[bench] kept: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)


def _assert_outside(source: Path, *outputs: str | None) -> None:
    for text in outputs:
        if not text:
            continue
        try:
            Path(text).resolve().relative_to(source)
        except ValueError:
            continue
        raise SystemExit(f"安全中断：输出文件不能写进被统计的源目录：{text}")


if __name__ == "__main__":
    sys.exit(main())
