"""文件夹 1～4 的扫描/预览快照对比工具（只读，不改名、不移动）。

用法（容器内）：
    python tools/compare_scan.py --save /tmp/base --folders 1,2,3,4
    python tools/compare_scan.py --compare /tmp/base --folders 1,2,3,4
    python tools/compare_scan.py --compare /tmp/base --folders 1,2,3,4 --patterns

`--patterns` 会额外给每个视频挂一条命名规律再扫一次，用来证明：
即使算出了 AI 结果、记录了冲突，现有的预览、识别字段、重复集、季号计划也完全不变。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置临时配置，避免碰到用户正在用的配置和数据库。
_TEMP = Path(tempfile.mkdtemp(prefix="compare-scan-"))
os.environ["APP_CONFIG_DIR"] = str(_TEMP / "config")
os.environ["APP_DB_PATH"] = str(_TEMP / "config" / "app.db")
os.environ.setdefault("MEDIA_ROOT", "/media")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_media_root, load_settings  # noqa: E402
from app.core.duplicates import describe_groups  # noqa: E402
from app.core.identify import apply_identification  # noqa: E402
from app.core.pattern import fingerprint  # noqa: E402
from app.core.preview import build_preview, load_preset  # noqa: E402
from app.core.queue import init_queue, overview  # noqa: E402
from app.core.scanner import scan_groups  # noqa: E402
from app.core.season_plan import build_season_plan  # noqa: E402
from app.store import init_db, list_rules  # noqa: E402

GENERIC_PATTERN = {
    "rules": [{"role": "episode", "container": "无", "match": "纯数字", "take": "最后一个"}]
}


def _patterns_for(scan: dict) -> list[dict]:
    items: dict[str, dict] = {}
    for row in scan.get("files") or []:
        if row.get("category") != "video":
            continue
        rel = str(row.get("rel_path") or "").replace("\\", "/")
        parts = [part for part in rel.split("/") if part]
        code = fingerprint(str(row.get("group_id") or ""), "/".join(parts[:-1]), str(row.get("filename") or ""))
        if code in items:
            continue
        items[code] = {
            "id": f"test-{len(items) + 1}",
            "fingerprint": code,
            "source": "manual",
            "structure": GENERIC_PATTERN,
        }
    return list(items.values())


def _scan(folders: list[str], patterns: list[dict] | None = None) -> dict:
    media_root = get_media_root()
    settings = load_settings()
    rules = list_rules()
    groups = [
        {
            "id": f"folder-{name}",
            "name": f"文件夹{name}",
            "source": str(media_root / name),
            "target": str(media_root / name),
        }
        for name in folders
        if (media_root / name).is_dir()
    ]
    if not groups:
        raise SystemExit(f"媒体根目录下找不到这些文件夹：{', '.join(folders)}（{media_root}）")
    scan = scan_groups(groups, media_root, settings, rules, patterns=patterns)
    apply_identification(scan, [], settings, [])
    init_queue(scan)
    return scan


def _snapshot(scan: dict) -> dict:
    preset = load_preset(None)
    settings = load_settings()
    preview = build_preview(scan, preset, settings) if preset else {"rows": [], "summary": {}}
    files = {str(row.get("id")): row for row in scan.get("files") or []}
    rows = []
    for item in preview.get("rows") or []:
        source = files.get(str(item.get("file_id"))) or {}
        identify = source.get("identify") if isinstance(source.get("identify"), dict) else {}
        new_path = str(item.get("new_path") or "")
        rows.append(
            {
                "key": str(item.get("original_path") or ""),
                "new_path": new_path,
                "new_name": Path(new_path).name if new_path else "",
                "category": item.get("category"),
                "action": item.get("action"),
                "status": item.get("status"),
                "messages": item.get("messages") or [],
                "identify": identify,
                "kind": source.get("kind"),
                "season": source.get("season"),
                "episode": source.get("episode"),
                "episode_exact": source.get("episode_exact", ""),
                "special_type": source.get("special_type"),
                "special_no": source.get("special_no"),
                "collection": bool(source.get("collection")),
                "warnings": source.get("warnings") or [],
            }
        )
    rows.sort(key=lambda item: item["key"])
    return {
        "files": rows,
        "preview_summary": preview.get("summary") or {},
        "stats": scan.get("stats") or {},
        "queue": overview(scan),
        "duplicates": describe_groups(
            scan.get("files") or [], scan.get("duplicate_decisions") or {}
        ),
        "season_plan": build_season_plan(scan),
    }


def build_all(folders: list[str], patterns: list[dict] | None = None) -> dict:
    init_db()
    result = {}
    for name in folders:
        scan = _scan([name], patterns=patterns)
        result[name] = _snapshot(scan)
    return result


def _diff(old, new, path: str, out: list[str]) -> None:
    if type(old) is not type(new):
        out.append(f"{path}: 类型不同 {type(old).__name__} -> {type(new).__name__}")
        return
    if isinstance(old, dict):
        for key in sorted(set(old) | set(new)):
            if key not in old:
                out.append(f"{path}.{key}: 新增")
            elif key not in new:
                out.append(f"{path}.{key}: 消失")
            else:
                _diff(old[key], new[key], f"{path}.{key}", out)
        return
    if isinstance(old, list):
        if len(old) != len(new):
            out.append(f"{path}: 条数 {len(old)} -> {len(new)}")
        for index, (left, right) in enumerate(zip(old, new)):
            _diff(left, right, f"{path}[{index}]", out)
        return
    if old != new:
        out.append(f"{path}: {old!r} -> {new!r}")


def compare(base: dict, current: dict) -> int:
    total = 0
    for name in base:
        if name not in current:
            print(f"文件夹 {name}: 缺少对比结果")
            total += 1
            continue
        lines: list[str] = []
        _diff(base[name], current[name], name, lines)
        print(f"文件夹 {name}: {len(base[name]['files'])} 行，差异 {len(lines)} 处")
        for line in lines[:12]:
            print(f"  - {line}")
        if len(lines) > 12:
            print(f"  ... 还有 {len(lines) - 12} 处")
        total += len(lines)
    print(f"合计差异：{total} 处")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folders", default="1,2,3,4")
    parser.add_argument("--save", default="")
    parser.add_argument("--compare", default="")
    parser.add_argument("--patterns", action="store_true")
    args = parser.parse_args()
    if not args.save and not args.compare:
        parser.error("请指定 --save 或 --compare")
    folders = [item.strip() for item in args.folders.split(",") if item.strip()]

    if args.save:
        data = build_all(folders)
        target = Path(args.save)
        target.mkdir(parents=True, exist_ok=True)
        (target / "snapshot.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )
        for name in folders:
            print(f"文件夹 {name}: 已保存 {len(data[name]['files'])} 行")
        print(f"快照目录：{target}")
        return 0

    base_path = Path(args.compare) / "snapshot.json"
    if not base_path.exists():
        raise SystemExit(f"找不到基线快照：{base_path}")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    current = build_all(folders)
    print("== 与基线对比（不传命名规律） ==")
    total = compare(base, current)
    if args.patterns:
        seeded = build_all(folders)
        prepared = []
        for name in folders:
            prepared.extend(_patterns_for(_scan([name])))
        with_patterns = build_all(folders, patterns=prepared)
        print("== 传入命名规律后再对比（只应新增 pattern_* 字段，现有输出不变） ==")
        total += compare(seeded, with_patterns)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
