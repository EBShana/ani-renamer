"""配置存储：预设与识别规则放在 /config 下的 JSON 文件，执行历史放在 SQLite。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DB_PATH,
    default_preset_options,
    get_media_root,
    load_settings,
    save_settings,
    setting_values_from_preset_options,
    settings_to_preset_options,
)
from .core.groups import normalize_group, validate_groups
from .core.rules import BUILTIN_RULES, Rule

PRESETS_VERSION = 3
GROUPS_VERSION = 1
ASSIGNMENTS_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    scan_id TEXT,
    preset_id TEXT,
    preset_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'execute',
    dry_run INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'done',
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    undone_at TEXT,
    total_items INTEGER NOT NULL DEFAULT 0,
    processed_items INTEGER NOT NULL DEFAULT 0,
    current_item TEXT NOT NULL DEFAULT '',
    batch_index INTEGER NOT NULL DEFAULT 0,
    batch_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS operation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    group_id TEXT,
    file_id TEXT,
    category TEXT,
    original_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);
"""

PRESETS_FILE = CONFIG_DIR / "presets.json"
RULES_FILE = CONFIG_DIR / "rules.json"
GROUPS_FILE = CONFIG_DIR / "groups.json"
ASSIGNMENTS_FILE = CONFIG_DIR / "assignments.json"

_LOCK = threading.RLock()

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "episode": {"pattern": "{series} - S{season:02}E{episode:02}", "output_mode": "filename"},
    "special": {"pattern": "{series} - S{season:02}E{episode:02}", "output_mode": "filename"},
    "movie": {"pattern": "{series} ({year})", "output_mode": "filename"},
}

# 旧版内置模板 -> v3（统一 SxxExx）。只替换完全一致的旧内置写法，用户自定义模板保持不变。
LEGACY_PATTERN_V3: dict[str, str] = {
    "{series} - {episode:02}": "{series} - S{season:02}E{episode:02}",
    "[{group}] {series} - {episode:02} [{resolution}][{codec}]": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}][{codec}]",
    "{series} - {special_type}{special_no:02}": "{series} - S{season:02}E{episode:02}",
    "[{group}] {series} - {special_type}{special_no:02} [{resolution}]": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}]",
}

SEED_PRESETS: list[dict[str, Any]] = [
    {
        "id": "preset-simple",
        "name": "简洁命名",
        "is_default": True,
        "library_profile": "generic",
        "templates": DEFAULT_TEMPLATES,
    },
    {
        "id": "preset-technical",
        "name": "保留技术信息",
        "is_default": False,
        "library_profile": "generic",
        "templates": {
            "episode": {
                "pattern": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}][{codec}]",
                "output_mode": "filename",
            },
            "special": {
                "pattern": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}]",
                "output_mode": "filename",
            },
            "movie": {
                "pattern": "[{group}] {series} ({year}) [{resolution}][{codec}]",
                "output_mode": "filename",
            },
        },
    },
    {
        "id": "preset-jellyfin",
        "name": "Jellyfin 媒体库目录",
        "is_default": False,
        "library_profile": "jellyfin",
        "templates": {
            "episode": {
                "pattern": "{series} - S{season:02}E{episode:02}",
                "output_mode": "filename",
            },
            "special": {
                "pattern": "{series} - S{season:02}E{episode:02}",
                "output_mode": "filename",
            },
            "movie": {
                "pattern": "{series} ({year})",
                "output_mode": "filename",
            },
        },
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    """先写临时文件再替换，避免写一半断电把配置写坏。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _repair_text(value: str) -> str:
    """修复历史版本里被错误二次编码的中文（如“特典”变成 Ã§...）。"""
    if not value or not any("\u00c0" <= ch <= "\u00ff" for ch in value):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    result = default_preset_options()
    if isinstance(options, dict):
        for key, default in result.items():
            if key not in options:
                continue
            value = options[key]
            if isinstance(default, bool):
                result[key] = bool(value)
            elif isinstance(default, int):
                try:
                    result[key] = int(value)
                except (TypeError, ValueError):
                    pass
            else:
                result[key] = _repair_text(str(value)[:2000])
    return result


def _split_legacy_structure(pattern: str) -> tuple[str, str]:
    """把旧版“完整路径模板”拆成（目录部分, 文件名部分）。

    v2 起界面不再编辑目录结构，但第 2 步才会改成自动生成目录；
    在此之前把旧目录部分保存在 legacy_dir 里继续沿用，避免行为突变。
    """
    parts = [part for part in pattern.replace("\\", "/").split("/") if part.strip()]
    if len(parts) <= 1:
        return "", (parts[0] if parts else pattern).strip()
    return "/".join(parts[:-1]), parts[-1]


def _normalize_templates(templates: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """v3：目录结构改为按规范自动生成，预设只保留文件名规则。

    旧预设里的 legacy_dir（目录部分）在升级时丢弃；旧内置模板的写法会替换为 SxxExx。
    """
    result: dict[str, dict[str, str]] = {}
    for kind in ("episode", "special", "movie"):
        value = templates.get(kind) if isinstance(templates, dict) else None
        base = DEFAULT_TEMPLATES[kind]
        if isinstance(value, dict):
            pattern = str(value.get("pattern", base["pattern"]))[:2000]
            legacy_dir = str(value.get("legacy_dir") or "")
            mode = "path" if value.get("output_mode") == "path" else "filename"
            if not legacy_dir and mode == "path":
                legacy_dir, pattern = _split_legacy_structure(pattern)
            pattern = LEGACY_PATTERN_V3.get(pattern.strip(), pattern.strip())
            result[kind] = {"pattern": pattern or base["pattern"], "output_mode": "filename"}
        else:
            result[kind] = dict(base)
    return result


def _normalize_preset(item: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
    now = _now()
    return {
        "id": str(item.get("id") or fallback_id or f"preset-{uuid.uuid4().hex[:10]}"),
        "name": _repair_text(str(item.get("name") or "未命名预设"))[:120],
        "is_default": bool(item.get("is_default")),
        "library_profile": str(item.get("library_profile") or "generic")[:40],
        "templates": _normalize_templates(item.get("templates")),
        "options": _normalize_options(item.get("options")),
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _legacy_rows(table: str) -> list[dict[str, Any]]:
    """读取旧版本数据库里的配置，用于一次性迁移到 JSON 文件。"""
    with _connect() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _ensure_presets() -> dict[str, Any]:
    """确保 presets.json 存在；没有就从旧数据库或内置模板生成。"""
    with _LOCK:
        doc = _read_json(PRESETS_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("presets"), list) and doc["presets"]:
            presets = [
                _normalize_preset(item, item.get("id"))
                for item in doc["presets"]
                if isinstance(item, dict)
            ]
            if not any(item["is_default"] for item in presets):
                presets[0]["is_default"] = True
            default = next((item["id"] for item in presets if item["is_default"]), presets[0]["id"])
            normalized = {"version": PRESETS_VERSION, "default_id": default, "presets": presets}
            if doc.get("version") != PRESETS_VERSION:
                # 旧版预设可能是“完整路径模板”，规范化时会拆成目录 + 文件名两部分
                _write_json(PRESETS_FILE, normalized)
            return normalized

        legacy = _legacy_rows("presets")
        if legacy:
            presets = []
            for row in legacy:
                try:
                    templates = json.loads(row.get("templates") or "{}")
                except (TypeError, ValueError):
                    templates = {}
                try:
                    options = json.loads(row.get("options") or "{}")
                except (TypeError, ValueError):
                    options = {}
                presets.append(
                    _normalize_preset(
                        {
                            "id": row.get("id"),
                            "name": row.get("name"),
                            "is_default": bool(row.get("is_default")),
                            "library_profile": row.get("library_profile"),
                            "templates": templates,
                            "options": options,
                            "created_at": row.get("created_at"),
                            "updated_at": row.get("updated_at"),
                        }
                    )
                )
        else:
            presets = [
                _normalize_preset({**item, "options": default_preset_options()})
                for item in SEED_PRESETS
            ]

        if not any(item["is_default"] for item in presets):
            presets[0]["is_default"] = True
        doc = {
            "version": PRESETS_VERSION,
            "default_id": next(item["id"] for item in presets if item["is_default"]),
            "presets": presets,
        }
        _write_json(PRESETS_FILE, doc)
        return doc


def _ensure_rules() -> list[dict[str, Any]]:
    """确保 rules.json 存在；没有就从旧数据库或内置规则生成。"""
    with _LOCK:
        doc = _read_json(RULES_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("rules"), list) and doc["rules"]:
            return [dict(item) for item in doc["rules"] if isinstance(item, dict)]

        legacy = _legacy_rows("rules")
        if legacy:
            rules = [
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "scope": row.get("scope"),
                    "kind": row.get("kind"),
                    "priority": int(row.get("priority") or 0),
                    "regex": row.get("regex"),
                    "enabled": bool(row.get("enabled")),
                    "description": row.get("description") or "",
                    "sample": row.get("sample") or "",
                }
                for row in legacy
            ]
        else:
            rules = [
                {
                    "id": rule.id,
                    "name": rule.name,
                    "scope": rule.scope,
                    "kind": rule.kind,
                    "priority": rule.priority,
                    "regex": rule.regex,
                    "enabled": bool(rule.enabled),
                    "description": rule.description,
                    "sample": rule.sample,
                }
                for rule in BUILTIN_RULES
            ]
        _write_json(RULES_FILE, {"version": 1, "rules": rules})
        return rules


def _ensure_groups() -> dict[str, Any]:
    """确保 groups.json 存在。目录组由用户自己添加，避免默认根目录把子目录组挡住。"""
    with _LOCK:
        doc = _read_json(GROUPS_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("groups"), list):
            groups = [normalize_group(item) for item in doc["groups"] if isinstance(item, dict)]
            normalized = {"version": GROUPS_VERSION, "groups": groups}
            if doc.get("version") != GROUPS_VERSION:
                _write_json(GROUPS_FILE, normalized)
            return normalized

        doc = {"version": GROUPS_VERSION, "groups": []}
        _write_json(GROUPS_FILE, doc)
        return doc


def list_groups() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_groups()["groups"]]


def get_group(group_id: str) -> dict[str, Any] | None:
    return next((item for item in list_groups() if item["id"] == group_id), None)


def validate_group_payload(payload: dict[str, Any], ignore_id: str | None = None) -> list[str]:
    """校验一个“待保存”的目录组：把它放进现有组列表里整体检查边界。"""
    candidate = normalize_group({**payload, "id": payload.get("id") or ignore_id or "group-new"})
    groups = [item for item in list_groups() if item["id"] != ignore_id]
    groups.append(candidate)
    return validate_groups(groups, get_media_root())


def create_group(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_groups()
        group = normalize_group({**payload, "created_at": _now(), "updated_at": _now()})
        doc["groups"].append(group)
        _write_json(GROUPS_FILE, doc)
    return group


def update_group(group_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_groups()
        target = next((item for item in doc["groups"] if item["id"] == group_id), None)
        if target is None:
            return None
        updated = normalize_group(
            {
                "id": group_id,
                "name": payload.get("name", target.get("name")),
                "source": payload.get("source", target.get("source")),
                "target": payload.get("target", target.get("target")),
                "created_at": target.get("created_at"),
                "updated_at": _now(),
            }
        )
        doc["groups"][doc["groups"].index(target)] = updated
        _write_json(GROUPS_FILE, doc)
    return updated


def delete_group(group_id: str) -> bool:
    with _LOCK:
        doc = _ensure_groups()
        target = next((item for item in doc["groups"] if item["id"] == group_id), None)
        if target is None:
            return False
        doc["groups"].remove(target)
        _write_json(GROUPS_FILE, doc)
    return True


def _normalize_assignment(item: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    year = item.get("year")
    try:
        year_value = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_value = None
    return {
        "id": str(item.get("id") or f"as-{uuid.uuid4().hex[:10]}"),
        "group_id": str(item.get("group_id") or ""),
        "segment": _repair_text(str(item.get("segment") or ""))[:300],
        "title": _repair_text(str(item.get("title") or ""))[:300],
        "year": year_value,
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _ensure_assignments() -> dict[str, Any]:
    """确保 assignments.json 存在；保存“目录组 + 名称段 → 作品名”的记忆映射。"""
    with _LOCK:
        doc = _read_json(ASSIGNMENTS_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("assignments"), list):
            assignments = [
                _normalize_assignment(item)
                for item in doc["assignments"]
                if isinstance(item, dict)
            ]
            normalized = {"version": ASSIGNMENTS_VERSION, "assignments": assignments}
            if doc.get("version") != ASSIGNMENTS_VERSION:
                _write_json(ASSIGNMENTS_FILE, normalized)
            return normalized

        doc = {"version": ASSIGNMENTS_VERSION, "assignments": []}
        _write_json(ASSIGNMENTS_FILE, doc)
        return doc


def list_assignments() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_assignments()["assignments"]]


def create_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_assignments()
        assignment = _normalize_assignment(payload)
        doc["assignments"].append(assignment)
        _write_json(ASSIGNMENTS_FILE, doc)
    return assignment


def update_assignment(assignment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_assignments()
        target = next((item for item in doc["assignments"] if item["id"] == assignment_id), None)
        if target is None:
            return None
        updated = _normalize_assignment(
            {
                "id": assignment_id,
                "group_id": payload.get("group_id", target.get("group_id")),
                "segment": payload.get("segment", target.get("segment")),
                "title": payload.get("title", target.get("title")),
                "year": payload.get("year", target.get("year")),
                "created_at": target.get("created_at"),
                "updated_at": _now(),
            }
        )
        doc["assignments"][doc["assignments"].index(target)] = updated
        _write_json(ASSIGNMENTS_FILE, doc)
    return updated


def delete_assignment(assignment_id: str) -> bool:
    with _LOCK:
        doc = _ensure_assignments()
        target = next((item for item in doc["assignments"] if item["id"] == assignment_id), None)
        if target is None:
            return False
        doc["assignments"].remove(target)
        _write_json(ASSIGNMENTS_FILE, doc)
    return True


def _migrate_keep_empty_dirs() -> None:
    """旧版本默认保留空目录，改为默认清理，避免源文件夹残留。"""
    with _connect() as conn:
        migrated = conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("keep_empty_dirs_off_v1",)
        ).fetchone()
        if migrated is not None:
            return
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("keep_empty_dirs_off_v1", _now()),
        )
        conn.commit()

    doc = _ensure_presets()
    for preset in doc["presets"]:
        preset["options"]["keep_empty_dirs"] = False
    with _LOCK:
        _write_json(PRESETS_FILE, doc)


def _migrate_operation_items_v2() -> None:
    """老数据库的 operation_items 没有 group_id 列，补上（失败说明已存在）。"""
    with _connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(operation_items)").fetchall()
        }
        if "group_id" not in columns:
            conn.execute("ALTER TABLE operation_items ADD COLUMN group_id TEXT")
            conn.commit()


def _migrate_operations_v4() -> None:
    """第 4 步：执行记录增加进度字段（总数 / 已处理 / 当前文件 / 批次）。"""
    additions = {
        "total_items": "INTEGER NOT NULL DEFAULT 0",
        "processed_items": "INTEGER NOT NULL DEFAULT 0",
        "current_item": "TEXT NOT NULL DEFAULT ''",
        "batch_index": "INTEGER NOT NULL DEFAULT 0",
        "batch_count": "INTEGER NOT NULL DEFAULT 0",
    }
    with _connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(operations)").fetchall()}
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE operations ADD COLUMN {name} {ddl}")
        conn.commit()


def _migrate_settings_v2(had_presets: bool) -> None:
    """v2 迁移：设置页成为唯一权威。

    旧版本里预设选项会覆盖设置页，因此把“默认预设当时实际生效的选项”
    搬进 settings.json，保证升级后行为不变；之后运行期只读设置。
    如果旧配置里根本没有预设文件，就只把当前设置原样升到 v2，避免误伤手动设置。
    """
    raw = _read_json(CONFIG_FILE)
    version = 0
    if isinstance(raw, dict):
        try:
            version = int(raw.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
    if version >= 2:
        return

    values = load_settings()
    if had_presets:
        doc = _ensure_presets()
        preset = next((item for item in doc["presets"] if item["is_default"]), doc["presets"][0])
        values.update(setting_values_from_preset_options(preset.get("options") or {}))
    save_settings(values)


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    _migrate_operation_items_v2()
    _migrate_operations_v4()
    # 旧版本可能把预设存在 SQLite 里，迁移前先确认有没有可用的预设来源
    had_presets = PRESETS_FILE.exists() or bool(_legacy_rows("presets"))
    _ensure_presets()
    _ensure_rules()
    _ensure_groups()
    _ensure_assignments()
    _migrate_keep_empty_dirs()
    _migrate_settings_v2(had_presets)


def list_presets() -> list[dict[str, Any]]:
    doc = _ensure_presets()
    presets = [_normalize_preset(item, item.get("id")) for item in doc["presets"]]
    presets.sort(key=lambda item: (0 if item["is_default"] else 1, item["name"]))
    return presets


def get_preset(preset_id: str) -> dict[str, Any] | None:
    return next((item for item in list_presets() if item["id"] == preset_id), None)


def get_default_preset() -> dict[str, Any] | None:
    presets = list_presets()
    return presets[0] if presets else None


def _preset_options_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """保存预设时的选项来源：显式传入就沿用，否则记录当前设置快照。"""
    options = payload.get("options")
    if isinstance(options, dict) and options:
        return options
    return settings_to_preset_options(load_settings())


def create_preset(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_presets()
        preset = _normalize_preset(
            {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "is_default": False,
                "library_profile": payload.get("library_profile"),
                "templates": payload.get("templates"),
                "options": _preset_options_from_payload(payload),
            }
        )
        doc["presets"].append(preset)
        _write_json(PRESETS_FILE, doc)
    return get_preset(preset["id"])  # type: ignore[return-value]


def update_preset(preset_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next((item for item in doc["presets"] if item["id"] == preset_id), None)
        if target is None:
            return None
        updated = _normalize_preset(
            {
                "id": preset_id,
                "name": payload.get("name", target["name"]),
                "is_default": target["is_default"],
                "library_profile": payload.get("library_profile", target["library_profile"]),
                "templates": payload.get("templates", target["templates"]),
                "options": _preset_options_from_payload(payload),
                "created_at": target["created_at"],
                "updated_at": _now(),
            }
        )
        doc["presets"][doc["presets"].index(target)] = updated
        _write_json(PRESETS_FILE, doc)
    return get_preset(preset_id)


def delete_preset(preset_id: str) -> bool:
    with _LOCK:
        doc = _ensure_presets()
        target = next((item for item in doc["presets"] if item["id"] == preset_id), None)
        if target is None:
            return False
        doc["presets"].remove(target)
        if doc["presets"] and not any(item["is_default"] for item in doc["presets"]):
            doc["presets"][0]["is_default"] = True
        doc["default_id"] = next(
            (item["id"] for item in doc["presets"] if item["is_default"]), ""
        )
        _write_json(PRESETS_FILE, doc)
    return True


def set_default_preset(preset_id: str) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next((item for item in doc["presets"] if item["id"] == preset_id), None)
        if target is None:
            return None
        for item in doc["presets"]:
            item["is_default"] = item["id"] == preset_id
        doc["default_id"] = preset_id
        _write_json(PRESETS_FILE, doc)
    return get_preset(preset_id)


def duplicate_preset(preset_id: str) -> dict[str, Any] | None:
    source = get_preset(preset_id)
    if not source:
        return None
    return create_preset(
        {
            "name": f"{source['name']} 副本",
            "library_profile": source["library_profile"],
            "templates": source["templates"],
            "options": source.get("options"),
        }
    )


def export_presets() -> dict[str, Any]:
    doc = _ensure_presets()
    presets = []
    for item in doc["presets"]:
        preset = _normalize_preset(item, item.get("id"))
        preset.pop("created_at", None)
        preset.pop("updated_at", None)
        preset["is_default"] = False
        presets.append(preset)
    return {"version": 1, "presets": presets, "default_id": doc.get("default_id")}


def import_presets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for item in payload.get("presets", []):
        if not isinstance(item, dict):
            continue
        created.append(
            create_preset(
                {
                    "name": str(item.get("name", "导入预设"))[:120],
                    "library_profile": item.get("library_profile", "generic"),
                    "templates": item.get("templates") or {},
                    "options": item.get("options") or {},
                }
            )
        )
    return created


def list_rules() -> list[Rule]:
    rules = _ensure_rules()
    ordered = sorted(rules, key=lambda item: (str(item.get("scope")), int(item.get("priority") or 0), str(item.get("id"))))
    return [
        Rule(
            id=str(item.get("id")),
            name=str(item.get("name") or ""),
            scope=str(item.get("scope") or ""),
            kind=str(item.get("kind") or ""),
            priority=int(item.get("priority") or 0),
            regex=str(item.get("regex") or ""),
            enabled=bool(item.get("enabled")),
            description=str(item.get("description") or ""),
            sample=str(item.get("sample") or ""),
        )
        for item in ordered
    ]


def _operation_from_row(row: sqlite3.Row, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        summary = json.loads(row["summary"] or "{}")
    except (TypeError, ValueError):
        summary = {}
    data: dict[str, Any] = {
        "id": row["id"],
        "scan_id": row["scan_id"],
        "preset_id": row["preset_id"],
        "preset_name": row["preset_name"],
        "kind": row["kind"],
        "dry_run": bool(row["dry_run"]),
        "status": row["status"],
        "summary": summary,
        "created_at": row["created_at"],
        "undone_at": row["undone_at"],
        "total_items": row["total_items"] if "total_items" in row.keys() else 0,
        "processed_items": row["processed_items"] if "processed_items" in row.keys() else 0,
        "current_item": row["current_item"] if "current_item" in row.keys() else "",
        "batch_index": row["batch_index"] if "batch_index" in row.keys() else 0,
        "batch_count": row["batch_count"] if "batch_count" in row.keys() else 0,
    }
    if items is not None:
        data["items"] = items
    return data


def _operation_items(conn: sqlite3.Connection, operation_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM operation_items WHERE operation_id = ? ORDER BY seq",
        (operation_id,),
    ).fetchall()
    return [
        {
            "seq": row["seq"],
            "group_id": row["group_id"],
            "file_id": row["file_id"],
            "category": row["category"],
            "original_path": row["original_path"],
            "target_path": row["target_path"],
            "status": row["status"],
            "message": row["message"],
        }
        for row in rows
    ]


def save_operation(
    scan_id: str | None,
    preset_id: str | None,
    preset_name: str,
    kind: str,
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """记录一次执行 / 撤销，返回带明细的完整记录。"""
    operation_id = f"op-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO operations (id, scan_id, preset_id, preset_name, kind, dry_run, status, summary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'done', ?, ?)",
            (
                operation_id,
                scan_id,
                preset_id,
                preset_name,
                kind,
                1 if dry_run else 0,
                json.dumps(summary, ensure_ascii=False),
                now,
            ),
        )
        for seq, item in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO operation_items (operation_id, seq, group_id, file_id, category, original_path, target_path, status, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    seq,
                    item.get("group_id"),
                    item.get("file_id"),
                    item.get("category"),
                    item.get("original_path", ""),
                    item.get("target_path", ""),
                    item.get("status", "skipped"),
                    item.get("message", ""),
                ),
            )
        conn.commit()
    return get_operation(operation_id)  # type: ignore[return-value]


def create_pending_operation(
    scan_id: str | None,
    preset_id: str | None,
    preset_name: str,
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    status: str = "queued",
    batch_count: int = 0,
) -> str:
    """执行开始前先落库：整份执行计划和每一条目都写入，之后边执行边更新。"""
    operation_id = f"op-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO operations (id, scan_id, preset_id, preset_name, kind, dry_run, status, summary, created_at, total_items, processed_items, current_item, batch_index, batch_count) "
            "VALUES (?, ?, ?, ?, 'execute', 0, ?, ?, ?, ?, 0, '', 0, ?)",
            (
                operation_id,
                scan_id,
                preset_id,
                preset_name,
                status,
                json.dumps(summary, ensure_ascii=False),
                now,
                len(items),
                batch_count,
            ),
        )
        for seq, item in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO operation_items (operation_id, seq, group_id, file_id, category, original_path, target_path, status, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    seq,
                    item.get("group_id"),
                    item.get("file_id"),
                    item.get("category"),
                    item.get("original_path", ""),
                    item.get("target_path", ""),
                    item.get("status", "planned"),
                    item.get("message", ""),
                ),
            )
        conn.commit()
    return operation_id


def update_operation_item(
    operation_id: str,
    seq: int,
    status: str,
    target_path: str,
    message: str = "",
) -> None:
    """执行过程中逐行落库，保证中断后还能续跑。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE operation_items SET status = ?, target_path = ?, message = ? "
            "WHERE operation_id = ? AND seq = ?",
            (status, target_path, message, operation_id, seq),
        )
        conn.commit()


def finalize_operation(operation_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
    with _connect() as conn:
        if summary is None:
            conn.execute("UPDATE operations SET status = ? WHERE id = ?", (status, operation_id))
        else:
            conn.execute(
                "UPDATE operations SET status = ?, summary = ? WHERE id = ?",
                (status, json.dumps(summary, ensure_ascii=False), operation_id),
            )
        conn.commit()


def get_operation(operation_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        items = _operation_items(conn, operation_id)
    return _operation_from_row(row, items)


FINAL_ITEM_STATUS = ("done", "skipped", "failed", "conflict")


def operation_item_counts(operation_id: str) -> dict[str, int]:
    """统计各状态条目数量（进度条使用，不返回明细）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM operation_items WHERE operation_id = ? GROUP BY status",
            (operation_id,),
        ).fetchall()
    counts = {"done": 0, "skipped": 0, "failed": 0, "conflict": 0, "planned": 0}
    for row in rows:
        counts[str(row["status"])] = int(row["total"])
    counts["total"] = sum(counts.values())
    counts["processed"] = sum(counts[status] for status in FINAL_ITEM_STATUS)
    return counts


def get_operation_progress(operation_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
    if row is None:
        return None
    counts = operation_item_counts(operation_id)
    return {
        "operation_id": operation_id,
        "status": row["status"],
        "total": counts["total"],
        "processed": counts["processed"],
        "done": counts["done"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "conflict": counts["conflict"],
        "planned": counts["planned"],
        "current_item": row["current_item"],
        "batch_index": row["batch_index"],
        "batch_count": row["batch_count"],
        "summary": json.loads(row["summary"] or "{}"),
    }


def set_operation_status(operation_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE operations SET status = ? WHERE id = ?", (status, operation_id))
        conn.commit()


def update_operation_progress(
    operation_id: str,
    processed: int,
    current_item: str,
    batch_index: int,
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE operations SET processed_items = ?, current_item = ?, batch_index = ? WHERE id = ?",
            (processed, current_item, batch_index, operation_id),
        )
        conn.commit()


def set_operation_batches(operation_id: str, batch_count: int) -> None:
    """续跑 / 继续时按剩余条目重新计算批次，批号从 1 开始显示。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE operations SET batch_count = ?, batch_index = 0 WHERE id = ?",
            (batch_count, operation_id),
        )
        conn.commit()


def reset_operation_items(operation_id: str) -> int:
    """续跑前把未完成条目重置为 planned；已完成 / 已跳过的编号保持锁定。"""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE operation_items SET status = 'planned' "
            "WHERE operation_id = ? AND status IN ('failed', 'conflict', 'planned')",
            (operation_id,),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def pending_operation_items(operation_id: str) -> list[dict[str, Any]]:
    """续跑 / 继续时只取还未完成的条目。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_items WHERE operation_id = ? AND status = 'planned' ORDER BY seq",
            (operation_id,),
        ).fetchall()
    return [
        {
            "seq": row["seq"],
            "group_id": row["group_id"],
            "file_id": row["file_id"],
            "category": row["category"],
            "original_path": row["original_path"],
            "target_path": row["target_path"],
            "status": row["status"],
            "message": row["message"],
        }
        for row in rows
    ]


def mark_interrupted_operations() -> int:
    """容器重启后，内存里的后台任务已经消失，把未完成状态改成“已中断”。"""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE operations SET status = 'interrupted', current_item = '' "
            "WHERE status IN ('queued', 'running', 'paused')"
        )
        conn.commit()
        return int(cursor.rowcount or 0)


ACTIVE_STATUSES = ("queued", "running", "paused")


def active_operation() -> dict[str, Any] | None:
    """当前占用执行通道的任务（排队中 / 执行中 / 已暂停）。"""
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM operations WHERE status IN ({placeholders}) "
            "ORDER BY created_at DESC LIMIT 1",
            ACTIVE_STATUSES,
        ).fetchone()
    return _operation_from_row(row) if row is not None else None


def operation_done_items(operation_id: str) -> list[dict[str, Any]]:
    """已完成条目，用于执行结束后的空目录清理。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_items WHERE operation_id = ? AND status = 'done' ORDER BY seq",
            (operation_id,),
        ).fetchall()
    return [
        {
            "seq": row["seq"],
            "group_id": row["group_id"],
            "original_path": row["original_path"],
            "target_path": row["target_path"],
        }
        for row in rows
    ]


def list_operations(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        counts = {
            item["operation_id"]: item["total"]
            for item in conn.execute(
                "SELECT operation_id, COUNT(*) AS total FROM operation_items GROUP BY operation_id"
            ).fetchall()
        }
    operations = []
    for row in rows:
        data = _operation_from_row(row)
        data["item_count"] = counts.get(row["id"], 0)
        operations.append(data)
    return operations


def mark_operation_status(operation_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE operations SET status = ?, undone_at = ? WHERE id = ?",
            (status, _now() if status == "undone" else None, operation_id),
        )
        conn.commit()
