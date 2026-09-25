"""进度管家 + human-in-the-loop 自检。

验证三件事：
1. 路由能把「记录投递」类请求交给 progress_tracker
2. 普通工具（save_application）直接执行并落库
3. 高风险工具（delete_application）会挂起等确认——拒绝时不执行、同意时才执行

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\progress_check.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.graph.checkpointer import get_checkpointer  # noqa: E402
from app.graph.supervisor import build_supervisor  # noqa: E402


def show(title: str, state: dict) -> None:
    print(f"\n===== {title} =====")
    print("路由:", state.get("route"))
    last = state["messages"][-1]
    print("最后一条消息类型:", getattr(last, "type", "?"))
    print("内容:", (last.content or "")[:260])
    if "__interrupt__" in state:
        print("!! 触发 interrupt（挂起等确认）")


def main() -> None:
    app = build_supervisor(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": f"progress-{uuid.uuid4().hex[:6]}"}}

    state = app.invoke(
        {"messages": [HumanMessage(content="帮我记录一下：我投了字节跳动的 AI 应用开发岗，状态是已投递")]},
        config,
    )
    show("1. 记录投递（普通工具，直接执行）", state)

    state = app.invoke({"messages": [HumanMessage(content="我现在有哪些投递记录？")]}, config)
    show("2. 查询进度", state)

    state = app.invoke({"messages": [HumanMessage(content="把字节跳动那条记录删掉")]}, config)
    show("3. 请求删除（高风险 → 应挂起）", state)
    if "__interrupt__" not in state:
        print("\n!! 预期触发 interrupt，但没有触发，后续测试跳过")
        return

    state = app.invoke(Command(resume={"approved": False}), config)
    show("4. 用户拒绝 → 不执行", state)

    state = app.invoke({"messages": [HumanMessage(content="算了，还是删掉吧")]}, config)
    show("5. 再次请求删除", state)
    if "__interrupt__" in state:
        state = app.invoke(Command(resume={"approved": True}), config)
        show("6. 用户同意 → 执行删除", state)

    state = app.invoke({"messages": [HumanMessage(content="再查一下我的记录")]}, config)
    show("7. 删除后查询", state)


if __name__ == "__main__":
    main()
