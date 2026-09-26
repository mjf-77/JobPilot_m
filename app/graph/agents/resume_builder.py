"""简历制作 Agent（子图）：从零把用户的口述攒成一份成品简历。

和 resume_coach（诊断）的区别：
- resume_coach：输入是**已有的简历原文**，输出是**改进建议**——用户还得自己去改
- resume_builder：输入是**多轮口述**，输出是**结构化草稿**——攒齐后直接渲染成 PDF

这里两个关键设计：

1. **模型每轮只输出「本轮抽到的字段」（patch），不是完整草稿**。
   让它每轮重写整份草稿，它迟早会漏掉前面说过的内容。合并交给代码：
   `state["resume_draft"]` 是整字段替换语义，所以由这里负责把 patch 并进去，
   列表字段（教育/项目/技能）按各自的主键合并，同一条做更新、新的才追加。

2. **「收集完了没有」由代码判断，不让模型说了算**。
   和 mock_interviewer 的题数控制同一个思路——关键路径上的判断要可复现。
   模型只负责「还缺什么就问什么」。
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

SKILL_NAME = "resume-builder"
_MAX_ATTEMPTS = 3

# 列表字段按哪个键合并（同一个键视为同一条）
_LIST_KEYS = {"education": "school", "projects": "name", "skills": "label"}

# 必填项：齐了就能生成（求职意向是可选的）
_REQUIRED = ("name", "phone", "email", "education", "projects", "skills")
_LABELS = {
    "name": "姓名",
    "phone": "电话",
    "email": "邮箱",
    "education": "教育经历",
    "projects": "项目经历",
    "skills": "技能",
}


class BuilderTurn(BaseModel):
    patch: dict = Field(
        default_factory=dict,
        description="本轮从用户话里抽到的字段；这次没提到的不要放进来",
    )
    reply: str = Field(description="给用户的回复：先确认记下了什么，再问下一个问题")


_SYSTEM_TEMPLATE = """你正在执行 JobPilot 的「简历制作」任务，严格按下面的 SOP 作业：

<skill name="{skill_name}">
{sop}
</skill>

<current_draft>
{draft}
</current_draft>

<missing>
{missing}
</missing>

<output_schema>
{{
  "patch": {{
    "name": "", "phone": "", "email": "", "intent": "",
    "education": [{{"school": "", "major": "", "degree": "", "period": ""}}],
    "projects": [{{"name": "", "role": "", "period": "", "intro": "", "stack": "", "highlights": [""]}}],
    "skills": [{{"label": "", "detail": ""}}]
  }},
  "reply": "先一句话确认记住了什么，再问下一个问题"
}}
</output_schema>

硬性要求：
- patch 里**只放用户这轮提到的**，不要把 current_draft 里的旧内容重复输出
- 用户没说的**一律不要编**（尤其是数字、公司名、技术名）
- 只使用上面 schema 里出现过的字段名
- missing 显示「都齐了」时，reply 只确认本轮信息即可，**收尾提示由系统追加，你不用写**
只输出这个 JSON，不要任何解释文字，不要 Markdown 代码块标记。"""


def missing_labels(draft: dict) -> list[str]:
    """还缺哪些必填项。"""
    return [_LABELS[key] for key in _REQUIRED if not draft.get(key)]


def is_ready(draft: dict) -> bool:
    """草稿是否已够生成（供 API 层判断能否预览）。"""
    return not missing_labels(draft)


def _merge_list(old: list, new: list, key: str | None) -> list:
    """同主键的更新，新主键的追加；没有主键就整体拼在后面。"""
    if not key:
        return old + [item for item in new if item]

    index = {item.get(key): item for item in old if isinstance(item, dict)}
    result = list(old)
    for item in new:
        if not isinstance(item, dict):
            continue
        cleaned = {k: v for k, v in item.items() if v not in (None, "", [])}
        if not cleaned:
            continue
        item_key = cleaned.get(key)
        if item_key and item_key in index:
            index[item_key].update(cleaned)
        else:
            result.append(cleaned)
            if item_key:
                index[item_key] = cleaned
    return result


def merge_draft(old: dict, patch: dict) -> dict:
    """把本轮的 patch 合并进草稿。"""
    merged = dict(old or {})
    for key, value in (patch or {}).items():
        if isinstance(value, list):
            merged[key] = _merge_list(merged.get(key) or [], value, _LIST_KEYS.get(key))
        elif value not in (None, "", []):
            merged[key] = value
    return merged


def build(state: AgentState) -> dict:
    """单节点：抽取本轮信息 → 合并进草稿 → 回复用户。"""
    draft = state.get("resume_draft") or {}
    system = SystemMessage(
        content=_SYSTEM_TEMPLATE.format(
            skill_name=SKILL_NAME,
            sop=load_skill(SKILL_NAME),
            draft=json.dumps(draft, ensure_ascii=False) if draft else "（还是空的）",
            missing="、".join(missing_labels(draft)) or "（都齐了）",
        )
    )
    base = build_messages(system, state)

    # 结构化输出必须拿到完整 JSON 才能校验，没法边生成边推 token；
    # 但什么都不推前端会空白几秒，先给个进度提示。
    emit("token", text="正在整理你的信息…\n\n")

    last_error = ""
    for _ in range(_MAX_ATTEMPTS):
        messages = list(base)
        if last_error:
            messages.append(
                HumanMessage(
                    content=f"上次输出未通过校验：{last_error}\n请严格按 schema 重新输出 JSON。"
                )
            )

        raw = chat(messages, response_format={"type": "json_object"})
        try:
            turn = BuilderTurn.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            continue

        merged = merge_draft(draft, turn.patch)
        reply = (turn.reply or "").strip()
        if is_ready(merged):
            reply += "\n\n信息齐了——点输入框上方的「预览简历」看成品，满意就打印成 PDF。"

        emit("token", text=reply)
        return {"messages": [AIMessage(content=reply)], "resume_draft": merged}

    fallback = "这次没理解对，能再说一遍吗？可以分点说，比如「姓名 X，电话 Y，邮箱 Z」。"
    emit("token", text=fallback)
    return {"messages": [AIMessage(content=fallback)]}


def build_resume_builder():
    graph = StateGraph(AgentState)
    graph.add_node("build", build)
    graph.add_edge(START, "build")
    graph.add_edge("build", END)
    return graph.compile()
