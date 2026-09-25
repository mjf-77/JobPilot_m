"""职位分析 Agent（子图）。

当前结构：START → analyze → END（单 LLM 节点）。
M2 起会在子图内加 ReAct 工具环（BM25+向量混合检索 JD 库、读简历文件），
但对外接口不变——主图只管调用它，不关心内部有几个节点。
"""

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.context import build_messages
from app.skills import load_skill
from app.graph.state import AgentState
from app.llm import chat_streaming

SKILL_NAME = "jd-analysis"

_SYSTEM_TEMPLATE = """你正在执行 JobPilot 的「职位分析」任务，严格按下面的 SOP 作业：

<skill name="{skill_name}">
{sop}
</skill>

如果用户还没给出 JD 或候选人信息，先礼貌地要求补齐材料，不要凭空分析。"""


def analyze(state: AgentState) -> dict:
    """LLM 节点：把 Skill SOP 注入 system prompt，产出结构化分析结果。"""
    sop = load_skill(SKILL_NAME)
    system = SystemMessage(
        content=_SYSTEM_TEMPLATE.format(skill_name=SKILL_NAME, sop=sop)
    )
    # 流式调用：token 通过 custom stream 推给前端，返回值仍落 state
    answer = chat_streaming(build_messages(system, state))
    return {"messages": [AIMessage(content=answer)]}


def build_jd_analyst():
    """编译并返回子图。子图可单独 invoke 测试，不需要主图。"""
    graph = StateGraph(AgentState)
    graph.add_node("analyze", analyze)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", END)
    return graph.compile()
