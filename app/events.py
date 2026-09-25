"""SSE 事件协议：Agent 内部往外的唯一出口。

事件类型（与前端约定）：
- agent_switch : 切换到某个 Agent（前端显示提示）
- token        : 模型输出的一个增量片段
- confirm      : 高风险操作待确认
- error        : 本次执行出错
- done         : 本轮结束（API 层补发）

为什么不用 LangGraph 的 get_stream_writer：
实测**子图节点**里写的事件到不了主图的 custom stream（主图节点可以）。
而我们 4 个 Agent 全在子图里，结果就是——除了闲聊分支，所有 Agent 的
回复在浏览器里都是空白。改用自己的 sink（contextvar + 图在单线程内执行）
之后，主图、子图一视同仁。
"""

import contextvars
from typing import Any, Callable

_sink: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "event_sink", default=None
)


def set_sink(fn: Callable[[dict], None] | None) -> None:
    """设置事件出口（请求入口调用，通常是把事件写进队列）。"""
    _sink.set(fn)


def emit(event_type: str, **payload: Any) -> None:
    """推送一个事件；未设置出口时静默忽略，绝不打断业务逻辑。

    静默忽略是有意为之：图也可能被脚本直接 invoke（跑测试、离线批处理），
    那些场景下没人消费事件，但业务必须照常跑完。
    """
    fn = _sink.get()
    if fn is None:
        return
    fn({"type": event_type, **payload})
