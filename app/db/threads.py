"""会话元数据：列表、标题、删除（左栏历史会话用）。"""

from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Thread


def touch(user_id: str, thread_id: str, first_message: str = "") -> None:
    """记录一次会话活动；首次创建时用首条消息生成标题。"""
    now = datetime.now().isoformat(timespec="seconds")
    with SessionLocal() as session:
        row = session.scalar(
            select(Thread).where(
                Thread.user_id == user_id, Thread.thread_id == thread_id
            )
        )
        if row is None:
            title = first_message.strip().replace("\n", " ")[:30] or "新会话"
            session.add(
                Thread(
                    user_id=user_id,
                    thread_id=thread_id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.updated_at = now
        session.commit()


def list_for(user_id: str, limit: int = 50) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Thread)
            .where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc())
            .limit(limit)
        ).all()
    return [
        {"thread_id": r.thread_id, "title": r.title, "updated_at": r.updated_at}
        for r in rows
    ]


def remove(user_id: str, thread_id: str) -> None:
    with SessionLocal() as session:
        session.execute(
            sql_delete(Thread).where(
                Thread.user_id == user_id, Thread.thread_id == thread_id
            )
        )
        session.commit()
