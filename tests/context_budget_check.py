"""上下文预算层自检。

A 部分：纯逻辑验证裁剪——构造超长历史，确认发送前被裁进预算（不花 token）。
B 部分：真实多轮对话——观察摘要是否按预期触发、历史是否被覆盖。
C 部分：压缩率量化——给出「相比全量重发历史，输入 token 减少多少」的真实数字。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\context_budget_check.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 终端 GBK，输出含特殊字符会崩，统一切 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.config import settings  # noqa: E402
from app.context import build_messages, count_tokens  # noqa: E402
from app.graph.checkpointer import get_checkpointer  # noqa: E402
from app.graph.supervisor import build_supervisor  # noqa: E402


def part_a_裁剪逻辑() -> None:
    """超长历史 + 小预算 → 发送条数应被裁掉，且总 token 不超预算。"""
    system = SystemMessage(content="你是求职助手。" * 10)
    filler = "这是一段用来占位的中文内容。" * 50  # 单条约 700 token
    state = {"messages": [HumanMessage(content=filler) for _ in range(20)]}

    budget = count_tokens([system]) + 2000
    messages = build_messages(system, state, budget=budget)
    used = count_tokens(messages)

    print(f"[A] 预算={budget} token，历史 20 条 → 实际发送 {len(messages)} 条，估算 {used} token")
    assert used <= budget, "裁剪后仍超预算"
    print("[A] PASS 裁剪后落在预算内\n")


TURNS = [
    "我在看一个 JD：招聘 Python 后端，要求 3 年经验，熟悉 LangChain/RAG",
    "我有 1 年 Python 后端经验，用过 FastAPI 和 PostgreSQL",
    "我自学过 RAG，但没有生产落地经验",
    "那你觉得我最该补哪块？",
]


def part_b_多轮摘要() -> None:
    """跑真实多轮，观察摘要触发节奏与迟滞效果。

    keep_recent=2 时触发阈值为 2*2=4：
      轮1 pending=1、轮2 pending=3 → 不触发
      轮3 pending=5 > 4 → 一次摘到只剩 2 条（覆盖 3 条）
      轮4 pending=2+2=4 → 不触发（迟滞生效，避免每轮都调摘要）
    """
    settings.keep_recent_messages = 2  # 调小阈值，让 4 轮内可观察到 1 次触发
    app = build_supervisor(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": f"budget-{uuid.uuid4().hex[:6]}"}}

    state = {}
    for i, text in enumerate(TURNS, 1):
        state = app.invoke({"messages": [HumanMessage(content=text)]}, config)
        print(
            f"[B] 轮次{i}: 历史共 {len(state['messages'])} 条 | "
            f"已被摘要覆盖 {state.get('summarized_count', 0)} 条 | "
            f"摘要 {len(state.get('summary') or '')} 字"
        )

    print("\n[B] 摘要内容预览：")
    print((state.get("summary") or "（未触发摘要）")[:200])


# 长对话语料：轮数要够多，摘要才会真正触发（keep_recent=4 → 阈值 8 条）
LONG_TURNS = [
    "我在准备秋招，主要投 Python 后端和 AI 应用开发方向的岗位。",
    "我的主力项目是一个多 Agent 求职助手，用 LangGraph 编排的。",
    "上周面了腾讯一面，问的都是 JVM 内存模型和 MySQL 索引。",
    "字节的笔试有三道算法题，第二题动态规划我没做出来。",
    "美团 HR 说这周内给答复，到现在还没有消息。",
    "我想重点补一下 RAG 的工程落地，简历上怎么写更可信？",
    "高并发要不要写进简历？我只有单机部署的经验。",
    "帮我看下投递进度，再排一下下周要做的事。",
]


def part_c_压缩率() -> None:
    """量化压缩率：同一段真实对话，对比「历史全量重发」与「压缩层实际发送」。

    两个数字都从同一份真实 state 算出，都用 cl100k 估算（和线上预算层同一把尺子），
    差别只在发全部历史、还是发「摘要 + 最近窗口」。
    不量化就只能说「做了压缩」，说不清省了多少——数字要能报出来。
    """
    settings.keep_recent_messages = 4
    app = build_supervisor(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": f"compress-{uuid.uuid4().hex[:6]}"}}
    system = SystemMessage(content="你是求职助手，负责简历诊断、面试模拟与投递管理。")

    baseline_total = 0
    actual_total = 0
    state: dict = {}
    for i, text in enumerate(LONG_TURNS, 1):
        state = app.invoke({"messages": [HumanMessage(content=text)]}, config)
        baseline = count_tokens([system]) + count_tokens(state["messages"])
        actual = count_tokens(build_messages(system, state))
        baseline_total += baseline
        actual_total += actual
        print(
            f"[C] 轮次{i}: 全量重发 {baseline:>6} | 实际发送 {actual:>6} | "
            f"已摘要覆盖 {state.get('summarized_count', 0)} 条"
        )

    drop = (1 - actual_total / baseline_total) * 100
    print(f"\n[C] 累计输入 token：全量 {baseline_total} → 压缩后 {actual_total}，减少 {drop:.2f}%")


if __name__ == "__main__":
    part_a_裁剪逻辑()
    part_b_多轮摘要()
    part_c_压缩率()
