"""站内提醒 API：列表 + 未读数 + 标记已读。

提醒由定时任务写入（见 app/scheduler.py），前端只读。
"""

from fastapi import APIRouter, Depends

from app import auth
from app.db import notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = False, user_id: int = Depends(auth.current_user)
) -> dict:
    items = notifications.list_for(str(user_id))
    if unread_only:
        items = [item for item in items if not item["is_read"]]
    return {"unread": notifications.unread_count(str(user_id)), "items": items}


@router.post("/read")
def mark_read(user_id: int = Depends(auth.current_user)) -> dict:
    """一次全标已读——提醒不值得做逐条已读的交互。"""
    return {"updated": notifications.mark_all_read(str(user_id))}
