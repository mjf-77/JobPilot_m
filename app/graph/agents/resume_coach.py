"""简历优化 Agent（子图）：诊断简历并给出逐条改写建议。

与其它三个 Agent 的关键区别：它要求模型输出**结构化 JSON**，再用 Pydantic 校验，
校验失败（少字段、类型不对、分数越界）就把错误回灌让它重试，而不是把半成品丢给用户。
这是「结构化输出 + 校验 + 重试」的标准做法，也是所有需要程序消费模型输出的场景的通解。
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from app.context import build_messages
from app.events import emit
from app.graph.state import AgentState
from app.llm import chat
from app.skills import load_skill

SKILL_NAME = "resume-coach"
_MAX_ATTEMPTS = 3


class Rewrite(BaseModel):
    original: str = Field(description="简历原文片段")
    problem: str = Field(description="这段的问题")
    rewritten: str = Field(description="改写后的版本")


class ResumeReview(BaseModel):
    summary: str = Field(description="一句话总评")
    score: int = Field(description="0-100 的质量分", ge=0, le=100)
    strengths: list[str] = Field(description="值得保留的亮点")
    rewrites: list[Rewrite] = Field(description="逐条改写建议")
    priorities: list[str] = Field(description="最优先改的三件事")


_SYSTEM_TEMPLATE = """你正在执行 JobPilot 的「简历优化」任务，严格按下面的 SOP 作业：

<skill name="{skill_name}">
{sop}
</skill>

<output_schema>
{{
  "summary": "一句话总评",
  "score": 0 到 100 的整数,
  "strengths": ["值得保留的亮点"],
  "rewrites": [{{"original": "简历原文片段", "problem": "问题", "rewritten": "改写后"}}],
  "priorities": ["最优先改的三件事"]
}}
</output_schema>

{resume}

只输出这个 JSON 对象，不要任何解释文字，不要 Markdown 代码块标记。"""


def _render(review: ResumeReview) -> str:
    """把结构化诊断渲染成 Markdown，直接展示给用户。"""
    lines = [
        "### 总评",
        review.summary,
        "",
        f"**质量分：{review.score}/100**",
        "",
        "### 值得保留的亮点",
        *[f"- {s}" for s in review.strengths],
        "",
        "### 逐条改写建议",
        "| 原文 | 问题 | 改写后 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {r.original} | {r.problem} | {r.rewritten} |" for r in review.rewrites)
    lines.append("")
    lines.append("### 最优先改的三件事")
    lines.extend(f"{i}. {p}" for i, p in enumerate(review.priorities, 1))
    return "\n".join(lines)


def diagnose(state: AgentState) -> dict:
    """LLM 节点：产出结构化诊断；校验失败就把错误回灌重试。"""
    resume_text = state.get("resume_text", "")
    # 简历原文由「上传 PDF」写入 state，不走聊天记录——
    # 放 system 里能保证每次诊断都拿到完整原文，也不会被上下文压缩吃掉。
    resume_block = (
        f"<resume>\n{resume_text}\n</resume>"
        if resume_text
        else "<resume>（用户尚未上传简历，请提示他在输入框左侧上传 PDF，不要凭空诊断）</resume>"
    )
    system = SystemMessage(
        content=_SYSTEM_TEMPLATE.format(
            skill_name=SKILL_NAME,
            sop=load_skill(SKILL_NAME),
            resume=resume_block,
        )
    )
    base = build_messages(system, state)
    last_error = ""

    # 结构化输出必须拿到完整 JSON 才能校验、才能渲染成表格，
    # 所以没法像别的 Agent 那样边生成边推 token（中间态是一堆半截 JSON，给用户看毫无意义）。
    # 但什么都不推的话前端会空白好几秒——所以先给个进度提示，出结果后再一次性渲染。
    emit("token", text="正在分析简历并生成结构化诊断…\n\n")

    for _ in range(_MAX_ATTEMPTS):
        messages = list(base)
        if last_error:
            # 把校验错误原文回灌：模型看到具体哪不对，比笼统说"格式错了"更有效
            messages.append(
                HumanMessage(
                    content=f"上次输出未通过校验：{last_error}\n请严格按 schema 重新输出 JSON。"
                )
            )

        raw = chat(messages, response_format={"type": "json_object"})
        try:
            review = ResumeReview.model_validate(json.loads(raw))
            rendered = _render(review)
            emit("token", text=rendered)
            return {"messages": [AIMessage(content=rendered)]}
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:300]

    failure = f"生成结构化诊断失败（已重试 {_MAX_ATTEMPTS} 次）：{last_error}"
    emit("token", text=failure)
    return {"messages": [AIMessage(content=failure)]}


def build_resume_coach():
    graph = StateGraph(AgentState)
    graph.add_node("diagnose", diagnose)
    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", END)
    return graph.compile()
