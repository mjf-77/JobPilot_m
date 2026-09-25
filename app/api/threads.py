"""会话管理：列表、历史消息、删除。

历史消息直接从图的 checkpoint 里读——状态本来就在那儿，
不必再存一份纯文本记录（避免两处数据不一致）。
"""

from fastapi import APIRouter, Depends
from langchain_core.messages import AIMessage, HumanMessage

from app import auth
from app.api.chat import get_graph
from app.db import threads

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _config(user_id: int, thread_id: str) -> dict:
    """会话隔离：thread_id 始终带用户前缀，防止越权读别人的历史。"""
    return {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}


@router.get("")
def list_threads(user_id: int = Depends(auth.current_user)) -> list[dict]:
    return threads.list_for(str(user_id))


@router.get("/{thread_id}/messages")
def thread_messages(
    thread_id: str, user_id: int = Depends(auth.current_user)
) -> list[dict]:
    """某个会话的历史消息（只取用户与助手的可见消息，工具消息不外露）。"""
    state = get_graph().get_state(_config(user_id, thread_id))
    messages = (state.values or {}).get("messages", [])

    history: list[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            history.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage) and msg.content:
            # 带工具调用的 AIMessage 可能 content 为空，跳过
            history.append({"role": "assistant", "content": msg.content})
    return history


@router.delete("/{thread_id}")
def delete_thread(
    thread_id: str, user_id: int = Depends(auth.current_user)
) -> dict:
    threads.remove(str(user_id), thread_id)
    return {"deleted": thread_id}
