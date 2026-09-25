"""路由复验：直接调 route 节点（不走完整图，快且只花 1 次 LLM 调用/条）。

重点是「省略说法」——用户不会说完整句子，路由必须能吃住这些说法。
运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\route_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.graph.supervisor import route  # noqa: E402

CASES = [
    # 进度管家：各种省略/口语说法
    ("把字节那条删掉", "progress_tracker"),
    ("我投了腾讯，帮我记一下", "progress_tracker"),
    ("我最近有哪些在面试", "progress_tracker"),
    ("把美团那条记录移除", "progress_tracker"),
    ("字节的进度更新一下，约面了", "progress_tracker"),
    ("我现在投了几家了", "progress_tracker"),
    # 对照组
    ("帮我分析这个 JD：招 Python 后端，3 年经验", "jd_analyst"),
    ("帮我模拟面试，AI 应用开发岗", "mock_interviewer"),
    ("你好，你能做什么", "chat"),
]


def main() -> None:
    passed = 0
    for query, expect in CASES:
        result = route({"messages": [HumanMessage(content=query)]})
        got = result.get("route")
        ok = got == expect
        passed += int(ok)
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] 期望={expect:17s} 实际={str(got):17s} | {query}")

    print(f"\n路由准确率：{passed}/{len(CASES)}")


if __name__ == "__main__":
    main()
