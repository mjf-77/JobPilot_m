"""调用计量：统计「一次请求」内的 LLM 调用次数与 token 用量。

为什么用 contextvars：
计量的边界是「一次请求」，但 LLM 调用散落在路由节点、各 Agent 节点里。
用上下文变量可以在请求入口设置、在图内任意深度累加，
既不用把计数器一路当参数传下去，也不会在并发请求之间串数据。
"""

import contextvars
from dataclasses import dataclass

from app.config import settings


@dataclass
class Usage:
    llm_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def cost_yuan(self) -> float:
        """按配置的单价换算成本。

        单价是配置项而不是硬编码——模型定价会变，且不同模型价格不同。
        """
        return (
            self.prompt_tokens / 1_000_000 * settings.price_input_per_million
            + self.completion_tokens / 1_000_000 * settings.price_output_per_million
        )

"""Python 标准库 `contextvars`，**上下文变量**。
作用：在异步代码（asyncio、LangGraph 异步链路）里面，保存**每个任务独立的上下文数据**"""

_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar(
    "usage", default=None#usage是用来记录当前请求的计量信息
)


def start() -> Usage:
    """请求入口调用：开启一份新的计量。"""
    usage = Usage()
    _usage.set(usage)
    return usage


def record(prompt_tokens: int, completion_tokens: int) -> None:
    """每次 LLM 调用后调用；未开启计量时静默忽略（脚本里直接调用也不报错）。"""
    usage = _usage.get()
    if usage is None:
        return
    usage.llm_calls += 1
    usage.prompt_tokens += prompt_tokens or 0
    usage.completion_tokens += completion_tokens or 0


def current() -> Usage | None:
    return _usage.get()


def record_tool() -> None:
    """每次工具调用后调用（由 ToolRegistry 统一触发）。"""
    usage = _usage.get()
    if usage is None:
        return
    usage.tool_calls += 1
