"""聊天 API：把 LangGraph 的 custom stream 转成 SSE 推给前端。

三个关键设计：
1. 用 POST + fetch 而不是 EventSource（后者只支持 GET，不能带请求体/header）
2. 用 stream_mode="custom"（只发节点 emit 的事件，不吐内部 state）
3. 在独立线程里跑完整张图（见 _stream_graph 的说明）
"""

import json
import queue
import threading
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app import auth, metering
from app.db import runs, threads
from app.events import set_sink
from app.graph.checkpointer import get_checkpointer
from app.graph.supervisor import build_supervisor

router = APIRouter()

# 图只编译一次（进程内单例）：checkpointer 有状态，重复编译会白白多建连接
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_supervisor(checkpointer=get_checkpointer())
    return _graph


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: str = Field(min_length=1, max_length=64)


class ConfirmRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    approved: bool


def _frame(event: dict) -> str:
    """SSE 帧格式：`data: <json>\\n\\n`（空行是帧分隔符）。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream_graph(graph_input, thread_id: str, user_id: int):
    """执行图并逐帧产出 SSE。

    为什么在独立线程里跑整张图：
    Starlette 迭代同步生成器是「逐块丢线程池」，每次 next() 不一定同线程，
    而计量（contextvars）和当前用户上下文都依赖线程一致性——
    切线程会让它们丢失（实测会漏掉一半 LLM 调用的 token）。
    把整图收敛到一个 worker 线程内执行，事件再用队列搬回生成器逐帧 yield。

    thread_id 会加上用户前缀：防止用户猜到别人的会话 id 就能读到别人的历史。
    """
    scoped_thread = f"{user_id}:{thread_id}"
    config = {"configurable": {"thread_id": scoped_thread}}
    events: queue.Queue = queue.Queue()
    box: dict = {}

    # 会话元数据（左栏历史列表用）：首次创建时用用户首条消息当标题。
    # 放在这里而不是 worker 里，是因为它只用请求参数、不依赖图内上下文。
    first_text = ""
    if isinstance(graph_input, dict):
        first_msgs = graph_input.get("messages") or []
        if first_msgs:
            first_text = getattr(first_msgs[0], "content", "") or ""
    threads.touch(str(user_id), thread_id, first_text)

    def worker() -> None:
        # 计量、用户上下文、事件出口都必须在这个线程里开启
        box["usage"] = metering.start()
        auth.set_current_user(user_id)
        set_sink(lambda event: events.put(("event", event)))
        try:
            # 事件已经走 sink 实时进队列了，这里不需要 LangGraph 的流式输出，
            # 用 invoke 反而更简单（子图的 custom 事件本来就冒不出来）。
            get_graph().invoke(graph_input, config)
        except Exception as exc:
            box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            events.put(("end", None))

    threading.Thread(target=worker, daemon=True).start()

    started = time.perf_counter()
    route = ""
    while True:
        kind, payload = events.get()
        if kind == "end":
            break
        if payload.get("type") == "agent_switch" and not route:
            route = payload.get("route", "")
        yield _frame(payload)

    usage = box.get("usage") or metering.Usage()
    latency_ms = int((time.perf_counter() - started) * 1000)
    error = box.get("error")

    if error:
        yield _frame({"type": "error", "message": error})

    # 无论成功、失败还是客户端断开，这一次执行都要留痕
    runs.save(
        route=route,
        llm_calls=usage.llm_calls,
        tool_calls=usage.tool_calls,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_yuan=usage.cost_yuan,
        latency_ms=latency_ms,
        ok=not error,
        thread_id=scoped_thread,
    )

    # 收尾帧带本轮用量，前端可以直接显示"本次消耗"
    yield _frame(
        {
            "type": "done",
            "usage": {
                "llm_calls": usage.llm_calls,
                "tool_calls": usage.tool_calls,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_yuan": round(usage.cost_yuan, 6),
                "latency_ms": latency_ms,
            },
        }
    )


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 若有反向代理，禁用其缓冲
        },
    )


@router.post("/api/chat")
def chat(req: ChatRequest, user_id: int = Depends(auth.current_user)) -> StreamingResponse:
    graph_input = {"messages": [HumanMessage(content=req.message)]}
    return _sse_response(_stream_graph(graph_input, req.thread_id, user_id))


@router.post("/api/chat/confirm")
def confirm(
    req: ConfirmRequest, user_id: int = Depends(auth.current_user)
) -> StreamingResponse:
    """恢复被 interrupt 挂起的执行。

    高风险工具执行前会挂起等确认，前端点「确认/取消」后调这里，
    用 Command(resume=...) 把决定送回图里，从挂起点继续跑。
    """
    graph_input = Command(resume={"approved": req.approved})
    return _sse_response(_stream_graph(graph_input, req.thread_id, user_id))


@router.get("/api/runs/summary")
def runs_summary(user_id: int = Depends(auth.current_user)) -> dict:
    """全局用量汇总（总调用次数 / token / 成本 / 平均延迟）。"""
    return runs.summary()


@router.get("/api/runs")
def runs_recent(
    limit: int = 10, user_id: int = Depends(auth.current_user)
) -> list[dict]:
    """最近的执行记录。"""
    return runs.recent(limit)
