"""Checkpointer 工厂：把图的状态持久化到本地 SQLite。

为什么需要它（不只是"记住聊天记录"）：
- 多轮记忆：同一 thread_id 的 invoke 会自动带上历史 state。
- 断线/崩溃恢复：状态每步都落盘，重放时从最近的 checkpoint 继续。
- human-in-the-loop 的前提：LangGraph 的 interrupt() 挂起时，把整个状态存进
  checkpointer，之后 resume 才能恢复现场——没有它，interrupt 无法实现。

为什么不用 SqliteSaver.from_conn_string(...)：
那是 contextmanager，连接生命周期绑在一个 with 块内。我们的图要长驻
（CLI 整个会话、FastAPI 进程），所以自己持有连接并复用。

check_same_thread=False：FastAPI/多线程下可能从不同线程访问同一连接，
需显式放开该限制（SQLite 默认禁止跨线程复用连接）。
"""

import sqlite3
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings


@lru_cache(maxsize=1)
def get_checkpointer() -> SqliteSaver:
    """进程内单例：一个 SQLite 文件承载所有会话的 checkpoints。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.data_dir / "checkpoints.db",
        check_same_thread=False,
    )
    saver = SqliteSaver(conn)
    saver.setup()  # 建表，幂等
    return saver
