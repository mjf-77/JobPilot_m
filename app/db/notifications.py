"""站内提醒：定时任务写、前端读。

只有「写一条」「读最近几条」「标记已读」三个操作——提醒是不可编辑的，
用户唯一的动作就是看一眼、然后消掉红点。
"""

from datetime import datetime

from sqlalchemy import func, select, update

from app.db.base import SessionLocal
from app.db.models import Notification


def add(user_id: str, content: str) -> None:
    with SessionLocal() as session:
        session.add(
            Notification(
                user_id=user_id,
                content=content,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        session.commit()


def list_for(user_id: str, limit: int = 20) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.id.desc())
            .limit(limit)
        ).all()
    return [
        {
            "id": r.id,
            "content": r.content,
            "created_at": r.created_at.replace("T", " ")[:16],
            "is_read": bool(r.is_read),
        }
        for r in rows
    ]


def unread_count(user_id: str) -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.is_read == 0)
        ) or 0


def mark_all_read(user_id: str) -> int:
    """把该用户的提醒全部标为已读，返回受影响条数。"""
    with SessionLocal() as session:
        result = session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read == 0)
            .values(is_read=1)
        )
        session.commit()
        return result.rowcount
