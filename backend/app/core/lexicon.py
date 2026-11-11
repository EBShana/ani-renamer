"""统一识别词典：目录名、季目录、版本号、集号清理写法都只在这里定义。

为什么要有这个模块：
- 同一个概念以前在 parser / identify / scanner / operations / preview / config / rules
  里各写一份，改一处容易漏另一处；
- 现在名字表与正则集中在这里，其它模块只引用，不再自己实现。

约定：本模块只放数据与纯函数，不导入项目里的其它模块
（config.py 也会引用它，避免循环导入）。

注意：真正“解析集号”的规则表在 rules.py（用户可以在界面编辑、存在 rules.json）；
这里放的是解析之外、代码内部固定使用的写法（例如从文件名清洗作品名）。
两者用途不同，不能合并，否则用户改规则会连作品名清洗一起变。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 特典目录名
# 目录名（小写）→ 特典类型标签。
# 用在：parser 判断特典类型、identify 判断收纳目录、operations 写忽略标记。
SPECIAL_DIR_TYPES: dict[str, str] = {
    "特典": "SP",
    "映像特典": "SP",
    "特别篇": "SP",
    "花絮": "SP",
    "bonus": "SP",
    "extra": "SP",
    "extras": "SP",
    "special": "SP",
    "specials": "SP",
    "sp": "SP",
    "sps": "SP",
    "ncop": "NCOP",
    "nced": "NCED",
    "menu": "Menu",
    "preview": "PV",
    "previews": "PV",
    "ova": "OVA",
    "ovas": "OVA",
    "oad": "OAD",
    "oads": "OAD",
}

# OVA 目录：可以随路径模板移动到 Season 00，也可以原地跳过（设置项 parse.ova_policy）
OVA_DIR_NAMES = {"ova", "ovas", "oad", "oads"}

# 扫描时默认整体跳过的特典目录（设置项 parse.special_dir_names 的默认值）
DEFAULT_SPECIAL_DIR_NAME_ITEMS = (
    "SPs",
    "Specials",
    "特典",
    "映像特典",
    "CDs",
    "Scans",
    "Bonus",
    "Extras",
    "Menu",
    "PV",
    "CM",
    "Preview",
    "Trailer",
    "EXTRA",
)
DEFAULT_SPECIAL_DIR_NAMES = ",".join(DEFAULT_SPECIAL_DIR_NAME_ITEMS)

# 默认不当作作品名的通用目录（设置项 parse.generic_dir_names 的默认值）
DEFAULT_GENERIC_DIR_NAME_ITEMS = (
    "Anime",
    "动画",
    "番剧",
    "TV",
    "Movies",
    "Media",
    "media",
    "Videos",
    "Downloads",
    "Download",
    "特典",
    "SPs",
    "Specials",
    "Scans",
    "CDs",
)
DEFAULT_GENERIC_DIR_NAMES = ",".join(DEFAULT_GENERIC_DIR_NAME_ITEMS)

# 内置收纳目录：即使用户把设置清空，这些名字也不会被当成作品标题
BUILTIN_GENERIC_DIRS = {
    "anime",
    "animation",
    "动画",
    "番剧",
    "tv",
    "movies",
    "movie",
    "media",
    "videos",
    "video",
    "downloads",
    "download",
    "subs",
    "sub",
    "subtitles",
    "字幕",
    "fonts",
    "cds",
    "scans",
    "sp",
    "sps",
    "specials",
    "special",
    "bonus",
    "extras",
    "menu",
    "pv",
    "cm",
    "preview",
    "trailer",
    "ova",
    "ovas",
    "oad",
    "oads",
}

# rules.py 内置规则 dir-special 的目录名清单。
# 规则表是用户可编辑、只播种一次的数据；这里只作为“首次生成”的唯一来源。
DIR_SPECIAL_RULE_NAMES = (
    "sp",
    "sps",
    "special",
    "specials",
    "extra",
    "extras",
    "menu",
    "menus",
    "preview",
    "previews",
    "ncop",
    "nced",
    "cd",
    "cds",
    "scan",
    "scans",
    "bonus",
    "特典",
    "映像特典",
    "特别篇",
    "花絮",
)


def split_dir_names(value: object) -> set[str]:
    """把设置里的逗号分隔目录名拆成小写集合。"""
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def special_dir_name_set(settings_value: object) -> set[str]:
    """扫描/写标记时使用的特典目录名：用户设置 + 内置词典。"""
    return split_dir_names(settings_value) | set(SPECIAL_DIR_TYPES)


# ---------------------------------------------------------------- 季目录写法
# 识别阶段：判断目录名本身是不是“季目录”，用来跳过收纳层继续往上找作品名。
# 例：Season 2 / S02 / 第一季。
SEASON_DIR_NAME_RE = re.compile(
    r"(?i)^(season\s*\d{1,2}|s\d{1,2}|第[0-9一二三四五六七八九十百零两]+季)$"
)

# 预览阶段：判断“已经生成出来的目录名”是不是季目录，用来向上找作品根目录。
# 只认 Season 00 / S01 这类生成格式；与上面那条故意不同（S1、第一季不算），
# 因为预览处理的是程序自己生成的目录，合并会改变识别边界。
SEASON_FOLDER_NAME_RE = re.compile(r"^(season\s*0?\d+|s\d{2,})$", re.IGNORECASE)

# 特典季目录的几种写法，渲染时统一成设置里选择的名字（默认 Season 00）
SPECIAL_SEASON_FOLDER_NAMES = {
    "specials",
    "special",
    "season 00",
    "season 0",
    "season00",
    "s00",
}
DEFAULT_SPECIALS_DIR_NAME = "Season 00"


# ---------------------------------------------------------------- 版本号
# 文件名里的修正版标记 v2 / v10。
# identify 用它把版本号从作品名里去掉，duplicates 用它区分同集多版本。
VERSION_MARK_RE = re.compile(r"(?i)(?<![A-Za-z])v(\d{1,2})(?![A-Za-z])")

# 技术标签里的版本写法：version 2 / ver.2 / v2。
# techname 用它提取 {version}；比上面宽（允许 version / ver. 前缀，且要求后面不是字母数字），
# 两者合并会改变 v12 这类边界结果，所以保留两条。
TECH_VERSION_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:version|ver\.?|v)(\d{1,2})(?![A-Za-z0-9])"
)


# ---------------------------------------------------------------- 集号清理写法
# 从文件名推导作品名时，把这些集号 / 特典标记从名字里去掉。
# 与 rules.py 的解析规则表用途不同，不能合并（见模块开头说明）。
EPISODE_MARKER_PATTERNS = (
    re.compile(r"(?i)\bS\d{1,2}E\d{1,4}\b"),
    re.compile(r"(?i)\bS\d{1,2}\b"),
    re.compile(r"(?i)\b(?:EP|E)\.?\s?\d{1,4}\b"),
    re.compile(r"(?i)\b(?:NCOP|NCED|PV|CM|MENU|SP|OVA|OAD)\s?\d{0,3}\b"),
    re.compile(r"第\s*[0-9一二三四五六七八九十百零两]+\s*[话話集回]"),
    # “作品名 - 01”这种裸集号（避免误伤 Sennen Kessen-hen 这类词内连字符）
    re.compile(r"(?<![0-9])\s[-–—]\s*0?\d{1,4}(?![0-9])"),
)
