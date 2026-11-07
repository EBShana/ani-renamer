"""扫描结果会话存储与内置样例数据。"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .identify import apply_identification
from .parser import parse_file
from .rules import Rule

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()
SAMPLE_ROOT = Path("/media")

SAMPLE_FILES = [
    "/media/葬送的芙莉莲/Season 1/[Lilith-Raws] Sousou no Frieren - 01 [Baha][WEB-DL][1080p][AVC AAC][CHT].mp4",
    "/media/葬送的芙莉莲/Season 1/[Lilith-Raws] Sousou no Frieren - 02 [Baha][WEB-DL][1080p][AVC AAC][CHT].mp4",
    "/media/葬送的芙莉莲/特典/[Lilith-Raws] Sousou no Frieren - NCOP01 [1080p].mkv",
    "/media/孤独摇滚/S01/[Nekomoe kissaten][Bocchi the Rock!][01][1080p][CHS].mkv",
    "/media/孤独摇滚/S01/[Nekomoe kissaten][Bocchi the Rock!][02][1080p][CHS].mkv",
    "/media/灵能百分百/2nd Season/[VCB-Studio] Mob Psycho 100 [01][Ma10p_1080p][x265_flac].mkv",
    "/media/铃芽之旅/铃芽之旅 (2022) [BDRip 1080p x265].mkv",
]


def put_scan(payload: dict) -> str:
    scan_id = f"scan-{uuid.uuid4().hex[:12]}"
    payload["scan_id"] = scan_id
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        _SESSIONS[scan_id] = payload
    return scan_id


def get_scan(scan_id: str) -> dict | None:
    with _LOCK:
        return _SESSIONS.get(scan_id)


def apply_field_updates(scan_id: str, updates: dict[str, dict]) -> int:
    """把匹配结果写回扫描缓存，供后续预览与重命名使用。"""
    with _LOCK:
        payload = _SESSIONS.get(scan_id)
        if not payload:
            return 0
        count = 0
        for row in payload.get("files", []):
            patch = updates.get(str(row.get("id")))
            if not patch:
                continue
            row.setdefault("fields", {}).update(patch)
            count += 1
        return count


def build_samples(settings: dict, rules: list[Rule], assignments: list[dict] | None = None) -> dict:
    files: list[dict] = []
    for index, raw in enumerate(SAMPLE_FILES):
        path = Path(raw)
        try:
            row = parse_file(SAMPLE_ROOT, path, settings, rules)
        except (OSError, ValueError):
            continue
        row["id"] = f"s{index:04d}"
        row["group_id"] = "sample"
        row["size"] = 0
        row["path"] = raw
        row["parent"] = PurePosixPath(raw).parent.as_posix()
        files.append(row)
    stats = {
        "total": len(files),
        "video": 0,
        "subtitle": 0,
        "audio": 0,
        "image": 0,
        "archive": 0,
        "other": 0,
        "episode": 0,
        "special": 0,
        "movie": 0,
        "unknown": 0,
    }
    for row in files:
        category = str(row.get("category") or "other")
        if category in stats:
            stats[category] += 1
        if row.get("category") == "video":
            stats[row["kind"] if row["kind"] in stats else "unknown"] += 1
    return {
        "scan_id": "sample",
        "root": str(SAMPLE_ROOT),
        "groups": [
            {"id": "sample", "name": "内置示例", "source": str(SAMPLE_ROOT), "target": str(SAMPLE_ROOT)}
        ],
        "sources": [],
        "files": apply_identification(
            {"files": files, "groups": []}, assignments or [], settings
        )["files"],
        "stats": stats,
        "truncated": False,
        "is_sample": True,
    }
