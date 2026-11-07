"""技术标签与发布组的启发式提取。"""

from __future__ import annotations

import re

BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")

_RESOLUTION_RE = re.compile(r"(?i)(2160p|1080p|720p|576p|480p|4k|8k)")
_CODEC_RE = re.compile(r"(?i)(x265|x264|h\.?265|h\.?264|hevc|avc|av1|vp9)")
_SOURCE_RE = re.compile(
    r"(?i)(blu-?ray|bdremux|bdrip|web-?dl|web-?rip|web|hdtv|dvdrip|dvd|bd)"
)
_AUDIO_RE = re.compile(r"(?i)(truehd|dts-?hd|flac|eac3|ac3|aac|opus|alac|mp3)")
_BITDEPTH_RE = re.compile(r"(?i)(ma10p|10-?bit|12-?bit|8-?bit)")
_HDR_RE = re.compile(r"(?i)(hdr10\+|hdr10|hdr|dolby.?vision|dovi)")
_VERSION_RE = re.compile(r"(?<![A-Za-z])v(\d{1,2})(?![A-Za-z])")
_SUB_RE = re.compile(r"(?i)\b(chs|cht|gb|big5|jpn|eng|jpsc|jptc|sc|tc)\b")

# 用于判断方括号内容是否“整段都是技术标签”
_TAG_TOKEN_RE = re.compile(
    r"(?i)(2160p|1080p|720p|576p|480p|4k|8k"
    r"|x265|x264|h\.?265|h\.?264|hevc|avc|av1|vp9"
    r"|blu-?ray|bdremux|bdrip|web-?dl|web-?rip|web|hdtv|dvdrip|dvd|bd"
    r"|truehd|dts-?hd|flac|eac3|ac3|aac|opus|alac|mp3"
    r"|ma10p|10-?bit|12-?bit|8-?bit"
    r"|hdr10\+|hdr10|hdr|dovi"
    r"|mp4|mkv|avi|ts|m2ts|flv|wmv|rmvb|mov"
    r"|chs|cht|gb|big5|jpn|eng|jpsc|jptc|sc|tc|multi|简体|繁体|简繁)"
)
_TAG_SEP_RE = re.compile(r"[\s_.,+\-\[\]()（）【】/|]+")

PLATFORM_NAMES = {
    "baha",
    "b-global",
    "bglobal",
    "bilibili",
    "abema",
    "crunchyroll",
    "cr",
    "netflix",
    "amazon",
    "iqiyi",
    "viu",
    "hulu",
    "disney",
    "at-x",
    "atx",
    "youtube",
    "tv",
    "web",
    "tvri",
}


def _normalize_codec(raw: str) -> str:
    key = raw.lower().replace(".", "").replace("-", "")
    if key in ("x265", "h265", "hevc"):
        return "HEVC"
    if key in ("x264", "h264", "avc"):
        return "AVC"
    return key.upper()


def _normalize_bit_depth(raw: str) -> str:
    digits = re.sub(r"(?i)[^0-9]", "", raw)
    return f"{digits}bit" if digits else ""


def extract_tech(name: str) -> dict[str, str]:
    tech: dict[str, str] = {}
    if m := _RESOLUTION_RE.search(name):
        value = m.group(1).lower()
        tech["resolution"] = "2160p" if value in ("4k", "8k") else value
    if m := _CODEC_RE.search(name):
        tech["codec"] = _normalize_codec(m.group(1))
    if m := _SOURCE_RE.search(name):
        tech["source"] = m.group(1).upper().replace("BDRIP", "BDRip").replace("WEBRIP", "WEBRip")
    if m := _AUDIO_RE.search(name):
        tech["audio"] = m.group(1).upper()
    if m := _BITDEPTH_RE.search(name):
        tech["bit_depth"] = _normalize_bit_depth(m.group(1))
    if m := _HDR_RE.search(name):
        tech["hdr"] = m.group(1).upper()
    if m := _VERSION_RE.search(name):
        tech["version"] = m.group(1)
    if m := _SUB_RE.search(name):
        tech["sub_lang"] = m.group(1).upper()
    return tech


def is_tag_content(content: str) -> bool:
    remainder = _TAG_TOKEN_RE.sub("", content)
    remainder = _TAG_SEP_RE.sub("", remainder)
    return remainder == ""


def extract_group(name: str) -> str | None:
    """取第一个“像发布组”的方括号内容。"""
    for match in BRACKET_RE.finditer(name):
        content = match.group(1).strip()
        if not content:
            continue
        if content.lower() in PLATFORM_NAMES:
            continue
        if is_tag_content(content):
            continue
        if not re.search(r"[A-Za-z]", content):
            continue
        return content
    return None


def extract_year(name: str) -> int | None:
    match = re.search(r"(?:^|[^\d])(19|20)(\d{2})(?:[^\d]|$)", name)
    if not match:
        return None
    year = int(f"{match.group(1)}{match.group(2)}")
    if 1900 <= year <= 2100:
        return year
    return None
