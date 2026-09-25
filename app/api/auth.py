"""注册 / 登录接口。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import auth
from app.db import users

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)


@router.post("/register")
def register(body: Credentials) -> dict:
    user_id = users.create(body.username, body.password)
    if user_id is None:
        raise HTTPException(status_code=409, detail="用户名已被占用")
    return {"token": auth.create_token(user_id), "username": body.username}


@router.post("/login")
def login(body: Credentials) -> dict:
    user_id = users.verify(body.username, body.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": auth.create_token(user_id), "username": body.username}
