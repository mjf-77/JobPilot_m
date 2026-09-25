"""human-in-the-loop 自检：直接驱动 progress_tracker 子图，验证 interrupt 挂起与恢复。

刻意不走主图路由——路由判断会引入噪声，而这里要单独验证「挂起 → 确认 → 恢复执行」这条机制。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\interrupt_check.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.graph.agents.progress_tracker import build_progress_tracker  # noqa: E402
from app.graph.checkpointer import get_checkpointer  # noqa: E402


def show(title: str, state: dict) -> None:
    print(f"\n===== {title} =====")
    last = state["messages"][-1]
    print(f"[{getattr(last, 'type', '?')}] {(last.content or '')[:220]}")
    if "__interrupt__" in state:
        print("!! interrupt 已触发：", state["__interrupt__"])


def main() -> None:
    app = build_progress_tracker(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": f"hitl-{uuid.uuid4().hex[:6]}"}}

    state = app.invoke(
        {"messages": [HumanMessage(content="记录一下：我投了某公司 数据开发岗，状态已投递")]},
        config,
    )
    show("1. 建一条记录", state)

    state = app.invoke(
        {"messages": [HumanMessage(content="把某公司 数据开发岗这条记录删掉")]}, config
    )
    show("2. 请求删除（期望挂起等确认）", state)
    if "__interrupt__" not in state:
        print("\n!! 未触发 interrupt，测试中止")
        return

    state = app.invoke(Command(resume={"approved": False}), config)
    show("3. 用户拒绝（不应删除）", state)

    state = app.invoke({"messages": [HumanMessage(content="现在还有哪些记录")]}, config)
    show("4. 拒绝后查询（记录应还在）", state)

    state = app.invoke(
        {"messages": [HumanMessage(content="删掉某公司 数据开发岗")]}, config
    )
    if "__interrupt__" in state:
        state = app.invoke(Command(resume={"approved": True}), config)
        show("5. 用户同意（应删除）", state)

    state = app.invoke({"messages": [HumanMessage(content="再查一遍记录")]}, config)
    show("6. 删除后查询（记录应消失）", state)


if __name__ == "__main__":
    main()
