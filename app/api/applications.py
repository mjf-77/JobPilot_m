"""投递记录的 REST 入口：结构化操作走表单/表格，不走 LLM。

投递记录只有「公司 / 岗位 / 状态」三个明确字段，用对话录入要等模型理解，
还可能理解偏；而「哪些超过两周没回音」这类模糊查询对话更合适。

状态是会变的（投递→笔试→面试），公司名岗位名也可能记错，
所以这里给出行内编辑和删除的能力——点一下就能改，不用「删了再加」。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import auth
from app.db import applications

router = APIRouter(prefix="/api/applications", tags=["applications"])

# 取值与 applications.STATUSES 一致，写成字面量是为了让校验和接口文档都带上枚举
Status = Literal["已投递", "笔试", "面试", "offer", "已拒"]


class ApplicationIn(BaseModel):
    company: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=128)
    status: Status
    note: str = Field(default="", max_length=500)


def _snapshot(user_id: int) -> dict:
    """当前用户的完整进度表。前端拿 summary + items 自己渲染成可操作的表格。"""
    rows = applications.list_rows(user_id=str(user_id))
    return {"summary": applications.summarize(rows), "items": rows}


def _clean(payload: ApplicationIn) -> tuple[str, str, str]:
    """去空格后再判空。

    Field(min_length=1) 挡不住 "   " 这种全空格输入（长度够，strip 完才是空的），
    不补这一步就会往库里写一条没有公司名的记录。
    """
    company, role = payload.company.strip(), payload.role.strip()
    if not company or not role:
        raise HTTPException(status_code=422, detail="公司和岗位不能为空")
    return company, role, payload.note.strip()


@router.get("")
def list_applications(user_id: int = Depends(auth.current_user)) -> dict:
    return _snapshot(user_id)


@router.post("")
def add_application(
    payload: ApplicationIn, user_id: int = Depends(auth.current_user)
) -> dict:
    """新增或更新一条记录，顺手返回最新进度表——前端一次请求就完成「写入 + 刷新」。"""
    company, role, note = _clean(payload)
    message = applications.save(
        company, role, payload.status, note, user_id=str(user_id)
    )
    return {"message": message, **_snapshot(user_id)}


@router.put("/{app_id}")
def update_application(
    app_id: int, payload: ApplicationIn, user_id: int = Depends(auth.current_user)
) -> dict:
    """整条覆盖更新（公司、岗位、状态、备注都能改，不用删了重加）。"""
    company, role, note = _clean(payload)
    try:
        ok = applications.update(
            app_id, company, role, payload.status, note, user_id=str(user_id)
        )
    except applications.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not ok:
        # 别人的记录也返回 404 而不是 403：不透露「这条存在但不属于你」
        raise HTTPException(status_code=404, detail="记录不存在")
    return _snapshot(user_id)


@router.delete("/{app_id}")
def delete_application(
    app_id: int, user_id: int = Depends(auth.current_user)
) -> dict:
    if not applications.delete_by_id(app_id, user_id=str(user_id)):
        raise HTTPException(status_code=404, detail="记录不存在")
    return _snapshot(user_id)
