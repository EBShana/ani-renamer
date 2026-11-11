"""AI 命名规律：结构描述的校验、编译与本地取值。

AI 只输出「结构描述」，不输出正则、不输出文件名。一条结构描述由若干条规律组成，
每条规律只说四件事：

- role：集号 / 季号 / 单集标题 / 作品名段 / 特典类型 / 特典序号 / 版本 / 区间 / 忽略；
- container：`【】` / `[]` / `()` / 无（括号外）；
- match：纯数字 / 数字+v2 / 小数 / 区间 / 纯文字 / 技术标签 / 数字带字；
- take：第一个 / 最后一个 / 第 N 个 / 其余全部。

本模块负责把这些描述翻译成本地可执行的取值逻辑，并给出「哪一段、取了什么」的判断依据，
供第一次启用时的样例预览展示。真正的自检和写回在后续模块里做。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .techname import is_tag_content

ROLES = ("episode", "season", "title", "special_type", "special_no", "version", "range", "ignore")
CONTAINERS = ("【】", "[]", "()", "无")
MATCHES = ("纯数字", "数字+v2", "小数", "区间", "纯文字", "技术标签", "数字带字")
TAKES = ("第一个", "最后一个", "其余全部")

ROLE_LABELS = {
    "episode": "集号",
    "season": "季号",
    "title": "作品名段",
    "special_type": "特典类型",
    "special_no": "特典序号",
    "version": "版本",
    "range": "区间",
    "ignore": "忽略",
}
MATCH_LABELS = {
    "纯数字": "纯数字",
    "数字+v2": "数字+v2",
    "小数": "小数",
    "区间": "区间",
    "纯文字": "纯文字",
    "技术标签": "技术标签",
    "数字带字": "数字带字",
}
CONTAINER_LABELS = {
    "【】": "全角方括号【】",
    "[]": "方括号[]",
    "()": "圆括号()",
    "无": "括号外",
}
CORE_ROLES = ("episode", "special_no", "range")
ALLOWED_KEYS = ("role", "container", "match", "take")

# 角色和匹配方式必须搭配使用，避免「区间」被当成集号这类歧义。
_COMPAT = {
    "episode": {"纯数字", "数字+v2", "小数"},
    "season": {"纯数字"},
    "title": {"纯文字"},
    # 特典类型可以直接取括号原文（含技术标签），或用「数字带字」里的文字部分；
    # 特典序号可以取「数字带字」里的数字部分。
    "special_type": {"纯文字", "技术标签", "数字带字"},
    "special_no": {"纯数字", "小数", "数字带字"},
    "version": {"数字+v2"},
    "range": {"区间"},
    "ignore": set(MATCHES),
}

_TAKE_INDEX = re.compile(r"^第\s*(\d{1,2})\s*个$")
_DIGITS = re.compile(r"^\d{1,4}$")
_DECIMAL = re.compile(r"^\d{1,4}\.\d{1,2}$")
_RANGE = re.compile(r"^\d{1,4}\s*[-~～—]\s*\d{1,4}$")
_DIGITS_V2 = re.compile(r"^(\d{1,4})[vV](\d{1,2})$")
# 数字带字：字母与数字直接相邻，如 ED01 / OP02 / NCOP01 / SP02 / CM03，也兼容 01ED。
_ALNUM = re.compile(r"^([A-Za-z]{1,12})(\d{1,4})$|^(\d{1,4})([A-Za-z]{1,12})$")
_TEXT = re.compile(r"[A-Za-z\u4e00-\u9fff]")
_REGEX_HINT = re.compile(r"\\d|\\w|\.\*|\(\?|[\^$]")
_CONTAINER_SCAN = re.compile(r"【([^】]*)】|\[([^\]]*)\]|\(([^)]*)\)")
_TOKEN_SPLIT = re.compile(r"[\s._\-–—/]+")
_ATOMIC = re.compile(r"\d{1,4}\s*[-~～—]\s*\d{1,4}|\d{1,4}\.\d{1,2}")
_DISC_HINT = re.compile(r"(?i)\b(disc|disk|cd|vol|volume|dvd|part)\b")


class PatternError(ValueError):
    """可以直接显示给用户的中文错误。"""


def _take_ok(take: str) -> bool:
    if take in ("第一个", "最后一个", "其余全部"):
        return True
    match = _TAKE_INDEX.match(take)
    return bool(match) and int(match.group(1)) >= 1


def validate_structure(payload: Any) -> list[str]:
    """检查 AI 返回的结构描述；返回中文错误列表，空列表表示通过。"""
    if not isinstance(payload, dict):
        return ["结构描述必须是一个对象"]
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        return ["结构描述里必须有 rules 列表，而且至少有一条规律"]
    if len(rules) > 30:
        return ["结构描述最多 30 条规律，请把重复的段合并"]

    errors: list[str] = []
    seen: dict[str, int] = {}
    for index, rule in enumerate(rules, 1):
        prefix = f"第 {index} 条"
        if not isinstance(rule, dict):
            errors.append(f"{prefix}不是对象")
            continue
        extra = [str(key) for key in rule if key not in ALLOWED_KEYS]
        if extra:
            errors.append(
                f"{prefix}出现了「{'、'.join(extra)}」：AI 只能输出 role/container/match/take，"
                "不能输出正则或文件名"
            )
            continue

        values = [str(rule.get(key) or "").strip() for key in ALLOWED_KEYS]
        for key, value in zip(ALLOWED_KEYS, values):
            if value and _REGEX_HINT.search(value):
                errors.append(
                    f"{prefix}的「{key}」看起来是正则表达式：这里只能写固定的中文选项，不能写正则"
                )
        role, container, match, take = values
        if role not in ROLES:
            errors.append(f"{prefix}的角色「{role}」不认识，只能是：{'、'.join(ROLES)}")
            continue
        if container not in CONTAINERS:
            errors.append(
                f"{prefix}的容器「{container}」不认识，只能是：{'、'.join(CONTAINERS)}"
            )
            continue
        if match not in MATCHES:
            errors.append(f"{prefix}的匹配方式「{match}」不认识，只能是：{'、'.join(MATCHES)}")
            continue
        if not _take_ok(take):
            errors.append(f"{prefix}的取值「{take}」不认识，只能是第一个 / 最后一个 / 第 N 个 / 其余全部")
            continue

        allowed = _COMPAT.get(role, set())
        if match not in allowed:
            errors.append(
                f"{prefix}是「{ROLE_LABELS[role]}」，不能配「{MATCH_LABELS[match]}」；"
                f"它只能配：{'、'.join(sorted(allowed))}"
            )
            continue
        if take == "其余全部" and role != "ignore":
            errors.append(f"{prefix}用了「其余全部」，这只能用于「忽略」，否则一个角色会拿到多个值")
            continue
        if role != "ignore":
            if role in seen:
                errors.append(
                    f"第 {seen[role]} 条和第 {index} 条都要识别「{ROLE_LABELS[role]}」，无法确定用哪个"
                )
                continue
            seen[role] = index

    if not any(role in CORE_ROLES for role in seen):
        errors.append("结构描述里至少要有一条「集号」「特典序号」或「区间」，否则没有识别任何编号")
    return errors


def compile_structure(payload: Any) -> dict:
    """校验并整理成可执行的结构；不合法时抛出带中文说明的 PatternError。"""
    errors = validate_structure(payload)
    if errors:
        raise PatternError("；".join(errors))
    rules = []
    for rule in payload["rules"]:
        rules.append(
            {
                "role": str(rule["role"]).strip(),
                "container": str(rule["container"]).strip(),
                "match": str(rule["match"]).strip(),
                "take": str(rule["take"]).strip(),
            }
        )
    return {"rules": rules}


def _split_tokens(text: str, container: str, display: str | None) -> list[dict]:
    tokens = []
    for part in _TOKEN_SPLIT.split(text):
        value = part.strip()
        if value:
            tokens.append({"container": container, "text": value, "display": display or value})
    return tokens


def _chunk_tokens(text: str, container: str, display: str | None = None) -> list[dict]:
    """按出现顺序拆片段；区间的 01-13、小数的 24.5 要整体保留，不能被分隔符切开。"""
    tokens: list[dict] = []
    last = 0
    for match in _ATOMIC.finditer(text):
        tokens.extend(_split_tokens(text[last : match.start()], container, display))
        value = re.sub(r"\s+", "", match.group(0))
        tokens.append({"container": container, "text": value, "display": display or value})
        last = match.end()
    tokens.extend(_split_tokens(text[last:], container, display))
    return tokens


def _tokens_of(stem: str) -> list[dict]:
    """把文件名拆成「括号内」和「括号外」的候选片段，保留出现顺序。"""
    tokens: list[dict] = []
    last = 0
    for match in _CONTAINER_SCAN.finditer(stem):
        tokens.extend(_chunk_tokens(stem[last : match.start()], "无"))
        if match.group(1) is not None:
            container, inner = "【】", match.group(1)
        elif match.group(2) is not None:
            container, inner = "[]", match.group(2)
        else:
            container, inner = "()", match.group(3)
        tokens.extend(_chunk_tokens(inner, container, inner.strip() or None))
        last = match.end()
    tokens.extend(_chunk_tokens(stem[last:], "无"))
    return tokens


def _alnum_parts(text: str) -> tuple[str, str] | None:
    """把「数字带字」拆成（文字部分, 数字部分）；不是这种形状返回 None。"""
    match = _ALNUM.match(text)
    if not match:
        return None
    if match.group(1) is not None:
        return match.group(1), match.group(2)
    return match.group(4), match.group(3)


def _matches(kind: str, text: str) -> bool:
    if kind == "纯数字":
        return bool(_DIGITS.match(text))
    if kind == "小数":
        return bool(_DECIMAL.match(text))
    if kind == "区间":
        return bool(_RANGE.match(text))
    if kind == "数字+v2":
        return bool(_DIGITS_V2.match(text))
    if kind == "数字带字":
        return _alnum_parts(text) is not None
    if kind == "技术标签":
        return is_tag_content(text)
    if kind == "纯文字":
        if _DIGITS.match(text) or _DECIMAL.match(text) or _RANGE.match(text) or _DIGITS_V2.match(text):
            return False
        if is_tag_content(text):
            return False
        return bool(_TEXT.search(text))
    return False


def _take(candidates: list[dict], take: str) -> list[dict]:
    if not candidates:
        return []
    if take == "第一个":
        return candidates[:1]
    if take == "最后一个":
        return candidates[-1:]
    if take == "其余全部":
        return candidates
    match = _TAKE_INDEX.match(take)
    if not match:
        return []
    index = int(match.group(1))
    return candidates[index - 1 : index] if 1 <= index <= len(candidates) else []


def _numeric(text: str) -> int | float:
    return float(text) if "." in text else int(text)


def _range_bounds(text: str) -> tuple[int, int]:
    parts = re.split(r"[-~～—]", text, maxsplit=1)
    return int(parts[0].strip()), int(parts[1].strip())


def _note(role: str, token: dict, take: str) -> str:
    label = ROLE_LABELS.get(role, role)
    where = CONTAINER_LABELS.get(token["container"], token["container"])
    taken = ""
    if take not in ("第一个", "最后一个", "其余全部"):
        taken = f"（{take}）"
    return f"AI 认为 {token['text']} 是{label}{taken}，来自{where}里的「{token['display']}」这一段"


def _suspects(role: str, token: dict) -> list[str]:
    if role not in ("episode", "special_no"):
        return []
    if _DISC_HINT.search(token["display"]):
        return [f"「{token['display']}」里含碟片/卷号字样，可能不是集号，请重点确认"]
    return []


def apply_structure(compiled: dict, stem: str) -> dict:
    """按结构描述取值，返回 values / evidence / missing / suspects。

    values 里：
    - episode / special_no 存数值（int 或 float），同时给 episode_text / special_no_text 原文；
    - range 存原文，并给 range_start / range_end；
    - season / version 存 int；
    - title / special_type 存文字。
    """
    tokens = _tokens_of(stem)
    values: dict[str, Any] = {}
    evidence: list[dict] = []
    suspects: list[str] = []
    missing: list[str] = []

    for rule in compiled.get("rules") or []:
        role = rule["role"]
        candidates = [
            token
            for token in tokens
            if token["container"] == rule["container"] and _matches(rule["match"], token["text"])
        ]
        selected = _take(candidates, rule["take"])
        if not selected:
            if role != "ignore":
                missing.append(role)
            continue
        if role == "ignore":
            for token in selected:
                evidence.append(
                    {
                        "role": role,
                        "value": token["text"],
                        "text": token["text"],
                        "display": token["display"],
                        "container": token["container"],
                        "take": rule["take"],
                        "note": _note(role, token, rule["take"]),
                    }
                )
            continue

        token = selected[0]
        text = token["text"]
        if role == "episode":
            if rule["match"] == "数字+v2":
                match = _DIGITS_V2.match(text)
                number, version = int(match.group(1)), int(match.group(2))
                values["episode"] = number
                values["episode_text"] = str(number)
                values["version"] = version
            else:
                values["episode"] = _numeric(text)
                values["episode_text"] = text
        elif role == "special_no":
            if rule["match"] == "数字带字":
                _, digits = _alnum_parts(text)
                values["special_no"] = _numeric(digits)
                values["special_no_text"] = digits
            else:
                values["special_no"] = _numeric(text)
                values["special_no_text"] = text
        elif role == "season":
            values["season"] = int(text)
        elif role == "version":
            match = _DIGITS_V2.match(text)
            values["version"] = int(match.group(2))
        elif role == "range":
            start, end = _range_bounds(text)
            values["range"] = text
            values["range_start"] = start
            values["range_end"] = end
        elif role == "special_type":
            if rule["match"] == "数字带字":
                letters, _ = _alnum_parts(text)
                values[role] = letters
            else:
                values[role] = text
        elif role == "title":
            values[role] = text

        if role == "episode":
            shown = str(values.get("episode_text") or text)
        elif role == "special_type" and rule["match"] == "数字带字":
            shown = str(values.get("special_type") or text)
        elif role == "special_no" and rule["match"] == "数字带字":
            shown = str(values.get("special_no_text") or text)
        else:
            shown = text
        evidence.append(
            {
                "role": role,
                "value": shown,
                "text": text,
                "display": token["display"],
                "container": token["container"],
                "take": rule["take"],
                "note": _note(role, token, rule["take"]),
            }
        )
        suspects.extend(_suspects(role, token))

    return {"values": values, "evidence": evidence, "missing": missing, "suspects": suspects}


def shape_of(stem: str) -> str:
    """文件名形状：字母换成 A、数字换成 N，其余保留。"""
    text = re.sub(r"[A-Za-z]+", "A", stem)
    return re.sub(r"\d+", "N", text)


_VERSION_HINT = re.compile(r"(?i)\d{1,4}v\d{1,2}")
_DECIMAL_HINT = re.compile(r"\d{1,4}\.\d{1,2}")
_RANGE_HINT = re.compile(r"\d{1,4}\s*[-~～—]\s*\d{1,4}")


def _shape_kind(text: str) -> str:
    """单段内容类型：R 区间 / D 小数 / V 数字+版本 / N 纯数字 / T 技术标签 / X 数字带字 / A 纯文字。"""
    if _RANGE.match(text):
        return "R"
    if _DECIMAL.match(text):
        return "D"
    if _DIGITS_V2.match(text):
        return "V"
    if _DIGITS.match(text):
        return "N"
    if is_tag_content(text):
        return "T"
    if any(ch.isdigit() for ch in text):
        return "X"
    return "A"


def shape_signature(stem: str) -> str:
    """比 shape_of 更细的形状，用来分桶抽样。

    保留括号的种类与出现顺序，并标出每段内容类型（纯数字 / 数字带字 / 纯文字 /
    技术标签 / 版本 / 区间 / 小数），最后追加全局标记：
    `V` 有版本号、`R` 有区间、`D` 有小数。

    只用文件名、不看路径：同一目录里形态相同的文件会被分到同一桶。
    """
    parts = [f"{token['container']}:{_shape_kind(token['text'])}" for token in _tokens_of(stem)]
    flags = []
    if _VERSION_HINT.search(stem):
        flags.append("V")
    if _RANGE_HINT.search(stem):
        flags.append("R")
    if _DECIMAL_HINT.search(stem):
        flags.append("D")
    signature = "|".join(parts)
    return f"{signature}#{''.join(flags)}" if flags else signature


def fingerprint_parts(group_id: str, dir_rel: str, filename: str) -> dict:
    path = Path(filename)
    return {
        "group_id": str(group_id or ""),
        "dir_rel": str(dir_rel or "").replace("\\", "/").strip("/"),
        "shape": shape_of(path.stem),
        "ext": path.suffix.lower(),
    }


def fingerprint(group_id: str, dir_rel: str, filename: str) -> str:
    parts = fingerprint_parts(group_id, dir_rel, filename)
    raw = "|".join((parts["group_id"], parts["dir_rel"], parts["shape"], parts["ext"]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def prepare_patterns(items: list[dict] | None) -> list[dict]:
    """把缓存里的规律整理成可执行形式；结构不合法会直接报错，不静默跳过。"""
    prepared = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        compiled = item.get("compiled")
        structure = item.get("structure")
        if not isinstance(compiled, dict):
            compiled = compile_structure(structure)
        prepared.append(
            {
                "id": str(item.get("id") or ""),
                "fingerprint": str(item.get("fingerprint") or ""),
                "source": str(item.get("source") or "manual"),
                "structure": structure if isinstance(structure, dict) else {"rules": compiled["rules"]},
                "compiled": compiled,
            }
        )
    return prepared


def _ai_kind(values: dict) -> str:
    """与自检里的归类保持一致：区间 > 集号 > 特典。

    顺序必须一致，否则「AI 把特典当正片」时冲突记录会和自检结论对不上。
    """
    if "range" in values:
        return "collection"
    if "episode" in values:
        return "episode"
    if "special_no" in values or "special_type" in values:
        return "special"
    return ""


def _conflicts(row: dict, values: dict, evidence: list[dict]) -> dict:
    """只比较 AI 明确给出的字段；AI 没说的字段不算冲突。"""
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    builtin = {
        "episode": row.get("episode"),
        "season": row.get("season"),
        "version": str(fields.get("version") or ""),
        "special_type": row.get("special_type") or "",
        "special_no": row.get("special_no"),
        "kind": row.get("kind"),
    }
    ai = {
        "episode": values.get("episode"),
        "season": values.get("season"),
        "version": str(values["version"]) if "version" in values else "",
        "special_type": values.get("special_type", ""),
        "special_no": values.get("special_no"),
        "kind": _ai_kind(values),
    }
    conflicts: dict[str, dict] = {}
    for key in ("episode", "season", "version", "special_type", "special_no", "kind"):
        value = ai[key]
        if value in (None, ""):
            continue
        if str(builtin[key]) != str(value):
            conflicts[key] = {"builtin": builtin[key], "ai": value, "evidence": evidence}
    if "range" in values and not row.get("collection"):
        conflicts["collection"] = {
            "builtin": False,
            "ai": True,
            "range": values["range"],
            "evidence": evidence,
        }
    return conflicts


def annotate_pattern(row: dict, prepared: list[dict] | None) -> None:
    """只计算并记下 AI 规律的结果，不修改任何现有字段。

    结果写在 `row["pattern_candidate"]`；与内置结果不同时，另写 `row["pattern_conflict"]`。
    界面要不要采用，由用户后续决定（内置优先）。
    """
    if not prepared or row.get("category") != "video":
        return
    rel = str(row.get("rel_path") or "").replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    dir_rel = "/".join(parts[:-1])
    code = fingerprint(str(row.get("group_id") or ""), dir_rel, str(row.get("filename") or ""))
    hit = next((item for item in prepared if item.get("fingerprint") == code), None)
    if hit is None:
        return

    result = apply_structure(hit["compiled"], Path(str(row.get("filename") or "")).stem)
    candidate = {
        "pattern_id": hit["id"],
        "source": hit["source"],
        "values": result["values"],
        "evidence": result["evidence"],
        "suspects": result["suspects"],
        "missing": result["missing"],
    }
    row["pattern_candidate"] = candidate
    conflicts = _conflicts(row, result["values"], result["evidence"])
    if conflicts:
        row["pattern_conflict"] = {
            "fields": conflicts,
            "evidence": result["evidence"],
            "suspects": result["suspects"],
        }
