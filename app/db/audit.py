"""工具调用审计：每次调用落一条记录，可回溯。

为什么需要：Agent 的「黑盒感」主要来自工具调用不可见——
失败了几次、花了多久、参数传了什么，全靠猜。落库后这些问题都能查。
也是 runs 表（请求级汇总）的明细补充。
"""

from datetime import datetime

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import ToolCall


def log_call(
    tool: str,
    args: dict,
    ok: bool,
    result: str | None,
    error: str | None,
    duration_ms: int,
    thread_id: str = "",
) -> None:
    with SessionLocal() as session:
        session.add(
            ToolCall(
                created_at=datetime.now().isoformat(timespec="seconds"),
                thread_id=thread_id,
                tool=tool,
                args=str(args),
                ok=int(ok),
                result=(result or "")[:500],
                error=error,
                duration_ms=duration_ms,
            )
        )
        session.commit()


def recent(limit: int = 20) -> list[dict]:
    """最近的调用记录，倒序。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(ToolCall).order_by(ToolCall.id.desc()).limit(limit)
        ).all()
    return [
        {
            "created_at": r.created_at,
            "tool": r.tool,
            "args": r.args,
            "ok": r.ok,
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]
