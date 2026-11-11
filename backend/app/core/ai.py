"""AI 辅助：调用 OpenAI 兼容的 /v1/chat/completions 接口。

设计约束（用户已确认）：
- 批量匹配「卡住」时才自动调用：本地关键词搜不到，或本地分不清多个候选；
- AI 只能给 TMDB 搜索关键词，或从已有候选里挑一个编号；标题一律取 TMDB 返回值；
- 只发送文件名、目录名、候选标题等文本，绝不发送文件内容；
- 失败必须优雅降级：返回可读的中文错误，不影响规则识别和 TMDB 匹配。

这里的函数都是同步的；FastAPI 会把同步路由放进线程池执行。

`suggest_season` 和 `suggest_special` 分别给「匹配页的季号选择」和「特典归属候选」提供建议，
同样只发文本、只给建议，用户不确认不生效。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..config import load_settings
from .metadata import USER_AGENT, MetadataError, _build_opener, _network_hint, _redact

AI_TIMEOUT = 90
MAX_BATCH_LIMIT = 50
KINDS = ("episode", "special", "movie")
CONFIDENCE = ("high", "medium", "low")


class AiError(Exception):
    """可以直接显示给用户的中文错误。"""


def ai_status(settings: dict | None = None) -> dict:
    """AI 是否可用；不返回地址和 Key 原文。"""
    values = settings or load_settings()
    enabled = bool(values.get("providers.ai_enabled"))
    base = str(values.get("providers.ai_base_url") or "").strip()
    model = str(values.get("providers.ai_model") or "").strip()
    try:
        batch_limit = int(values.get("providers.ai_batch_limit") or 10)
    except (TypeError, ValueError):
        batch_limit = 10
    batch_limit = max(1, min(batch_limit, MAX_BATCH_LIMIT))

    special_enabled = bool(values.get("providers.ai_special_enabled"))
    configured = enabled and bool(base) and bool(model)
    if not enabled:
        message = "AI 辅助未启用：请到设置页打开「启用 AI 辅助」"
    elif not base:
        message = "AI 辅助还没配置接口地址：请到设置页填写 OpenAI 兼容地址"
    elif not model:
        message = "AI 辅助还没配置模型名：请到设置页填写模型名"
    else:
        message = "AI 辅助已就绪：所有建议都需要你确认后才会生效"
    return {
        "enabled": enabled,
        "configured": configured,
        "special_enabled": special_enabled,
        "message": message,
        "batch_limit": batch_limit,
    }


def _endpoint(base: str) -> str:
    text = (base or "").strip().rstrip("/")
    if not text:
        raise AiError("请先在设置页填写 AI 接口地址")
    if not text.startswith(("http://", "https://")):
        raise AiError("AI 接口地址必须以 http:// 或 https:// 开头")
    if text.endswith("/chat/completions"):
        return text
    if text.endswith("/v1"):
        return f"{text}/chat/completions"
    return f"{text}/v1/chat/completions"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return ""


def _chat(messages: list[dict], settings: dict) -> str:
    """发一次 chat completions 请求，返回文字内容；错误统一转成 AiError。"""
    base = str(settings.get("providers.ai_base_url") or "")
    model = str(settings.get("providers.ai_model") or "").strip()
    key = str(settings.get("providers.ai_api_key") or "").strip()
    proxy = str(settings.get("providers.proxy_url") or "")
    if not model:
        raise AiError("请先在设置页填写 AI 模型名")

    url = _endpoint(base)
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)

    try:
        opener = _build_opener(proxy)
        with opener.open(request, timeout=AI_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except MetadataError as exc:
        raise AiError(f"AI 接口的代理设置有问题：{exc}") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 407:
            raise AiError("代理需要认证或认证失败，请检查代理地址里的用户名和密码") from exc
        if exc.code in (401, 403):
            raise AiError("AI 接口 Key 无效或没有权限，请到设置页检查") from exc
        if exc.code == 404:
            raise AiError("AI 接口地址不对（返回 404），请检查地址是否以 /v1 结尾") from exc
        if exc.code == 429:
            raise AiError("AI 接口请求太频繁，请稍后再试") from exc
        if exc.code in (500, 502, 503, 504):
            raise AiError(f"AI 服务暂时不可用（HTTP {exc.code}），请稍后再试") from exc
        raise AiError(f"AI 接口返回错误（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        reason = _network_hint(_redact(str(exc.reason), proxy, key))
        raise AiError(f"连不上 AI 接口：{reason}") from exc
    except TimeoutError as exc:
        raise AiError("AI 接口响应超时，请稍后再试，或把「每次最多处理条数」调小") from exc
    except OSError as exc:
        raise AiError(f"连接 AI 接口失败：{_redact(str(exc), proxy, key)}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AiError("AI 接口返回的不是 JSON，请确认地址填的是 OpenAI 兼容接口") from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise AiError("AI 接口返回格式不对（没有 choices 内容）")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = _message_text(message.get("content"))
    if not content.strip():
        raise AiError("AI 接口没有返回文字内容")
    return content


def _extract_json(text: str) -> dict:
    """从模型输出里取出 JSON 对象；兼容 ```json 包裹和前后多余文字。"""
    value = (text or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1] if "\n" in value else value
        if value.endswith("```"):
            value = value[:-3]
        value = value.strip()
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    start = next((index for index, char in enumerate(value) if char in "{["), None)
    if start is None:
        raise AiError("AI 返回的内容里没有找到 JSON")
    try:
        data, _ = json.JSONDecoder().raw_decode(value[start:])
    except ValueError as exc:
        raise AiError("AI 返回的内容不是有效的 JSON 格式") from exc
    if not isinstance(data, dict):
        raise AiError("AI 返回的 JSON 不是一个对象")
    return data


def _ask(system: str, user: str, settings: dict) -> dict:
    content = _chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        settings,
    )
    return _extract_json(content)


def _int_or_none(value: Any, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= minimum else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= minimum else None
    return None


def _tag_text(tags: Any) -> dict[str, str]:
    if not isinstance(tags, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in tags.items():
        text = str(value or "").strip()
        if text:
            result[str(key)[:24]] = text[:120]
    return result


def _folder_of(row: dict) -> str:
    rel = str(row.get("rel_path") or "").replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    return parts[-2] if len(parts) >= 2 else ""


def _video_rows(scan: dict) -> list[dict]:
    rows = []
    for row in scan.get("files") or []:
        if row.get("category") != "video":
            continue
        identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        if identify.get("ignored"):
            continue
        rows.append(row)
    return rows


IDENTIFY_SYSTEM = """你是动画媒体库整理助手。你只能根据用户提供的文件名、目录名和已有候选标题推断作品信息，不要虚构、不要编造不存在的作品。
必须只输出一个 JSON 对象，不要输出解释文字，不要使用 markdown 代码块。
格式必须为：
{"work_note":"整体说明","files":[{"id":"文件 id","title":"作品正式名称","year":2020,"season":1,"episode":1,"kind":"episode","confidence":"high","reason":"判断依据","tags":{"resolution":"1080p","release_group":"字幕组","note":"特典归属等"}}]}
说明：
- id 必须原样使用用户给的 id；
- kind 取 episode / special / movie；
- year、season、episode 不确定时填 null，不要猜一个看似合理的数字；
- 特典如果能看出属于哪部作品，写在 tags.note 里；
- confidence 取 high / medium / low，表示你有多确定；
- reason 用一句中文说明你的判断依据。"""


def suggest_identify(
    scan: dict,
    settings: dict,
    group_id: str | None = None,
    limit: int | None = None,
) -> dict:
    """把未归属的视频按目录打包，一次问 AI，返回临时建议（不写回扫描结果）。"""
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])
    batch_limit = int(status["batch_limit"])
    try:
        wanted = int(limit) if limit is not None else batch_limit
    except (TypeError, ValueError):
        wanted = batch_limit
    batch = max(1, min(wanted, batch_limit))

    rows = [
        row
        for row in _video_rows(scan)
        if not str((row.get("identify") or {}).get("work_id") or "")
        and (group_id is None or str(row.get("group_id") or "") == group_id)
    ]
    selected = rows[:batch]
    if not selected:
        return {
            "suggestions": [],
            "total": len(rows),
            "considered": 0,
            "message": "没有需要 AI 判断的未归属文件",
        }

    payload_files = []
    for row in selected:
        identify = row.get("identify") or {}
        payload_files.append(
            {
                "id": str(row.get("id") or ""),
                "filename": str(row.get("filename") or ""),
                "folder": _folder_of(row),
                "name_segment": str(identify.get("segment") or ""),
                "guess_title": str(identify.get("title") or ""),
                "guess_year": identify.get("year") if isinstance(identify.get("year"), int) else None,
                "guess_season": row.get("season") if isinstance(row.get("season"), int) else None,
                "guess_episode": row.get("episode") if isinstance(row.get("episode"), int) else None,
                "guess_kind": str(row.get("kind") or ""),
            }
        )
    user = json.dumps({"files": payload_files}, ensure_ascii=False, indent=2)
    data = _ask(IDENTIFY_SYSTEM, user, settings)

    items = data.get("files")
    if not isinstance(items, list):
        raise AiError("AI 返回的内容里没有 files 列表，已忽略本次建议")
    by_id = {str(row.get("id") or ""): row for row in selected}
    suggestions: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id") or "")
        row = by_id.get(file_id)
        if row is None:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        identify = row.get("identify") or {}
        kind = str(item.get("kind") or "").strip().lower()
        confidence = str(item.get("confidence") or "").strip().lower()
        suggestions.append(
            {
                "file_id": file_id,
                "filename": str(row.get("filename") or ""),
                "folder": _folder_of(row),
                "segment": str(identify.get("segment") or ""),
                "title": title[:160],
                "year": _int_or_none(item.get("year"), 1900),
                "season": _int_or_none(item.get("season")),
                "episode": _int_or_none(item.get("episode")),
                "kind": kind if kind in KINDS else str(row.get("kind") or ""),
                "confidence": confidence if confidence in CONFIDENCE else "medium",
                "reason": str(item.get("reason") or "")[:400],
                "tags": _tag_text(item.get("tags")),
            }
        )

    note = str(data.get("work_note") or "").strip()
    message = note[:300] if note else ""
    if len(suggestions) < len(selected):
        extra = f"AI 只给出了 {len(suggestions)} 条建议，其余仍留在未归属区"
        message = f"{message}；{extra}" if message else extra
    return {
        "suggestions": suggestions,
        "total": len(rows),
        "considered": len(selected),
        "message": message,
    }


MATCH_SYSTEM = """你是动画媒体库整理助手，熟悉动画的日文名、罗马音、中文译名和英文名。
用户会给出一批作品（可能只是文件名或目录名推断出来的写法），请为每个作品给出最适合拿去 TMDB 搜索的关键词。
必须只输出一个 JSON 对象，不要输出解释文字，不要使用 markdown 代码块。
格式必须为：
{"work_note":"整体说明","works":[{"id":"作品 id","query":"建议的搜索关键词","title":"你判断的正式名称","year":2020,"media_type":"tv","confidence":"high","reason":"判断依据"}]}
说明：
- id 必须原样使用用户给的 id；
- query 优先使用 TMDB 更容易命中的正式名称（中文 / 日文 / 英文均可，选一个最可能的）；
- media_type 取 tv（剧集/动画）或 movie（剧场版）；
- year 不确定填 null；confidence 取 high / medium / low；
- reason 用一句中文说明为什么建议这个关键词；
- 拿不准时 query 保留用户原来的标题，不要编造不存在的作品；
- 如果条目里带有 previous_query 或 note，说明上一次的关键词在 TMDB 没搜到，请换一种写法（中文 / 日文原名 / 罗马音 / 英文）再给一个。"""


def suggest_match(
    scan: dict,
    settings: dict,
    limit: int | None = None,
    include_matched: bool = False,
) -> dict:
    """把作品列表打包问 AI，返回建议的 TMDB 搜索关键词（不直接套用）。"""
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])
    batch_limit = int(status["batch_limit"])
    try:
        wanted = int(limit) if limit is not None else batch_limit
    except (TypeError, ValueError):
        wanted = batch_limit
    batch = max(1, min(wanted, batch_limit))

    from .matcher import build_groups

    groups = build_groups(scan.get("files") or [])
    targets = [
        group for group in groups if include_matched or not group.get("match")
    ][:batch]
    if not targets:
        return {
            "suggestions": [],
            "total": 0,
            "considered": 0,
            "message": "没有需要 AI 建议的作品（可能都已匹配）",
        }

    payload = []
    for group in targets:
        samples = []
        for row in scan.get("files") or []:
            identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
            if str(identify.get("work_id") or "") != str(group.get("group_id") or ""):
                continue
            samples.append(str(row.get("filename") or ""))
            if len(samples) >= 4:
                break
        payload.append(
            {
                "id": str(group.get("group_id") or ""),
                "title": str(group.get("title") or ""),
                "year": group.get("year") if isinstance(group.get("year"), int) else None,
                "seasons": group.get("seasons") or [],
                "sample_files": samples,
            }
        )

    data = _ask(MATCH_SYSTEM, json.dumps({"works": payload}, ensure_ascii=False, indent=2), settings)
    items = data.get("works")
    if not isinstance(items, list):
        raise AiError("AI 返回的内容里没有 works 列表，已忽略本次建议")
    by_id = {str(group.get("group_id") or ""): group for group in targets}
    suggestions: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        group_key = str(item.get("id") or "")
        group = by_id.get(group_key)
        if group is None:
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        media_type = str(item.get("media_type") or "").strip().lower()
        confidence = str(item.get("confidence") or "").strip().lower()
        suggestions.append(
            {
                "group_id": group_key,
                "current_title": str(group.get("title") or ""),
                "query": query[:160],
                "title": str(item.get("title") or "")[:160],
                "year": _int_or_none(item.get("year"), 1900),
                "media_type": media_type if media_type in ("tv", "movie") else "tv",
                "confidence": confidence if confidence in CONFIDENCE else "medium",
                "reason": str(item.get("reason") or "")[:400],
            }
        )

    note = str(data.get("work_note") or "").strip()
    message = note[:300] if note else ""
    if len(suggestions) < len(targets):
        extra = f"AI 只给出了 {len(suggestions)} 条建议，其余请手动搜索"
        message = f"{message}；{extra}" if message else extra
    return {
        "suggestions": suggestions,
        "total": len(targets),
        "considered": len(targets),
        "message": message,
    }


def ask_query(work: dict, settings: dict) -> dict:
    """给单个作品要一个 TMDB 搜索关键词；只返回关键词，不返回标题。

    work 可带 previous_query / note，表示上一次关键词没搜到、需要换一种写法。
    """
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])

    payload = {
        "id": str(work.get("id") or ""),
        "title": str(work.get("title") or "")[:160],
        "year": work.get("year") if isinstance(work.get("year"), int) else None,
        "seasons": [int(item) for item in (work.get("seasons") or []) if isinstance(item, int)],
        "sample_files": [str(text)[:200] for text in (work.get("sample_files") or [])[:4]],
    }
    if work.get("previous_query"):
        payload["previous_query"] = str(work["previous_query"])[:160]
    if work.get("note"):
        payload["note"] = str(work["note"])[:200]

    data = _ask(
        MATCH_SYSTEM,
        json.dumps({"works": [payload]}, ensure_ascii=False, indent=2),
        settings,
    )
    items = data.get("works")
    if not isinstance(items, list):
        raise AiError("AI 返回的内容里没有 works 列表，已忽略本次建议")
    item = next(
        (
            entry
            for entry in items
            if isinstance(entry, dict) and str(entry.get("id") or "") == payload["id"]
        ),
        None,
    )
    if item is None:
        item = next((entry for entry in items if isinstance(entry, dict)), None)
    query = str((item or {}).get("query") or "").strip()
    if not query:
        return {"query": "", "reason": "", "message": "AI 没能给出可用的搜索关键词"}
    return {
        "query": query[:160],
        "reason": str((item or {}).get("reason") or "")[:400],
        "message": "",
    }


PICK_SYSTEM = """你是动画媒体库整理助手。用户会给出一个作品，以及 TMDB 搜索到的候选列表。
你只能从候选列表里挑一个编号，不能编造编号，也不能改写标题或另起名字。
必须只输出一个 JSON 对象，不要输出解释文字，不要使用 markdown 代码块。
格式必须为：
{"id":候选的编号或 null,"confidence":"high","reason":"判断依据","message":"给用户的一句话提示"}
说明：
- id 必须严格等于候选列表里某个 id，不能填列表以外的数字，也不要自己编造；
- 拿不准就把 id 填 null，不要猜；
- 不要输出作品名，最终名称由 TMDB 决定；
- confidence 取 high / medium / low；
- reason 用一句中文说明依据；message 用一句中文提示用户是否需要手动确认。"""

UNCERTAIN_IDS = {"", "null", "none", "unknown", "不确定", "无法判断", "无法确定"}


def pick_candidate(work: dict, candidates: list[dict], settings: dict) -> dict:
    """让 AI 从已有候选里挑一个编号。

    返回 {id, valid, reason, message}：
    - id=None + valid=True 表示 AI 说不确定，不猜；
    - valid=False 表示 AI 选了不存在的编号（或没有候选），这条结果必须整条丢弃。
    """
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])

    items = []
    valid_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("id") is None:
            continue
        key = str(candidate.get("id"))
        valid_ids.add(key)
        items.append(
            {
                "id": key,
                "name": str(candidate.get("name") or "")[:160],
                "original_name": str(candidate.get("original_name") or "")[:160],
                "year": candidate.get("year") if isinstance(candidate.get("year"), int) else None,
                "media_type": str(candidate.get("media_type") or ""),
                "popularity": candidate.get("popularity"),
            }
        )
    if not items:
        return {
            "id": None,
            "valid": False,
            "reason": "",
            "message": "没有候选可以交给 AI 判断，请手动搜索",
        }

    payload = {
        "work": {
            "title": str(work.get("title") or "")[:160],
            "year": work.get("year") if isinstance(work.get("year"), int) else None,
            "sample_files": [str(text)[:200] for text in (work.get("sample_files") or [])[:4]],
        },
        "candidates": items,
    }
    data = _ask(PICK_SYSTEM, json.dumps(payload, ensure_ascii=False, indent=2), settings)

    raw = data.get("id")
    reason = str(data.get("reason") or "")[:400]
    note = str(data.get("message") or "")[:200]
    text = "" if raw is None else str(raw).strip()
    if text.lower() in UNCERTAIN_IDS:
        return {
            "id": None,
            "valid": True,
            "reason": reason,
            "message": note or "AI 说不确定是哪一部，请手动选择",
        }
    if text.lower() not in valid_ids:
        return {
            "id": None,
            "valid": False,
            "reason": reason,
            "message": f"AI 选了一个不存在的编号（{text}），已丢弃这条结果，请手动选择",
        }
    return {"id": int(text), "valid": True, "reason": reason, "message": note}


SEASON_SYSTEM = """你是动画媒体库整理助手。用户会给出一个作品在 TMDB 上的季列表，以及从某个目录扫描到的文件名。
请判断这些正片文件最可能属于季列表里的哪一季。
必须只输出一个 JSON 对象，不要输出解释文字，不要使用 markdown 代码块。
格式必须为：
{"season":1,"candidates":[1,2],"reason":"判断依据","message":"给用户的一句话提示"}
说明：
- season 只能填季列表里出现过的 season_number，无法判断时填 null；
- 只有确实无法二选一时才在 candidates 里放多个季号，否则留空数组；
- 文件名或目录名里的 S2、第二季、2nd Season 等线索可以参考；
- 不要编造季号，也不要编造作品；
- reason 用一句中文说明依据；message 用一句中文提示用户是否需要手动确认。"""


def suggest_season(context: dict, settings: dict) -> dict:
    """让 AI 给一个作品推荐季号；只返回建议，季号必须落在 TMDB 季列表里。"""
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])

    seasons = []
    valid: set[int] = set()
    for item in context.get("seasons") or []:
        if not isinstance(item, dict):
            continue
        number = _int_or_none(item.get("season_number"))
        if number is None:
            continue
        valid.add(number)
        seasons.append(
            {
                "season_number": number,
                "name": str(item.get("name") or "")[:120],
                "episode_count": _int_or_none(item.get("episode_count")),
            }
        )
    if not seasons:
        return {"season": None, "candidates": [], "reason": "", "message": "这个作品没有可用的季列表，请先匹配 TMDB 作品"}

    payload = {
        "title": str(context.get("title") or "")[:160],
        "year": context.get("year") if isinstance(context.get("year"), int) else None,
        "seasons": seasons,
        "folders": [str(text)[:160] for text in (context.get("folders") or [])[:8]],
        "sample_files": [str(text)[:200] for text in (context.get("files") or [])[:8]],
        "current_guess": context.get("current_season")
        if isinstance(context.get("current_season"), int)
        else None,
    }
    data = _ask(SEASON_SYSTEM, json.dumps(payload, ensure_ascii=False, indent=2), settings)

    raw_season = _int_or_none(data.get("season"))
    candidates: list[int] = []
    for value in data.get("candidates") or []:
        number = _int_or_none(value)
        if number is not None and number in valid and number not in candidates:
            candidates.append(number)
    reason = str(data.get("reason") or "").strip()[:400]
    note = str(data.get("message") or "").strip()[:200]

    if raw_season is not None and raw_season not in valid:
        # AI 给了不存在的季号：忽略并把季列表告诉用户，避免套错
        if candidates:
            return {
                "season": None,
                "candidates": candidates,
                "reason": reason,
                "message": (
                    f"AI 给出的季号 {raw_season} 不在这个作品的季列表里，已忽略；"
                    f"它还提到候选 {('、'.join(str(x) for x in candidates))}，请手动选择"
                ),
            }
        return {
            "season": None,
            "candidates": [],
            "reason": reason,
            "message": (
                f"AI 给出的季号 {raw_season} 不在这个作品的季列表里，已忽略。"
                f"可选季号：{('、'.join(str(x) for x in sorted(valid)))}，请手动选择"
            ),
        }
    if raw_season is not None:
        return {
            "season": raw_season,
            "candidates": [raw_season],
            "reason": reason,
            "message": note or f"AI 建议第 {raw_season} 季，确认后才会生效",
        }
    if len(candidates) > 1:
        return {
            "season": None,
            "candidates": candidates,
            "reason": reason,
            "message": (
                f"AI 无法确定唯一季号，候选：{('、'.join(str(x) for x in candidates))}，请手动选择"
            ),
        }
    if len(candidates) == 1:
        return {
            "season": candidates[0],
            "candidates": candidates,
            "reason": reason,
            "message": note or f"AI 建议第 {candidates[0]} 季，确认后才会生效",
        }
    return {
        "season": None,
        "candidates": [],
        "reason": reason,
        "message": note or "AI 无法根据文件名判断季号，请手动选择",
    }


SPECIAL_SYSTEM = """你是动画媒体库整理助手。用户会给出若干特典目录（目录名 + 示例文件名），以及扫描到的作品列表（标题 + 年份 + 示例文件名）。
请判断每个特典目录最可能属于哪部作品。
必须只输出一个 JSON 对象，不要输出解释文字，不要使用 markdown 代码块。
格式必须为：
{"suggestions":[{"folder_key":"目录 key","work_id":"作品 id","confidence":"high","reason":"判断依据"}],"message":"给用户的一句话提示"}
说明：
- folder_key 和 work_id 必须原样使用用户给出的值；
- 无法判断时 work_id 填空字符串，不要编造作品；
- confidence 取 high / medium / low；
- reason 用一句中文说明依据。"""


def _work_samples(scan: dict) -> dict[str, dict]:
    works: dict[str, dict] = {}
    for row in _video_rows(scan):
        identify = row.get("identify") if isinstance(row.get("identify"), dict) else {}
        work_id = str(identify.get("work_id") or "")
        if not work_id:
            continue
        work = works.setdefault(
            work_id,
            {
                "work_id": work_id,
                "title": str(identify.get("title") or "")[:160],
                "year": identify.get("year") if isinstance(identify.get("year"), int) else None,
                "folders": [],
                "sample_files": [],
            },
        )
        folder = _folder_of(row)
        if folder and folder not in work["folders"] and len(work["folders"]) < 4:
            work["folders"].append(folder[:160])
        if len(work["sample_files"]) < 4:
            work["sample_files"].append(str(row.get("filename") or "")[:200])
    return works


def suggest_special(scan: dict, settings: dict, limit: int | None = None) -> dict:
    """让 AI 判断特典目录归属；只返回建议，不写回扫描结果。"""
    status = ai_status(settings)
    if not status["configured"]:
        raise AiError(status["message"])
    if not status["special_enabled"]:
        raise AiError("AI 特典归属判断未启用：请到设置页打开「AI 特典归属判断」")

    from .identify import special_suggestions

    folders = [item for item in special_suggestions(scan) if not item.get("confirmed")]
    batch_limit = int(status["batch_limit"])
    try:
        wanted = int(limit) if limit is not None else batch_limit
    except (TypeError, ValueError):
        wanted = batch_limit
    selected = folders[: max(1, min(wanted, batch_limit))]
    if not selected:
        return {
            "suggestions": [],
            "total": len(folders),
            "considered": 0,
            "message": "没有待判断的特典目录",
        }

    works = _work_samples(scan)
    payload_folders = []
    for item in selected:
        key = str(item.get("folder_key") or "")
        names = []
        for row in _video_rows(scan):
            rel = str(row.get("rel_path") or "").replace("\\", "/")
            if f"/{item.get('rel_path')}/" in f"/{rel}" and len(names) < 4:
                names.append(str(row.get("filename") or "")[:200])
        payload_folders.append(
            {
                "folder_key": key,
                "folder": str(item.get("rel_path") or "")[:200],
                "sample_files": names,
                "rule_candidates": [
                    str(c.get("title") or "")[:120] for c in (item.get("candidates") or [])[:4]
                ],
            }
        )
    payload = {
        "special_folders": payload_folders,
        "works": list(works.values()),
    }
    data = _ask(SPECIAL_SYSTEM, json.dumps(payload, ensure_ascii=False, indent=2), settings)

    items = data.get("suggestions")
    if not isinstance(items, list):
        raise AiError("AI 返回的内容里没有 suggestions 列表，已忽略本次建议")
    by_folder = {str(item.get("folder_key") or ""): item for item in selected}
    suggestions: list[dict] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        folder_key = str(entry.get("folder_key") or "")
        folder = by_folder.get(folder_key)
        if folder is None:
            continue
        work_id = str(entry.get("work_id") or "")
        work = works.get(work_id)
        confidence = str(entry.get("confidence") or "").strip().lower()
        suggestions.append(
            {
                "folder_key": folder_key,
                "rel_path": str(folder.get("rel_path") or ""),
                "work_id": work_id,
                "title": str((work or {}).get("title") or ""),
                "year": (work or {}).get("year"),
                "confidence": confidence if confidence in CONFIDENCE else "medium",
                "reason": str(entry.get("reason") or "")[:400],
            }
        )

    note = str(data.get("message") or "").strip()[:300]
    unable = [item for item in suggestions if not item["work_id"]]
    if unable:
        extra = f"AI 有 {len(unable)} 个特典目录判断不出归属，请手动选择"
        note = f"{note}；{extra}" if note else extra
    return {
        "suggestions": suggestions,
        "total": len(folders),
        "considered": len(selected),
        "message": note,
    }
