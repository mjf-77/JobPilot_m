"""进度管家 Agent（子图）：用工具记录/查询/删除投递进度。

结构是标准 ReAct 环：

    START → agent ──(模型要求调工具)──→ tools ──→ agent（回模型）
                 └──(模型给出最终答复)──→ END

与另外两个 Agent 的关键区别：它会真正「动手」——写库、删记录。
所以这里也是 human-in-the-loop 的落点：高风险工具在 tools 节点里
先 interrupt() 挂起等用户确认，确认通过才真正执行。

关于流式：agent 节点用「流式 + 累积 tool_calls 分片」——content 分片照常推给前端，
tool_calls 按 index 累积到完整再执行。两者并不冲突；早前用非流式是走了弯路，
结果前端收不到任何 token，最终答复在界面上是空白。
"""

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.context import build_messages
from app.events import emit
from app.graph.state import AgentState
from app.llm import stream_chat_with_tools
from app.skills import load_skill
from app.tools.builtin import registry

SKILL_NAME = "progress-tracker"

_SYSTEM_TEMPLATE = """你正在执行 JobPilot 的「投递进度管理」任务，严格按下面的 SOP 作业：

<skill name="{skill_name}">
{sop}
</skill>

需要记录或查询时直接调用提供的工具；信息不足以调用工具时，先向用户问清楚。"""


def _agent(state: AgentState) -> dict:
    """LLM 节点：带工具定义，决定继续调工具还是给出最终答复。

    用「流式 + 累积 tool_calls」而不是非流式：非流式下模型不会推 token 事件，
    前端最终答复会是空白——这是实测踩到的坑。
    """
    system = SystemMessage(
        content=_SYSTEM_TEMPLATE.format(
            skill_name=SKILL_NAME, sop=load_skill(SKILL_NAME)
        )
    )
    messages = build_messages(system, state)

    content, tool_calls = stream_chat_with_tools(
        messages,
        tools=registry.to_openai_tools(),
        on_token=lambda text: emit("token", text=text),
    )

    if tool_calls:
        names = "、".join(c["name"] for c in tool_calls)
        emit("agent_switch", route="progress_tracker", skill=f"{SKILL_NAME}·{names}")

    return {"messages": [AIMessage(content=content, tool_calls=tool_calls)]}


def _tools(state: AgentState, config: RunnableConfig) -> dict:
    """执行模型请求的工具调用；高风险工具先挂起等用户确认。"""
    thread_id = config.get("configurable", {}).get("thread_id", "")
    last = state["messages"][-1]
    outputs: list[ToolMessage] = []

    for call in getattr(last, "tool_calls", None) or []:
        name, args = call["name"], call["args"]
        spec = registry.get(name)

        if spec is not None and spec.requires_confirmation:
            # 高风险操作：把决定权交回用户。
            # interrupt 挂起整张图、状态存进 checkpointer，
            # 前端确认后用 Command(resume={"approved": True}) 恢复现场继续执行。
            emit("confirm", tool=name, args=args)
            decision = interrupt({"tool": name, "args": args})
            if not (decision or {}).get("approved"):
                outputs.append(
                    ToolMessage(content="用户拒绝了该操作，未执行。", tool_call_id=call["id"])
                )
                continue

        result = registry.execute(name, args, thread_id=thread_id)
        outputs.append(ToolMessage(content=result, tool_call_id=call["id"]))

    return {"messages": outputs}


def _needs_tools(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "end"


def build_progress_tracker(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent)
    graph.add_node("tools", _tools)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _needs_tools, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")  # 工具结果回灌后回到模型，形成 ReAct 环

    return graph.compile(checkpointer=checkpointer)
