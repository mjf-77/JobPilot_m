"""LangGraph 状态契约。

设计意图：
- 主图与所有子图共享同一个 AgentState，子图读写字段即"交接班"。
- messages 用 add_messages reducer：节点返回新消息时是「追加」而非「覆盖」。
- total=False：字段可选，图刚启动时只有 messages。
"""

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

# Supervisor 可路由到的目标（每新增一个 Agent，这里加一项）
RouteTarget = Literal[
    "jd_analyst", "resume_coach", "mock_interviewer", "progress_tracker", "chat"
]


class AgentState(TypedDict, total=False):
    # 会话消息（reducer 决定追加语义）
    messages: Annotated[list, add_messages]
    # 本轮路由结果
    route: RouteTarget
    # 本轮激活的 Skill 名（决定注入哪份 SOP）
    active_skill: str
    # 用户画像（长期记忆，M4 接入）
    user_profile: dict
    # 早期对话的压缩摘要（由 compress 节点渐进更新，只保留一份，不随轮次增长）
    summary: str
    # 已纳入摘要的消息条数：messages[:summarized_count] 已被 summary 覆盖
    summarized_count: int
    # 模拟面试的进度状态：{"asked": int, "max_questions": int, "status": "ongoing|done|finished"}
    interview: dict
    # 上传的简历原文（按会话保存，供简历优化 Agent 使用；不混入对话历史）
    resume_text: str
