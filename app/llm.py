"""LLM 客户端工厂。

设计意图：
1. 两类客户端职责分离——对话走 DeepSeek，向量化走智谱，互不影响。
2. 用 lru_cache 做进程内单例：懒加载（首次调用才建连接池）且线程安全。
3. LangChain 的 Message 对象与 OpenAI dict 之间的转换集中在这里，
   避免每个 Agent 各写一份。
"""

import json
from functools import lru_cache

from langchain_core.messages import BaseMessage
from openai import OpenAI

from app.config import settings
from app.metering import record as _record_usage

# LangChain message.type → OpenAI role
_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system"}


@lru_cache(maxsize=1)
def get_chat_client() -> OpenAI:
    """DeepSeek 客户端（对话 / Function Calling）。"""
    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )


@lru_cache(maxsize=1)
def get_embed_client() -> OpenAI:
    """智谱客户端（仅 Embedding，M2 检索用）。"""
    return OpenAI(
        api_key=settings.zhipu_api_key,
        base_url=settings.zhipu_base_url,
    )


def to_openai_messages(messages: list[BaseMessage]) -> list[dict]:
    """LangChain Message 列表 → OpenAI 协议 messages。

    比初版多处理两种「工具调用」消息，否则多轮 Function Calling 会断链：
    - AIMessage.tool_calls → assistant 消息的 tool_calls 字段（模型请求调工具）
    - ToolMessage          → role=tool + tool_call_id（把工具结果回灌给模型）
    """
    out: list[dict] = []
    for m in messages:
        item: dict = {"role": _ROLE_MAP.get(m.type, m.type), "content": m.content or ""}

        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]

        if m.type == "tool":
            item["tool_call_id"] = getattr(m, "tool_call_id", "")

        out.append(item)
    return out


def chat_raw(messages: list[BaseMessage], **kwargs):#**kwargs**收集所有多余的关键字参数，打包成一个字典**。
    """一次对话补全，返回完整 response。

    需要 tool_calls / usage 等字段时用它。
    所有非流式调用都应走这里——漏一处，token 计量就不准，成本统计跟着失真。
    """
    resp = get_chat_client().chat.completions.create(
        model=settings.chat_model,
        messages=to_openai_messages(messages),
        **kwargs,
    )
    if resp.usage:
        _record_usage(resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return resp


def chat(messages: list[BaseMessage], **kwargs) -> str:
    """一次对话补全，只取文本。不流式的场景用（如路由节点只取 JSON）。"""
    return chat_raw(messages, **kwargs).choices[0].message.content or ""


def stream_chat(messages: list[BaseMessage], on_token=None, **kwargs) -> str:
    """流式对话补全：每收到一个增量片段就回调 on_token，最终返回完整文本。

    返回值仍是完整字符串，所以调用方既能拿到整段结果（落 state），
    又能把过程推给前端。
    """
    # 流式响应默认不返回 usage，必须显式要求，否则 token 统计会缺掉所有流式调用
    kwargs.setdefault("stream_options", {"include_usage": True})
    stream = get_chat_client().chat.completions.create(
        model=settings.chat_model,
        messages=to_openai_messages(messages),
        stream=True,
        **kwargs,
    )
    parts: list[str] = []
    for chunk in stream:
        if chunk.usage:  # 最后一个 chunk 才带 usage
            _record_usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        if not chunk.choices:  # 含 usage 的收尾 chunk 没有 choices
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            parts.append(delta)
            if on_token is not None:
                on_token(delta)
    return "".join(parts)


def chat_streaming(messages: list[BaseMessage], **kwargs) -> str:
    """流式补全 + 自动把增量片段推成 token 事件。Agent 节点统一用这个入口。"""
    from app.events import emit  # 局部导入，避免与 events 模块形成层级耦合

    return stream_chat(messages, on_token=lambda text: emit("token", text=text), **kwargs)


def stream_chat_with_tools(messages: list[BaseMessage], tools: list[dict], on_token=None):
    """流式补全 + 工具调用，返回 (完整文本, tool_calls 列表)。

    为什么需要它：Function Calling 要求「拿到完整参数才能执行」，
    而前端要「逐字看到输出」——两者看似冲突，其实可以并存：
    content 分片照常回调输出，tool_calls 分片按 index 累积到完整再用。
    少了这个函数，带工具的节点就只能非流式，用户在界面上会看到一片空白。
    """
    kwargs = {"stream_options": {"include_usage": True}}
    stream = get_chat_client().chat.completions.create(
        model=settings.chat_model,
        messages=to_openai_messages(messages),
        tools=tools,
        stream=True,
        **kwargs,
    )

    parts: list[str] = []
    buf: dict[int, dict] = {}  # index → {"id","name","arguments"}

    for chunk in stream:
        if chunk.usage:
            _record_usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        if delta.content:
            parts.append(delta.content)
            if on_token is not None:
                on_token(delta.content)

        # tool_calls 是分片下发的：name 只出现一次，arguments 会拆成多段
        for call in delta.tool_calls or []:
            slot = buf.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
            if call.id:
                slot["id"] = call.id
            if call.function:
                if call.function.name:
                    slot["name"] = call.function.name
                if call.function.arguments:
                    slot["arguments"] += call.function.arguments

    tool_calls = [
        {
            "name": slot["name"],
            "args": json.loads(slot["arguments"] or "{}"),
            "id": slot["id"],
            "type": "tool_call",
        }
        for slot in buf.values()
    ]
    return "".join(parts), tool_calls
