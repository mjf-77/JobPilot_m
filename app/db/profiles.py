"""用户画像的读写：跨会话的长期记忆。

和 applications 的区别：投递记录是「业务数据」（用户自己也会去看），
画像是「给模型看的背景」（用户不直接管理，但影响每次回答的针对性）。

约定：value 传空字符串表示**删除**该字段——用户说「忘掉我之前说的目标岗位」时走这条路，
不用单独设计一个删除工具。
"""

from datetime import datetime

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import UserProfile


def get_all(user_id: str) -> dict[str, str]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).all()
    return {r.field: r.value for r in rows}


def set_one(user_id: str, field: str, value: str) -> str:
    """写入一条画像；value 为空则删除。返回给模型看的确认文案。"""
    field = field.strip()
    value = value.strip()
    if not field:
        return "字段名不能为空。"

    now = datetime.now().isoformat(timespec="seconds")
    with SessionLocal() as session:
        row = session.scalar(
            select(UserProfile).where(
                UserProfile.user_id == user_id, UserProfile.field == field
            )
        )
        if not value:
            if row is not None:
                session.delete(row)
            session.commit()
            return f"已忘掉「{field}」"

        if row is None:
            session.add(
                UserProfile(user_id=user_id, field=field, value=value, updated_at=now)
            )
        else:
            row.value, row.updated_at = value, now
        session.commit()
    return f"已记住：{field} = {value}"


def format_block(profile: dict[str, str]) -> str:
    """渲染成注入 prompt 的片段；没有画像时返回空串（调用方据此跳过注入）。"""
    if not profile:
        return ""
    lines = "\n".join(f"- {k}：{v}" for k, v in profile.items())
    return f"<user_profile>\n{lines}\n</user_profile>"
