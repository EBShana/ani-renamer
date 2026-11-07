from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("APP_CONFIG_DIR") or (BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("APP_DB_PATH") or (CONFIG_DIR / "app.db"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

CONFIG_VERSION = 3

_lock = threading.Lock()


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    type: str
    default: Any
    group: str
    description: str = ""
    options: tuple[str, ...] = ()


SETTING_GROUPS: list[tuple[str, str]] = [
    ("parse", "识别"),
    ("naming", "命名"),
    ("providers", "元数据源"),
    ("execution", "执行"),
    ("scraper", "刮削（预留）"),
]

SETTING_SPECS: list[SettingSpec] = [
    SettingSpec(
        "parse.default_season",
        "默认季号",
        "int",
        1,
        "parse",
        "无法从目录或文件名识别季信息时使用的季号",
    ),
    SettingSpec(
        "parse.special_season_zero",
        "特典归入第 0 季",
        "bool",
        True,
        "parse",
        "特典按 Season 0 处理，便于媒体库识别",
    ),
    SettingSpec(
        "parse.generic_dir_names",
        "通用目录名（逗号分隔）",
        "string",
        "Anime,动画,番剧,TV,Movies,Media,media,Videos,Downloads,Download,特典,SPs,Specials,Scans,CDs",
        "parse",
        "这些目录名不会被当作作品标题",
    ),
    SettingSpec(
        "parse.min_title_length",
        "标题最短长度",
        "int",
        2,
        "parse",
        "清洗后短于该长度的目录名会被忽略",
    ),
    SettingSpec(
        "parse.scan_all_files",
        "扫描所有文件类型",
        "bool",
        True,
        "parse",
        "开启后字幕、音频、图片、压缩包也会列出（非视频文件默认不改名）",
    ),
    SettingSpec(
        "parse.ignore_special_dirs",
        "忽略特典类目录",
        "bool",
        True,
        "parse",
        "扫描时跳过下列目录，避免把 CD、扫图等杂项当作剧集",
    ),
    SettingSpec(
        "parse.ova_policy",
        "OVA 处理方式",
        "select",
        "move",
        "parse",
        "move=归到 Season 00（第 0 季）随路径模板移动；ignore=原地跳过并写入忽略标记",
        ("move", "ignore"),
    ),
    SettingSpec(
        "parse.special_dir_names",
        "特典类目录名（逗号分隔）",
        "string",
        "SPs,Specials,特典,映像特典,CDs,Scans,Bonus,Extras,Menu,PV,CM,Preview,Trailer",
        "parse",
        "命中这些名字的目录会被忽略（不区分大小写）",
    ),
    SettingSpec(
        "parse.ignore_marker_files",
        "忽略标记文件名（逗号分隔）",
        "string",
        ".ignore,.nomedia",
        "parse",
        "写入被忽略目录的空文件；Jellyfin/Emby 认 .ignore，Kodi 认 .nomedia",
    ),
    SettingSpec(
        "parse.write_ignore_markers",
        "自动写入忽略标记",
        "bool",
        True,
        "parse",
        "扫描时在特典类目录里创建空标记文件（已存在则跳过）",
    ),
    SettingSpec(
        "naming.subtitle_mode",
        "字幕处理方式",
        "select",
        "follow",
        "naming",
        "follow=跟随对应视频重命名并保留语言后缀；keep=保持原名",
        ("follow", "keep"),
    ),
    SettingSpec(
        "naming.auto_append_ext",
        "自动补全扩展名",
        "bool",
        True,
        "naming",
        "模板未包含 {ext} 时自动追加原扩展名",
    ),
    SettingSpec(
        "naming.max_filename_bytes",
        "文件名最大字节数",
        "int",
        240,
        "naming",
        "超出后截断，保证跨平台可写",
    ),
    SettingSpec(
        "naming.specials_dir",
        "特典季目录名",
        "string",
        "Season 00",
        "naming",
        "特典文件放进哪个季目录，可自定义；默认 Season 00（主流媒体库推荐）",
    ),
    SettingSpec(
        "naming.forbidden_chars",
        "强制移除字符",
        "string",
        '<>:"/\\|?*',
        "naming",
        "Windows / SMB 不允许的字符，重命名时会自动移除",
    ),
    SettingSpec(
        "naming.preserve_structure",
        "只重命名文件（保持原目录结构）",
        "bool",
        False,
        "naming",
        "勾选后不生成作品文件夹和季目录，源目录里的相对结构原样映射到目标目录，只改文件名",
    ),
    SettingSpec(
        "naming.movie_layout",
        "剧场版放在哪里",
        "select",
        "folder",
        "naming",
        "folder=放在作品文件夹内；special=按特典方式放进特典季目录",
        ("folder", "special"),
    ),
    SettingSpec(
        "providers.proxy_url",
        "外部网络代理",
        "string",
        "",
        "providers",
        "填写 HTTP/HTTPS 代理地址，例如 http://127.0.0.1:7890；留空表示直连。"
        "如果代理跑在 Windows 宿主机上、程序在 Docker 容器里，请填 "
        "http://host.docker.internal:7890（不能填 127.0.0.1）。",
    ),
    SettingSpec(
        "providers.tmdb_api_key",
        "TMDB API Key",
        "secret",
        "",
        "providers",
        "M2 匹配剧集时使用，可在 tmdb.org 免费申请",
    ),
    SettingSpec(
        "providers.tmdb_lang",
        "TMDB 语言",
        "string",
        "zh-CN",
        "providers",
        "例如 zh-CN / ja-JP / en-US",
    ),
    SettingSpec(
        "providers.bangumi_token",
        "Bangumi Token",
        "secret",
        "",
        "providers",
        "可选，用于提高 Bangumi 请求限额",
    ),
    SettingSpec(
        "execution.conflict_policy",
        "冲突处理",
        "select",
        "skip",
        "execution",
        "目标文件已存在时的策略",
        ("skip", "overwrite", "suffix"),
    ),
    SettingSpec(
        "execution.keep_empty_dirs",
        "保留空目录",
        "bool",
        False,
        "execution",
        "移动文件后是否保留已清空的源目录（默认关闭，空目录会被自动清理）",
    ),
    SettingSpec(
        "execution.asset_policy",
        "特典等其余文件处理",
        "select",
        "move",
        "execution",
        "move=扫描时跳过的特典目录、压缩包等一并移到作品目录；keep=保持原处",
        ("move", "keep"),
    ),
    SettingSpec(
        "scraper.enabled",
        "启用刮削（预留）",
        "bool",
        False,
        "scraper",
        "M4 功能，当前版本不生效",
    ),
]


def _defaults() -> dict[str, Any]:
    return {spec.key: spec.default for spec in SETTING_SPECS}


def load_settings() -> dict[str, Any]:
    values = _defaults()
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            stored = raw.get("values") if isinstance(raw, dict) else None
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if key in values:
                        values[key] = value
        except (json.JSONDecodeError, OSError):
            pass
    return values


# 预设选项快照与全局设置的字段映射（预设选项 key -> 全局设置 key）。
# 从 v2 起，预设不再在运行时覆盖设置；这张表只用于“保存预设快照”和“应用预设设置”。
PRESET_OPTION_KEYS: dict[str, str] = {
    "subtitle_mode": "naming.subtitle_mode",
    "auto_append_ext": "naming.auto_append_ext",
    "max_filename_bytes": "naming.max_filename_bytes",
    "forbidden_chars": "naming.forbidden_chars",
    "specials_dir": "naming.specials_dir",
    "preserve_structure": "naming.preserve_structure",
    "movie_layout": "naming.movie_layout",
    "tmdb_lang": "providers.tmdb_lang",
    "scan_all_files": "parse.scan_all_files",
    "ignore_special_dirs": "parse.ignore_special_dirs",
    "ova_policy": "parse.ova_policy",
    "special_dir_names": "parse.special_dir_names",
    "write_ignore_markers": "parse.write_ignore_markers",
    "default_season": "parse.default_season",
    "special_season_zero": "parse.special_season_zero",
    "conflict_policy": "execution.conflict_policy",
    "keep_empty_dirs": "execution.keep_empty_dirs",
    "asset_policy": "execution.asset_policy",
}


def default_preset_options() -> dict[str, Any]:
    """把全局默认值变成一份预设选项，用于首次生成预设。"""
    defaults = _defaults()
    return {option: defaults[setting] for option, setting in PRESET_OPTION_KEYS.items()}


def settings_to_preset_options(values: dict[str, Any]) -> dict[str, Any]:
    """把当前设置打包成一份预设选项快照（保存预设时使用）。"""
    specs = {spec.key: spec for spec in SETTING_SPECS}
    return {
        option: values.get(setting, specs[setting].default)
        for option, setting in PRESET_OPTION_KEYS.items()
    }


def setting_values_from_preset_options(options: dict[str, Any]) -> dict[str, Any]:
    """把预设选项快照转回设置键值（切换预设或迁移旧配置时使用）。"""
    specs = {spec.key: spec for spec in SETTING_SPECS}
    values: dict[str, Any] = {}
    for option, setting in PRESET_OPTION_KEYS.items():
        if option not in options:
            continue
        value = options[option]
        if value is None or value == "":
            continue
        spec = specs.get(setting)
        values[setting] = _coerce(spec, value) if spec else value
    return values


def _coerce(spec: SettingSpec, value: Any) -> Any:
    if spec.type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return spec.default
    if spec.type == "bool":
        return bool(value)
    if spec.type == "select":
        return value if value in spec.options else spec.default
    return "" if value is None else str(value)


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = load_settings()
        by_key = {spec.key: spec for spec in SETTING_SPECS}
        for key, value in values.items():
            spec = by_key.get(key)
            if spec is not None:
                current[key] = _coerce(spec, value)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"version": CONFIG_VERSION, "values": current}
        CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return current


def get_media_root() -> Path:
    raw = os.environ.get("MEDIA_ROOT")
    if not raw:
        return Path("/media")
    return Path(raw).expanduser().resolve()


def config_schema() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "groups": [{"key": key, "label": label} for key, label in SETTING_GROUPS],
        "fields": [
            {
                "key": spec.key,
                "label": spec.label,
                "type": spec.type,
                "default": spec.default,
                "group": spec.group,
                "description": spec.description,
                "options": list(spec.options),
            }
            for spec in SETTING_SPECS
        ],
        "media_root": str(get_media_root()),
    }
