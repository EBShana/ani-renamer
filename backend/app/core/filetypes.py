"""文件类型归类：扫描时把视频、字幕、音频、图片等都收集起来并按类别区分。"""

from __future__ import annotations

VIDEO_EXTENSIONS = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".m2ts", ".mpg", ".mpeg", ".rmvb", ".rm", ".ogv", ".divx",
})
SUBTITLE_EXTENSIONS = frozenset({
    ".ass", ".ssa", ".srt", ".vtt", ".sub", ".idx", ".sup", ".ttml", ".smi",
})
AUDIO_EXTENSIONS = frozenset({
    ".flac", ".mp3", ".m4a", ".aac", ".opus", ".wav", ".mka", ".ape", ".ogg", ".cue",
})
IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif",
})
ARCHIVE_EXTENSIONS = frozenset({
    ".7z", ".zip", ".rar", ".tar", ".gz", ".bz2", ".xz",
})

CATEGORY_LABELS: dict[str, str] = {
    "video": "视频",
    "subtitle": "字幕",
    "audio": "音频",
    "image": "图片",
    "archive": "压缩包",
    "other": "其他",
}
CATEGORY_ORDER: list[str] = list(CATEGORY_LABELS)


def category_for(ext: str) -> str:
    """按扩展名返回类别。ext 为小写、不带点。"""
    suffix = f".{ext.lstrip('.').lower()}"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    return "other"
