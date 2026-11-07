"""命名模板引擎：渲染、空值折叠、字符净化与校验。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .fields import FIELD_REGISTRY

EMPTY = "\x00"
TOKEN_RE = re.compile(r"({{|}})|{(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<spec>[^{}]*))?}")
FORMAT_SPEC_RE = re.compile(r"0(\d{1,2})")
# 特典季目录的几种写法，渲染时统一成设置里选择的名字
SPECIALS_SEGMENTS = {"specials", "special", "season 00", "season 0", "season00", "s00"}


def _parse_tokens(pattern: str) -> tuple[list[tuple], list[str]]:
    tokens: list[tuple] = []
    errors: list[str] = []
    position = 0
    for match in TOKEN_RE.finditer(pattern):
        literal = pattern[position : match.start()]
        if literal:
            tokens.append(("lit", literal))
        raw = match.group(0)
        if raw == "{{":
            tokens.append(("lit", "{"))
        elif raw == "}}":
            tokens.append(("lit", "}"))
        else:
            tokens.append(("var", match.group("key"), match.group("spec") or ""))
        position = match.end()
    tail = pattern[position:]
    if tail:
        tokens.append(("lit", tail))
    for token in tokens:
        if token[0] == "lit" and ("{" in token[1] or "}" in token[1]):
            errors.append("存在未闭合或多余的括号")
            break
    return tokens, errors


def _format_value(value: object, spec: str, key: str, errors: list[str]) -> str:
    if value is None or value == "":
        return EMPTY
    text = str(value)
    if not spec:
        return text
    if spec == "upper":
        return text.upper()
    if spec == "lower":
        return text.lower()
    padding = FORMAT_SPEC_RE.fullmatch(spec)
    if padding:
        width = int(padding.group(1))
        return text.zfill(width) if text.isdigit() else text
    errors.append(f"变量 {{{key}}} 的格式说明「{spec}」不受支持")
    return text


def _cleanup(text: str) -> str:
    value = text.replace(EMPTY, "")
    value = re.sub(r"[\[\(【]\s*[\]\)】]", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"(\s*-\s*){2,}", " - ", value)
    value = re.sub(r"(\s*_\s*){2,}", "_", value)
    value = re.sub(r"\s+([,，、)\]】])", r"\1", value)
    value = value.strip(" -_.")
    return value


def render_pattern(pattern: str, fields: dict, settings: dict) -> tuple[str, list[str]]:
    if not pattern or not pattern.strip():
        return "", ["模板为空"]
    tokens, errors = _parse_tokens(pattern)
    chunks: list[str] = []
    for token in tokens:
        if token[0] == "lit":
            chunks.append(token[1])
            continue
        key, spec = token[1], token[2]
        if key not in FIELD_REGISTRY:
            errors.append(f"未知变量 {{{key}}}")
            chunks.append(EMPTY)
            continue
        chunks.append(_format_value(fields.get(key, ""), spec, key, errors))
    return _cleanup("".join(chunks)), errors


def _truncate_bytes(text: str, limit: int) -> tuple[str, bool]:
    if len(text.encode("utf-8")) <= limit:
        return text, False
    encoded = text.encode("utf-8")[: max(limit, 1)]
    try:
        return encoded.decode("utf-8"), True
    except UnicodeDecodeError:
        return encoded[:-1].decode("utf-8", errors="ignore"), True


def sanitize_component(name: str, forbidden: str, max_bytes: int) -> tuple[str, bool]:
    text = unicodedata.normalize("NFC", name)
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    for char in forbidden:
        text = text.replace(char, "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    stem, dot, suffix = text.rpartition(".")
    if dot and len(suffix) <= 12:
        stem, truncated = _truncate_bytes(stem, max(max_bytes - len(suffix) - 1, 1))
        return (f"{stem}.{suffix}" if stem else "").strip(" ."), truncated
    return _truncate_bytes(text, max_bytes)


def validate_template(pattern: str, output_mode: str, settings: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not pattern or not pattern.strip():
        errors.append("模板不能为空")
        return {"errors": errors, "warnings": warnings}
    tokens, token_errors = _parse_tokens(pattern)
    errors.extend(token_errors)
    for token in tokens:
        if token[0] != "var":
            continue
        key, spec = token[1], token[2]
        if key not in FIELD_REGISTRY:
            errors.append(f"未知变量 {{{key}}}")
        elif spec and not (spec in ("upper", "lower") or FORMAT_SPEC_RE.fullmatch(spec)):
            errors.append(f"变量 {{{key}}} 的格式说明「{spec}」不受支持")
    if output_mode == "path":
        if pattern.startswith("/") or pattern.startswith("\\"):
            errors.append("目录模板不能以斜杠开头（路径相对于媒体根目录）")
        if ".." in pattern:
            errors.append("目录模板不能包含 ..")
    if "{ext}" not in pattern and settings.get("naming.auto_append_ext", True):
        warnings.append("模板未包含 {ext}，将自动追加原扩展名")
    forbidden = str(settings.get("naming.forbidden_chars", ""))
    if output_mode == "path":
        forbidden = forbidden.replace("/", "")
    literals = "".join(token[1] for token in tokens if token[0] == "lit")
    if any(char in literals for char in forbidden):
        warnings.append("模板包含非法字符，生成结果时会自动移除")
    return {"errors": errors, "warnings": warnings}


def build_filename(
    row: dict,
    template: dict,
    settings: dict,
) -> tuple[str, list[str], list[str]]:
    """只渲染文件名（v3 起目录结构由系统自动生成）。"""
    pattern = str(template.get("pattern", "")).strip()
    rendered, errors = render_pattern(pattern, row.get("fields", {}), settings)
    warnings: list[str] = []
    ext = str(row.get("fields", {}).get("ext", ""))
    auto_ext = settings.get("naming.auto_append_ext", True) and "{ext}" not in pattern and ext
    forbidden = str(settings.get("naming.forbidden_chars", ""))
    max_bytes = int(settings.get("naming.max_filename_bytes", 240))

    name = rendered + (f".{ext}" if auto_ext else "")
    name, truncated = sanitize_component(name, forbidden, max_bytes)
    if truncated:
        warnings.append("文件名过长，已截断")
    if not name and not errors:
        errors.append("模板未生成有效文件名")
    return name, errors, warnings


def build_target(
    row: dict,
    template: dict,
    settings: dict,
    media_root: Path,
) -> dict:
    pattern = str(template.get("pattern", "")).strip()
    mode = str(template.get("output_mode", "filename"))
    rendered, errors = render_pattern(pattern, row.get("fields", {}), settings)
    warnings: list[str] = []
    original = Path(row["path"])
    ext = str(row.get("fields", {}).get("ext", ""))
    auto_ext = settings.get("naming.auto_append_ext", True) and "{ext}" not in pattern and ext
    forbidden = str(settings.get("naming.forbidden_chars", ""))
    max_bytes = int(settings.get("naming.max_filename_bytes", 240))

    target: Path | None = None
    new_name = ""
    if errors:
        return {
            "file_id": row.get("id"),
            "original_path": str(original),
            "new_path": "",
            "new_name": "",
            "kind": row.get("kind"),
            "status": "error",
            "messages": errors,
        }

    if mode == "path":
        components = [part for part in rendered.split("/") if part.strip()]
        if auto_ext and components:
            components[-1] = f"{components[-1]}.{ext}"
        if not components:
            errors.append("模板未生成有效路径")
        cleaned: list[str] = []
        specials_name = str(settings.get("naming.specials_dir") or "Season 00")
        for part in components:
            if part in (".", ".."):
                errors.append("路径中不能包含 . 或 ..")
                break
            if part.strip().lower() in SPECIALS_SEGMENTS:
                part = specials_name
            value, truncated = sanitize_component(part, forbidden, max_bytes)
            if truncated:
                warnings.append(f"路径片段「{part}」过长，已截断")
            if value:
                cleaned.append(value)
        if cleaned and not errors:
            target = media_root.joinpath(*cleaned)
            new_name = cleaned[-1]
    else:
        name = rendered + (f".{ext}" if auto_ext else "")
        name, truncated = sanitize_component(name, forbidden, max_bytes)
        if truncated:
            warnings.append("文件名过长，已截断")
        if not name:
            errors.append("模板未生成有效文件名")
        else:
            target = original.parent / name
            new_name = name

    if errors:
        status = "error"
    elif target is None:
        errors.append("无法计算目标路径")
        status = "error"
    elif target == original:
        warnings.append("目标与源路径相同")
        status = "warning"
    elif target.exists():
        warnings.append("目标文件已存在")
        status = "warning"
    else:
        status = "ok"

    return {
        "file_id": row.get("id"),
        "original_path": str(original),
        "new_path": str(target) if target else "",
        "new_name": new_name,
        "kind": row.get("kind"),
        "status": status,
        "messages": errors + warnings,
    }
