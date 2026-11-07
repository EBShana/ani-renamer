"""识别规则定义与规则引擎。

规则以数据形式存放于 SQLite，启动时从内置规则播种；
新增 / 调整识别规则可直接改数据或通过 API 提交，无需改代码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

CHINESE_NUMERALS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100,
}


@dataclass
class Rule:
    id: str
    name: str
    scope: str  # filename | dirname
    kind: str  # episode | special | movie | season | any
    priority: int
    regex: str
    enabled: bool = True
    stop: bool = False
    description: str = ""
    sample: str = ""
    pattern: re.Pattern = dc_field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.pattern = re.compile(self.regex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "kind": self.kind,
            "priority": self.priority,
            "regex": self.regex,
            "enabled": self.enabled,
            "description": self.description,
            "sample": self.sample,
        }


BUILTIN_RULES: list[Rule] = [
    # ---- 文件名 ----
    Rule(
        "fn-movie",
        "剧场版关键字",
        "filename",
        "movie",
        5,
        r"(?i)(剧场版|劇場版|剧场|劇場|the movie|\bmovie\b)",
        description="命中则整部作品按剧场版处理",
        sample="[Group] 铃芽之旅 The Movie [BDRip 1080p]",
    ),
    Rule(
        "fn-special",
        "特典 / SP / OVA / NCOP",
        "filename",
        "special",
        10,
        r"(?i)(?<![A-Za-z])(?P<special_type>special|ncoped|ncop|nced|ova|oad|teaser|preview|menu|sp|pv|cm)[\s_.-]*(?P<special_no>\d{1,3})?",
        description="识别特典类型与序号，允许后续规则补充集号",
        sample="[Group] Frieren - NCOP01 [1080p]",
    ),
    Rule(
        "fn-sxxexx",
        "SxxExx",
        "filename",
        "episode",
        20,
        r"(?i)(?:s|season\s*)(?P<season>\d{1,2})\s*e(?P<episode>\d{1,4})",
        sample="Frieren S01E03 1080p BluRay x265",
    ),
    Rule(
        "fn-season-episode",
        "Season x Episode y",
        "filename",
        "episode",
        22,
        r"(?i)\bseason\s*(?P<season>\d{1,2})\s*(?:episode|ep|e)\s*(?P<episode>\d{1,4})",
        sample="Frieren Season 1 Episode 3",
    ),
    Rule(
        "fn-x-episode",
        "1x03",
        "filename",
        "episode",
        24,
        r"(?i)(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,4})(?!\d)",
        sample="Frieren 1x03 [1080p]",
    ),
    Rule(
        "fn-chinese-episode",
        "第 x 话",
        "filename",
        "episode",
        26,
        r"第\s*(?P<episode>\d{1,4})\s*[话話集回]",
        sample="葬送的芙莉莲 第01话",
    ),
    Rule(
        "fn-ep-keyword",
        "EP / Episode",
        "filename",
        "episode",
        28,
        r"(?i)(?<![A-Za-z])(?:episode|ep|e)\s*\.?\s*(?P<episode>\d{1,4})(?!\d)",
        sample="Frieren EP03",
    ),
    Rule(
        "fn-bracket-episode",
        "方括号集号",
        "filename",
        "episode",
        30,
        r"[\[\(]\s*(?P<episode>\d{1,3})(?:v(?P<version>\d))?\s*[\]\)]",
        sample="[Group][Frieren][01][1080p]",
    ),
    Rule(
        "fn-dash-episode",
        "破折号集号",
        "filename",
        "episode",
        32,
        r"\s-\s(?P<episode>\d{1,3})(?:v(?P<version>\d))?(?:\s|$|[\[\(])",
        sample="[Group] Frieren - 01 [1080p]",
    ),
    Rule(
        "fn-tail-episode",
        "结尾集号",
        "filename",
        "episode",
        34,
        r"(?<![0-9])(?P<episode>\d{1,3})(?:v(?P<version>\d))?\s*(?:[\[\(].*)?$",
        sample="Frieren 01v2",
    ),
    # ---- 目录名 ----
    Rule(
        "dir-special",
        "特典目录",
        "dirname",
        "special",
        5,
        r"(?i)^(sp|sps|special|specials|extra|extras|menu|menus|preview|previews|ncop|nced|cd|cds|scan|scans|bonus|特典|映像特典|特别篇|花絮)$",
        sample="特典 / SPs / Specials",
    ),
    Rule(
        "dir-season-s",
        "S01 目录",
        "dirname",
        "season",
        8,
        r"(?i)^s(?P<season>\d{1,2})$",
        sample="S01",
    ),
    Rule(
        "dir-season-word",
        "Season 1 目录",
        "dirname",
        "season",
        10,
        r"(?i)^season\s*(?P<season>\d{1,2})$",
        sample="Season 1",
    ),
    Rule(
        "dir-season-cn",
        "第 x 季目录",
        "dirname",
        "season",
        12,
        r"^第(?P<season_cn>[0-9一二三四五六七八九十百零两]+)季$",
        sample="第二季",
    ),
    Rule(
        "dir-season-ordinal",
        "2nd Season 目录",
        "dirname",
        "season",
        14,
        r"(?i)^(?P<season>\d{1,2})(?:st|nd|rd|th)\s*season$",
        sample="2nd Season",
    ),
]


def chinese_to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text:
        return None
    total = 0
    current = 0
    for char in text:
        value = CHINESE_NUMERALS.get(char)
        if value is None:
            return None
        if value >= 10:
            if current == 0:
                current = 1
            total += current * value
            current = 0
        else:
            current = value
    return total + current


def match_rules(text: str, scope: str, rules: list[Rule]) -> dict[str, str]:
    """按优先级合并匹配结果，同一字段取优先级最高（数值最小）的规则。"""
    result: dict[str, str] = {}
    for rule in sorted(rules, key=lambda item: item.priority):
        if not rule.enabled or rule.scope != scope:
            continue
        match = rule.pattern.search(text)
        if not match:
            continue
        for key, value in match.groupdict().items():
            if value is None:
                continue
            result.setdefault(key, value.strip())
        if rule.kind and rule.kind != "season":
            result.setdefault("kind", rule.kind)
        if rule.stop:
            break
    return result
