"""Agent 层评测：路由准确率 / 工具调用准确率 / 拒答准确率。

与 run_eval.py 互补——那个测「检索准不准」（recall@k），这个测「Agent 决策对不对」。
它回答的是面试官一定会问的那句：你怎么知道这些 Agent 真的有效？

跑法（在 jobpilot/ 目录下，或在容器内 /app）：
    python evals/run_agent_eval.py              # 全部
    python evals/run_agent_eval.py routing      # 只跑路由（最快，20 条）
    python evals/run_agent_eval.py tool_use refusal

两个设计决定：
1. 用独立的评测用户（EVAL_USER），不碰真实数据。工具会真写库，
   所以必须既能验证副作用、又不污染线上记录。
2. 每个用例前清一次库。否则 t05 删掉的记录会影响 t06 的判断。
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app import auth  # noqa: E402
from app.api.chat import get_graph  # noqa: E402
from app.db.base import SessionLocal, init_db  # noqa: E402
from app.db.models import Application  # noqa: E402
from app.events import set_sink  # noqa: E402
from app.graph.supervisor import route as route_node  # noqa: E402

EVAL_USER = "evalsuite"
EVAL_PATH = Path(__file__).resolve().parent / "agent_eval.json"

# 每次运行一个独立命名空间。
# 必须这样：checkpointer 是跨运行持久化的，如果 thread_id 固定成 eval-t05，
# 第二次跑时模型会看到第一次跑的历史（"这条我刚才已经删过了"），结果全是假的。
RUN_ID = str(int(time.time()))


# ---------------- 数据准备 ----------------

def reset_apps() -> None:
    """清空评测用户的投递记录，让用例之间互不影响。"""
    with SessionLocal() as session:
        session.execute(delete(Application).where(Application.user_id == EVAL_USER))
        session.commit()


def seed_apps(rows: list[dict]) -> None:
    with SessionLocal() as session:
        for row in rows:
            session.add(Application(user_id=EVAL_USER, **row))
        session.commit()


def list_apps() -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Application).where(Application.user_id == EVAL_USER)
        ).all()
        return [
            {"company": r.company, "role": r.role, "status": r.status} for r in rows
        ]


# ---------------- 跑图 ----------------

def run_turn(text: str, thread: str, auto_confirm: bool | None = None) -> dict:
    """跑一轮完整对话；遇到 interrupt 时按 auto_confirm 决定是否继续。

    这里的 contextvar 设置是必须的：工具通过 auth.current_user_id() 认当前用户，
    不设的话写库时 user_id 会是空串（也就是 Bug 6 那个坑）。
    """
    graph = get_graph()
    config = {"configurable": {"thread_id": f"eval-{RUN_ID}-{thread}"}}
    auth.set_current_user(EVAL_USER)
    set_sink(lambda _event: None)  # 评测不关心事件流，丢弃

    result = graph.invoke({"messages": [HumanMessage(content=text)]}, config)
    interrupted = bool(graph.get_state(config).next)

    if interrupted and auto_confirm is not None:
        result = graph.invoke(Command(resume={"approved": auto_confirm}), config)
        interrupted = bool(graph.get_state(config).next)

    answer = ""
    for msg in reversed(result.get("messages") or []):
        if getattr(msg, "type", "") == "ai" and getattr(msg, "content", ""):
            answer = msg.content
            break
    return {"answer": answer, "interrupted": interrupted}


# ---------------- 判定 ----------------

def judge(check: dict, answer: str) -> tuple[bool, str]:
    """按 check 类型判定通过与否，返回 (是否通过, 失败原因)。"""
    kind = check["type"]

    if kind in ("db_has", "db_lacks", "db_count"):
        apps = list_apps()
        if kind == "db_has":
            hit = [
                a
                for a in apps
                if a["company"] == check["company"]
                and (not check.get("status") or a["status"] == check["status"])
            ]
            return bool(hit), f"库里没有匹配记录（实际 {apps}）"
        if kind == "db_lacks":
            hit = [a for a in apps if a["company"] == check["company"]]
            return not hit, f"不该存在的记录还在（实际 {apps}）"
        return len(apps) == check["count"], f"条数不符：期望 {check['count']}，实际 {len(apps)}"

    if kind == "answer_has_any":
        return any(w in answer for w in check["words"]), "回答里没出现任一预期关键词"

    if kind == "answer_has_words":
        has_ok = all(w in answer for w in check["has"])
        lacks_ok = all(w not in answer for w in check.get("lacks", []))
        return has_ok and lacks_ok, f"需含 {check['has']}，且不含 {check.get('lacks', [])}"

    return False, f"未知的 check 类型：{kind}"


# ---------------- 三个维度的评测 ----------------

def eval_routing(cases: list[dict]) -> tuple[int, int]:
    print(f"── 路由准确率（{len(cases)} 条）")
    ok = 0
    for case in cases:
        auth.set_current_user(EVAL_USER)
        set_sink(lambda _event: None)
        try:
            got = route_node({"messages": [HumanMessage(content=case["input"])]})["route"]
        except Exception as exc:  # 路由本身炸了也算不通过
            got = f"ERR:{type(exc).__name__}"
        hit = got == case["expect_route"]
        ok += hit
        mark = "PASS" if hit else "FAIL"
        print(
            f"  [{mark}] {case['id']}  期望 {case['expect_route']:<17}"
            f"实际 {got:<17} | {case['input'][:30]}"
        )
    print(f"  → 路由准确率 {ok}/{len(cases)} = {ok / len(cases) * 100:.0f}%\n")
    return ok, len(cases)


def eval_tool_use(cases: list[dict]) -> tuple[int, int]:
    print(f"── 工具调用 / 任务完成（{len(cases)} 条）")
    ok = 0
    for case in cases:
        reset_apps()
        if case.get("seed"):
            seed_apps(case["seed"])

        answer, why = "", ""
        try:
            result = run_turn(case["input"], case["id"], auto_confirm=case.get("auto_confirm"))
            answer = result["answer"]
            passed, why = judge(case["check"], answer)
        except Exception as exc:
            passed, why = False, f"{type(exc).__name__}: {exc}"

        ok += passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {case['id']}  {case['desc']}")
        if not passed:
            print(f"         输入：{case['input']}")
            print(f"         原因：{why}")
            print(f"         回答：{answer[:150]}")
    print(f"  → 工具调用准确率 {ok}/{len(cases)} = {ok / len(cases) * 100:.0f}%\n")
    reset_apps()
    return ok, len(cases)


def eval_refusal(cases: list[dict]) -> tuple[int, int]:
    print(f"── 拒答 / 边界（{len(cases)} 条）")
    ok = 0
    for case in cases:
        reset_apps()
        answer, why = "", ""
        try:
            result = run_turn(case["input"], case["id"])
            answer = result["answer"]
            passed, why = judge(case["check"], answer)
        except Exception as exc:
            passed, why = False, f"{type(exc).__name__}: {exc}"

        ok += passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {case['id']}  {case['desc']}")
        # 拒答类用例的判定靠关键词，比较脆；无论成败都把回答打出来供人工复核
        print(f"         输入：{case['input']}")
        print(f"         回答：{answer[:180].replace(chr(10), ' ')}")
        if not passed:
            print(f"         原因：{why}")
    print(f"  → 拒答准确率 {ok}/{len(cases)} = {ok / len(cases) * 100:.0f}%\n")
    return ok, len(cases)


def main() -> None:
    which = sys.argv[1:] or ["routing", "tool_use", "refusal"]
    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))

    init_db()
    auth.set_current_user(EVAL_USER)

    print("=" * 72)
    print("  JobPilot Agent 层评测")
    print("=" * 72 + "\n")

    started = time.perf_counter()
    total_ok = total = 0

    if "routing" in which:
        a, b = eval_routing(cases["routing"])
        total_ok += a
        total += b
    if "tool_use" in which:
        a, b = eval_tool_use(cases["tool_use"])
        total_ok += a
        total += b
    if "refusal" in which:
        a, b = eval_refusal(cases["refusal"])
        total_ok += a
        total += b

    elapsed = time.perf_counter() - started
    print("=" * 72)
    print(f"  合计 {total_ok}/{total} = {total_ok / total * 100:.0f}%    耗时 {elapsed:.0f}s")
    print("=" * 72)


if __name__ == "__main__":
    main()
