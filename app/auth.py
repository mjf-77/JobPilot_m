"""认证：JWT 签发校验 + 当前用户上下文。

两个层次：
1. HTTP 层：把 `Authorization: Bearer <token>` 解析成 user_id（FastAPI 依赖）
2. 图内层：让工具知道「现在是谁在操作」——用 contextvar 传递

为什么图内层可以用 contextvar：图在**单个 worker 线程内**执行
（见 app/api/chat.py 里为什么这么设计），同一线程里 contextvar 是一致的。
"""

import contextvars
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer = HTTPBearer(auto_error=False)

_current_user: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user", default=""
)


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    """FastAPI 依赖：从 Bearer token 解析用户 id，失败一律 401。"""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录"
        )
    user_id = decode_token(creds.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    return user_id


def set_current_user(user_id: int) -> None:
    _current_user.set(str(user_id))


def current_user_id() -> str:
    return _current_user.get()
