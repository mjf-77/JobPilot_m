"""用户画像自检：验证「跨会话长期记忆」是不是真的生效。

验证链路（每一环都可能看着对、实际断掉）：

    写入画像 → 图入口 load_profile 读进 state → build_messages 注入 prompt
             → 换一个全新会话，Agent 仍然认识你

最后一环最关键：前面几步都过了，也不代表模型真的看得到——所以必须新开
一个 thread（毫无本会话历史）问一句，看它答不答得出来。

运行（在 jobpilot/ 目录下）：
    python tests/profile_check.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app import auth  # noqa: E402
from app.api.chat import get_graph  # noqa: E402
from app.context import build_messages  # noqa: E402
from app.db import profiles  # noqa: E402
from app.db.base import init_db  # noqa: E402
from app.events import set_sink  # noqa: E402
from app.graph.supervisor import load_profile  # noqa: E402

USER_A = "profilecheck"
USER_B = "profilecheck-other"

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def wipe(user_id: str) -> None:
    for field in profiles.get_all(user_id):
        profiles.set_one(user_id, field, "")


def ask_new_thread(text: str) -> str:
    """在全新 thread 里问一句——没有任何本会话历史，只能靠长期画像回答。"""
    graph = get_graph()
    config = {"configurable": {"thread_id": f"profilecheck-{int(time.time() * 1000)}"}}
    auth.set_current_user(USER_A)
    set_sink(lambda _event: None)

    result = graph.invoke({"messages": [HumanMessage(content=text)]}, config)
    for msg in reversed(result.get("messages") or []):
        if getattr(msg, "type", "") == "ai" and getattr(msg, "content", ""):
            return msg.content
    return ""


def main() -> int:
    init_db()
    set_sink(lambda _event: None)
    wipe(USER_A)
    wipe(USER_B)

    # ---- 1. 写入与读取 ----
    print("── 写入与读取")
    auth.set_current_user(USER_A)
    profiles.set_one(USER_A, "目标岗位", "AI 应用开发")
    profiles.set_one(USER_A, "技术栈", "Python、LangGraph、RAG")
    got = profiles.get_all(USER_A)
    check("写入两条后能读到", len(got) == 2, str(got))

    # ---- 2. 图入口节点 ----
    print("\n── 图入口节点读取")
    auth.set_current_user(USER_A)
    patch = load_profile({})
    check(
        "load_profile 把画像读进 state",
        patch.get("user_profile", {}).get("目标岗位") == "AI 应用开发",
    )

    # ---- 3. 注入 prompt ----
    print("\n── 注入 prompt")
    state = {
        "messages": [HumanMessage(content="你好")],
        "user_profile": profiles.get_all(USER_A),
    }
    messages = build_messages(SystemMessage(content="你是一个助手"), state)
    joined = "\n".join(m.content or "" for m in messages)
    check("prompt 里出现了画像内容", "AI 应用开发" in joined)
    check(
        "画像被包成 user_profile 块且排在 system 之后",
        len(messages) > 1 and "user_profile" in (messages[1].content or ""),
    )

    # ---- 4. 用户隔离 ----
    print("\n── 用户隔离")
    check("另一个用户读不到这些字段", profiles.get_all(USER_B) == {})

    # ---- 5. 跨会话端到端 ----
    print("\n── 跨会话端到端（全新 thread，问它认不认识我）")
    answer = ask_new_thread("我的目标岗位是什么？")
    check(
        "新会话里答得出目标岗位",
        "AI 应用开发" in answer,
        answer[:90].replace("\n", " "),
    )

    # ---- 6. 删除 ----
    print("\n── 删除")
    profiles.set_one(USER_A, "技术栈", "")
    check("value 传空即删除该条", "技术栈" not in profiles.get_all(USER_A))

    wipe(USER_A)
    wipe(USER_B)

    passed = sum(results)
    print("\n" + "=" * 56)
    print(f"  画像自检 {passed}/{len(results)}")
    print("=" * 56)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
