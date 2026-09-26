"""离线（Mock）回归 + 故障注入自检：全程不联网、不花 token。

第一部分：用一个脚本化的假 chat 客户端替换 app.llm.get_chat_client()，
驱动真实业务代码（route / progress_tracker 子图 / merge_draft / 上下文层），
断言一批确定性的行为。

第二部分：人为注入故障（LLM 超时、流中断、工具抛异常、摘要失败），
核对代码里声明的降级行为是否真的生效；不成立的地方按真实行为如实标记。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\offline_regression.py
"""

import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from sqlalchemy import delete  # noqa: E402

import openai  # noqa: E402
from app import auth, llm, metering  # noqa: E402
from app.config import settings  # noqa: E402
from app.context import _summarize, build_messages, compress_history, count_tokens  # noqa: E402
from app.db.base import SessionLocal, init_db  # noqa: E402
from app.db.models import Application  # noqa: E402
from app.graph.agents.progress_tracker import build_progress_tracker  # noqa: E402
from app.graph.agents.resume_builder import merge_draft  # noqa: E402
from app.graph.supervisor import build_supervisor, route  # noqa: E402
from app.tools.builtin import registry  # noqa: E402
from app.tools.registry import ToolRegistry, ToolSpec  # noqa: E402

TEST_USER = "999999997"
ORIG_KEEP = settings.keep_recent_messages

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    print(f"  [SKIP] {name}  {why}")


# ---------------- 假 chat 客户端（形态对齐 OpenAI 客户端）----------------
# 脚本里每一项都是 (kwargs) -> 响应对象 的可调用，按调用顺序消费；
# 这样既能按顺序出招，也能按请求内容（kwargs）决定返回什么。

_FAKE_HITS = 0


class _FakeLLM:
    def __init__(self, scripts: list):
        self._scripts = list(scripts)
        self.calls: list[dict] = []  # 记录每次请求参数，供断言「工具结果是否回灌」
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        global _FAKE_HITS
        _FAKE_HITS += 1
        self.calls.append(kwargs)
        if not self._scripts:
            raise AssertionError("假客户端脚本用尽：调用次数超出预期")
        return self._scripts.pop(0)(kwargs)


def _usage(pt=7, ct=3):
    return SimpleNamespace(prompt_tokens=pt, completion_tokens=ct)


def _chunk(content=None, tool_calls=None, usage=None):
    choices = [] if usage is not None else [
        SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))
    ]
    return SimpleNamespace(usage=usage, choices=choices)


def full(content, pt=7, ct=3):
    """非流式响应：choices[0].message.content + usage。"""

    def _make(_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
            usage=_usage(pt, ct),
        )

    return _make


def stream_text(parts, pt=7, ct=3):
    """流式响应：逐段 content，最后补一个带 usage 的空 choices 收尾 chunk。"""

    def _make(_kwargs):
        def gen():
            for p in parts:
                yield _chunk(content=p)
            yield _chunk(usage=_usage(pt, ct))

        return gen()

    return _make


def stream_tool_call(name, args_json, call_id="call_1", prefix=None, pt=7, ct=3):
    """流式响应：可选 content 前缀 + 一段 tool_calls 分片（arguments 拆成两段验证累积）。"""

    def _make(_kwargs):
        def gen():
            if prefix:
                yield _chunk(content=prefix)
            half = len(args_json) // 2
            yield _chunk(tool_calls=[SimpleNamespace(
                index=0, id=call_id,
                function=SimpleNamespace(name=name, arguments=args_json[:half]),
            )])
            yield _chunk(tool_calls=[SimpleNamespace(
                index=0, id=None,
                function=SimpleNamespace(name=None, arguments=args_json[half:]),
            )])
            yield _chunk(usage=_usage(pt, ct))

        return gen()

    return _make


def raises(exc):
    def _make(_kwargs):
        raise exc

    return _make


def stream_breaks(parts, exc):
    """先正常吐出 parts，再在迭代中途抛异常。"""

    def _make(_kwargs):
        def gen():
            for p in parts:
                yield _chunk(content=p)
            raise exc

        return gen()

    return _make


_ORIG_GET_CLIENT = llm.get_chat_client


def install(scripts: list) -> _FakeLLM:
    fake = _FakeLLM(scripts)
    llm.get_chat_client = lambda: fake  # 覆盖模块属性：llm 内所有入口随即走假客户端
    return fake


def uninstall() -> None:
    llm.get_chat_client = _ORIG_GET_CLIENT


# ---- 真实客户端构造探针：证明全程没建过真的 OpenAI 连接（=不联网/不花钱）----
_REAL_INIT = openai.OpenAI.__init__
_REAL_CLIENTS: list = []


def _spy_init(self, *a, **k):
    _REAL_CLIENTS.append((a, k))
    return _REAL_INIT(self, *a, **k)


openai.OpenAI.__init__ = _spy_init


# ---------------- 数据准备 ----------------

def wipe_applications(user_id: str) -> None:
    with SessionLocal() as s:
        s.execute(delete(Application).where(Application.user_id == user_id))
        s.commit()


def seed_application(user_id: str, company: str, role: str, status: str) -> None:
    with SessionLocal() as s:
        s.add(Application(
            user_id=user_id, company=company, role=role, status=status, note="",
            updated_at=datetime.now().isoformat(timespec="seconds"),
        ))
        s.commit()


# ---------------- 第一部分：Mock 回归 ----------------

def sec1_routing() -> None:
    print("── 1.1 路由节点（真实 route()，假 LLM 返回 JSON）")
    fake = install([full('{"route": "jd_analyst", "skill": "jd-analysis"}')])
    out = route({"messages": [HumanMessage(content="帮我分析这个 JD 值不值得投")]})
    check("路由到 jd_analyst", out.get("route") == "jd_analyst", str(out))
    check("active_skill 同步为 jd-analysis", out.get("active_skill") == "jd-analysis")
    check("确实发起了 1 次非流式调用", len(fake.calls) == 1 and fake.calls[0].get("stream") is None)
    uninstall()

    print("── 1.2 路由兜底（非法 JSON / 未知分支 → chat）")
    install([full("这不是 JSON")])
    out = route({"messages": [HumanMessage(content="随便说点什么")]})
    check("非法 JSON 兜底到 chat", out.get("route") == "chat", str(out))
    uninstall()
    install([full('{"route": "不存在的分支", "skill": "x"}')])
    out = route({"messages": [HumanMessage(content="随便说点什么")]})
    check("未知分支兜底到 chat", out.get("route") == "chat", str(out))
    uninstall()

    print("── 1.3 状态短路优先于模型判断（不应触发 LLM）")
    fake = install([full("本不该被调用")])
    out = route({"messages": [HumanMessage(content="chunk 用 500")],
                 "interview": {"status": "ongoing"}})
    check("面试进行中直接续接面试官", out.get("route") == "mock_interviewer", str(out))
    check("面试短路时未调用 LLM", len(fake.calls) == 0)
    uninstall()

    fake = install([full("本不该被调用")])
    out = route({"messages": [HumanMessage(content="教育经历：广西大学")],
                 "resume_draft": {"name": "张三"}})
    check("简历收集未完成续接 resume_builder", out.get("route") == "resume_builder", str(out))
    check("简历短路时未调用 LLM", len(fake.calls) == 0)
    uninstall()


def sec1_tool_chain() -> None:
    print("── 1.4 工具调用链路（真实 progress_tracker 子图 + list_applications）")
    wipe_applications(TEST_USER)
    seed_application(TEST_USER, "字节跳动", "AI 应用开发", "已投递")
    fake = install([
        stream_tool_call("list_applications", "{}"),
        stream_text(["你目前", "有 1 条", "投递记录。"]),
    ])
    auth.set_current_user(int(TEST_USER))
    usage = metering.start()
    graph = build_progress_tracker()
    state = graph.invoke({"messages": [HumanMessage(content="我投了哪些？")]})

    tool_msgs = [m for m in state["messages"] if getattr(m, "type", "") == "tool"]
    check("工具真的执行了（回灌了真实库数据）",
          bool(tool_msgs) and "字节跳动" in tool_msgs[0].content,
          (tool_msgs[0].content.replace("\n", " ")[:60] if tool_msgs else "无 ToolMessage"))
    check("工具结果回灌给模型（第二次请求含 role=tool 且带库数据）",
          len(fake.calls) >= 2 and any(
              m.get("role") == "tool" and "字节跳动" in m.get("content", "")
              for m in fake.calls[1].get("messages", [])
          ))
    check("工具计量 tool_calls == 1", usage.tool_calls == 1, f"实际 {usage.tool_calls}")
    check("LLM 计量 llm_calls == 2（工具轮 + 收尾轮）", usage.llm_calls == 2, f"实际 {usage.llm_calls}")
    ai = [m for m in state["messages"] if getattr(m, "type", "") == "ai" and m.content]
    check("ReAct 环回到模型并给出最终答复", bool(ai) and "投递记录" in ai[-1].content,
          (ai[-1].content if ai else "无最终答复"))
    uninstall()
    wipe_applications(TEST_USER)


def sec1_merge_draft() -> None:
    print("── 1.5 简历草稿 patch 合并（merge_draft，曾经的丢姓名 bug）")
    r1 = merge_draft({}, {"name": "张三", "phone": "13800000000"})
    r2 = merge_draft(r1, {"education": [{"school": "广西大学", "major": "计算机"}]})
    check("第二轮补充教育经历后姓名仍在", r2.get("name") == "张三", str(r2))
    check("第二轮补充教育经历后电话仍在", r2.get("phone") == "13800000000")
    check("新的教育经历被写入", r2.get("education", [{}])[0].get("school") == "广西大学")
    r3 = merge_draft(r2, {"education": [{"school": "广西大学", "degree": "本科"}]})
    check("列表按主键(school)合并而非重复追加",
          len(r3["education"]) == 1 and r3["education"][0].get("degree") == "本科",
          str(r3["education"]))
    r4 = merge_draft(r3, {"name": "", "phone": None})
    check("空串/None 不覆盖已有值", r4.get("name") == "张三" and r4.get("phone") == "13800000000")


def sec1_budget() -> None:
    print("── 1.6 上下文预算（交叉引用 tests/context_budget_check.py，这里只做预算内断言）")
    system = SystemMessage(content="你是求职助手。" * 10)
    state = {"messages": [HumanMessage(content="占位内容。" * 300) for _ in range(10)]}
    budget = count_tokens([system]) + 1500
    msgs = build_messages(system, state, budget=budget)
    used = count_tokens(msgs)
    check("build_messages 输出落在预算内", used <= budget, f"{used}/{budget} token")


# ---------------- 第二部分：故障注入 ----------------

# 本地可能没装 redis（app.limits 依赖它），注入最小替身只为让 API 层可导入；
# 替身 from_url 必失败 → limits 走「连不上」降级，与本地裸跑的等价。
API_NOTE = ""
_stream_graph = None


def prepare_api_layer() -> None:
    global _stream_graph, API_NOTE
    try:
        import redis  # noqa: F401
        API_NOTE = "redis 已安装"
    except ImportError:
        stub = types.ModuleType("redis")

        class _StubRedis:
            @staticmethod
            def from_url(*a, **k):
                raise ConnectionError("offline stub: redis unreachable")

        stub.Redis = _StubRedis
        sys.modules["redis"] = stub
        API_NOTE = "本地未装 redis：注入最小替身以导入 API 层（from_url 必失败，等价「连不上」降级）"
    try:
        from app.api.chat import _stream_graph as fn
        _stream_graph = fn
    except Exception as exc:  # 导入都失败就算依赖缺失，相关条目改判 SKIP
        API_NOTE += f"；API 层不可导入（{type(exc).__name__}: {exc}）"


def run_stream(scripts: list, text: str, thread_id: str) -> list[dict]:
    """跑一次真实的 API 层调用方，回收它产出的 SSE 事件帧。"""
    import json

    install(scripts)
    try:
        frames = []
        for raw in _stream_graph({"messages": [HumanMessage(content=text)]}, thread_id, 999999997):
            if raw.startswith("data: "):
                frames.append(json.loads(raw[len("data: "):]))
        return frames
    finally:
        uninstall()


def sec2_llm_timeout() -> None:
    print("── 2.1 LLM 超时 / 异常")
    install([raises(TimeoutError("模拟 LLM 超时"))])
    out = _summarize("旧摘要", [HumanMessage(content="很久以前的一段对话")])
    check("摘要 LLM 超时 → _summarize 返回空串（不抛给上层）", out == "", repr(out))
    uninstall()

    settings.keep_recent_messages = 2
    try:
        install([raises(TimeoutError("模拟 LLM 超时"))])
        long_state = {"messages": [HumanMessage(content=f"第 {i} 轮对话内容") for i in range(6)]}
        out = compress_history(long_state)
        check("摘要失败 → compress_history 返回 {}（交给硬裁剪兜底）", out == {}, str(out))
        uninstall()
    finally:
        settings.keep_recent_messages = ORIG_KEEP

    install([raises(TimeoutError("模拟 LLM 超时"))])
    raised = None
    try:
        route({"messages": [HumanMessage(content="你好")]})
    except Exception as exc:
        raised = exc
    uninstall()
    check("route 节点不吞异常（异常向上传播，兜底责任在调用方）",
          isinstance(raised, TimeoutError), f"{type(raised).__name__}")

    print(f"      API 层可用性：{API_NOTE}")
    if _stream_graph is None:
        skip("API 调用方把异常转成可读 error 帧", "API 层不可导入")
        return
    frames = run_stream([raises(TimeoutError("模拟 LLM 超时"))], "你好", "offline-timeout")
    errs = [f for f in frames if f.get("type") == "error"]
    check("调用方不挂死（生成了 error 帧）", bool(errs), str(frames))
    check("异常被转成可读错误、不回吐堆栈",
          bool(errs) and "TimeoutError" in errs[0].get("message", "")
          and "Traceback" not in errs[0].get("message", ""),
          (errs[0].get("message", "") if errs else "无 error 帧"))
    check("失败请求也正常收尾（有 done 帧）", any(f.get("type") == "done" for f in frames))


def sec2_stream_break() -> None:
    print("── 2.2 结论文本流中断（迭代中途抛异常）")
    install([stream_breaks(["这是被截断的", "前半段"], TimeoutError("模拟流中断"))])
    raised = None
    try:
        llm.stream_chat([HumanMessage(content="你好")])
    except Exception as exc:
        raised = exc
    uninstall()
    check("stream_chat 不吞流异常（异常向上传播）", isinstance(raised, TimeoutError),
          f"{type(raised).__name__}")

    if _stream_graph is None:
        skip("API 层：已流出内容不丢 + 明确报错", "API 层不可导入")
        return
    frames = run_stream(
        [full('{"route": "chat", "skill": ""}'),
         stream_breaks(["这是被截断的", "前半段"], TimeoutError("模拟流中断"))],
        "你好", "offline-stream",
    )
    tokens = "".join(f.get("text", "") for f in frames if f.get("type") == "token")
    errs = [f for f in frames if f.get("type") == "error"]
    check("已流出的部分已通过 token 事件送达（未丢失）", "这是被截断的" in tokens, repr(tokens))
    check("中断被转成可读 error 帧", bool(errs) and "Traceback" not in errs[0].get("message", ""),
          (errs[0].get("message", "") if errs else "无 error 帧"))
    check("调用方未挂死（正常收尾）", any(f.get("type") == "done" for f in frames))


def sec2_tool_error() -> None:
    print("── 2.3 工具执行抛异常")
    probe = ToolRegistry()
    probe.register(ToolSpec(
        name="_boom", description="测试用", parameters={"type": "object", "properties": {}},
        func=lambda: 1 / 0, max_retries=1,
    ))
    out = probe.execute("_boom", {})
    check("异常被转成给模型看的错误文本",
          isinstance(out, str) and out.startswith("错误：") and "ZeroDivisionError" in out, out)
    check("错误文本不含堆栈", "Traceback" not in out)

    registry.register(ToolSpec(
        name="_boom_graph", description="测试用", parameters={"type": "object", "properties": {}},
        func=lambda: 1 / 0, max_retries=1,
    ))
    try:
        install([
            stream_tool_call("_boom_graph", "{}"),
            stream_text(["工具没成功，", "我换个方式回答。"]),
        ])
        usage = metering.start()
        graph = build_progress_tracker()
        state = graph.invoke({"messages": [HumanMessage(content="随便")]})
        uninstall()
        tool_msgs = [m for m in state["messages"] if getattr(m, "type", "") == "tool"]
        check("图未中断：工具错误作为 ToolMessage 回灌给模型",
              bool(tool_msgs) and tool_msgs[0].content.startswith("错误："),
              (tool_msgs[0].content if tool_msgs else "无 ToolMessage"))
        check("工具失败仍计入 tool_calls", usage.tool_calls == 1, f"实际 {usage.tool_calls}")
        ai = [m for m in state["messages"] if getattr(m, "type", "") == "ai" and m.content]
        check("ReAct 环继续到最终答复", bool(ai) and "换个方式" in ai[-1].content,
              (ai[-1].content if ai else "无最终答复"))
    finally:
        registry._tools.pop("_boom_graph", None)


def sec2_summary_failure() -> None:
    print("── 2.4 摘要失败不中断对话（整图）")
    settings.keep_recent_messages = 2
    try:
        fake = install([
            raises(TimeoutError("摘要模型挂了")),      # compress 节点里的摘要调用
            full('{"route": "chat", "skill": ""}'),    # router
            stream_text(["你好，", "我是 JobPilot。"]),  # chat 分支
        ])
        graph = build_supervisor()
        state = graph.invoke(
            {"messages": [HumanMessage(content=f"第 {i} 轮") for i in range(6)]}
        )
        uninstall()
        check("摘要失败后仍走完路由", state.get("route") == "chat", str(state.get("route")))
        ai = [m for m in state["messages"] if getattr(m, "type", "") == "ai" and m.content]
        check("用户仍收到回复（不因摘要失败中断）", bool(ai) and "JobPilot" in ai[-1].content,
              (ai[-1].content if ai else "无回复"))
        check("摘要未写入（summarized_count 未推进）",
              state.get("summarized_count", 0) == 0, str(state.get("summarized_count")))
        check("压缩节点确实尝试过摘要（消耗 1 次假调用）", len(fake.calls) >= 1)
    finally:
        settings.keep_recent_messages = ORIG_KEEP


def sec_offline_proof() -> None:
    print("── 3 离线性自证")
    check("全程未构造真实 OpenAI 客户端（不联网/不花 token）",
          len(_REAL_CLIENTS) == 0, f"真实客户端实例数={len(_REAL_CLIENTS)}")
    check("假客户端确实被使用（业务真的跑了）", _FAKE_HITS > 0, f"假调用次数={_FAKE_HITS}")
    check("get_chat_client 已还原为原始实现", llm.get_chat_client is _ORIG_GET_CLIENT)


def run_section(fn) -> None:
    try:
        fn()
    except Exception as exc:
        check(f"{fn.__name__} 未抛出未预期异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        uninstall()


def main() -> int:
    init_db()
    prepare_api_layer()

    print("=" * 64)
    print("  JobPilot 离线回归 + 故障注入自检")
    print("=" * 64)

    print("\n【第一部分：Mock 回归（离线、确定性）】")
    for fn in (sec1_routing, sec1_tool_chain, sec1_merge_draft, sec1_budget):
        run_section(fn)

    print("\n【第二部分：故障注入（验证降级是否真的生效）】")
    for fn in (sec2_llm_timeout, sec2_stream_break, sec2_tool_error, sec2_summary_failure):
        run_section(fn)

    print("\n【第三部分：离线性自证】")
    run_section(sec_offline_proof)

    openai.OpenAI.__init__ = _REAL_INIT  # 还原探针

    passed = sum(results)
    print("\n" + "=" * 64)
    print(f"  离线回归自检 {passed}/{len(results)}")
    print("=" * 64)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
