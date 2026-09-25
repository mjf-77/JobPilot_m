"""模拟面试官 Agent（子图）。

与 jd_analyst 的单节点结构不同，这个子图内部有「两节点 + 一条条件边」：

    START → interviewer ──(还有题)──→ END
                        └─(题数用尽)──→ review → END

设计要点：
1. 把「面试对话」和「复盘报告」拆成两个阶段——前者是逐轮交互，后者是一次性结构化产出，
   两者的 prompt、输出形态、触发条件都不同，混在一个节点里会让 prompt 臃肿且互相干扰。
2. 阶段切换由 state["interview"]["asked"] 计数驱动（程序控制），不靠 LLM 自我判断。
   LLM 判断"我该收尾了"是不可靠的；把这种判断从关键路径上移走，是可靠性的基本手法。
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.context import build_messages
from app.graph.state import AgentState
from app.llm import chat_streaming
from app.retrieval.service import search
from app.skills import load_skill

SKILL_NAME = "mock-interview"

_INTERVIEW_TEMPLATE = """你正在执行 JobPilot 的「模拟面试」任务，严格按下面的 SOP 作业：

<skill name="{skill_name}">
{sop}
</skill>

<progress>
已提问 {asked} 题，计划共 {max_q} 题。
</progress>

<question_bank>
以下是题库中与本轮话题相关的题目（混合检索召回），请参考它们的**考察点**来出题或追问：
{questions}
</question_bank>

本轮要求：
- 若对话中还没有任何题目，这是第 1 题：先用一句话确认岗位方向，然后出第 1 题。
- 否则：先对候选人上一轮回答给一句即时反馈，再决定追问或换方向。
- 出题优先从上面题库中挑选，并结合候选人的具体背景改写措辞；不要与之前问过的题重复。
- 本轮只输出「反馈 + 一个问题」，**不要**输出复盘报告。"""

_REVIEW_TEMPLATE = """面试已结束，现在进入「复盘阶段」。

<review_template>
{template}
</review_template>

要求：
- 逐题复盘表必须覆盖本次面试问过的所有题目，基于候选人的真实回答，不得编造。
- 候选人未答或答不上来的题，如实标注，并给出改进示范。
- **不要再提问、不要追问**，本轮只输出复盘报告。
- 直接输出 Markdown，不要任何前言。"""


def _retrieve_questions(state: AgentState, top_k: int = 5) -> str:
    """按最近对话内容从题库检索相关题目，格式化成 prompt 片段。

    检索失败（索引缺失 / API 异常）时返回占位文案，绝不影响面试继续——
    检索是「让出题更靠谱」的增强，不是关键路径。
    """
    recent = " ".join(m.content for m in state["messages"][-3:] if m.content)
    if not recent.strip():
        return "（首轮无上下文，按岗位方向自行出题）"
    try:
        candidates = search(recent[-300:], top_k=top_k)
    except Exception:
        candidates = []
    if not candidates:
        return "（题库未命中，按 SOP 自行出题）"
    return "\n".join(
        f"- [{c['id']}] {c['question']}（考察点：{'；'.join(c['key_points'])}）"
        for c in candidates
    )


def _interview(state: AgentState) -> dict:
    """推进面试：出题 / 追问。题数用尽时只切换状态，把复盘交给 review 节点。"""
    interview = state.get("interview") or {}
    if interview.get("status") == "finished":
        interview = {}  # 上一轮面试已结束，本次视为重新开始
    if not interview:
        interview = {
            "asked": 0,
            "max_questions": settings.interview_max_questions,
            "status": "ongoing",
        }

    if interview["asked"] >= interview["max_questions"]:
        # 只切状态，不生成复盘——职责分离，复盘交给 review 节点专职完成
        return {"interview": {**interview, "status": "done"}}

    system = SystemMessage(
        content=_INTERVIEW_TEMPLATE.format(
            skill_name=SKILL_NAME,
            sop=load_skill(SKILL_NAME),
            asked=interview["asked"],
            max_q=interview["max_questions"],
            questions=_retrieve_questions(state),
        )
    )
    answer = chat_streaming(build_messages(system, state))
    return {
        "messages": [AIMessage(content=answer)],
        "interview": {**interview, "asked": interview["asked"] + 1},
    }


def _review(state: AgentState) -> dict:
    """面试收尾：一次性产出结构化复盘报告。

    两个关键点：
    1. 加载 Skill 目录下的 review-template.md，而不是 SKILL.md——后者的规则是
       「每轮只输出反馈+一个问题」，会和「输出完整复盘」直接冲突。
    2. 在消息末尾追加一条明确指令。只靠 system 里的要求不够：历史里连续的
       「面试官追问」形成了很强的模式惯性，模型会顺着往下继续追问。
       把指令放到最后一条，才能压过历史模式。
    """
    system = SystemMessage(
        content=_REVIEW_TEMPLATE.format(
            template=load_skill(SKILL_NAME, "review-template.md")
        )
    )
    instruction = HumanMessage(
        content="面试到此结束。请基于上面的完整面试记录，直接输出结构化复盘报告，不要再提问。"
    )
    answer = chat_streaming([*build_messages(system, state), instruction])
    return {
        "messages": [AIMessage(content=answer)],
        "interview": {**(state.get("interview") or {}), "status": "finished"},
    }


def _route_after_interview(state: AgentState) -> str:
    """条件边选择器：面试题数用尽 → 进复盘；否则本轮结束等用户作答。"""
    done = (state.get("interview") or {}).get("status") == "done"
    return "review" if done else "end"


def build_mock_interviewer():
    graph = StateGraph(AgentState)
    graph.add_node("interviewer", _interview)
    graph.add_node("review", _review)

    graph.add_edge(START, "interviewer")
    graph.add_conditional_edges(
        "interviewer",
        _route_after_interview,
        {"review": "review", "end": END},
    )
    graph.add_edge("review", END)
    return graph.compile()
