"""上下文预算层：所有「发给 LLM 的消息」的唯一出口。

为什么要有这一层：
之前路由节点只截最近 6 条，执行节点却发全量历史——两套裁剪策略并存。
后果有二：一是多轮对话的累计 token 成本近似 n²（每轮都重发全部历史）；
二是两层节点的判断依据不一致，可能路由和执行走向不同方向。
这里把「裁剪 + 摘要」收敛成一处：任何节点要发消息给模型，都调 build_messages()。

两类压缩手段（互补）：
1. 摘要（优先）：把确定要丢弃的旧对话压成要点，存进 state["summary"]。
   渐进更新——每轮只合并最旧的一批，state 里始终只有一份摘要，不随轮次增长。
2. 硬裁剪（保底）：若摘要后仍超预算，从最旧的消息开始丢。
"""

import tiktoken
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import AgentState
from app.llm import chat

# DeepSeek 未公开 tokenizer，这里用 cl100k_base 近似（估算偏保守即可）。
# 精确用量以 API 返回的 usage 字段为准，本层只用于「预算控制」。
_ENCODER = tiktoken.get_encoding("cl100k_base")

# 每条消息的角色/分隔符开销（官方对 chat 模型的估算约 3~4 token）
_PER_MESSAGE_OVERHEAD = 4

_SUMMARY_SYSTEM = """你负责压缩 Agent 的对话历史，为后续对话保留必要上下文。

要求：
1. 保留：用户的关键事实与背景、已达成的结论、进行中的任务与待办、重要约束与偏好。
2. 丢弃：寒暄、重复表述、模型的冗长解释。
3. 若提供了「已有摘要」，把新内容合并进去并去重，不要重复描述同一件事。
4. 用简洁的中文要点列表输出，控制在 300 字以内，不要任何前言或评论。"""


def count_tokens(messages: list[BaseMessage]) -> int:
    """估算一组消息的 token 数（含每条消息的固定开销）。"""
    return sum(
        len(_ENCODER.encode(m.content or "")) + _PER_MESSAGE_OVERHEAD for m in messages
    )


def _fit(messages: list[BaseMessage], budget: int) -> list[BaseMessage]:
    """从最近往前装，装不下的（更旧的）丢弃。保证留下的都是最新上下文。"""
    kept: list[BaseMessage] = []
    used = 0
    for message in reversed(messages):
        cost = len(_ENCODER.encode(message.content or "")) + _PER_MESSAGE_OVERHEAD
        if used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept


def build_messages(
    system: SystemMessage,
    state: AgentState,
    budget: int | None = None,
) -> list[BaseMessage]:
    """组装本轮发给模型的消息：system（必留）→ 历史摘要 → 预算内最近的原文历史。"""
    budget = budget or settings.context_budget_tokens
    remaining = budget - count_tokens([system])

    prefix: list[BaseMessage] = []
    if state.get("summary"):
        summary_msg = SystemMessage(
            content=f"<history_summary>\n{state['summary']}\n</history_summary>"
        )
        prefix = [summary_msg]
        remaining -= count_tokens(prefix)

    # 已被摘要覆盖的部分不再发送原文，避免同一信息出现两次
    pending = state["messages"][state.get("summarized_count", 0):]
    return [system, *prefix, *_fit(pending, max(remaining, 0))]


def compress_history(state: AgentState) -> dict:
    """图入口节点：未摘要的历史过长时，把旧对话压成摘要。

    触发条件：未摘要消息数 > keep_recent_messages * summary_trigger_ratio。
    这个「迟滞」设计很重要：若一超阈值就摘，等于每轮都多一次 LLM 调用；
    现在超到 2 倍才摘，且一次摘到只剩 keep_recent 条，之后若干轮都不会再触发。

    返回：{"summary": 新摘要, "summarized_count": 已覆盖条数}；不触发时返回 {}。
    """
    summarized = state.get("summarized_count", 0)
    pending = state["messages"][summarized:]
    trigger_at = settings.keep_recent_messages * settings.summary_trigger_ratio
    if len(pending) <= trigger_at:
        return {}

    # 一次摘到位：只留最近 keep_recent 条原文，其余全部并入摘要
    batch = pending[: len(pending) - settings.keep_recent_messages]
    new_summary = _summarize(state.get("summary", ""), batch)
    if not new_summary:
        return {}  # 摘要失败就交给硬裁剪兜底，不让流程中断

    return {"summary": new_summary, "summarized_count": summarized + len(batch)}


def _summarize(previous: str, batch: list[BaseMessage]) -> str:
    """把一批旧消息合并进已有摘要。失败返回空串（由调用方兜底）。"""
    transcript = "\n".join(f"{_role(m)}: {m.content}" for m in batch)
    prompt = (
        f"已有摘要：\n{previous or '（无）'}\n\n"
        f"需要合并进摘要的新对话：\n{transcript}"
    )
    try:
        return chat(
            [SystemMessage(content=_SUMMARY_SYSTEM), HumanMessage(content=prompt)]
        )
    except Exception:
        return ""


def _role(message: BaseMessage) -> str:
    return {"human": "用户", "ai": "助手", "system": "系统"}.get(
        message.type, message.type
    )
