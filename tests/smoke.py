"""M1 冒烟自检：验证 Supervisor 路由分流是否正确。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\smoke.py

这不是单元测试（不 mock LLM），而是真实调用的一次端到端自检，
用来确认「路由 → 子 Agent → 回复」这条链路是通的。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 终端默认 GBK，输出含 ✅/⚠️ 会抛 UnicodeEncodeError，统一切 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.graph.supervisor import build_supervisor  # noqa: E402

CASES = [
    ("你好，你都能帮我做什么？", "chat"),
    (
        "帮我分析这个 JD：招聘资深 Python 后端工程师，要求 5 年经验，"
        "熟悉 LangGraph、RAG、向量数据库，有 Agent 项目落地经验优先。",
        "jd_analyst",
    ),
]


def main() -> None:
    app = build_supervisor()
    passed = 0
    last_answer = ""

    for text, expect in CASES:
        result = app.invoke({"messages": [HumanMessage(content=text)]})
        got = result.get("route")
        ok = got == expect
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] 期望={expect:10s} 实际={str(got):10s} | {text[:28]}")
        last_answer = result["messages"][-1].content

    print(f"\n路由自检：{passed}/{len(CASES)} 通过")
    print("\n---- 最后一条回复开头 400 字 ----")
    print(last_answer[:400])


if __name__ == "__main__":
    main()
