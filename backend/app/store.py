"""配置存储：预设与识别规则放在 /config 下的 JSON 文件，执行历史放在 SQLite。"""

from __future__ import annotations

import json
import os
import re
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
    GLOBAL_ONLY_KEYS,
    LEGACY_PRESET_OPTION_KEYS,
    WORKFLOW_PRESET_OPTION_KEYS,
    default_naming_options,
    default_workflow_options,
    get_media_root,
    load_settings,
    naming_options_from_settings,
    save_settings,
    setting_values_from_preset_options,
    workflow_options_from_settings,
)
from .core.groups import normalize_group, validate_groups
from .core.rules import BUILTIN_RULES, Rule
from .core.run_advance import RUN_STAGE_ORDER

# v7：预设拆成两层——命名预设（模板 + 命名/落点选项）与工作流预设（其余选项）。
PRESETS_VERSION = 7
GROUPS_VERSION = 1
ASSIGNMENTS_VERSION = 1
ALIASES_VERSION = 1

# 扫描结果持久化：只保留最近若干次，避免 SQLite 无限膨胀。
SCAN_KEEP_LATEST = 10
SCAN_TTL_DAYS = 14

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
CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'scan',
    status TEXT NOT NULL DEFAULT 'active',
    preset_id TEXT,
    operation_id TEXT,
    groups TEXT NOT NULL DEFAULT '[]',
    snapshot TEXT NOT NULL DEFAULT '{}',
    lock_owner TEXT NOT NULL DEFAULT '',
    lock_expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_scan ON runs(scan_id);
CREATE TABLE IF NOT EXISTS pending_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    ref_type TEXT NOT NULL DEFAULT '',
    ref_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    blocking INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    decision_source TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, kind, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_pending_run ON pending_items(run_id, status);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    scan_id TEXT NOT NULL DEFAULT '',
    operation_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    ref_id TEXT NOT NULL DEFAULT '',
    choice TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'human',
    rule_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_scan ON decisions(scan_id);
CREATE TABLE IF NOT EXISTS auto_authorizations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL DEFAULT '',
    operation_id TEXT NOT NULL DEFAULT '',
    confirmed_by TEXT NOT NULL,
    risk_version TEXT NOT NULL,
    risk_text TEXT NOT NULL DEFAULT '',
    gate_checked_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'granted',
    created_at TEXT NOT NULL,
    consumed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auto_auth_run ON auto_authorizations(run_id, status);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'human',
    rule_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, id);
"""

PRESETS_FILE = CONFIG_DIR / "presets.json"
RULES_FILE = CONFIG_DIR / "rules.json"
GROUPS_FILE = CONFIG_DIR / "groups.json"
ASSIGNMENTS_FILE = CONFIG_DIR / "assignments.json"
ALIASES_FILE = CONFIG_DIR / "aliases.json"
SPECIAL_MEMORY_FILE = CONFIG_DIR / "special_memory.json"
SPECIAL_MEMORY_VERSION = 1
PATTERN_CACHE_FILE = CONFIG_DIR / "pattern_cache.json"
PATTERN_CACHE_VERSION = 1
PATTERN_DECISIONS_FILE = CONFIG_DIR / "pattern_decisions.json"
PATTERN_DECISIONS_VERSION = 1
PATTERN_DECISIONS = ("adopted", "rejected", "later")
PATTERN_DECISION_LABELS = {
    "adopted": "采用",
    "rejected": "不采用",
    "later": "稍后再说",
}
# 缓存条目的来源：AI 首次生成 / 用户手动改过 / 从别的指纹命中后写入
PATTERN_SOURCES = ("ai", "manual", "cache_hit")
PATTERN_SOURCE_LABELS = {
    "ai": "AI 生成",
    "manual": "用户手动修改",
    "cache_hit": "从缓存命中",
}

_LOCK = threading.RLock()

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    # v5：正片默认带上 {resolution}，同一集多版本时不会因为撞名直接失败；
    # 分辨率识别不到时变量为空，会被折叠掉，不会留下多余的空括号。
    # v6：正片默认带上 {version}，41v2 这类版本号不会在改名时丢掉；
    # 识别不到版本时变量为空，整段 [v{version}] 会被折叠，不影响普通文件。
    "episode": {
        "pattern": "{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]",
        "output_mode": "filename",
    },
    # 内置特典模板：来源季在前（S01 CM01 / S02 NCOP01），用来区分同一作品不同季的同名特典。
    # {source_season} 为空时整段自动折叠，不会留下双空格或孤立的「-」。
    "special": {
        "pattern": "{series} - {source_season} {special_type}{special_no}[v{version}]",
        "output_mode": "filename",
    },
    "movie": {"pattern": "{series} ({year})", "output_mode": "filename"},
}

# 旧版内置模板 -> v3（统一 SxxExx）。只替换完全一致的旧内置写法，用户自定义模板保持不变。
LEGACY_PATTERN_V3: dict[str, str] = {
    "{series} - {episode:02}": "{series} - S{season:02}E{episode:02}",
    "[{group}] {series} - {episode:02} [{resolution}][{codec}]": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}][{codec}]",
    "{series} - {special_type}{special_no:02}": "{series} - S{season:02}E{episode:02}",
    "[{group}] {series} - {special_type}{special_no:02} [{resolution}]": "[{group}] {series} - S{season:02}E{episode:02} [{resolution}]",
}

# 第 9 步：特典默认模板改为「作品名 - 特典类型+编号」，仍然放进特典季目录。
# 只替换完全一致的内置写法（v4 之前的默认），用户自定义模板保持不变。
LEGACY_SPECIAL_V4: dict[str, str] = {
    "{series} - S{season:02}E{episode:02}": "{series} - {special_type}{special_no}",
    "[{group}] {series} - S{season:02}E{episode:02} [{resolution}]": "[{group}] {series} - {special_type}{special_no} [{resolution}]",
}

# 第 10 步：内置正片模板加入 {resolution}，同一集多版本可以用清晰度区分。
# 只替换与旧内置写法完全一致的模板；用户自己改过的模板不会被覆盖。
LEGACY_EPISODE_V5: dict[str, str] = {
    "{series} - S{season:02}E{episode:02}": "{series} - S{season:02}E{episode:02} [{resolution}]",
}

# 第 11 步 / v6：内置正片模板加入 {version}，41v2 这类版本号不再丢失。
# 只替换与 v5 内置写法完全一致的模板；用户自己改过的模板不会被覆盖。
LEGACY_EPISODE_V6: dict[str, str] = {
    "{series} - S{season:02}E{episode:02} [{resolution}]": (
        "{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]"
    ),
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
                "pattern": "[{group}] {series} - {source_season} {special_type}{special_no}[v{version}] [{resolution}]",
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
                "pattern": "{series} - S{season:02}E{episode:02} [{resolution}] [v{version}]",
                "output_mode": "filename",
            },
            "special": {
                "pattern": "{series} - {source_season} {special_type}{special_no}[v{version}]",
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
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # 配置目录常常位于 Windows 挂载盘上：默认的 rollback journal + FULL 同步
    # 每次提交要两次 fsync（实测约 16ms）。WAL + NORMAL 把每次提交降到约 2ms，
    # 执行阶段“每个文件都要落库”时才不会卡。失败时退回默认模式，保证还能用。
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass
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


def _normalize_option_map(
    options: dict[str, Any] | None, result: dict[str, Any]
) -> dict[str, Any]:
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


def _normalize_naming_options(options: dict[str, Any] | None) -> dict[str, Any]:
    return _normalize_option_map(options, default_naming_options())


def _normalize_workflow_options(options: dict[str, Any] | None) -> dict[str, Any]:
    return _normalize_option_map(options, default_workflow_options())


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


def _normalize_naming_preset(
    item: dict[str, Any], fallback_id: str | None = None
) -> dict[str, Any]:
    now = _now()
    return {
        "id": str(item.get("id") or fallback_id or f"naming-{uuid.uuid4().hex[:10]}"),
        "name": _repair_text(str(item.get("name") or "未命名命名预设"))[:120],
        "is_default": bool(item.get("is_default")),
        "library_profile": str(item.get("library_profile") or "generic")[:40],
        "templates": _normalize_templates(item.get("templates")),
        "options": _normalize_naming_options(item.get("options")),
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _normalize_workflow_preset(
    item: dict[str, Any], fallback_id: str | None = None
) -> dict[str, Any]:
    now = _now()
    return {
        "id": str(item.get("id") or fallback_id or f"workflow-{uuid.uuid4().hex[:10]}"),
        "name": _repair_text(str(item.get("name") or "未命名工作流预设"))[:120],
        "is_default": bool(item.get("is_default")),
        "library_profile": str(item.get("library_profile") or "generic")[:40],
        "naming_preset_id": str(item.get("naming_preset_id") or ""),
        "options": _normalize_workflow_options(item.get("options")),
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


def _migrate_special_templates(presets: list[dict[str, Any]]) -> None:
    """v4 一次性迁移：把旧内置特典模板换成「作品名 - 特典类型+编号」。

    只在升级时执行一次，之后用户自己改成别的写法不会被反复覆盖。
    """
    for preset in presets:
        templates = preset.get("templates")
        if not isinstance(templates, dict):
            continue
        special = templates.get("special")
        if isinstance(special, dict):
            pattern = str(special.get("pattern") or "")
            if pattern in LEGACY_SPECIAL_V4:
                special["pattern"] = LEGACY_SPECIAL_V4[pattern]


def _migrate_episode_resolution_v5(presets: list[dict[str, Any]]) -> None:
    """v5 一次性迁移：给旧内置正片模板补上 {resolution}。

    只替换和旧内置写法完全一致的模板，用户改过的写法保持原样。
    """
    for preset in presets:
        templates = preset.get("templates")
        if not isinstance(templates, dict):
            continue
        episode = templates.get("episode")
        if isinstance(episode, dict):
            pattern = str(episode.get("pattern") or "")
            if pattern in LEGACY_EPISODE_V5:
                episode["pattern"] = LEGACY_EPISODE_V5[pattern]


def _migrate_episode_version_v6(presets: list[dict[str, Any]]) -> None:
    """v6 一次性迁移：给旧内置正片模板补上 {version}。

    只替换和 v5 内置写法完全一致的模板，用户改过的写法保持原样。
    """
    for preset in presets:
        templates = preset.get("templates")
        if not isinstance(templates, dict):
            continue
        episode = templates.get("episode")
        if isinstance(episode, dict):
            pattern = str(episode.get("pattern") or "")
            if pattern in LEGACY_EPISODE_V6:
                episode["pattern"] = LEGACY_EPISODE_V6[pattern]


def _seed_naming_presets() -> list[dict[str, Any]]:
    return [
        _normalize_naming_preset({**item, "options": default_naming_options()})
        for item in SEED_PRESETS
    ]


def _seed_workflow_presets() -> list[dict[str, Any]]:
    """三条内置预设各配一份同名工作流预设，并指向同名的命名预设。"""
    return [
        _normalize_workflow_preset(
            {
                "id": item["id"],
                "name": item["name"],
                "is_default": item["is_default"],
                "library_profile": item["library_profile"],
                "naming_preset_id": item["id"],
                "options": default_workflow_options(),
            }
        )
        for item in SEED_PRESETS
    ]


def _legacy_global_option_key(option: str) -> str:
    return {
        "tmdb_lang": "providers.tmdb_lang",
        "name_language_order": "metadata.name_language_order",
    }.get(option, "")


def _split_legacy_presets(
    presets: list[dict[str, Any]], global_settings: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """v7 一次性迁移：把“模板 + 全部选项”的单层预设拆成两层。

    只搬运能确认来源的值：旧快照里有的键按原值搬过去；旧快照里没有的键
    取默认值并写进迁移记录；无法归类或已改为全局的键也逐条记录，不靠猜。
    """
    record: dict[str, Any] = {
        "from_version": 6,
        "to_version": PRESETS_VERSION,
        "migrated_at": _now(),
        "filled_defaults": [],
        "dropped_keys": [],
        "global_only_ignored": [],
    }
    naming_presets: list[dict[str, Any]] = []
    workflow_presets: list[dict[str, Any]] = []
    for item in presets:
        preset_id = str(item.get("id") or f"preset-{uuid.uuid4().hex[:10]}")
        name = item.get("name")
        options = item.get("options") if isinstance(item.get("options"), dict) else {}
        naming_options: dict[str, Any] = {}
        workflow_options: dict[str, Any] = {}
        for key in default_naming_options():
            if key in options:
                naming_options[key] = options[key]
            else:
                record["filled_defaults"].append(
                    {"preset_id": preset_id, "layer": "naming", "key": key}
                )
        for key in default_workflow_options():
            if key in options:
                workflow_options[key] = options[key]
            else:
                record["filled_defaults"].append(
                    {"preset_id": preset_id, "layer": "workflow", "key": key}
                )
        for key, value in options.items():
            if key in naming_options or key in workflow_options:
                continue
            global_key = _legacy_global_option_key(key)
            if global_key in GLOBAL_ONLY_KEYS:
                if value != global_settings.get(global_key):
                    record["global_only_ignored"].append(
                        {
                            "preset_id": preset_id,
                            "key": global_key,
                            "preset_value": value,
                            "global_value": global_settings.get(global_key),
                        }
                    )
                continue
            record["dropped_keys"].append({"preset_id": preset_id, "key": key})
        naming_presets.append(
            _normalize_naming_preset(
                {
                    "id": preset_id,
                    "name": name,
                    "is_default": item.get("is_default"),
                    "library_profile": item.get("library_profile"),
                    "templates": item.get("templates"),
                    "options": naming_options,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
        )
        workflow_presets.append(
            _normalize_workflow_preset(
                {
                    "id": preset_id,
                    "name": name,
                    "is_default": item.get("is_default"),
                    "library_profile": item.get("library_profile"),
                    "naming_preset_id": preset_id,
                    "options": workflow_options,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
        )
    return naming_presets, workflow_presets, record


def _fix_preset_defaults_and_links(
    naming: list[dict[str, Any]], workflow: list[dict[str, Any]]
) -> bool:
    """保证两层都各有一个默认预设，并把断掉的工作流→命名引用指回默认命名预设。"""
    changed = False
    if naming and not any(item["is_default"] for item in naming):
        naming[0]["is_default"] = True
        changed = True
    naming_ids = {item["id"] for item in naming}
    default_naming = next(
        (item["id"] for item in naming if item["is_default"]), naming[0]["id"] if naming else ""
    )
    for item in workflow:
        if not item.get("naming_preset_id") or item["naming_preset_id"] not in naming_ids:
            item["naming_preset_id"] = default_naming
            changed = True
    if workflow and not any(item["is_default"] for item in workflow):
        workflow[0]["is_default"] = True
        changed = True
    return changed


def _presets_doc(
    naming: list[dict[str, Any]],
    workflow: list[dict[str, Any]],
    migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    default_workflow = next(
        (item["id"] for item in workflow if item["is_default"]),
        workflow[0]["id"] if workflow else "",
    )
    default_naming = next(
        (item["id"] for item in naming if item["is_default"]),
        naming[0]["id"] if naming else "",
    )
    doc: dict[str, Any] = {
        "version": PRESETS_VERSION,
        "default_id": default_workflow,
        "default_naming_id": default_naming,
        "naming_presets": naming,
        "workflow_presets": workflow,
    }
    if migration is not None:
        doc["migration"] = migration
    return doc


def _ensure_presets() -> dict[str, Any]:
    """确保 presets.json 存在；旧版单层预设会一次性拆成命名预设 + 工作流预设。"""
    with _LOCK:
        doc = _read_json(PRESETS_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("naming_presets"), list):
            naming = [
                _normalize_naming_preset(item, item.get("id"))
                for item in doc["naming_presets"]
                if isinstance(item, dict)
            ]
            workflow = [
                _normalize_workflow_preset(item, item.get("id"))
                for item in doc.get("workflow_presets") or []
                if isinstance(item, dict)
            ]
            if not naming:
                naming = _seed_naming_presets()
            if not workflow:
                workflow = _seed_workflow_presets()
            changed = _fix_preset_defaults_and_links(naming, workflow)
            migration = doc.get("migration") if isinstance(doc.get("migration"), dict) else None
            normalized = _presets_doc(naming, workflow, migration)
            if doc.get("version") != PRESETS_VERSION or changed:
                _write_json(PRESETS_FILE, normalized)
            return normalized

        legacy: list[dict[str, Any]] = []
        if isinstance(doc, dict) and isinstance(doc.get("presets"), list) and doc["presets"]:
            legacy = [item for item in doc["presets"] if isinstance(item, dict)]
        else:
            for row in _legacy_rows("presets"):
                try:
                    templates = json.loads(row.get("templates") or "{}")
                except (TypeError, ValueError):
                    templates = {}
                try:
                    options = json.loads(row.get("options") or "{}")
                except (TypeError, ValueError):
                    options = {}
                legacy.append(
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
        if legacy:
            _migrate_special_templates(legacy)
            _migrate_episode_resolution_v5(legacy)
            _migrate_episode_version_v6(legacy)
            naming, workflow, record = _split_legacy_presets(legacy, load_settings())
        else:
            naming = _seed_naming_presets()
            workflow = _seed_workflow_presets()
            record = {
                "from_version": 0,
                "to_version": PRESETS_VERSION,
                "migrated_at": _now(),
                "filled_defaults": [],
                "dropped_keys": [],
                "global_only_ignored": [],
            }
        _fix_preset_defaults_and_links(naming, workflow)
        normalized = _presets_doc(naming, workflow, record)
        _write_json(PRESETS_FILE, normalized)
        return normalized


def _append_missing_builtin_rules(rules: list[dict[str, Any]]) -> bool:
    """把新版本新增的内置规则补进现有规则表；用户改过的旧规则保持原样。"""
    existing = {str(item.get("id")) for item in rules}
    added = False
    for rule in BUILTIN_RULES:
        if rule.id in existing:
            continue
        rules.append(
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
        )
        added = True
    return added


def _ensure_rules() -> list[dict[str, Any]]:
    """确保 rules.json 存在；没有就从旧数据库或内置规则生成。"""
    with _LOCK:
        doc = _read_json(RULES_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("rules"), list) and doc["rules"]:
            rules = [dict(item) for item in doc["rules"] if isinstance(item, dict)]
            if _append_missing_builtin_rules(rules):
                _write_json(RULES_FILE, {"version": 1, "rules": rules})
            return rules

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


def _normalize_special_memory(item: dict[str, Any]) -> dict[str, Any]:
    """特典目录的归属记忆：按「目录组 + 目录相对路径」记住用户确认过的作品。"""
    now = _now()
    year = item.get("year")
    try:
        year_value = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_value = None
    return {
        "id": str(item.get("id") or f"sm-{uuid.uuid4().hex[:10]}"),
        "group_id": str(item.get("group_id") or ""),
        "rel_path": str(item.get("rel_path") or "").strip("/"),
        "work_id": str(item.get("work_id") or ""),
        "title": _repair_text(str(item.get("title") or ""))[:300],
        "year": year_value,
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _ensure_special_memory() -> dict[str, Any]:
    with _LOCK:
        doc = _read_json(SPECIAL_MEMORY_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
            entries = [
                _normalize_special_memory(item)
                for item in doc["entries"]
                if isinstance(item, dict) and str(item.get("rel_path") or "").strip()
            ]
            normalized = {"version": SPECIAL_MEMORY_VERSION, "entries": entries}
            if doc.get("version") != SPECIAL_MEMORY_VERSION:
                _write_json(SPECIAL_MEMORY_FILE, normalized)
            return normalized
        doc = {"version": SPECIAL_MEMORY_VERSION, "entries": []}
        _write_json(SPECIAL_MEMORY_FILE, doc)
        return doc


def list_special_memory() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_special_memory()["entries"]]


def remember_special_folder(payload: dict[str, Any]) -> dict[str, Any]:
    """记住特典目录的归属；同一目录组 + 同一目录已存在时直接更新。"""
    with _LOCK:
        doc = _ensure_special_memory()
        entry = _normalize_special_memory(payload)
        target = next(
            (
                item
                for item in doc["entries"]
                if item["group_id"] == entry["group_id"] and item["rel_path"] == entry["rel_path"]
            ),
            None,
        )
        if target is not None:
            entry["id"] = target["id"]
            entry["created_at"] = target.get("created_at") or entry["created_at"]
            doc["entries"][doc["entries"].index(target)] = entry
        else:
            doc["entries"].append(entry)
        _write_json(SPECIAL_MEMORY_FILE, doc)
    return entry


def delete_special_memory(memory_id: str) -> bool:
    with _LOCK:
        doc = _ensure_special_memory()
        target = next((item for item in doc["entries"] if item["id"] == memory_id), None)
        if target is None:
            return False
        doc["entries"].remove(target)
        _write_json(SPECIAL_MEMORY_FILE, doc)
    return True


def _normalize_pattern_entry(item: dict[str, Any]) -> dict[str, Any]:
    """命名规律缓存条目：指纹 + 目录组 + 子目录 + 结构 + 来源 + 命中次数。"""
    now = _now()
    structure = item.get("structure")
    if not isinstance(structure, dict):
        structure = {}
    source = str(item.get("source") or "ai").strip().lower()
    if source not in PATTERN_SOURCES:
        source = "ai"
    try:
        hits = max(int(item.get("hits") or 0), 0)
    except (TypeError, ValueError):
        hits = 0
    return {
        "id": str(item.get("id") or f"pc-{uuid.uuid4().hex[:10]}"),
        "fingerprint": str(item.get("fingerprint") or "").strip(),
        "group_id": str(item.get("group_id") or ""),
        "dir_rel": str(item.get("dir_rel") or "").strip("/").replace("\\", "/"),
        "structure": structure,
        "source": source,
        "source_label": PATTERN_SOURCE_LABELS.get(source, "AI 生成"),
        "enabled": bool(item.get("enabled", True)),
        "hits": hits,
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
        "last_used_at": str(item.get("last_used_at") or ""),
    }


def _ensure_pattern_cache() -> dict[str, Any]:
    with _LOCK:
        doc = _read_json(PATTERN_CACHE_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
            entries = [
                _normalize_pattern_entry(item)
                for item in doc["entries"]
                if isinstance(item, dict) and str(item.get("fingerprint") or "").strip()
            ]
            normalized = {"version": PATTERN_CACHE_VERSION, "entries": entries}
            if doc.get("version") != PATTERN_CACHE_VERSION:
                _write_json(PATTERN_CACHE_FILE, normalized)
            return normalized
        doc = {"version": PATTERN_CACHE_VERSION, "entries": []}
        _write_json(PATTERN_CACHE_FILE, doc)
        return doc


def list_pattern_entries() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_pattern_cache()["entries"]]


def find_pattern_entry(fingerprint_code: str) -> dict[str, Any] | None:
    code = str(fingerprint_code or "").strip()
    if not code:
        return None
    target = next(
        (item for item in _ensure_pattern_cache()["entries"] if item["fingerprint"] == code),
        None,
    )
    return dict(target) if target is not None else None


def save_pattern_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """按指纹新增或更新；同一指纹只保留一条，避免出现两份互相矛盾的规律。"""
    with _LOCK:
        doc = _ensure_pattern_cache()
        entry = _normalize_pattern_entry(payload)
        if not entry["fingerprint"]:
            raise ValueError("缺少指纹，无法保存命名规律")
        target = next(
            (item for item in doc["entries"] if item["fingerprint"] == entry["fingerprint"]),
            None,
        )
        if target is None:
            doc["entries"].append(entry)
        else:
            entry["id"] = target["id"]
            entry["created_at"] = target.get("created_at") or entry["created_at"]
            entry["hits"] = int(target.get("hits") or 0)
            entry["last_used_at"] = str(target.get("last_used_at") or "")
            doc["entries"][doc["entries"].index(target)] = entry
        _write_json(PATTERN_CACHE_FILE, doc)
    return entry


def mark_pattern_hit(fingerprint_code: str) -> dict[str, Any] | None:
    """缓存命中自动套用后累加次数；只动命中统计，不改规律内容。"""
    code = str(fingerprint_code or "").strip()
    with _LOCK:
        doc = _ensure_pattern_cache()
        target = next((item for item in doc["entries"] if item["fingerprint"] == code), None)
        if target is None:
            return None
        target["hits"] = int(target.get("hits") or 0) + 1
        target["last_used_at"] = _now()
        _write_json(PATTERN_CACHE_FILE, doc)
        return dict(target)


def set_pattern_enabled(entry_id: str, enabled: bool) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_pattern_cache()
        target = next((item for item in doc["entries"] if item["id"] == entry_id), None)
        if target is None:
            return None
        target["enabled"] = bool(enabled)
        target["updated_at"] = _now()
        _write_json(PATTERN_CACHE_FILE, doc)
        return dict(target)


def delete_pattern_entry(entry_id: str) -> bool:
    with _LOCK:
        doc = _ensure_pattern_cache()
        target = next((item for item in doc["entries"] if item["id"] == entry_id), None)
        if target is None:
            return False
        doc["entries"].remove(target)
        _write_json(PATTERN_CACHE_FILE, doc)
    return True


def clear_pattern_cache() -> int:
    with _LOCK:
        doc = _ensure_pattern_cache()
        count = len(doc["entries"])
        doc["entries"] = []
        _write_json(PATTERN_CACHE_FILE, doc)
    return count


def _normalize_pattern_decision(item: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    decision = str(item.get("decision") or "later").strip().lower()
    if decision not in PATTERN_DECISIONS:
        decision = "later"
    structure = item.get("structure")
    return {
        "id": str(item.get("id") or f"pd-{uuid.uuid4().hex[:10]}"),
        "group_id": str(item.get("group_id") or ""),
        "dir_rel": str(item.get("dir_rel") or "").strip("/").replace("\\", "/"),
        "decision": decision,
        "decision_label": PATTERN_DECISION_LABELS[decision],
        "note": str(item.get("note") or ""),
        "structure": structure if isinstance(structure, dict) else {},
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _ensure_pattern_decisions() -> dict[str, Any]:
    with _LOCK:
        doc = _read_json(PATTERN_DECISIONS_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
            entries = [
                _normalize_pattern_decision(item)
                for item in doc["entries"]
                if isinstance(item, dict) and str(item.get("group_id") or "")
            ]
            normalized = {"version": PATTERN_DECISIONS_VERSION, "entries": entries}
            if doc.get("version") != PATTERN_DECISIONS_VERSION:
                _write_json(PATTERN_DECISIONS_FILE, normalized)
            return normalized
        doc = {"version": PATTERN_DECISIONS_VERSION, "entries": []}
        _write_json(PATTERN_DECISIONS_FILE, doc)
        return doc


def list_pattern_decisions() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_pattern_decisions()["entries"]]


def get_pattern_decision(group_id: str, dir_rel: str) -> dict[str, Any] | None:
    """取某个「目录组 + 子目录」最近一次的取舍记录。"""
    group = str(group_id or "")
    rel = str(dir_rel or "").strip("/").replace("\\", "/")
    target = next(
        (
            item
            for item in _ensure_pattern_decisions()["entries"]
            if item["group_id"] == group and item["dir_rel"] == rel
        ),
        None,
    )
    return dict(target) if target is not None else None


def save_pattern_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """记录采用 / 不采用 / 稍后再说；同一目录只保留最新一条。

    只写取舍记录，不删除、不改动规律缓存本身。
    """
    with _LOCK:
        doc = _ensure_pattern_decisions()
        entry = _normalize_pattern_decision(payload)
        if not entry["group_id"]:
            raise ValueError("缺少目录组，无法记录取舍")
        target = next(
            (
                item
                for item in doc["entries"]
                if item["group_id"] == entry["group_id"] and item["dir_rel"] == entry["dir_rel"]
            ),
            None,
        )
        if target is None:
            doc["entries"].append(entry)
        else:
            entry["id"] = target["id"]
            entry["created_at"] = target.get("created_at") or entry["created_at"]
            doc["entries"][doc["entries"].index(target)] = entry
        _write_json(PATTERN_DECISIONS_FILE, doc)
    return entry


def _normalize_alias(item: dict[str, Any]) -> dict[str, Any]:
    """别名：同一部作品的另一种写法（日文名 / 罗马音 / 简称 / 繁简差异）。

    只做精确匹配复用，不参与相似度合并，避免把两部作品自动合成一部。
    """
    now = _now()
    year = item.get("year")
    try:
        year_value = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_value = None
    raw_id = item.get("tmdb_id")
    try:
        tmdb_id = int(raw_id) if raw_id not in (None, "") else None
    except (TypeError, ValueError):
        tmdb_id = None
    media_type = str(item.get("media_type") or "").strip().lower()
    if media_type not in ("tv", "movie"):
        media_type = ""
    return {
        "id": str(item.get("id") or f"al-{uuid.uuid4().hex[:10]}"),
        "title": _repair_text(str(item.get("title") or ""))[:300],
        "year": year_value,
        "alias": _repair_text(str(item.get("alias") or ""))[:300],
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _ensure_aliases() -> dict[str, Any]:
    with _LOCK:
        doc = _read_json(ALIASES_FILE)
        if isinstance(doc, dict) and isinstance(doc.get("aliases"), list):
            aliases = [
                _normalize_alias(item) for item in doc["aliases"] if isinstance(item, dict)
            ]
            normalized = {"version": ALIASES_VERSION, "aliases": aliases}
            if doc.get("version") != ALIASES_VERSION:
                _write_json(ALIASES_FILE, normalized)
            return normalized

        doc = {"version": ALIASES_VERSION, "aliases": []}
        _write_json(ALIASES_FILE, doc)
        return doc


def list_aliases() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_aliases()["aliases"]]


def create_alias(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_aliases()
        alias = _normalize_alias(payload)
        doc["aliases"].append(alias)
        _write_json(ALIASES_FILE, doc)
    return alias


def update_alias(alias_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_aliases()
        target = next((item for item in doc["aliases"] if item["id"] == alias_id), None)
        if target is None:
            return None
        updated = _normalize_alias(
            {
                "id": alias_id,
                "title": payload.get("title", target.get("title")),
                "year": payload.get("year", target.get("year")),
                "alias": payload.get("alias", target.get("alias")),
                "tmdb_id": payload.get("tmdb_id", target.get("tmdb_id")),
                "media_type": payload.get("media_type", target.get("media_type")),
                "created_at": target.get("created_at"),
                "updated_at": _now(),
            }
        )
        doc["aliases"][doc["aliases"].index(target)] = updated
        _write_json(ALIASES_FILE, doc)
    return updated


def delete_alias(alias_id: str) -> bool:
    with _LOCK:
        doc = _ensure_aliases()
        target = next((item for item in doc["aliases"] if item["id"] == alias_id), None)
        if target is None:
            return False
        doc["aliases"].remove(target)
        _write_json(ALIASES_FILE, doc)
    return True


def latest_operation_for_scan(scan_id: str) -> dict[str, Any] | None:
    """轻量核对时取这个扫描最近一次执行记录，用来判断文件是不是已经移动到目标。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM operations WHERE scan_id = ? AND kind = 'execute' "
            "ORDER BY created_at DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if row is None:
            return None
        items = _operation_items(conn, row["id"])
    return _operation_from_row(row, items)


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
    for preset in doc["workflow_presets"]:
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


def _migrate_operation_items_v5() -> None:
    """第 6 步：冲突解决需要记住用户选的处理方式（跳过 / 覆盖 / 生成副本 / 合并…）。"""
    with _connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(operation_items)").fetchall()
        }
        if "choice" not in columns:
            conn.execute("ALTER TABLE operation_items ADD COLUMN choice TEXT NOT NULL DEFAULT ''")
            conn.commit()


def _migrate_operation_items_v7() -> None:
    """记录每个需要人拍板的条目是谁定的：人工 / 规则 / 未来 AI，以及决策时间。"""
    additions = {
        "decision_source": "TEXT NOT NULL DEFAULT ''",
        "rule_id": "TEXT NOT NULL DEFAULT ''",
        "decided_at": "TEXT NOT NULL DEFAULT ''",
    }
    with _connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(operation_items)").fetchall()
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE operation_items ADD COLUMN {name} {ddl}")
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
        naming = next(
            (item for item in doc["naming_presets"] if item["is_default"]),
            doc["naming_presets"][0],
        )
        workflow = next(
            (item for item in doc["workflow_presets"] if item["is_default"]),
            doc["workflow_presets"][0],
        )
        merged = {**naming.get("options", {}), **workflow.get("options", {})}
        values.update(setting_values_from_preset_options(merged))
    save_settings(values)


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    _migrate_operation_items_v2()
    _migrate_operation_items_v5()
    _migrate_operation_items_v7()
    _migrate_operations_v4()
    # 旧版本可能把预设存在 SQLite 里，迁移前先确认有没有可用的预设来源
    had_presets = PRESETS_FILE.exists() or bool(_legacy_rows("presets"))
    _ensure_presets()
    _ensure_rules()
    _ensure_groups()
    _ensure_assignments()
    _ensure_aliases()
    _migrate_keep_empty_dirs()
    _migrate_settings_v2(had_presets)


def create_session(token_hash: str, expires_at: str) -> None:
    """登录成功后保存会话（只存 token 的哈希，不存原文）。"""
    now = _now()
    with _connect() as conn:
        conn.execute(
            "insert or replace into auth_sessions (token_hash, created_at, expires_at) values (?, ?, ?)",
            (token_hash, now, expires_at),
        )
        conn.commit()


def session_is_valid(token_hash: str) -> bool:
    if not token_hash:
        return False
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "select expires_at from auth_sessions where token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return False
        return str(row["expires_at"]) > now


def delete_session(token_hash: str) -> None:
    if not token_hash:
        return
    with _connect() as conn:
        conn.execute("delete from auth_sessions where token_hash = ?", (token_hash,))
        conn.commit()


def delete_all_sessions() -> None:
    with _connect() as conn:
        conn.execute("delete from auth_sessions")
        conn.commit()


def cleanup_expired_sessions() -> None:
    now = _now()
    with _connect() as conn:
        conn.execute("delete from auth_sessions where expires_at <= ?", (now,))
        conn.commit()


def _sorted_presets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items, key=lambda item: (0 if item["is_default"] else 1, item["name"])
    )


def list_naming_presets() -> list[dict[str, Any]]:
    doc = _ensure_presets()
    return _sorted_presets(
        [_normalize_naming_preset(item, item.get("id")) for item in doc["naming_presets"]]
    )


def list_workflow_presets() -> list[dict[str, Any]]:
    doc = _ensure_presets()
    return _sorted_presets(
        [
            _normalize_workflow_preset(item, item.get("id"))
            for item in doc["workflow_presets"]
        ]
    )


def get_naming_preset(preset_id: str) -> dict[str, Any] | None:
    return next((item for item in list_naming_presets() if item["id"] == preset_id), None)


def get_workflow_preset(preset_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in list_workflow_presets() if item["id"] == preset_id), None
    )


def get_default_naming_preset() -> dict[str, Any] | None:
    presets = list_naming_presets()
    return next((item for item in presets if item["is_default"]), presets[0] if presets else None)


def get_default_workflow_preset() -> dict[str, Any] | None:
    presets = list_workflow_presets()
    return next((item for item in presets if item["is_default"]), presets[0] if presets else None)


# 兼容旧调用：预览、AI 规律和回归脚本只关心“带模板的那层”，即命名预设。
def list_presets() -> list[dict[str, Any]]:
    return list_naming_presets()


def get_preset(preset_id: str) -> dict[str, Any] | None:
    return get_naming_preset(preset_id)


def get_default_preset() -> dict[str, Any] | None:
    return get_default_naming_preset()


def _naming_options_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """保存命名预设：显式传入就用传入值，否则记录当前设置里的命名部分。"""
    options = payload.get("options")
    if isinstance(options, dict) and options:
        return options
    return naming_options_from_settings(load_settings())


def _workflow_options_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """保存工作流预设：显式传入就用传入值，否则记录当前设置里的工作流部分。"""
    options = payload.get("options")
    if isinstance(options, dict) and options:
        return options
    return workflow_options_from_settings(load_settings())


def create_naming_preset(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_presets()
        preset = _normalize_naming_preset(
            {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "is_default": False,
                "library_profile": payload.get("library_profile"),
                "templates": payload.get("templates"),
                "options": _naming_options_from_payload(payload),
            }
        )
        doc["naming_presets"].append(preset)
        _write_json(PRESETS_FILE, doc)
    return get_naming_preset(preset["id"])  # type: ignore[return-value]


def update_naming_preset(
    preset_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["naming_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return None
        updated = _normalize_naming_preset(
            {
                "id": preset_id,
                "name": payload.get("name", target["name"]),
                "is_default": target["is_default"],
                "library_profile": payload.get("library_profile", target["library_profile"]),
                "templates": payload.get("templates", target["templates"]),
                "options": _naming_options_from_payload(payload),
                "created_at": target["created_at"],
                "updated_at": _now(),
            }
        )
        doc["naming_presets"][doc["naming_presets"].index(target)] = updated
        _write_json(PRESETS_FILE, doc)
    return get_naming_preset(preset_id)


def naming_preset_references(preset_id: str) -> list[dict[str, Any]]:
    """哪些工作流预设正在使用这个命名预设。删除前用来给出明确提示。"""
    return [
        {"id": item["id"], "name": item["name"]}
        for item in list_workflow_presets()
        if item.get("naming_preset_id") == preset_id
    ]


def delete_naming_preset(preset_id: str) -> bool:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["naming_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return False
        doc["naming_presets"].remove(target)
        _fix_preset_defaults_and_links(doc["naming_presets"], doc["workflow_presets"])
        _write_json(PRESETS_FILE, doc)
    return True


def set_default_naming_preset(preset_id: str) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["naming_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return None
        for item in doc["naming_presets"]:
            item["is_default"] = item["id"] == preset_id
        doc["default_naming_id"] = preset_id
        _write_json(PRESETS_FILE, doc)
    return get_naming_preset(preset_id)


def duplicate_naming_preset(preset_id: str) -> dict[str, Any] | None:
    source = get_naming_preset(preset_id)
    if not source:
        return None
    return create_naming_preset(
        {
            "name": f"{source['name']} 副本",
            "library_profile": source["library_profile"],
            "templates": source["templates"],
            "options": source.get("options"),
        }
    )


def create_workflow_preset(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        doc = _ensure_presets()
        preset = _normalize_workflow_preset(
            {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "is_default": False,
                "library_profile": payload.get("library_profile"),
                "naming_preset_id": payload.get("naming_preset_id"),
                "options": _workflow_options_from_payload(payload),
            }
        )
        doc["workflow_presets"].append(preset)
        _fix_preset_defaults_and_links(doc["naming_presets"], doc["workflow_presets"])
        _write_json(PRESETS_FILE, doc)
    return get_workflow_preset(preset["id"])  # type: ignore[return-value]


def update_workflow_preset(
    preset_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["workflow_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return None
        naming_ids = {item["id"] for item in doc["naming_presets"]}
        naming_preset_id = payload.get("naming_preset_id", target.get("naming_preset_id", ""))
        if naming_preset_id and naming_preset_id not in naming_ids:
            raise ValueError("指定的命名预设不存在")
        updated = _normalize_workflow_preset(
            {
                "id": preset_id,
                "name": payload.get("name", target["name"]),
                "is_default": target["is_default"],
                "library_profile": payload.get("library_profile", target["library_profile"]),
                "naming_preset_id": naming_preset_id,
                "options": _workflow_options_from_payload(payload),
                "created_at": target["created_at"],
                "updated_at": _now(),
            }
        )
        doc["workflow_presets"][doc["workflow_presets"].index(target)] = updated
        _write_json(PRESETS_FILE, doc)
    return get_workflow_preset(preset_id)


def delete_workflow_preset(preset_id: str) -> bool:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["workflow_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return False
        doc["workflow_presets"].remove(target)
        _fix_preset_defaults_and_links(doc["naming_presets"], doc["workflow_presets"])
        _write_json(PRESETS_FILE, doc)
    return True


def set_default_workflow_preset(preset_id: str) -> dict[str, Any] | None:
    with _LOCK:
        doc = _ensure_presets()
        target = next(
            (item for item in doc["workflow_presets"] if item["id"] == preset_id), None
        )
        if target is None:
            return None
        for item in doc["workflow_presets"]:
            item["is_default"] = item["id"] == preset_id
        doc["default_id"] = preset_id
        _write_json(PRESETS_FILE, doc)
    return get_workflow_preset(preset_id)


def duplicate_workflow_preset(preset_id: str) -> dict[str, Any] | None:
    source = get_workflow_preset(preset_id)
    if not source:
        return None
    return create_workflow_preset(
        {
            "name": f"{source['name']} 副本",
            "library_profile": source["library_profile"],
            "naming_preset_id": source.get("naming_preset_id"),
            "options": source.get("options"),
        }
    )


def export_presets() -> dict[str, Any]:
    """导出两层预设。version 2 是拆分后的结构，导入时仍兼容旧 version 1 文件。"""
    doc = _ensure_presets()
    naming = []
    for item in doc["naming_presets"]:
        preset = _normalize_naming_preset(item, item.get("id"))
        preset.pop("created_at", None)
        preset.pop("updated_at", None)
        preset["is_default"] = False
        naming.append(preset)
    workflow = []
    for item in doc["workflow_presets"]:
        preset = _normalize_workflow_preset(item, item.get("id"))
        preset.pop("created_at", None)
        preset.pop("updated_at", None)
        preset["is_default"] = False
        workflow.append(preset)
    return {"version": 2, "naming_presets": naming, "workflow_presets": workflow}


def import_presets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """导入预设：新的两层文件直接分层导入，旧版单层文件自动拆成两层。

    导入会生成新的 id，因此工作流预设里指向命名预设的引用要同步换成新 id，
    否则导进来就会指到不存在的命名预设。
    """
    created: list[dict[str, Any]] = []

    def _import_naming(items: list[dict[str, Any]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            result = create_naming_preset(
                {
                    "name": str(item.get("name", "导入命名预设"))[:120],
                    "library_profile": item.get("library_profile", "generic"),
                    "templates": item.get("templates") or {},
                    "options": item.get("options") or {},
                }
            )
            if isinstance(item.get("id"), str) and item.get("id"):
                mapping[item["id"]] = result["id"]
            created.append(result)
        return mapping

    def _import_workflow(items: list[dict[str, Any]], mapping: dict[str, str]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            source_ref = item.get("naming_preset_id")
            created.append(
                create_workflow_preset(
                    {
                        "name": str(item.get("name", "导入工作流预设"))[:120],
                        "library_profile": item.get("library_profile", "generic"),
                        "naming_preset_id": mapping.get(str(source_ref), ""),
                        "options": item.get("options") or {},
                    }
                )
            )

    if isinstance(payload.get("naming_presets"), list):
        mapping = _import_naming(list(payload.get("naming_presets") or []))
        _import_workflow(list(payload.get("workflow_presets") or []), mapping)
        return created
    legacy = [
        item for item in payload.get("presets", []) if isinstance(item, dict)
    ]
    if legacy:
        _migrate_special_templates(legacy)
        _migrate_episode_resolution_v5(legacy)
        _migrate_episode_version_v6(legacy)
        naming, workflow, _record = _split_legacy_presets(legacy, load_settings())
        mapping = _import_naming(naming)
        _import_workflow(workflow, mapping)
    return created


BUILTIN_RULE_IDS = {rule.id for rule in BUILTIN_RULES}


def _rule_dict(item: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(item.get("id") or "")
    return {
        "id": rule_id,
        "name": str(item.get("name") or ""),
        "scope": str(item.get("scope") or "filename"),
        "kind": str(item.get("kind") or "episode"),
        "priority": int(item.get("priority") or 0),
        "regex": str(item.get("regex") or ""),
        "enabled": bool(item.get("enabled")),
        "description": str(item.get("description") or ""),
        "sample": str(item.get("sample") or ""),
        # 内置规则不允许删除，只能启用 / 停用；用户自定义规则可以自由增删改。
        "builtin": rule_id in BUILTIN_RULE_IDS,
    }


def list_rule_dicts() -> list[dict[str, Any]]:
    rules = [_rule_dict(item) for item in _ensure_rules()]
    rules.sort(key=lambda item: (item["scope"], item["priority"], item["id"]))
    return rules


def _validate_rule(rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .core.rules import validate_rule_payload

    errors = validate_rule_payload(payload)
    if errors:
        raise ValueError("；".join(errors))
    return {
        "id": rule_id,
        "name": str(payload.get("name") or "").strip()[:80] or rule_id,
        "scope": str(payload.get("scope") or "filename"),
        "kind": str(payload.get("kind") or "episode"),
        "priority": int(payload.get("priority") or 0),
        "regex": str(payload.get("regex") or "").strip(),
        "enabled": bool(payload.get("enabled", True)),
        "description": str(payload.get("description") or "")[:500],
        "sample": str(payload.get("sample") or "")[:500],
    }


def _save_rules(rules: list[dict[str, Any]]) -> None:
    _write_json(RULES_FILE, {"version": 1, "rules": rules})


def create_rule(payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        rule_id = f"rule-{uuid.uuid4().hex[:10]}"
        rule = _validate_rule(rule_id, payload)
        rules = _ensure_rules()
        rules.append(rule)
        _save_rules(rules)
    return _rule_dict(rule)


def update_rule(rule_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        rules = _ensure_rules()
        for index, item in enumerate(rules):
            if str(item.get("id")) != rule_id:
                continue
            # 内置规则的 id 保持不变，只允许改启用状态、优先级等字段
            merged = {**item, **payload}
            if rule_id in BUILTIN_RULE_IDS:
                merged["id"] = rule_id
            rule = _validate_rule(rule_id, merged)
            rules[index] = rule
            _save_rules(rules)
            return _rule_dict(rule)
    return None


def delete_rule(rule_id: str) -> bool:
    if rule_id in BUILTIN_RULE_IDS:
        raise ValueError("内置规则不能删除，可以停用它")
    with _LOCK:
        rules = _ensure_rules()
        remaining = [item for item in rules if str(item.get("id")) != rule_id]
        if len(remaining) == len(rules):
            return False
        _save_rules(remaining)
    return True


def reset_builtin_rules() -> list[dict[str, Any]]:
    """恢复内置默认：重置内置规则，保留用户自定义规则。"""
    with _LOCK:
        rules = _ensure_rules()
        custom = [item for item in rules if str(item.get("id")) not in BUILTIN_RULE_IDS]
        builtin = [
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
        _save_rules(builtin + custom)
    return list_rule_dicts()


def list_rules() -> list[Rule]:
    rules = _ensure_rules()
    ordered = sorted(rules, key=lambda item: (str(item.get("scope")), int(item.get("priority") or 0), str(item.get("id"))))
    result: list[Rule] = []
    for item in ordered:
        try:
            result.append(
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
            )
        except re.error:
            # 手工改坏的正则直接跳过，避免整个扫描因此崩溃
            continue
    return result


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
    return [_item_dict(row) for row in rows]


def _item_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "seq": row["seq"],
        "group_id": row["group_id"],
        "file_id": row["file_id"],
        "category": row["category"],
        "original_path": row["original_path"],
        "target_path": row["target_path"],
        "status": row["status"],
        "message": row["message"],
        "choice": row["choice"] if "choice" in keys else "",
        "decision_source": row["decision_source"] if "decision_source" in keys else "",
        "rule_id": row["rule_id"] if "rule_id" in keys else "",
        "decided_at": row["decided_at"] if "decided_at" in keys else "",
    }


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
                "INSERT INTO operation_items (operation_id, seq, group_id, file_id, category, original_path, target_path, status, message, decision_source, rule_id, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    item.get("decision_source", ""),
                    item.get("rule_id", ""),
                    item.get("decided_at", ""),
                ),
            )
        conn.commit()
    return get_operation(operation_id)  # type: ignore[return-value]


def _decision_map(conn: sqlite3.Connection, scan_id: str | None) -> dict[str, dict[str, Any]]:
    """同一次扫描里，按 ref_id 取最新的一条决策，供执行条目继承来源。"""
    if not scan_id:
        return {}
    rows = conn.execute(
        "SELECT * FROM decisions WHERE scan_id = ? ORDER BY created_at", (scan_id,)
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[str(row["ref_id"])] = dict(row)
    return result


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
        decisions = _decision_map(conn, scan_id)
        for seq, item in enumerate(items, start=1):
            decision = decisions.get(str(item.get("file_id") or ""))
            conn.execute(
                "INSERT INTO operation_items (operation_id, seq, group_id, file_id, category, original_path, target_path, status, message, decision_source, rule_id, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    item.get("decision_source") or (decision or {}).get("source", ""),
                    item.get("rule_id") or (decision or {}).get("rule_id", ""),
                    item.get("decided_at") or (decision or {}).get("created_at", ""),
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
    choice: str | None = None,
) -> None:
    """执行过程中逐行落库，保证中断后还能续跑。"""
    with _connect() as conn:
        if choice is None:
            conn.execute(
                "UPDATE operation_items SET status = ?, target_path = ?, message = ? "
                "WHERE operation_id = ? AND seq = ?",
                (status, target_path, message, operation_id, seq),
            )
        else:
            conn.execute(
                "UPDATE operation_items SET status = ?, target_path = ?, message = ?, choice = ? "
                "WHERE operation_id = ? AND seq = ?",
                (status, target_path, message, choice, operation_id, seq),
            )
        conn.commit()


def open_connection() -> sqlite3.Connection:
    """后台执行线程复用的长连接（每个文件都新建连接在挂载盘上很贵）。"""
    return _connect()


def update_operation_item_progress(
    operation_id: str,
    seq: int,
    status: str,
    target_path: str,
    message: str,
    processed: int,
    current_item: str,
    batch_index: int,
    conn: sqlite3.Connection | None = None,
) -> None:
    """一条目执行完：条目状态和整体进度放在同一次提交里。

    之前每个文件要提交三次（执行前进度、条目状态、执行后进度），
    在 Windows 挂载盘上每次提交都会 fsync，等于每个文件多花两份冤枉时间。
    传入 conn 时复用同一个连接（执行线程用一条连接跑完整批，省掉反复开关库的开销）。
    """

    def write(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE operation_items SET status = ?, target_path = ?, message = ? "
            "WHERE operation_id = ? AND seq = ?",
            (status, target_path, message, operation_id, seq),
        )
        connection.execute(
            "UPDATE operations SET processed_items = ?, current_item = ?, batch_index = ? WHERE id = ?",
            (processed, current_item, batch_index, operation_id),
        )
        connection.commit()

    if conn is not None:
        write(conn)
        return
    with _connect() as own:
        write(own)


def insert_operation_items(operation_id: str, items: list[dict[str, Any]]) -> list[int]:
    """冲突解决时把文件夹合并展开成逐文件条目，接在已有编号后面。"""
    if not items:
        return []
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(seq) AS last FROM operation_items WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        next_seq = int((row["last"] if row and row["last"] is not None else 0) + 1)
        seqs: list[int] = []
        for item in items:
            conn.execute(
                "INSERT INTO operation_items (operation_id, seq, group_id, file_id, category, original_path, target_path, status, message, choice, decision_source, rule_id, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    next_seq,
                    item.get("group_id"),
                    item.get("file_id"),
                    item.get("category"),
                    item.get("original_path", ""),
                    item.get("target_path", ""),
                    item.get("status", "planned"),
                    item.get("message", ""),
                    item.get("choice", ""),
                    item.get("decision_source", ""),
                    item.get("rule_id", ""),
                    item.get("decided_at", ""),
                ),
            )
            seqs.append(next_seq)
            next_seq += 1
        conn.execute(
            "UPDATE operations SET total_items = total_items + ? WHERE id = ?",
            (len(items), operation_id),
        )
        conn.commit()
    return seqs


def update_operation_summary(operation_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """在保留原有摘要的前提下追加信息（例如冲突解决的统计）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT summary FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        try:
            summary = json.loads((row["summary"] if row else "") or "{}")
        except (TypeError, ValueError):
            summary = {}
        summary.update(patch)
        conn.execute(
            "UPDATE operations SET summary = ? WHERE id = ?",
            (json.dumps(summary, ensure_ascii=False), operation_id),
        )
        conn.commit()
    return summary


def operation_conflict_items(operation_id: str) -> list[dict[str, Any]]:
    """取还没处理的冲突条目（第一轮执行结束后统一展示）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_items WHERE operation_id = ? AND status = 'conflict' ORDER BY seq",
            (operation_id,),
        ).fetchall()
    return [_item_dict(row) for row in rows]


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


FINAL_ITEM_STATUS = ("done", "skipped", "failed", "conflict", "merged")


def operation_item_counts(operation_id: str) -> dict[str, int]:
    """统计各状态条目数量（进度条使用，不返回明细）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM operation_items WHERE operation_id = ? GROUP BY status",
            (operation_id,),
        ).fetchall()
    counts = {"done": 0, "skipped": 0, "failed": 0, "conflict": 0, "planned": 0, "merged": 0}
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
        "merged": counts["merged"],
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
            "choice": row["choice"] if "choice" in row.keys() else "",
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
            "category": row["category"],
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
        decision_rows = conn.execute(
            "SELECT operation_id, decision_source, COUNT(*) AS total FROM operation_items "
            "GROUP BY operation_id, decision_source"
        ).fetchall()
    decisions: dict[str, dict[str, int]] = {}
    for item in decision_rows:
        bucket = decisions.setdefault(
            item["operation_id"], {"human": 0, "rule": 0, "ai": 0, "unknown": 0}
        )
        key = str(item["decision_source"] or "")
        if key in bucket:
            bucket[key] += int(item["total"])
        else:
            bucket["unknown"] += int(item["total"])
    operations = []
    for row in rows:
        data = _operation_from_row(row)
        data["item_count"] = counts.get(row["id"], 0)
        data["decision_counts"] = decisions.get(
            row["id"], {"human": 0, "rule": 0, "ai": 0, "unknown": 0}
        )
        operations.append(data)
    return operations


def mark_operation_status(operation_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE operations SET status = ?, undone_at = ? WHERE id = ?",
            (status, _now() if status == "undone" else None, operation_id),
        )
        conn.commit()


# ---------------------------------------------------------------- 运行记录


RUN_STAGES = RUN_STAGE_ORDER
RUN_STAGE_LABELS = {
    "scan": "扫描",
    "match": "匹配作品",
    "preview": "模板与预览",
    "execute": "执行",
    "done": "完成",
}


def _run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        groups = json.loads(row["groups"] or "[]")
    except (TypeError, ValueError):
        groups = []
    try:
        snapshot = json.loads(row["snapshot"] or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    stage = str(row["stage"] or "scan")
    return {
        "id": row["id"],
        "scan_id": row["scan_id"],
        "stage": stage,
        "stage_label": RUN_STAGE_LABELS.get(stage, stage),
        "status": row["status"],
        "preset_id": row["preset_id"],
        "operation_id": row["operation_id"],
        "groups": groups if isinstance(groups, list) else [],
        "snapshot": snapshot if isinstance(snapshot, dict) else {},
        "lock_owner": row["lock_owner"],
        "lock_expires_at": row["lock_expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def ensure_run(
    scan_id: str,
    groups: list[dict[str, Any]] | None = None,
    preset_id: str | None = None,
    naming_preset_id: str | None = None,
) -> dict[str, Any]:
    """取这次扫描对应的运行；没有就新建一条。一次扫描从开始到完成算一次运行。

    本次运行使用的命名预设记在 snapshot.naming_preset_id：自动推进时以此为准，
    中途在界面上换预设不会让前后步骤用不同的命名规则。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE scan_id = ? AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if row is not None:
            updates: list[str] = []
            values: list[Any] = []
            if groups and not json.loads(row["groups"] or "[]"):
                updates.append("groups = ?")
                values.append(json.dumps(groups, ensure_ascii=False))
            try:
                snapshot = json.loads(row["snapshot"] or "{}")
            except (TypeError, ValueError):
                snapshot = {}
            # 进入执行阶段后不再改这个记录：避免自动推进中途换预设导致前后不一致。
            if (
                naming_preset_id
                and snapshot.get("naming_preset_id") != naming_preset_id
                and row["stage"] in ("scan", "match", "preview")
            ):
                snapshot["naming_preset_id"] = naming_preset_id
                updates.append("snapshot = ?")
                values.append(json.dumps(snapshot, ensure_ascii=False))
            if updates:
                updates.append("updated_at = ?")
                values.append(_now())
                values.append(row["id"])
                conn.execute(
                    f"UPDATE runs SET {', '.join(updates)} WHERE id = ?", values
                )
                conn.commit()
                row = _run_row(conn, row["id"])
            return _run_from_row(row)  # type: ignore[arg-type]
        now = _now()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        snapshot = {"naming_preset_id": naming_preset_id} if naming_preset_id else {}
        conn.execute(
            "INSERT INTO runs (id, scan_id, stage, status, preset_id, groups, snapshot, created_at, updated_at) "
            "VALUES (?, ?, 'scan', 'active', ?, ?, ?, ?, ?)",
            (
                run_id,
                scan_id,
                preset_id,
                json.dumps(groups or [], ensure_ascii=False),
                json.dumps(snapshot, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        row = _run_row(conn, run_id)
    return _run_from_row(row)  # type: ignore[arg-type]


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = _run_row(conn, run_id)
    return _run_from_row(row) if row is not None else None


def run_for_scan(scan_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE scan_id = ? AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
    return _run_from_row(row) if row is not None else None


def current_run() -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return _run_from_row(row) if row is not None else None


def update_run(
    run_id: str,
    stage: str | None = None,
    snapshot_patch: dict[str, Any] | None = None,
    preset_id: str | None = None,
    operation_id: str | None = None,
    status: str | None = None,
    event_source: str = "human",
    event_rule_id: str = "",
    actor: str = "",
) -> dict[str, Any] | None:
    """推进运行阶段。阶段只前进不后退，避免来回切页面把“上次停在哪”冲掉。"""
    with _connect() as conn:
        row = _run_row(conn, run_id)
        if row is None:
            return None
        current_stage = str(row["stage"] or "scan")
        next_stage = current_stage
        if stage in RUN_STAGES:
            if RUN_STAGES.index(stage) > RUN_STAGES.index(current_stage) or current_stage == "done":
                next_stage = stage if current_stage != "done" else current_stage
        try:
            snapshot = json.loads(row["snapshot"] or "{}")
        except (TypeError, ValueError):
            snapshot = {}
        if snapshot_patch:
            for key, value in snapshot_patch.items():
                snapshot[key] = value
        now = _now()
        fields = ["stage = ?", "snapshot = ?", "updated_at = ?"]
        values: list[Any] = [next_stage, json.dumps(snapshot, ensure_ascii=False), now]
        if preset_id is not None:
            fields.append("preset_id = ?")
            values.append(preset_id)
        if operation_id is not None:
            fields.append("operation_id = ?")
            values.append(operation_id)
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        values.append(run_id)
        conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)
        if stage in RUN_STAGES and next_stage != current_stage:
            append_run_event(
                run_id,
                "stage",
                f"阶段：{RUN_STAGE_LABELS.get(current_stage, current_stage)} → "
                f"{RUN_STAGE_LABELS.get(next_stage, next_stage)}",
                stage=next_stage,
                detail={"from": current_stage, "to": next_stage},
                source=event_source,
                rule_id=event_rule_id,
                actor=actor,
                scan_id=str(row["scan_id"] or ""),
                conn=conn,
            )
        conn.commit()
        row = _run_row(conn, run_id)
    return _run_from_row(row)  # type: ignore[arg-type]


def claim_run(run_id: str, owner: str, ttl_seconds: int = 3600) -> bool:
    """简单租约：同一时间只允许一个推进者。租约过期后其他人可以接手。"""
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE runs SET lock_owner = ?, lock_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND (lock_owner = '' OR lock_expires_at = '' "
            "OR lock_expires_at <= ? OR lock_owner = ?)",
            (owner, _run_deadline(ttl_seconds), now, run_id, now, owner),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0


def release_run(run_id: str, owner: str = "") -> None:
    with _connect() as conn:
        if owner:
            conn.execute(
                "UPDATE runs SET lock_owner = '', lock_expires_at = '', updated_at = ? "
                "WHERE id = ? AND lock_owner = ?",
                (_now(), run_id, owner),
            )
        else:
            conn.execute(
                "UPDATE runs SET lock_owner = '', lock_expires_at = '', updated_at = ? WHERE id = ?",
                (_now(), run_id),
            )
        conn.commit()


def clear_run_locks() -> None:
    """进程刚启动时没有任何后台推进者，所有租约都是上次遗留的，直接清空。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET lock_owner = '', lock_expires_at = '' WHERE lock_owner != ''"
        )
        conn.commit()


def _run_deadline(ttl_seconds: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=max(5, ttl_seconds))).isoformat()


# ---------------------------------------------------------------- 运行事件流水


RUN_EVENT_KIND_LABELS = {
    "stage": "阶段切换",
    "human": "人工确认",
    "rule": "规则自动",
    "ai": "AI 决策",
    "gate": "门槛检查",
    "authorization": "自动执行授权",
    "execute": "执行",
}


def _run_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        detail = json.loads(row["detail"] or "{}")
    except (TypeError, ValueError):
        detail = {}
    kind = str(row["kind"] or "")
    stage = str(row["stage"] or "")
    source = str(row["source"] or "")
    return {
        "id": int(row["id"]),
        "run_id": row["run_id"],
        "scan_id": row["scan_id"],
        "kind": kind,
        "kind_label": RUN_EVENT_KIND_LABELS.get(kind, kind),
        "stage": stage,
        "stage_label": RUN_STAGE_LABELS.get(stage, stage),
        "title": row["title"],
        "detail": detail if isinstance(detail, dict) else {},
        "source": source,
        "source_label": DECISION_SOURCE_LABELS.get(source, "未知"),
        "rule_id": row["rule_id"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def append_run_event(
    run_id: str,
    kind: str,
    title: str,
    *,
    stage: str = "",
    detail: dict[str, Any] | None = None,
    source: str = "human",
    rule_id: str = "",
    actor: str = "",
    scan_id: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """追加一条运行事件。自增 id 就是时间顺序，按它回放即可还原这次运行。"""
    now = _now()
    payload = json.dumps(detail or {}, ensure_ascii=False)
    sql = (
        "INSERT INTO run_events (run_id, scan_id, kind, stage, title, detail, source, rule_id, "
        "actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    values = (run_id, scan_id, kind, stage, title, payload, source, rule_id, actor, now)
    if conn is None:
        with _connect() as own:
            cursor = own.execute(sql, values)
            own.commit()
    else:
        cursor = conn.execute(sql, values)
    return {
        "id": int(cursor.lastrowid or 0),
        "run_id": run_id,
        "scan_id": scan_id,
        "kind": kind,
        "kind_label": RUN_EVENT_KIND_LABELS.get(kind, kind),
        "stage": stage,
        "stage_label": RUN_STAGE_LABELS.get(stage, stage),
        "title": title,
        "detail": detail or {},
        "source": source,
        "source_label": DECISION_SOURCE_LABELS.get(source, "未知"),
        "rule_id": rule_id,
        "actor": actor,
        "created_at": now,
    }


def list_run_events(run_id: str, limit: int = 500) -> list[dict[str, Any]]:
    """按自增 id 正序返回，保证是这次运行真实发生的顺序。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    return [_run_event_from_row(row) for row in rows]


# ---------------------------------------------------------------- 待人工清单


def _pending_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    kind = str(row["kind"] or "")
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "scan_id": row["scan_id"],
        "kind": kind,
        "kind_label": PENDING_KIND_LABELS.get(kind, kind),
        "ref_type": row["ref_type"],
        "ref_id": row["ref_id"],
        "title": row["title"],
        "reason": row["reason"],
        "blocking": bool(row["blocking"]),
        "status": row["status"],
        "resolved_by": row["resolved_by"],
        "resolved_at": row["resolved_at"],
        "decision_source": row["decision_source"],
        "decision_source_label": DECISION_SOURCE_LABELS.get(
            str(row["decision_source"] or ""), "未知"
        ),
        "meta": meta if isinstance(meta, dict) else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


PENDING_KIND_LABELS = {
    "unassigned": "未归属文件",
    "season": "季号未定",
    "conflict": "冲突未选",
    "special": "特典归属待确认",
    "preview_warning": "预览警告",
    "preview_error": "预览错误",
}


def list_pending_items(run_id: str, include_resolved: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        if include_resolved:
            rows = conn.execute(
                "SELECT * FROM pending_items WHERE run_id = ? ORDER BY created_at", (run_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pending_items WHERE run_id = ? AND status = 'open' "
                "ORDER BY blocking DESC, created_at",
                (run_id,),
            ).fetchall()
    return [_pending_from_row(row) for row in rows]


def pending_counts(run_id: str) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM pending_items WHERE run_id = ? GROUP BY status",
            (run_id,),
        ).fetchall()
    counts = {"open": 0, "resolved": 0, "blocking": 0}
    for row in rows:
        counts[str(row["status"])] = int(row["total"])
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM pending_items "
            "WHERE run_id = ? AND status = 'open' AND blocking = 1",
            (run_id,),
        ).fetchone()
    counts["blocking"] = int(row["total"] if row else 0)
    counts["total"] = counts["open"] + counts["resolved"]
    return counts


def sync_pending_items(run_id: str, scan_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用最新计算结果对齐清单：新问题加进来，已经处理掉的问题自动关闭。"""
    now = _now()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM pending_items WHERE run_id = ?", (run_id,)).fetchall()
        existing = {(str(row["kind"]), str(row["ref_id"])): row for row in rows}
        seen: set[tuple[str, str]] = set()
        for item in items:
            kind = str(item.get("kind") or "")
            ref_id = str(item.get("ref_id") or "")
            if not kind or not ref_id:
                continue
            key = (kind, ref_id)
            seen.add(key)
            row = existing.get(key)
            meta = json.dumps(item.get("meta") or {}, ensure_ascii=False)
            if row is None:
                conn.execute(
                    "INSERT INTO pending_items (id, run_id, scan_id, kind, ref_type, ref_id, title, reason, "
                    "blocking, status, decision_source, meta, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
                    (
                        f"pd-{uuid.uuid4().hex[:12]}",
                        run_id,
                        scan_id,
                        kind,
                        str(item.get("ref_type") or ""),
                        ref_id,
                        str(item.get("title") or ""),
                        str(item.get("reason") or ""),
                        1 if item.get("blocking") else 0,
                        str(item.get("decision_source") or ""),
                        meta,
                        now,
                        now,
                    ),
                )
                continue
            conn.execute(
                "UPDATE pending_items SET scan_id = ?, ref_type = ?, title = ?, reason = ?, "
                "blocking = ?, status = 'open', resolved_by = '', resolved_at = '', "
                "decision_source = ?, meta = ?, updated_at = ? WHERE id = ?",
                (
                    scan_id,
                    str(item.get("ref_type") or ""),
                    str(item.get("title") or ""),
                    str(item.get("reason") or ""),
                    1 if item.get("blocking") else 0,
                    str(item.get("decision_source") or ""),
                    meta,
                    now,
                    row["id"],
                ),
            )
        for key, row in existing.items():
            if key in seen or str(row["status"]) != "open":
                continue
            conn.execute(
                "UPDATE pending_items SET status = 'resolved', resolved_by = ?, resolved_at = ?, "
                "updated_at = ? WHERE id = ?",
                ("系统自动", now, now, row["id"]),
            )
        conn.commit()
    return list_pending_items(run_id)


# ---------------------------------------------------------------- 决策来源


DECISION_SOURCE_LABELS = {
    "human": "人工确认",
    "rule": "规则自动",
    "ai": "AI 决策（预留）",
    "": "未知",
}

DECISION_KIND_LABELS = {
    "assign": "指派作品",
    "match": "TMDB 匹配",
    "rename": "手动改名",
    "season": "季号选择",
    "conflict": "冲突处理",
    "auto_authorize": "自动执行授权",
    "auto_execute": "自动执行",
    "advance": "推进运行",
}


def record_decision(
    scan_id: str,
    kind: str,
    ref_id: str,
    choice: str = "",
    source: str = "human",
    rule_id: str = "",
    note: str = "",
    operation_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """记一条“谁拍的板”。source 预留 ai，以后接 AI 只多一个取值，不改表结构。"""
    if source not in ("human", "rule", "ai"):
        source = "human"
    if not run_id:
        run = ensure_run(scan_id) if scan_id else None
        run_id = str((run or {}).get("id") or "")
    decision_id = f"dc-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO decisions (id, run_id, scan_id, operation_id, kind, ref_id, choice, source, rule_id, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                run_id,
                scan_id,
                operation_id,
                kind,
                ref_id,
                choice,
                source,
                rule_id,
                note,
                now,
            ),
        )
        if run_id:
            append_run_event(
                run_id,
                source,
                f"{DECISION_KIND_LABELS.get(kind, kind)}（{DECISION_SOURCE_LABELS.get(source, '未知')}）",
                detail={
                    "kind": kind,
                    "ref_id": ref_id,
                    "choice": choice,
                    "operation_id": operation_id,
                    "note": note,
                },
                source=source,
                rule_id=rule_id,
                scan_id=scan_id,
                conn=conn,
            )
        conn.commit()
    return {
        "id": decision_id,
        "run_id": run_id,
        "scan_id": scan_id,
        "operation_id": operation_id,
        "kind": kind,
        "ref_id": ref_id,
        "choice": choice,
        "source": source,
        "source_label": DECISION_SOURCE_LABELS.get(source, "未知"),
        "rule_id": rule_id,
        "note": note,
        "created_at": now,
    }


def list_decisions(scan_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with _connect() as conn:
        if scan_id:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE scan_id = ? ORDER BY created_at DESC LIMIT ?",
                (scan_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["source_label"] = DECISION_SOURCE_LABELS.get(str(row["source"] or ""), "未知")
        result.append(data)
    return result


def update_operation_item_decision(
    operation_id: str, seq: int, source: str, rule_id: str = "", decided_at: str = ""
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE operation_items SET decision_source = ?, rule_id = ?, decided_at = ? "
            "WHERE operation_id = ? AND seq = ?",
            (source, rule_id, decided_at or _now(), operation_id, seq),
        )
        conn.commit()


def operation_decision_counts(operation_id: str) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT decision_source, COUNT(*) AS total FROM operation_items "
            "WHERE operation_id = ? GROUP BY decision_source",
            (operation_id,),
        ).fetchall()
    counts = {"human": 0, "rule": 0, "ai": 0, "unknown": 0}
    for row in rows:
        key = str(row["decision_source"] or "")
        if key in counts:
            counts[key] += int(row["total"])
        else:
            counts["unknown"] += int(row["total"])
    return counts


# ---------------------------------------------------------------- 自动执行授权


AUTO_AUTH_STATUS_LABELS = {
    "granted": "已授权",
    "consumed": "已使用",
    "revoked": "已撤销",
}


def _auto_auth_from_row(row: sqlite3.Row) -> dict[str, Any]:
    status = str(row["status"] or "")
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "scan_id": row["scan_id"],
        "operation_id": row["operation_id"],
        "confirmed_by": row["confirmed_by"],
        "risk_version": row["risk_version"],
        "risk_text": row["risk_text"],
        "gate_checked_at": row["gate_checked_at"],
        "status": status,
        "status_label": AUTO_AUTH_STATUS_LABELS.get(status, status),
        "created_at": row["created_at"],
        "consumed_at": row["consumed_at"],
    }


def create_auto_authorization(
    *,
    run_id: str,
    scan_id: str = "",
    confirmed_by: str,
    risk_version: str,
    risk_text: str = "",
    gate_checked_at: str = "",
) -> dict[str, Any]:
    """每次自动执行单独生成一条授权；确认人、时间、风险文案版本、运行编号都留档。"""
    name = str(confirmed_by or "").strip()
    if not name:
        raise ValueError("必须填写确认人")
    if not str(run_id or "").strip():
        raise ValueError("授权必须绑定到一次运行")
    auth_id = f"au-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO auto_authorizations "
            "(id, run_id, scan_id, confirmed_by, risk_version, risk_text, gate_checked_at, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'granted', ?)",
            (auth_id, run_id, scan_id, name, risk_version, risk_text, gate_checked_at, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM auto_authorizations WHERE id = ?", (auth_id,)
        ).fetchone()
    return _auto_auth_from_row(row)  # type: ignore[arg-type]


def get_auto_authorization(auth_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM auto_authorizations WHERE id = ?", (auth_id,)
        ).fetchone()
    return _auto_auth_from_row(row) if row is not None else None


def list_auto_authorizations(run_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM auto_authorizations WHERE run_id = ? ORDER BY created_at DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    return [_auto_auth_from_row(row) for row in rows]


def latest_granted_authorization(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM auto_authorizations WHERE run_id = ? AND status = 'granted' "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return _auto_auth_from_row(row) if row is not None else None


def consume_auto_authorization(auth_id: str) -> bool:
    """原子消费：同一授权只能放行一次自动执行。"""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE auto_authorizations SET status = 'consumed', consumed_at = ? "
            "WHERE id = ? AND status = 'granted'",
            (_now(), auth_id),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0


def restore_auto_authorization(auth_id: str) -> None:
    """执行没有真正启动（例如预检要求确认）时，把刚消费的授权退回可用状态。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE auto_authorizations SET status = 'granted', consumed_at = '' "
            "WHERE id = ? AND status = 'consumed'",
            (auth_id,),
        )
        conn.commit()


def attach_auto_authorization_operation(auth_id: str, operation_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE auto_authorizations SET operation_id = ? WHERE id = ?",
            (operation_id, auth_id),
        )
        conn.commit()


# ---------------------------------------------------------------- 扫描结果持久化


def _scan_for_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """只存必要内容：去掉每个文件的临时核对结果，其余字段扫描恢复时都要用到。"""
    data = dict(payload)
    data.pop("restored", None)
    data.pop("restored_at", None)
    files: list[dict[str, Any]] = []
    for row in data.get("files") or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.pop("check", None)
        files.append(item)
    data["files"] = files
    return data


def _cleanup_scan_records() -> None:
    """清理旧扫描：超过保留次数或超过有效期的记录直接删掉。"""
    from datetime import timedelta

    with _connect() as conn:
        conn.execute(
            "DELETE FROM scans WHERE scan_id IN ("
            "  SELECT scan_id FROM scans ORDER BY created_at DESC LIMIT -1 OFFSET ?"
            ")",
            (SCAN_KEEP_LATEST,),
        )
        expired = (datetime.now(timezone.utc) - timedelta(days=SCAN_TTL_DAYS)).isoformat()
        conn.execute("DELETE FROM scans WHERE created_at < ?", (expired,))
        conn.commit()


def save_scan_record(payload: dict[str, Any]) -> None:
    """把扫描会话写入 SQLite；容器重启后可以接着用。"""
    scan_id = str(payload.get("scan_id") or "")
    if not scan_id:
        return
    data = _scan_for_storage(payload)
    now = _now()
    created_at = str(payload.get("created_at") or now)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scans (scan_id, created_at, updated_at, file_count, data) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scan_id) DO UPDATE SET "
            "data = excluded.data, updated_at = excluded.updated_at, file_count = excluded.file_count",
            (scan_id, created_at, now, len(data.get("files") or []), json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    _cleanup_scan_records()


def load_scan_record(scan_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT data FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def delete_scan_record(scan_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM scans WHERE scan_id = ?", (scan_id,))
        conn.commit()
        return bool(cursor.rowcount)


def list_scan_records(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, created_at, updated_at, file_count FROM scans "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
