"""访问密码接口：状态、登录、退出、设置/修改/关闭密码。

密码哈希只写进 settings.json 的 auth.password_hash，接口响应和日志里都不会出现。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..core import auth

router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.get("/auth/status")
def auth_status(request: Request) -> dict:
    return {
        "enabled": auth.password_enabled(),
        "authenticated": auth.is_authenticated(request.cookies.get(auth.COOKIE_NAME)),
    }


class LoginPayload(BaseModel):
    password: str = ""


@router.post("/auth/login")
def auth_login(payload: LoginPayload, request: Request, response: Response) -> dict:
    if not auth.password_enabled():
        return {"ok": True, "enabled": False}
    ip = _client_ip(request)
    remain = auth.locked_seconds(ip)
    if remain > 0:
        minutes = max(1, (remain + 59) // 60)
        raise HTTPException(
            status_code=429,
            detail=f"密码连续输错次数过多，请等待约 {minutes} 分钟后再试",
        )
    if not auth.verify_password(payload.password):
        left = auth.register_failure(ip)
        if left <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"密码连续输错 {auth.MAX_FAILS} 次，已临时锁定 {auth.LOCK_SECONDS // 60} 分钟，请稍后再试",
            )
        raise HTTPException(status_code=401, detail=f"密码不正确，还可以再试 {left} 次")
    auth.reset_failures(ip)
    _set_cookie(response, auth.new_session())
    return {"ok": True, "enabled": True}


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    auth.logout(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


class PasswordPayload(BaseModel):
    password: str | None = None


@router.post("/auth/password")
def auth_set_password(payload: PasswordPayload, request: Request, response: Response) -> dict:
    """设置 / 修改 / 关闭访问密码。password 为空表示关闭。"""
    try:
        enabled = auth.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not enabled:
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"enabled": False}
    # 修改密码会作废所有旧会话，给当前浏览器重新发一个
    _set_cookie(response, auth.new_session())
    return {"enabled": True}
