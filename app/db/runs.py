"""执行记录（trace）：每次请求落一条，可回放、可统计。

和 tool_calls 审计表的区别：
- tool_calls：单次工具调用的明细（参数、结果、耗时）
- runs：一次完整请求的汇总（走了哪个 Agent、几次 LLM、多少 token、多少钱）

两者配合，才能回答「这次为什么慢」「这个月花了多少」。
"""

from datetime import datetime

from sqlalchemy import func, select

from app.db.base import SessionLocal
from app.db.models import Run


def save(
    route: str,
    llm_calls: int,
    tool_calls: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost_yuan: float,
    latency_ms: int,
    ok: bool,
    thread_id: str = "",
) -> None:
    with SessionLocal() as session:
        session.add(
            Run(
                created_at=datetime.now().isoformat(timespec="seconds"),
                thread_id=thread_id,
                route=route,
                llm_calls=llm_calls,
                tool_calls=tool_calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_yuan=round(cost_yuan, 6),
                latency_ms=latency_ms,
                ok=int(ok),
            )
        )
        session.commit()


def recent(limit: int = 10) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(select(Run).order_by(Run.id.desc()).limit(limit)).all()
    return [
        {
            "created_at": r.created_at,
            "route": r.route,
            "llm_calls": r.llm_calls,
            "tool_calls": r.tool_calls,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "cost_yuan": r.cost_yuan,
            "latency_ms": r.latency_ms,
            "ok": r.ok,
        }
        for r in rows
    ]


def summary() -> dict:
    """全局汇总：总量、平均延迟、总成本——简历上写数字就用它。"""
    with SessionLocal() as session:
        row = session.execute(
            select(
                func.count(Run.id),
                func.sum(Run.llm_calls),
                func.sum(Run.tool_calls),
                func.sum(Run.prompt_tokens),
                func.sum(Run.completion_tokens),
                func.sum(Run.cost_yuan),
                func.avg(Run.latency_ms),
            )
        ).one()

    return {
        "runs": row[0] or 0,
        "llm_calls": row[1] or 0,
        "tool_calls": row[2] or 0,
        "prompt_tokens": row[3] or 0,
        "completion_tokens": row[4] or 0,
        "cost_yuan": float(row[5] or 0),
        "avg_latency_ms": float(row[6] or 0),
    }
