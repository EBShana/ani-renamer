"""识别规则定义与规则引擎。

规则以数据形式存放于 SQLite，启动时从内置规则播种；
新增 / 调整识别规则可直接改数据或通过 API 提交，无需改代码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from .lexicon import DIR_SPECIAL_RULE_NAMES

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


SCOPES = ("filename", "dirname")
KINDS = ("episode", "special", "movie", "season", "any")

_REGEX_HINTS = {
    "unterminated character set": "方括号 [ ] 没有闭合",
    "missing ), unterminated subpattern": "圆括号 ( ) 没有闭合",
    "unbalanced parenthesis": "圆括号 ( ) 多了一个或位置不对",
    "nothing to repeat": "? * + 等重复符号前面没有可重复的内容",
    "bad character range": "字符范围写法不对，例如 [a-z] 的中间必须是短横线",
    "bad escape": "反斜杠后面的转义写法不合法",
}


def _regex_message(exc: re.error) -> str:
    detail = str(exc)
    hint = ""
    for key, text in _REGEX_HINTS.items():
        if key in detail:
            hint = text
            break
    position = getattr(exc, "pos", None)
    where = f"，出错位置大约在第 {position + 1} 个字符" if isinstance(position, int) else ""
    reason = hint or detail
    return f"正则表达式不合法：{reason}{where}。请检查括号是否成对、重复符号前是否有内容。"


def validate_rule_payload(payload: dict) -> list[str]:
    """检查用户提交的规则；返回中文错误列表，空列表表示通过。"""
    errors: list[str] = []
    if not str(payload.get("name") or "").strip():
        errors.append("规则名称不能为空")
    scope = str(payload.get("scope") or "")
    if scope not in SCOPES:
        errors.append("适用范围只能是文件名或目录名")
    kind = str(payload.get("kind") or "")
    if kind not in KINDS:
        errors.append("识别类型不合法，只能是集号、特典、剧场版、季或任意")
    try:
        priority = int(payload.get("priority") or 0)
    except (TypeError, ValueError):
        priority = -1
    if priority < 0 or priority > 200:
        errors.append("优先级必须是 0 到 200 之间的整数")
    regex = str(payload.get("regex") or "").strip()
    if not regex:
        errors.append("正则表达式不能为空")
    else:
        try:
            re.compile(regex)
        except re.error as exc:
            errors.append(_regex_message(exc))
    return errors


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
        "fn-cn-op-ed",
        "全角 OP / ED 特典",
        "filename",
        "special",
        11,
        r"(?i)【\s*(?P<special_type>OP|ED)(?:[-\s]?(?P<special_no>\d{1,2}))?\s*】",
        description="【OP】【ED】这类片头片尾按特典处理，带序号（OP01）和不带序号（OP）都认",
        sample="【Disc-41】【OP】【DVDrip】[x264 Hi10p AAC][MP4]",
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
        "fn-cn-bracket-episode",
        "全角方括号集号（开头）",
        "filename",
        "episode",
        31,
        r"^\s*【\s*(?P<episode>\d{1,3})(?:v(?P<version>\d))?\s*】",
        description="只认文件名开头的 【001】；4 位数字（年份）不认，标题中间的【2】也不认",
        sample="【001】【游戏的继承者】【DVDrip】[x264 Hi10p AAC][MP4]",
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
        r"(?i)^(" + "|".join(DIR_SPECIAL_RULE_NAMES) + r")$",
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
