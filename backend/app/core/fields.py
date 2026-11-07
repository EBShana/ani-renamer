"""模板变量字段注册表。

字段以数据形式声明，前端变量面板由 /api/fields 动态生成，
后续新增字段（含刮削相关字段）只需在这里补充一条记录，无需改前端。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    category: str
    example: str
    description: str = ""


CATEGORY_LABELS: dict[str, str] = {
    "basic": "基础信息",
    "technical": "技术标签",
    "special": "特典",
    "original": "原始文件",
    "reserved": "预留字段",
}

FIELD_SPECS: list[FieldSpec] = [
    FieldSpec("series", "作品名", "basic", "葬送的芙莉莲", "从目录或文件名解析出的作品标题"),
    FieldSpec("original_title", "原名", "basic", "Sousou no Frieren", "元数据源提供的原始语言标题"),
    FieldSpec("year", "年份", "basic", "2023", "首播 / 上映年份"),
    FieldSpec("season", "季号", "basic", "1", "季度编号，特典通常为 0"),
    FieldSpec("episode", "集号", "basic", "1", "剧集编号"),
    FieldSpec("ep_title", "单集标题", "basic", "苍之海", "M2 匹配后由元数据源补充"),
    FieldSpec("air_date", "播出日期", "basic", "2023-09-29", "M2 匹配后补充"),
    FieldSpec("group", "发布组", "technical", "VCB-Studio", "字幕组 / 压制组 / 发布组标签"),
    FieldSpec("resolution", "分辨率", "technical", "1080p", "如 1080p / 2160p"),
    FieldSpec("source", "片源", "technical", "BDRip", "如 BDRip / WEB-DL / HDTV"),
    FieldSpec("codec", "编码", "technical", "HEVC", "如 HEVC / AVC / AV1"),
    FieldSpec("audio", "音频编码", "technical", "FLAC", "如 FLAC / AAC / DTS"),
    FieldSpec("bit_depth", "位深", "technical", "10bit", "如 8bit / 10bit"),
    FieldSpec("version", "版本号", "technical", "2", "v2 / v3 等修正版本"),
    FieldSpec("special_type", "特典类型", "special", "SP", "如 SP / OVA / NCOP / PV"),
    FieldSpec("special_no", "特典序号", "special", "1", "特典编号"),
    FieldSpec("ext", "扩展名", "original", "mkv", "不含前导点"),
    FieldSpec("original_stem", "原文件名（不含扩展名）", "original", "Frieren - 01", "完整原始名称，便于保留旧信息"),
    FieldSpec("original_filename", "原文件名", "original", "Frieren - 01.mkv", "含扩展名"),
    FieldSpec("file_size", "文件大小（字节）", "original", "1073741824", "原始文件字节数"),
    FieldSpec("tmdb_id", "TMDB ID", "reserved", "12345", "M2 匹配后补充"),
    FieldSpec("imdb_id", "IMDb ID", "reserved", "tt1234567", "预留"),
    FieldSpec("hdr", "HDR", "reserved", "HDR10", "从文件名识别或元数据补充"),
    FieldSpec("sub_lang", "字幕语言", "reserved", "CHS", "从文件名识别"),
    FieldSpec("crc32", "CRC32", "reserved", "-", "预留校验值"),
]

FIELD_REGISTRY: dict[str, FieldSpec] = {spec.key: spec for spec in FIELD_SPECS}


def field_payload() -> dict[str, Any]:
    return {
        "categories": [{"key": key, "label": label} for key, label in CATEGORY_LABELS.items()],
        "fields": [asdict(spec) for spec in FIELD_SPECS],
    }
