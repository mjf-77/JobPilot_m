"""工具网关：所有工具调用的统一入口。

把「超时 / 重试 / 审计 / 权限标记」四件横切关注点收在一处，
工具函数本身只写业务逻辑。新增工具不用重复处理这些事，
"Agent 的工具调用不可控"这个痛点也就有了统一答案。
"""

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

from app import metering as meter
from app.db import audit

# 共享线程池，用于给同步工具加超时。
# 已知代价：Python 无法真正杀死线程，超时后我们放弃等待并返回错误，
# 被卡住的工具线程会自己跑完（因此工具实现应尽量短小且可重入）。
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema，直接喂给 Function Calling
    func: Callable[..., str]
    requires_confirmation: bool = False  # 高风险操作：执行前需人工确认
    timeout: float = 10.0
    max_retries: int = 1


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def to_openai_tools(self) -> list[dict]:
        """转成 Function Calling 的 tools 参数格式。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    def execute(self, name: str, args: dict, thread_id: str = "") -> str:
        """统一执行：超时 → 重试 → 审计。

        异常一律转成「给模型看的错误文本」，而不是抛出去：
        工具的调用者是 LLM，把错误回灌给它，它才有机会换参数重试或改用别的工具；
        直接抛异常会中断整轮对话。
        """
        spec = self._tools.get(name)
        if spec is None:
            return f"错误：工具 {name} 不存在，可用工具：{', '.join(self.names())}"

        meter.record_tool()  # 计入本次请求的工具调用次数（runs 表用）

        last_error = ""
        for _ in range(spec.max_retries + 1):
            started = time.perf_counter()
            try:
                result = self._run_with_timeout(spec, args)
                self._log(spec, args, True, result, None, started, thread_id)
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._log(spec, args, False, None, last_error, started, thread_id)

        return f"错误：工具 {name} 执行失败（已重试 {spec.max_retries} 次）：{last_error}"

    @staticmethod
    def _run_with_timeout(spec: ToolSpec, args: dict) -> str:
        # 工具跑在独立线程池里，必须把当前 context 一起带过去。
        # 否则由 contextvar 承载的「当前用户」等上下文会丢（实测：写库时 user_id 为空）。
        ctx = contextvars.copy_context()
        future = _EXECUTOR.submit(ctx.run, spec.func, **args)
        try:
            return future.result(timeout=spec.timeout)
        except FutureTimeout:
            raise TimeoutError(f"工具 {spec.name} 超过 {spec.timeout}s 未返回")

    @staticmethod
    def _log(spec, args, ok, result, error, started, thread_id) -> None:
        audit.log_call(
            tool=spec.name,
            args=args,
            ok=ok,
            result=result,
            error=error,
            duration_ms=int((time.perf_counter() - started) * 1000),
            thread_id=thread_id,
        )
