from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.lexicon import (
    DEFAULT_GENERIC_DIR_NAMES,
    DEFAULT_SPECIAL_DIR_NAMES,
    DEFAULT_SPECIALS_DIR_NAME,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("APP_CONFIG_DIR") or (BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("APP_DB_PATH") or (CONFIG_DIR / "app.db"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

CONFIG_VERSION = 4

# 特典类目录名默认值。EXTRA / Extras 这类写法也算特典目录：
# 默认（选项 A）整个目录跟着作品搬走，目录名和内部结构不变，视频/字幕不改名。
# 旧的 v3 默认值（没有 EXTRA）只用于识别“用户没改过”，升级时自动补上 EXTRA。
OLD_SPECIAL_DIR_DEFAULT = "SPs,Specials,特典,映像特典,CDs,Scans,Bonus,Extras,Menu,PV,CM,Preview,Trailer"
NEW_SPECIAL_DIR_DEFAULT = DEFAULT_SPECIAL_DIR_NAMES

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
    option_labels: tuple[str, ...] = ()
    min_value: int | None = None
    max_value: int | None = None


SETTING_GROUPS: list[tuple[str, str]] = [
    ("parse", "识别"),
    ("naming", "命名"),
    ("language", "语言（全局）"),
    ("providers", "元数据源与 AI"),
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
        DEFAULT_GENERIC_DIR_NAMES,
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
        NEW_SPECIAL_DIR_DEFAULT,
        "parse",
        "命中这些名字的目录会被当作特典文件夹，按「特典文件夹里的视频」设置处理"
        "（不区分大小写；EXTRA / Extras 也算特典目录）",
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
        "文件移动完成后，在目标目录里创建空标记文件（已存在则跳过）。"
        "特典视频选 B 时不写标记。",
    ),
    SettingSpec(
        "parse.max_files",
        "单次扫描最多文件数",
        "int",
        20000,
        "parse",
        "达到上限后会停止扫描并提示还有文件未扫描。范围 1~100000。"
        "调高会明显增加内存占用、扫描时间和后续 TMDB 请求量，建议配合扫描结果持久化使用。",
        min_value=1,
        max_value=100000,
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
        DEFAULT_SPECIALS_DIR_NAME,
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
        "language",
        "例如 zh-CN / ja-JP / en-US。全局生效，不随命名预设或工作流预设变化。",
    ),
    SettingSpec(
        "metadata.name_language_order",
        "作品名语言优先级",
        "select",
        "cn_tw_jp_en",
        "language",
        "TMDB 没有简体中文名时，按这个顺序找替代。"
        "判断标准：简体名称为空，或与原名相同（说明 TMDB 没有简体翻译）。"
        "作品名始终取自 TMDB 返回值，AI 不参与命名。",
        ("cn_tw_jp_en", "cn_jp_tw_en", "cn_tw_en_jp"),
        option_labels=(
            "简体 → 繁体 → 日文原名 → 英文（推荐）",
            "简体 → 日文原名 → 繁体 → 英文",
            "简体 → 繁体 → 英文 → 日文原名",
        ),
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
        "providers.ai_enabled",
        "启用 AI 辅助",
        "bool",
        False,
        "providers",
        "「一键自动匹配 TMDB」卡住时（本地搜不到，或候选分不清）才会自动问 AI；"
        "AI 只提供搜索关键词或从候选里挑编号，作品名始终取 TMDB 返回值。"
        "其它 AI 功能仍然只在你手动点击时调用。只发送文件名、目录名、候选标题等文本，绝不发送文件内容。",
    ),
    SettingSpec(
        "providers.ai_base_url",
        "AI 接口地址",
        "string",
        "",
        "providers",
        "OpenAI 兼容接口地址，例如 http://host.docker.internal:11434/v1（本地 Ollama）"
        "或 https://api.openai.com/v1。留空表示未配置。",
    ),
    SettingSpec(
        "providers.ai_api_key",
        "AI API Key",
        "secret",
        "",
        "providers",
        "本地模型通常可以留空；在线服务请填写。Key 不会出现在日志、接口响应或界面提示里。",
    ),
    SettingSpec(
        "providers.ai_model",
        "AI 模型名",
        "string",
        "gpt-4o-mini",
        "providers",
        "填进请求里的 model 字段，例如 gpt-4o-mini、qwen2.5:7b。",
    ),
    SettingSpec(
        "providers.ai_batch_limit",
        "AI 每次最多处理条数",
        "int",
        10,
        "providers",
        "一次请求最多打包多少个文件或作品，避免一次问太多。范围 1~50。",
    ),
    SettingSpec(
        "providers.ai_special_enabled",
        "AI 特典归属判断",
        "bool",
        True,
        "providers",
        "开启后，「匹配作品」页的特典归属候选可以点「AI 判断」请求建议；"
        "仍然只在你手动点击时调用，AI 只给建议，不确认不生效。",
    ),
    SettingSpec(
        "providers.ai_pattern_cache_auto_apply",
        "AI 规律缓存自动套用",
        "bool",
        True,
        "providers",
        "指纹完全一致时，自动使用已确认过的 AI 命名规律，并在结果里标明「缓存命中」；"
        "指纹有任何不同都只作为候选展示。关闭后即使指纹一致也不自动套用。",
    ),
    SettingSpec(
        "execution.conflict_policy",
        "冲突处理",
        "select",
        "skip",
        "execution",
        "目标文件已存在时的策略（第 6 步起改为执行结束后统一在冲突面板处理，此设置暂不使用）",
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
        "execution.special_video_policy",
        "特典文件夹里的视频",
        "select",
        "root",
        "execution",
        "CDs、SPs、EXTRA 这类特典文件夹怎么处理。"
        "A 推荐：整个文件夹跟着作品搬走，文件夹名和内部结构不变，视频、字幕都不改名；"
        "B：把里面的视频放进 Season 00，按特典模板改名。",
        ("root", "season"),
        option_labels=(
            "A：整个特典文件夹跟作品搬走（内部不改名）",
            "B：视频放进 Season 00（按特典模板改名）",
        ),
    ),
    SettingSpec(
        "execution.special_folder_conflict",
        "多季同名素材文件夹",
        "select",
        "season",
        "execution",
        "同一部作品有多季、每季都带 CDs / Scans / SPs 时，整目录搬会撞到同一个作品目录。"
        "「加季号区分」只在确实撞名时生效，没撞名的保持原名；"
        "「合并」则让它们都进同一个文件夹（同名文件执行时按冲突策略处理）。",
        ("season", "merge"),
        option_labels=(
            "加季号区分（推荐）",
            "合并进同一个文件夹",
        ),
    ),
    SettingSpec(
        "naming.special_folder_suffix",
        "撞名素材文件夹命名格式",
        "string",
        "{name} {source_season}",
        "naming",
        "撞名时素材文件夹的新名字。可用变量：{name}=原文件夹名，{source_season}=S01，"
        "{season}=1（写 {season:02} 得到 01）。"
        "例：{name} [{source_season}] → CDs [S01]；{source_season} {name} → S01 CDs。",
    ),
    SettingSpec(
        "execution.batch_size",
        "每批处理数量",
        "int",
        100,
        "execution",
        "执行时每批处理多少个条目，再进入下一批。范围 1~1000。"
        "网盘场景建议调小（例如 10），减少单批失败重试的代价。",
        min_value=1,
        max_value=1000,
    ),
    SettingSpec(
        "execution.batch_delay_seconds",
        "批与批之间的等待秒数",
        "int",
        0,
        "execution",
        "每批结束后等待多少秒再处理下一批，避免网盘客户端同时处理太多请求。范围 0~600。",
        min_value=0,
        max_value=600,
    ),
    SettingSpec(
        "execution.retry_times",
        "失败重试次数",
        "int",
        0,
        "execution",
        "单个文件移动失败后最多再试几次（0 表示不重试）。范围 0~5。",
        min_value=0,
        max_value=5,
    ),
    SettingSpec(
        "execution.retry_backoff_seconds",
        "重试间隔秒数",
        "int",
        3,
        "execution",
        "每次重试前等待的秒数，会按次数递增（第 1 次等 1 倍、第 2 次等 2 倍……）。范围 0~60。",
        min_value=0,
        max_value=60,
    ),
    SettingSpec(
        "execution.network_drive_mode",
        "网盘模式",
        "bool",
        False,
        "execution",
        "媒体放在网盘（WebDAV / 云盘挂载）时打开，降低单位时间的读盘请求，避免触发风控。"
        "打开时会把「每批处理数量 / 批间隔 / 重试次数 / 重试间隔」改写为推荐值，之后仍可单独微调；"
        "扫描会复用目录列表、不再为标记文件额外探测，执行会去重重复的 mkdir 与空目录检查。"
        "关闭后恢复常规行为。结果、命名与撤销能力在两种模式下完全一致。",
    ),
    SettingSpec(
        "execution.auto_execute_enabled",
        "允许自动执行（每次仍需单独授权）",
        "bool",
        False,
        "execution",
        "默认关闭。打开后也只是一个前置开关：每次自动执行都必须另行生成一条本次运行的授权，"
        "且预览零警告、零冲突、零未归属、季号全部确定四项门槛必须全绿。"
        "找不到授权或门槛未通过都会拒绝执行，没有强制跳过。",
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


# 打开「网盘模式」时一次性写入的低 IO 推荐值；之后用户可单独修改，不会被反复覆盖。
NETWORK_DRIVE_DEFAULTS: dict[str, Any] = {
    "execution.batch_size": 10,
    "execution.batch_delay_seconds": 2,
    "execution.retry_times": 1,
    "execution.retry_backoff_seconds": 10,
}


def load_settings() -> dict[str, Any]:
    values = _defaults()
    stored_version = 0
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                try:
                    stored_version = int(raw.get("version") or 0)
                except (TypeError, ValueError):
                    stored_version = 0
            stored = raw.get("values") if isinstance(raw, dict) else None
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if key in values:
                        values[key] = value
        except (json.JSONDecodeError, OSError):
            pass
    # 只升级“完全没改过”的旧默认值：用户自己改过的列表保持原样。
    if str(values.get("parse.special_dir_names") or "").strip() == OLD_SPECIAL_DIR_DEFAULT:
        values["parse.special_dir_names"] = NEW_SPECIAL_DIR_DEFAULT
    if stored_version < 4 and values.get("providers.ai_special_enabled") is False:
        # v3 及更早的「AI 特典识别」只是占位开关、从未生效，升级后默认打开（仍只手动触发）
        values["providers.ai_special_enabled"] = True
    return values


# ---------------------------------------------------------------------------
# 两层预设
#
# 命名预设：3 条模板 + 13 个“命名与目录摆放”选项。
#   8 项 naming.*：字幕、扩展名、文件名长度、禁用字符、特典季目录、保留原结构、
#   剧场版落点、撞名素材文件夹命名。
#   5 项跨边界：默认季号、特典归入第 0 季、OVA 处理方式、特典文件夹里的视频、
#   多季同名素材文件夹——它们直接决定文件/目录落在哪，和模板一起编辑更顺手。
#
# 工作流预设：其余 23 项（识别与扫描、元数据源与 AI、执行、刮削）。
#
# providers.tmdb_lang 与 metadata.name_language_order 是全局语言设置，
# 两层预设都不再保存，切换预设不会改变它们。
# ---------------------------------------------------------------------------
NAMING_PRESET_OPTION_KEYS: dict[str, str] = {
    "subtitle_mode": "naming.subtitle_mode",
    "auto_append_ext": "naming.auto_append_ext",
    "max_filename_bytes": "naming.max_filename_bytes",
    "forbidden_chars": "naming.forbidden_chars",
    "specials_dir": "naming.specials_dir",
    "preserve_structure": "naming.preserve_structure",
    "movie_layout": "naming.movie_layout",
    "special_folder_suffix": "naming.special_folder_suffix",
    "default_season": "parse.default_season",
    "special_season_zero": "parse.special_season_zero",
    "ova_policy": "parse.ova_policy",
    "special_video_policy": "execution.special_video_policy",
    "special_folder_conflict": "execution.special_folder_conflict",
}

WORKFLOW_PRESET_OPTION_KEYS: dict[str, str] = {
    "proxy_url": "providers.proxy_url",
    "ai_enabled": "providers.ai_enabled",
    "ai_base_url": "providers.ai_base_url",
    "ai_model": "providers.ai_model",
    "ai_batch_limit": "providers.ai_batch_limit",
    "ai_special_enabled": "providers.ai_special_enabled",
    "ai_pattern_cache_auto_apply": "providers.ai_pattern_cache_auto_apply",
    "scan_all_files": "parse.scan_all_files",
    "ignore_special_dirs": "parse.ignore_special_dirs",
    "special_dir_names": "parse.special_dir_names",
    "ignore_marker_files": "parse.ignore_marker_files",
    "write_ignore_markers": "parse.write_ignore_markers",
    "generic_dir_names": "parse.generic_dir_names",
    "min_title_length": "parse.min_title_length",
    "max_files": "parse.max_files",
    "conflict_policy": "execution.conflict_policy",
    "keep_empty_dirs": "execution.keep_empty_dirs",
    "asset_policy": "execution.asset_policy",
    "batch_size": "execution.batch_size",
    "batch_delay_seconds": "execution.batch_delay_seconds",
    "retry_times": "execution.retry_times",
    "retry_backoff_seconds": "execution.retry_backoff_seconds",
    "scraper_enabled": "scraper.enabled",
}

# 旧版单层预设的完整键表：只用于读取 v6 及更早的快照和导入旧文件，
# 新代码不再用它生成快照。
LEGACY_PRESET_OPTION_KEYS: dict[str, str] = {
    **NAMING_PRESET_OPTION_KEYS,
    **WORKFLOW_PRESET_OPTION_KEYS,
}

# 不进预设的设置项：
# - 三个密钥（TMDB / Bangumi / AI）避免随预设导出泄露；
# - 网盘模式是本机读盘策略开关，不是命名/识别选项，切换预设时保持当前值；
# - 自动执行开关是每次运行都要重新授权的安全开关，不能被预设永久放行；
# - 语言相关两项是全局设置，不随任何预设变化。
PRESET_EXCLUDED_KEYS: tuple[str, ...] = (
    "providers.tmdb_api_key",
    "providers.bangumi_token",
    "providers.ai_api_key",
    "execution.network_drive_mode",
    "execution.auto_execute_enabled",
    "providers.tmdb_lang",
    "metadata.name_language_order",
)

GLOBAL_ONLY_KEYS: tuple[str, ...] = (
    "providers.tmdb_lang",
    "metadata.name_language_order",
)

# 旧版 /api/presets 接口的明确报错文案：拆分后不能静默返回旧数据。
LEGACY_PRESET_NOTICE = (
    "预设已经拆成两层：命名预设（模板与命名/落点选项）和工作流预设（其余设置）。"
    "请改用 /api/naming-presets 或 /api/workflow-presets；"
    "导出/导入仍可用 /api/presets/export 与 /api/presets/import（新格式 version 2）。"
)


def default_naming_options() -> dict[str, Any]:
    """命名预设的默认选项，用于首次生成命名预设。"""
    defaults = _defaults()
    return {
        option: defaults[setting] for option, setting in NAMING_PRESET_OPTION_KEYS.items()
    }


def default_workflow_options() -> dict[str, Any]:
    """工作流预设的默认选项，用于首次生成工作流预设。"""
    defaults = _defaults()
    return {
        option: defaults[setting] for option, setting in WORKFLOW_PRESET_OPTION_KEYS.items()
    }


def _options_from_settings(
    values: dict[str, Any], keys: dict[str, str]
) -> dict[str, Any]:
    specs = {spec.key: spec for spec in SETTING_SPECS}
    return {
        option: values.get(setting, specs[setting].default)
        for option, setting in keys.items()
    }


def naming_options_from_settings(values: dict[str, Any]) -> dict[str, Any]:
    return _options_from_settings(values, NAMING_PRESET_OPTION_KEYS)


def workflow_options_from_settings(values: dict[str, Any]) -> dict[str, Any]:
    return _options_from_settings(values, WORKFLOW_PRESET_OPTION_KEYS)


def _settings_from_options(
    options: dict[str, Any], keys: dict[str, str]
) -> dict[str, Any]:
    specs = {spec.key: spec for spec in SETTING_SPECS}
    values: dict[str, Any] = {}
    for option, setting in keys.items():
        spec = specs.get(setting)
        if spec is None:
            continue
        value = options.get(option)
        if value is None:
            value = spec.default
        elif spec.type == "string" and not str(value).strip() and spec.default:
            # 旧预设里的空字符串表示“当时没有保存这一项”，按默认值补齐
            value = spec.default
        values[setting] = _coerce(spec, value)
    return values


def naming_settings_from_options(options: dict[str, Any]) -> dict[str, Any]:
    """把命名预设快照转回设置键值（切换命名预设时使用）。"""
    return _settings_from_options(options, NAMING_PRESET_OPTION_KEYS)


def workflow_settings_from_options(options: dict[str, Any]) -> dict[str, Any]:
    """把工作流预设快照转回设置键值（切换工作流预设时使用）。"""
    return _settings_from_options(options, WORKFLOW_PRESET_OPTION_KEYS)


def setting_values_from_preset_options(options: dict[str, Any]) -> dict[str, Any]:
    """旧版单层快照转回设置键值，仅用于 v2 设置迁移和旧文件导入。"""
    return _settings_from_options(options, LEGACY_PRESET_OPTION_KEYS)


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


def _coerce_strict(spec: SettingSpec, value: Any) -> Any:
    """保存设置时用：范围或类型不对就抛中文错误，不静默改成默认值。"""
    if spec.type == "int":
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"「{spec.label}」必须是整数") from exc
        low, high = spec.min_value, spec.max_value
        if (low is not None and number < low) or (high is not None and number > high):
            if low is not None and high is not None:
                raise ValueError(
                    f"「{spec.label}」超出允许范围（{low}~{high}），请填写范围内的整数"
                )
            raise ValueError(f"「{spec.label}」超出允许范围")
        return number
    if spec.type == "select":
        if value not in spec.options:
            raise ValueError(f"「{spec.label}」不是可选项：{value}")
        return value
    if spec.type == "bool":
        return bool(value)
    return "" if value is None else str(value)


def _read_raw_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = load_settings()
        was_network_mode = bool(current.get("execution.network_drive_mode"))
        by_key = {spec.key: spec for spec in SETTING_SPECS}
        for key, value in values.items():
            spec = by_key.get(key)
            if spec is not None:
                current[key] = _coerce_strict(spec, value)
        # 只在“刚刚打开”的那一次写入推荐值；已经在网盘模式下保存设置时，
        # 用户自己调过的批大小 / 间隔不会被覆盖。
        if current.get("execution.network_drive_mode") and not was_network_mode:
            current.update(NETWORK_DRIVE_DEFAULTS)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # 保留 auth 等非设置字段（例如访问密码哈希），避免保存设置时被覆盖
        raw = _read_raw_config()
        payload = {**raw, "version": CONFIG_VERSION, "values": current}
        CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return current


def read_auth_hash() -> str:
    """访问密码哈希；存在 settings.json 的 auth.password_hash，不属于普通设置项。"""
    raw = _read_raw_config()
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    return str(auth.get("password_hash") or "")


def write_auth_hash(password_hash: str | None) -> None:
    """写入或清除访问密码哈希（None / 空字符串表示关闭密码）。"""
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        raw = _read_raw_config()
        auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
        if password_hash:
            auth["password_hash"] = password_hash
            raw["auth"] = auth
        else:
            raw.pop("auth", None)
        raw.setdefault("version", CONFIG_VERSION)
        CONFIG_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


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
                "option_labels": list(spec.option_labels),
                "min": spec.min_value,
                "max": spec.max_value,
            }
            for spec in SETTING_SPECS
        ],
        "media_root": str(get_media_root()),
    }
