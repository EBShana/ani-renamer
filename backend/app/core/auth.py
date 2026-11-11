"""访问密码：密码只保存不可逆哈希，登录状态用随机 token + 数据库会话。

- 哈希算法用标准库 hashlib.scrypt（无需额外依赖）。
- 浏览器里只放随机 token（HttpOnly Cookie），数据库里只存 token 的 SHA-256。
- 连续输错 5 次锁定 5 分钟（按来源 IP 计数，进程内存储，重启后清零）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from ..config import read_auth_hash, write_auth_hash
from ..store import (
    cleanup_expired_sessions,
    create_session,
    delete_all_sessions,
    delete_session,
    session_is_valid,
)

COOKIE_NAME = "ani_session"
SESSION_DAYS = 7
MAX_FAILS = 5
LOCK_SECONDS = 300
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32

_fail_lock = threading.Lock()
_failures: dict[str, dict[str, float]] = {}


def password_enabled() -> bool:
    return bool(read_auth_hash())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str) -> bool:
    stored = read_auth_hash()
    if not stored:
        return False
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_new_password(password: str) -> str:
    """返回校验后的密码；不合法时抛中文 ValueError。"""
    text = str(password or "")
    if not text.strip():
        raise ValueError("访问密码不能为空")
    if len(text) < 4:
        raise ValueError("访问密码至少 4 个字符")
    if len(text) > 128:
        raise ValueError("访问密码最多 128 个字符")
    return text


def set_password(password: str | None) -> bool:
    """设置/修改密码；传 None 表示关闭密码。返回当前是否启用。"""
    if password is None:
        write_auth_hash(None)
        delete_all_sessions()
        return False
    text = validate_new_password(password)
    write_auth_hash(hash_password(text))
    # 修改密码后让所有旧登录失效
    delete_all_sessions()
    return True


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    create_session(_token_hash(token), expires)
    cleanup_expired_sessions()
    return token


def is_authenticated(token: str | None) -> bool:
    if not password_enabled():
        return True
    if not token:
        return False
    return session_is_valid(_token_hash(token))


def logout(token: str | None) -> None:
    if token:
        delete_session(_token_hash(token))


def _client_key(ip: str) -> str:
    return ip or "unknown"


def locked_seconds(ip: str) -> int:
    key = _client_key(ip)
    with _fail_lock:
        state = _failures.get(key)
        if not state:
            return 0
        remain = int(state.get("locked_until", 0) - time.time())
        return max(0, remain)


def register_failure(ip: str) -> int:
    """记录一次失败；返回剩余可尝试次数（0 表示已锁定）。"""
    key = _client_key(ip)
    now = time.time()
    with _fail_lock:
        state = _failures.setdefault(key, {"fails": 0, "locked_until": 0})
        if state.get("locked_until", 0) > now:
            return 0
        state["fails"] = float(state.get("fails", 0)) + 1
        if state["fails"] >= MAX_FAILS:
            state["fails"] = 0
            state["locked_until"] = now + LOCK_SECONDS
            return 0
        return int(MAX_FAILS - state["fails"])


def reset_failures(ip: str) -> None:
    with _fail_lock:
        _failures.pop(_client_key(ip), None)
