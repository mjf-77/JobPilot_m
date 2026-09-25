"""模拟面试子图自检：路由 → 多轮面试 → 自动进入复盘。

为控制耗时，把 interview_max_questions 临时压到 2（真实默认为 5）。
验证点：
  1. 「帮我模拟面试」能被路由到 mock_interviewer（并带上正确的 Skill）
  2. 面试进行中，即使回答内容不像面试请求，也能被短路续接（不被误判成闲聊）
  3. 题数用尽后自动切到 review 节点，输出结构化复盘

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\interview_check.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.config import settings  # noqa: E402
from app.graph.checkpointer import get_checkpointer  # noqa: E402
from app.graph.supervisor import build_supervisor  # noqa: E402

TURNS = [
    "帮我模拟面试，岗位是 AI 应用开发（Python）",
    "我做过一个 RAG 项目，用 Chroma 存向量，检索后拼进 prompt 让模型回答，效果还行。",
    "chunk size 用的 500，overlap 50。",
]


def main() -> None:
    settings.interview_max_questions = 2  # 缩短以便快速验证
    app = build_supervisor(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": f"interview-{uuid.uuid4().hex[:6]}"}}

    for i, text in enumerate(TURNS, 1):
        state = app.invoke({"messages": [HumanMessage(content=text)]}, config)
        interview = state.get("interview") or {}
        print(f"\n===== 第 {i} 轮 =====")
        print(f"[路由] {state.get('route')}   [技能] {state.get('active_skill') or '-'}")
        print(f"[面试状态] {interview}")
        print(state["messages"][-1].content[:700])


if __name__ == "__main__":
    main()
