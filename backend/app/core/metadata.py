"""元数据查询：TMDB（主）与 Bangumi（辅助找作品名）。

只用标准库 urllib，避免为一次 HTTP 查询引入额外依赖。
所有函数都是同步的，FastAPI 会把同步路由放进线程池执行。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..config import load_settings

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w342"
BG_BASE = "https://api.bgm.tv"
TIMEOUT = 15
USER_AGENT = "AnimeRenamer/0.1 (self-hosted media tool)"


class MetadataError(Exception):
    """可以直接显示给用户的中文错误。"""


def _normalize_proxy(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    scheme = urllib.parse.urlsplit(text).scheme.lower()
    if scheme not in ("http", "https"):
        raise MetadataError("代理地址只支持 http:// 或 https:// 开头的 HTTP 代理")
    return text


def _redact(text: str, proxy_url: str = "", api_key: str = "") -> str:
    """把密钥和代理密码从错误信息里抹掉，避免出现在界面 / 日志。"""
    value = str(text or "")
    if api_key:
        value = value.replace(api_key, "***")
    if proxy_url:
        parts = urllib.parse.urlsplit(proxy_url)
        if parts.username:
            value = value.replace(parts.username, "***")
        if parts.password:
            value = value.replace(parts.password, "***")
    return value


def _build_opener(proxy_url: str) -> urllib.request.OpenerDirector:
    proxy = _normalize_proxy(proxy_url)
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    # 留空表示“直连”，显式忽略系统环境变量里的代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _current_proxy() -> str:
    try:
        return str(load_settings().get("providers.proxy_url") or "")
    except Exception:
        return ""


def _get_json(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{url}{query}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    proxy = _current_proxy()
    try:
        opener = _build_opener(proxy)
        with opener.open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except MetadataError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 407:
            raise MetadataError("代理需要认证或认证失败，请检查代理地址里的用户名和密码") from exc
        if exc.code in (502, 503, 504):
            raise MetadataError(f"代理或元数据源暂时不可用（HTTP {exc.code}），请稍后重试") from exc
        if exc.code in (401, 403):
            raise MetadataError("TMDB API Key 无效或权限不足，请到设置页检查") from exc
        if exc.code == 404:
            raise MetadataError("元数据源没有找到这条记录") from exc
        if exc.code == 429:
            raise MetadataError("请求过于频繁，请稍后再试") from exc
        raise MetadataError(f"元数据源返回错误（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        reason = _redact(str(exc.reason), proxy, "")
        raise MetadataError(f"无法连接元数据源，请检查网络或代理设置（{reason}）") from exc
    except TimeoutError as exc:
        raise MetadataError("元数据源响应超时，请稍后重试") from exc
    except (ValueError, OSError) as exc:
        raise MetadataError(f"读取元数据失败：{_redact(str(exc), proxy, '')}") from exc


def _network_hint(reason: str) -> str:
    text = (reason or "").lower()
    if (
        "connection refused" in text
        or "errno 111" in text
        or "10061" in text
        or "积极拒绝" in text
        or "拒绝" in text
    ):
        return "连接被拒绝：代理软件没有启动，或者端口填错了"
    if "timed out" in text or "timeout" in text or "超时" in text:
        return "连接超时：检查代理地址、网络是否可用，或防火墙是否拦截"
    if (
        "name or service not known" in text
        or "nodename nor servname" in text
        or "找不到主机" in text
        or "未知的名称" in text
    ):
        return "找不到地址：检查代理地址拼写；容器里访问宿主机请用 host.docker.internal"
    if "certificate" in text or "ssl" in text or "证书" in text:
        return "证书校验失败：代理不支持 HTTPS，或证书有问题"
    if "proxy" in text or "代理" in text:
        return "代理连接失败：检查代理地址和端口"
    return reason or "未知网络错误"


def test_connectivity(proxy_url: str = "", api_key: str = "") -> dict:
    """测试 TMDB 连通性；返回可以直接显示给用户的中文结果。"""
    proxy = proxy_url
    key = (api_key or "").strip()
    params = _tmdb_params(key)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **_tmdb_auth(key)}
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(f"{TMDB_BASE}/configuration{query}", headers=headers)
    try:
        opener = _build_opener(proxy)
        with opener.open(request, timeout=TIMEOUT) as response:
            status = int(getattr(response, "status", 200))
            if status == 200:
                message = "连得通，TMDB API Key 有效" if key else "连得通（还没填 TMDB API Key）"
                return {"ok": True, "message": message, "detail": "", "proxy": bool(proxy)}
    except MetadataError as exc:
        return {"ok": False, "message": "连不通", "detail": str(exc), "proxy": bool(proxy)}
    except urllib.error.HTTPError as exc:
        if exc.code == 407:
            return {
                "ok": False,
                "message": "连不通",
                "detail": "代理需要认证或认证失败，请检查代理地址里的用户名和密码",
                "proxy": bool(proxy),
            }
        if exc.code in (502, 503, 504):
            return {
                "ok": False,
                "message": "连不通",
                "detail": f"代理或 TMDB 暂时不可用（HTTP {exc.code}），请稍后重试",
                "proxy": bool(proxy),
            }
        if exc.code == 401:
            message = "连得通，但 TMDB API Key 无效" if key else "连得通，但还没填 TMDB API Key"
            return {"ok": True, "message": message, "detail": "", "proxy": bool(proxy)}
        if exc.code == 429:
            return {"ok": True, "message": "连得通，但请求太频繁被限流", "detail": "", "proxy": bool(proxy)}
        return {
            "ok": True,
            "message": f"连得通，但 TMDB 返回错误（HTTP {exc.code}）",
            "detail": "",
            "proxy": bool(proxy),
        }
    except urllib.error.URLError as exc:
        reason = _redact(str(exc.reason), proxy, key)
        return {
            "ok": False,
            "message": "连不通",
            "detail": _network_hint(reason),
            "proxy": bool(proxy),
        }
    except TimeoutError:
        return {
            "ok": False,
            "message": "连不通",
            "detail": "连接超时：检查代理地址、网络是否可用，或防火墙是否拦截",
            "proxy": bool(proxy),
        }
    except (ValueError, OSError) as exc:
        return {
            "ok": False,
            "message": "连不通",
            "detail": _redact(str(exc), proxy, key),
            "proxy": bool(proxy),
        }
    return {"ok": False, "message": "连不通", "detail": "未知错误", "proxy": bool(proxy)}


def _year_of(text: str | None) -> int | None:
    if not text or not text[:4].isdigit():
        return None
    return int(text[:4])


def _poster(path: str | None) -> str:
    return f"{TMDB_IMAGE}{path}" if path else ""


def _short(text: str | None, length: int = 140) -> str:
    value = (text or "").strip().replace("\n", " ")
    return value[:length] + "…" if len(value) > length else value


def _tmdb_auth(api_key: str) -> dict[str, str]:
    """支持 v3 key（参数）与 v4 token（Bearer）两种填写方式。"""
    if api_key.startswith("eyJ"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _tmdb_params(api_key: str) -> dict[str, str]:
    if api_key.startswith("eyJ"):
        return {}
    return {"api_key": api_key}


def search_tmdb(query: str, api_key: str, language: str = "zh-CN") -> list[dict]:
    """同时搜索剧集与电影，按热度排序返回候选。"""
    text = query.strip()
    if not text:
        raise MetadataError("搜索关键词为空")
    if not api_key:
        raise MetadataError("请先在设置页填写 TMDB API Key")

    headers = _tmdb_auth(api_key)
    common = {"query": text, "language": language, "include_adult": "false", **_tmdb_params(api_key)}
    results: list[dict] = []

    for media_type, path in (("tv", "/search/tv"), ("movie", "/search/movie")):
        data = _get_json(f"{TMDB_BASE}{path}", common, headers)
        for item in data.get("results", [])[:10]:
            if item.get("id") is None:
                continue
            if media_type == "tv":
                name = item.get("name") or item.get("original_name") or ""
                original = item.get("original_name") or ""
            else:
                name = item.get("title") or item.get("original_title") or ""
                original = item.get("original_title") or ""
            results.append(
                {
                    "source": "tmdb",
                    "media_type": media_type,
                    "id": int(item["id"]),
                    "name": name,
                    "original_name": original,
                    "year": _year_of(item.get("first_air_date") or item.get("release_date")),
                    "overview": _short(item.get("overview")),
                    "poster": _poster(item.get("poster_path")),
                    "popularity": float(item.get("popularity") or 0),
                }
            )

    results.sort(key=lambda item: item["popularity"], reverse=True)
    return results[:14]


def search_bangumi(query: str, token: str = "") -> list[dict]:
    """Bangumi 旧版搜索接口（免 key），用于找到更准确的中/日文标题。"""
    text = query.strip()
    if not text:
        raise MetadataError("搜索关键词为空")
    url = f"{BG_BASE}/search/subject/{urllib.parse.quote(text)}"
    params = {"type": "2", "responseGroup": "small", "max_results": "10"}
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _get_json(url, params, headers)
    results = []
    for item in data.get("list", [])[:10]:
        images = item.get("images") or {}
        results.append(
            {
                "source": "bangumi",
                "media_type": "tv",
                "id": int(item.get("id") or 0),
                "name": item.get("name_cn") or item.get("name") or "",
                "original_name": item.get("name") or "",
                "year": _year_of(item.get("air_date")),
                "overview": _short(item.get("summary")),
                "poster": images.get("large") or images.get("common") or "",
                "popularity": float(item.get("rank") or 0),
            }
        )
    return results


def fetch_tv(tv_id: int, api_key: str, language: str, seasons: list[int]) -> dict:
    """取剧集主信息 + 指定季的单集标题。"""
    if not api_key:
        raise MetadataError("请先在设置页填写 TMDB API Key")
    headers = _tmdb_auth(api_key)
    params = {"language": language, **_tmdb_params(api_key)}
    detail = _get_json(f"{TMDB_BASE}/tv/{tv_id}", params, headers)

    episodes: dict[str, dict] = {}
    season_numbers = sorted({int(number) for number in seasons})[:8]
    if not season_numbers:
        season_numbers = [1]
    for number in season_numbers:
        try:
            season = _get_json(f"{TMDB_BASE}/tv/{tv_id}/season/{number}", params, headers)
        except MetadataError:
            continue
        for episode in season.get("episodes", []):
            key = f"{number}:{int(episode.get('episode_number') or 0)}"
            episodes[key] = {
                "name": episode.get("name") or "",
                "air_date": episode.get("air_date") or "",
            }

    return {
        "tmdb_id": int(detail["id"]),
        "media_type": "tv",
        "name": detail.get("name") or "",
        "original_name": detail.get("original_name") or "",
        "year": _year_of(detail.get("first_air_date")),
        "overview": _short(detail.get("overview")),
        "poster": _poster(detail.get("poster_path")),
        "seasons": [
            {
                "season_number": int(item.get("season_number") or 0),
                "name": item.get("name") or "",
                "episode_count": int(item.get("episode_count") or 0),
            }
            for item in detail.get("seasons", [])
        ],
        "episodes": episodes,
    }


def fetch_movie(movie_id: int, api_key: str, language: str) -> dict:
    if not api_key:
        raise MetadataError("请先在设置页填写 TMDB API Key")
    headers = _tmdb_auth(api_key)
    params = {"language": language, **_tmdb_params(api_key)}
    detail = _get_json(f"{TMDB_BASE}/movie/{movie_id}", params, headers)
    return {
        "tmdb_id": int(detail["id"]),
        "media_type": "movie",
        "name": detail.get("title") or "",
        "original_name": detail.get("original_title") or "",
        "year": _year_of(detail.get("release_date")),
        "overview": _short(detail.get("overview")),
        "poster": _poster(detail.get("poster_path")),
        "release_date": detail.get("release_date") or "",
        "seasons": [],
        "episodes": {},
    }
