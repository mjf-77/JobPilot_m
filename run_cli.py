"""JobPilot 命令行入口：验证「路由 → 职位分析 / 闲聊」闭环（多轮记忆版）。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe run_cli.py              # 默认会话
    D:\\dev\\python\\python.exe run_cli.py my-thread    # 指定会话名

会话状态存在 data/checkpoints.db：同一个 thread 关掉再启动，历史仍在，
这正是 checkpointer 带来的「跨进程持久化」。

交互命令：/new 开启新会话（验证会话隔离）；q 退出。
"""

import sys
import uuid
from pathlib import Path

# 允许直接以脚本方式运行（把 jobpilot/ 加入模块搜索路径）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 终端默认 GBK 编码，打印 ✅/⚠️ 这类符号会抛 UnicodeEncodeError。
# 把标准流切到 UTF-8，脚本在任意终端都能直接跑，无需手动设环境变量。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.graph.checkpointer import get_checkpointer  # noqa: E402
from app.graph.supervisor import build_supervisor  # noqa: E402


def main() -> None:
    thread_id = sys.argv[1] if len(sys.argv) > 1 else "cli-default"
    app = build_supervisor(checkpointer=get_checkpointer())

    print(f"JobPilot CLI —— 会话: {thread_id}（/new 开新会话，q 退出）")
    print("试试：帮我分析下这个 JD：招聘 Python 后端，要求 3 年经验、熟悉 LangChain / RAG ...\n")

    while True:
        try:
            text = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"q", "quit", "exit"}:
            break
        if not text:
            continue
        if text == "/new":
            thread_id = f"cli-{uuid.uuid4().hex[:8]}"
            print(f"已开启新会话: {thread_id}\n")
            continue

        # thread_id 是 checkpointer 的索引键：同一 id 自动续上历史 state
        config = {"configurable": {"thread_id": thread_id}}
        result = app.invoke({"messages": [HumanMessage(content=text)]}, config)
        print(f"[路由] {result.get('route')}   [技能] {result.get('active_skill') or '-'}")
        print(f"JobPilot: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
