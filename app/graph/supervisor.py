"""Supervisor 主图：意图路由 + 子 Agent 调度。

职责边界（关键设计）：
- Supervisor 只决定「派给谁」，不干具体活——具体活是子图的职责。
- 路由失败要能兜底：JSON 解析失败 / 模型返回未知分支时，退回 chat 分支，
  保证任何一次调用都有回复，不会整轮崩掉。
"""

import json

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app import auth
from app.context import build_messages, compress_history
from app.db import profiles
from app.events import emit
from app.graph.agents.jd_analyst import build_jd_analyst
from app.graph.agents.mock_interviewer import build_mock_interviewer
from app.graph.agents.progress_tracker import build_progress_tracker
from app.graph.agents.resume_builder import build_resume_builder, is_ready
from app.graph.agents.resume_coach import build_resume_coach
from app.skills import list_skills
from app.graph.state import AgentState
from app.llm import chat_raw, chat_streaming

_ROUTE_SYSTEM = """你是 JobPilot 的调度中枢，判断用户意图并选择执行分支。

可选分支：
- jd_analyst：分析岗位 JD、判断匹配度、评估某岗位值不值得投
- resume_coach：**用户已经有一份简历**，想诊断问题、要逐条改写建议
- resume_builder：**从零做一份新简历**——用户想新建、从头整理经历、或把零散口述攒成简历
- mock_interviewer：用户想模拟面试、练习答题、要面试点评或复盘
- progress_tracker：投递记录相关的一切——记录投递、更新状态、查询进度、删除记录。用户常省略「投递」二字（例如说「把字节那条删掉」），只要涉及具体公司或岗位的记录就属于本分支
- chat：寒暄、咨询能力范围，或与上述无关的通用问题

当前可用技能（辅助判断）：
{skills}

判断示例（注意区分「改现有」和「做新的」）：
- "帮我分析这个 JD 值不值得投" → jd_analyst
- "帮我看看简历哪里有问题" → resume_coach
- "我的简历这段怎么写更好" → resume_coach
- "帮我做一份简历" → resume_builder
- "我想从头整理一份简历" → resume_builder
- "我叫张三，电话是 138…，帮我做个简历" → resume_builder
- "帮我模拟面试" → mock_interviewer
- "我投了字节，帮我记一下" → progress_tracker
- "把腾讯那条删掉" → progress_tracker
- "我最近有哪些在面试" → progress_tracker
- "你好" → chat

只输出 JSON，不要任何解释文字：
{{"route": "分支名", "skill": "该分支对应的技能名：jd-analysis / resume-coach / resume-builder / mock-interview / progress-tracker；chat 时填空串"}}"""

_CHAT_SYSTEM = """你是 JobPilot，一个求职助手 Agent。
当前已上线能力：
1. 职位分析——分析 JD 与候选人的匹配度、给出投递建议
2. 模拟面试——扮演面试官连续追问，结束后给结构化复盘报告
3. 投递进度管家——记录、查询、删除投递进度
4. 简历优化——诊断已有简历的问题并给出逐条改写建议
5. 简历制作——从零把口述信息整理成一份排版好的成品简历
用户问能力范围时如实说明；闲聊则简短友好回应，并适时引导到这几个场景。
不要编造还不存在的功能。"""

# 分支名 → 图节点名
_ROUTES = {
    "jd_analyst",
    "resume_coach",
    "resume_builder",
    "mock_interviewer",
    "progress_tracker",
    "chat",
}


def load_profile(state: AgentState) -> dict:
    """图入口节点：把长期画像读进 state，供 build_messages 注入。

    每个请求现读一次数据库——单表查询很便宜，而且用户改了画像下一个请求就生效，
    不用担心 checkpointer 里存着旧值（画像属于「用户」，不属于「这个会话」）。
    """
    user_id = auth.current_user_id()
    if not user_id:
        return {}  # 脚本里直接 invoke 时没有身份，跳过即可
    return {"user_profile": profiles.get_all(user_id)}


def route(state: AgentState) -> dict:
    """路由节点：一次轻量 LLM 调用 + JSON 输出，决定进入哪个分支。

    例外（短路）：模拟面试进行中（interview.status == "ongoing"）时直接续接面试官，
    不让 LLM 重新判意图——否则用户一句简短答题（如"chunk 用 500"）很可能被
    误判成闲聊，把面试打断。有显式状态时，状态优先于模型判断。
    """
    if (state.get("interview") or {}).get("status") == "ongoing":
        emit("agent_switch", route="mock_interviewer", skill="mock-interview")
        return {"route": "mock_interviewer", "active_skill": "mock-interview"}

    # 简历收集进行中同理：用户这几轮只是在补信息（"教育经历：广西大学…"），
    # 这种句子本身没有意图，交给 LLM 判会被判成闲聊，把收集流程打断。
    # 收满之后就不再短路，用户能正常问别的事。
    draft = state.get("resume_draft") or {}
    if draft and not is_ready(draft):
        emit("agent_switch", route="resume_builder", skill="resume-builder")
        return {"route": "resume_builder", "active_skill": "resume-builder"}

    skills_text = (
        "\n".join(f"- {s['name']}：{s['description']}" for s in list_skills()) or "（无）"
    )
    system = SystemMessage(content=_ROUTE_SYSTEM.format(skills=skills_text))
    # 统一走上下文预算层：内部按预算裁剪，并注入历史摘要
    messages = build_messages(system, state)

    resp = chat_raw(
        messages,
        response_format={"type": "json_object"},  # 强制 JSON，降低解析失败率
    )

    target, skill = "chat", ""
    try:
        data = json.loads(resp.choices[0].message.content or "{}")
        if data.get("route") in _ROUTES:
            target = data["route"]
            skill = data.get("skill") or ""
    except json.JSONDecodeError:
        pass  # 兜底到 chat，保证有回复

    emit("agent_switch", route=target, skill=skill)  # 让前端知道切到了哪个 Agent
    return {"route": target, "active_skill": skill}


def small_talk(state: AgentState) -> dict:
    """chat 分支：无 Skill 注入的直答节点。"""
    answer = chat_streaming(build_messages(SystemMessage(content=_CHAT_SYSTEM), state))
    # 不再全量拼接 *state["messages"]：历史由 build_messages 统一按预算裁剪 + 注入摘要
    return {"messages": [AIMessage(content=answer)]}


def build_supervisor(checkpointer=None):
    """组装主图：router 做完决定后按 route 字段分流。

    checkpointer 为 None 时是无状态图（每次 invoke 全新开始）；
    传入 checkpointer 后，同一 thread_id 的多次 invoke 会共享历史 state。
    """
    graph = StateGraph(AgentState)

    graph.add_node("load_profile", load_profile)  # 图入口：读长期画像（跨会话）
    graph.add_node("compress", compress_history)  # 图入口：按需把旧对话压成摘要
    graph.add_node("router", route)
    graph.add_node("chat", small_talk)
    graph.add_node("jd_analyst", build_jd_analyst())  # 子图直接作为节点
    graph.add_node("resume_coach", build_resume_coach())
    graph.add_node("resume_builder", build_resume_builder())
    graph.add_node("mock_interviewer", build_mock_interviewer())
    graph.add_node("progress_tracker", build_progress_tracker())

    graph.add_edge(START, "load_profile")
    graph.add_edge("load_profile", "compress")
    graph.add_edge("compress", "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["route"],  # 分支选择器：读路由结果
        {
            "chat": "chat",
            "jd_analyst": "jd_analyst",
            "resume_coach": "resume_coach",
            "resume_builder": "resume_builder",
            "mock_interviewer": "mock_interviewer",
            "progress_tracker": "progress_tracker",
        },
    )
    graph.add_edge("chat", END)
    graph.add_edge("jd_analyst", END)
    graph.add_edge("resume_coach", END)
    graph.add_edge("resume_builder", END)
    graph.add_edge("mock_interviewer", END)
    graph.add_edge("progress_tracker", END)

    return graph.compile(checkpointer=checkpointer)
