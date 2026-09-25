# JobPilot —— AI 求职助手（多 Agent）实现笔记

> 2026-09-23 一天完成的项目骨架：从零到「能在浏览器里演示」的完整 Agent 系统。
> 覆盖：Supervisor 多 Agent 编排、Skill 机制、会话持久化、上下文预算、SSE 流式。

---

## 一、它解决什么问题

秋招期间的信息过载：搜岗位、判断 JD 值不值得投、对照简历改、练面试、记投递进度——全是重复劳动。

JobPilot 把这些做成一个多 Agent 系统。**场景是真实的**（我自己每天在做的事），这是它和"教程项目"最根本的区别。

当前已上线两个 Agent：

| Agent | 做什么 |
|---|---|
| 职位分析（jd_analyst） | 逐条比对 JD 与候选人，输出匹配度评分、差距清单、投递建议 |
| 模拟面试（mock_interviewer） | 扮演面试官连续追问，题数用尽后自动产出结构化复盘报告 |

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│  浏览器 web/index.html                                           │
│  对话界面 · 打字机渲染 · Agent 切换提示 · 会话号存 localStorage      │
└────────────────────────┬─────────────────────────────────────────┘
                         │ POST /api/chat  （fetch + ReadableStream 解析 SSE）
┌────────────────────────▼─────────────────────────────────────────┐
│  HTTP 层  app/main.py · app/api/chat.py                          │
│  FastAPI · Pydantic 校验(422) · lifespan 预热 · StreamingResponse │
└────────────────────────┬─────────────────────────────────────────┘
                         │ graph.stream(stream_mode="custom")
┌────────────────────────▼─────────────────────────────────────────┐
│  编排层  app/graph/supervisor.py  （LangGraph 主图）               │
│                                                                  │
│   START → compress → router ─┬─→ jd_analyst        ─→ END       │
│          （预算）  （路由）    ├─→ mock_interviewer  ─→ END       │
│                              └─→ chat              ─→ END       │
│                                                                  │
│   ▸ compress   上下文预算：按需把旧对话压成摘要（app/context.py）    │
│   ▸ router     意图路由 + JSON 兜底 + 面试中短路                    │
│   ▸ 子图       每个 Agent 是可独立编译/测试的 StateGraph            │
└────────────────────────┬─────────────────────────────────────────┘
                         │ 读/写  AgentState
┌────────────────────────▼─────────────────────────────────────────┐
│  能力层                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────────────────┐ │
│  │ Skill 机制    │ │ 上下文预算层  │ │ LLM 工厂 + 事件通道        │ │
│  │ app/skills.py│ │app/context.py│ │ app/llm.py · app/events.py│ │
│  │ 渐进披露加载  │ │ 裁剪+摘要     │ │ DeepSeek/智谱 · emit token│ │
│  └──────────────┘ └──────────────┘ └───────────────────────────┘ │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│  持久层  app/graph/checkpointer.py                                │
│  SqliteSaver → data/checkpoints.db   （按 thread_id 存全量 state） │
└──────────────────────────────────────────────────────────────────┘

配置：app/config.py（pydantic-settings 读 .env，唯一配置入口）
SOP：skills/<name>/SKILL.md（行为与代码解耦，改行为不改代码）
```

---

## 三、一次请求的完整生命周期

以「帮我模拟面试，岗位是 AI 应用开发」为例，把代码串起来：

```
① 浏览器                index.html :: send(text)
   POST /api/chat  {message, thread_id}
                    ↓
② API 层               api/chat.py :: _event_stream(req)
   config = {"configurable": {"thread_id": req.thread_id}}
   graph.stream({...}, config, stream_mode="custom")
                    ↓
③ checkpointer         graph/checkpointer.py
   按 thread_id 取出该会话的历史 state（含 messages、summary、interview）
                    ↓
④ compress 节点        context.py :: compress_history(state)
   未摘要消息数 > keep_recent × ratio ？→ 调 LLM 把最旧一批压成摘要
   返回 {"summary": ..., "summarized_count": ...}
                    ↓
⑤ router 节点          supervisor.py :: route(state)
   若 interview.status == "ongoing" → 短路直接进面试官（不调 LLM）
   否则：用「技能清单」构造 prompt → LLM 输出 JSON → 解析出 route
   emit("agent_switch", route=...)   ← 推给前端显示"→ mock_interviewer"
                    ↓
⑥ 子图节点             mock_interviewer.py :: _interview(state)
   组装 system = SKILL.md(SOP) + 进度(第N题/共M题)
   messages = context.py :: build_messages(system, state)  ← 统一裁剪+注入摘要
   answer = llm.py :: chat_streaming(messages)
            └─ openai stream=True 逐 chunk
            └─ emit("token", text=...)   ← 每个增量片段推给前端
   返回 {"messages": [AIMessage(answer)], "interview": {asked: +1}}
                    ↓
⑦ 条件边               _route_after_interview(state)
   status == "done" ？→ 进 review 节点出复盘 ；否则 END 等用户作答
                    ↓
⑧ 落盘                 checkpointer 把新 state 写回 checkpoints.db
                    ↓
⑨ 前端渲染             token 累加 → 打字机效果；收到 done 帧收尾
```

**关键**：③⑧ 是框架自动做的（`thread_id` 是索引键），④⑤⑥ 是业务节点，⑨ 只认事件不管内部实现。

---

## 四、模块逐个拆解

### 1. 配置层 `app/config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    deepseek_api_key: str
    chat_model: str = "deepseek-chat"
    # 上下文预算
    context_budget_tokens: int = 12000
    keep_recent_messages: int = 8
    summary_trigger_ratio: float = 2.0
    # 模拟面试
    interview_max_questions: int = 5

settings = Settings()
```

**设计**：所有密钥/模型名/阈值只在这里定义一次，其它模块一律 `from app.config import settings`。换模型、改预算只动一处；密钥不进代码。

### 2. LLM 工厂与事件通道 `app/llm.py` · `app/events.py`

```python
# llm.py
@lru_cache(maxsize=1)
def get_chat_client() -> OpenAI:          # DeepSeek：对话/FC
@lru_cache(maxsize=1)
def get_embed_client() -> OpenAI:         # 智谱：Embedding（M2 用）

def chat_streaming(messages) -> str:
    """流式补全 + 每个增量片段推成 token 事件；返回值仍是完整字符串"""
    return stream_chat(messages, on_token=lambda t: emit("token", text=t))
```

```python
# events.py
def emit(event_type: str, **payload) -> None:
    """推送一个事件；不在流式上下文时静默忽略，绝不打断业务逻辑。"""
    try:
        get_stream_writer()({"type": event_type, **payload})
    except Exception:
        pass
```

**两个要点**：

- `lru_cache` 做单例：懒加载 + 线程安全，比全局变量可靠。
- `events.py` 是"Agent 内部往外说话"的唯一出口。事件走 LangGraph 的 **custom stream** 通道；在脚本里直接 `invoke` 时 `get_stream_writer()` 不可用，`try/except` 静默忽略——**节点代码不用写任何"我是否在流式环境"的判断**。

### 3. Skill 机制 `app/skills.py` + `skills/`

```
skills/
├── jd-analysis/SKILL.md              # 职位分析 SOP
└── mock-interview/
    ├── SKILL.md                      # 面试推进 SOP
    └── review-template.md            # 复盘报告模板（独立文件！）
```

```python
def list_skills() -> list[dict]:
    """轻量清单：只含 name + description，供路由 prompt 使用（每个约 10 token）"""

def load_skill(name: str, filename: str = "SKILL.md") -> str:
    """按需加载 Skill 目录下的任意文件（SKILL.md 自动剥离 frontmatter）"""
```

**渐进披露（progressive disclosure）**：

| 阶段 | 加载什么 | 成本 |
|---|---|---|
| 路由 | 只有 `name + description` | 每技能约 10 token |
| 命中后 | `SKILL.md` 全文注入该子 Agent | 按需 |
| 特定阶段 | `review-template.md` 等附加文件 | 按需 |

对比"把所有 SOP 全文塞进 system prompt"：那样 5 个 Skill 就是几千 token 常驻，还会互相干扰指令。

**一个 Skill = 一个目录**，SKILL.md 是主 SOP，其余文件是按阶段的附加资源——这个设计是踩坑之后才明白的（见第六节 Bug 1）。

### 4. 状态契约 `app/graph/state.py`

```python
RouteTarget = Literal["jd_analyst", "mock_interviewer", "chat"]

class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]   # reducer：追加而非覆盖
    route: RouteTarget
    active_skill: str
    summary: str                # 早期对话的压缩摘要
    summarized_count: int       # messages[:n] 已被 summary 覆盖
    interview: dict             # {"asked", "max_questions", "status"}
    user_profile: dict          # 长期记忆（M4）
```

**为什么用 `add_messages` reducer**：节点返回 `{"messages": [新消息]}` 时是追加，不会把历史冲掉。其它没有 reducer 的字段（如 `interview`）是**整字段替换**语义——所以节点更新时必须 `{**interview, "asked": ...}` 带上全部字段，漏了就把字段写没了。

主图与所有子图共用这一份 State，子图读写的字段就是"交接班"。

### 5. Supervisor 主图 `app/graph/supervisor.py`

```python
def route(state: AgentState) -> dict:
    # 短路：面试进行中直接用状态决策，不让 LLM 重新判意图
    if (state.get("interview") or {}).get("status") == "ongoing":
        emit("agent_switch", route="mock_interviewer", skill="mock-interview")
        return {"route": "mock_interviewer", "active_skill": "mock-interview"}

    skills_text = "\n".join(f"- {s['name']}：{s['description']}" for s in list_skills())
    system = SystemMessage(content=_ROUTE_SYSTEM.format(skills=skills_text))
    messages = build_messages(system, state)            # 走统一预算层
    resp = get_chat_client().chat.completions.create(
        model=settings.chat_model,
        messages=to_openai_messages(messages),
        response_format={"type": "json_object"},        # 强制 JSON
    )
    target, skill = "chat", ""
    try:
        data = json.loads(resp.choices[0].message.content or "{}")
        if data.get("route") in _ROUTES:
            target, skill = data["route"], data.get("skill") or ""
    except json.JSONDecodeError:
        pass                                            # 兜底到 chat
    emit("agent_switch", route=target, skill=skill)
    return {"route": target, "active_skill": skill}
```

```python
def build_supervisor(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("compress", compress_history)
    graph.add_node("router", route)
    graph.add_node("chat", small_talk)
    graph.add_node("jd_analyst", build_jd_analyst())          # 子图直接作为节点
    graph.add_node("mock_interviewer", build_mock_interviewer())

    graph.add_edge(START, "compress")
    graph.add_edge("compress", "router")
    graph.add_conditional_edges(
        "router", lambda state: state["route"],
        {"chat": "chat", "jd_analyst": "jd_analyst", "mock_interviewer": "mock_interviewer"},
    )
    graph.add_edge("chat", END)
    graph.add_edge("jd_analyst", END)
    graph.add_edge("mock_interviewer", END)
    return graph.compile(checkpointer=checkpointer)
```

**三个设计决策**：

1. **子图直接当节点用**：`add_node("jd_analyst", build_jd_analyst())`。主图不知道子图内部有几个节点——以后往子图里加 ReAct 工具环，主图一行不用改。
2. **路由必须兜底**：JSON 解析失败或返回未知分支 → 退回 `chat`。路由是 LLM 做的，必须假设它会出错；路由挂掉不能整轮崩。
3. **状态优先于模型判断**：面试进行中，用户一句"chunk 用 500"根本不像面试请求，让 LLM 判意图极可能判成闲聊、把面试打断。有显式状态时，用状态。

### 6. 子 Agent

**职位分析 `app/graph/agents/jd_analyst.py`** —— 单节点子图：

```python
def analyze(state: AgentState) -> dict:
    sop = load_skill(SKILL_NAME)                       # 取 SOP
    system = SystemMessage(content=_SYSTEM_TEMPLATE.format(skill_name=SKILL_NAME, sop=sop))
    answer = chat_streaming(build_messages(system, state))
    return {"messages": [AIMessage(content=answer)]}
```

**模拟面试 `app/graph/agents/mock_interviewer.py`** —— 两节点 + 条件边：

```
START → interviewer ──(还有题)──→ END
                    └─(题数用尽)──→ review → END
```

```python
def _interview(state: AgentState) -> dict:
    interview = state.get("interview") or {}
    if interview.get("status") == "finished":
        interview = {}                                  # 重新开始一场
    if not interview:
        interview = {"asked": 0,
                     "max_questions": settings.interview_max_questions,
                     "status": "ongoing"}
    if interview["asked"] >= interview["max_questions"]:
        return {"interview": {**interview, "status": "done"}}   # 只切状态，不出复盘
    system = SystemMessage(content=_INTERVIEW_TEMPLATE.format(..., asked=..., max_q=...))
    answer = chat_streaming(build_messages(system, state))
    return {"messages": [AIMessage(content=answer)],
            "interview": {**interview, "asked": interview["asked"] + 1}}

def _route_after_interview(state: AgentState) -> str:
    done = (state.get("interview") or {}).get("status") == "done"
    return "review" if done else "end"
```

`interview` 的状态机：`ongoing →（题数用尽）→ done →（复盘写完）→ finished`。

**为什么阶段切换由计数器驱动，不让 LLM 自己判断"该收尾了"**：LLM 的自我判断既不可复现也不可靠（可能聊两句就要收尾，也可能追问十轮不肯停），而这是关键路径上的分支决策。**把不确定性从关键路径上移走**。

### 7. 会话持久化 `app/graph/checkpointer.py`

```python
@lru_cache(maxsize=1)
def get_checkpointer() -> SqliteSaver:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "checkpoints.db", check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()          # 建表，幂等
    return saver
```

**它带来三件事**（不只是"记住聊天记录"）：

| 能力 | 说明 |
|---|---|
| 多轮记忆 | 同一 `thread_id` 的 invoke 自动带上历史 |
| 跨进程持久化 | 进程退出重启，同一 thread 的历史仍在 |
| `interrupt()` 的前提 | 人工确认挂起时状态存进 checkpointer，resume 才能恢复现场 |

**为什么不用 `from_conn_string`**：那是 contextmanager，连接生命周期绑死在 `with` 块内；我们的图要长驻（FastAPI 进程），所以自己持有连接复用。

**为什么 `check_same_thread=False`**：SQLite 默认禁止跨线程复用连接，而 FastAPI 的 SSE 端点可能在不同线程访问同一个 saver。

### 8. 上下文预算层 `app/context.py`

**问题**：路由节点只截最近 6 条，执行节点却发全量历史——两套裁剪策略并存。后果有二：多轮对话累计 token 成本近似 n²；两层节点的判断依据不一致。

**解法**：把所有"发给模型的消息"收敛到一个出口。

```python
def build_messages(system, state, budget=None) -> list[BaseMessage]:
    """system（必留）→ 历史摘要 → 预算内最近的原文历史"""
    budget = budget or settings.context_budget_tokens
    remaining = budget - count_tokens([system])

    prefix = []
    if state.get("summary"):
        prefix = [SystemMessage(content=f"<history_summary>\n{state['summary']}\n</history_summary>")]
        remaining -= count_tokens(prefix)

    pending = state["messages"][state.get("summarized_count", 0):]   # 已摘要的不再发原文
    return [system, *prefix, *_fit(pending, max(remaining, 0))]      # _fit：从最近往前装
```

```python
def compress_history(state: AgentState) -> dict:
    """图入口节点：未摘要的历史过长时，把旧对话压成摘要"""
    summarized = state.get("summarized_count", 0)
    pending = state["messages"][summarized:]
    trigger_at = settings.keep_recent_messages * settings.summary_trigger_ratio
    if len(pending) <= trigger_at:
        return {}                                    # 未到阈值，什么都不做
    batch = pending[: len(pending) - settings.keep_recent_messages]
    new_summary = _summarize(state.get("summary", ""), batch)
    if not new_summary:
        return {}                                    # 摘要失败 → 交给硬裁剪兜底
    return {"summary": new_summary, "summarized_count": summarized + len(batch)}
```

**两种压缩手段**：

1. **摘要（优先）**：把要丢弃的旧对话压成要点。渐进合并——prompt 是"已有摘要 + 新对话 → 合并去重"，所以 `state["summary"]` 永远是**一个固定大小的字符串**，不随轮次增长。
2. **硬裁剪（保底）**：`_fit()` 从最近往前装，装不下的丢最旧的。

**迟滞（hysteresis）设计**：触发阈值是 `keep_recent × 2.0`，且触发时一次摘到只剩 `keep_recent` 条。若一超阈值就摘，等于每轮都多一次 LLM 调用；现在攒够了再摘，之后若干轮都不触发。

> 这个思想和缓存淘汰、GC 分代阈值、TCP 拥塞控制是同一个：给阈值加缓冲带，避免在临界点附近高频抖动。

**实测数据**（`tests/context_budget_check.py`）：

```
[A] 预算=2094 token，历史 20 条 → 实际发送 3 条，估算 1502 token
[B] 轮次1: 覆盖 0 条 | 轮次2: 覆盖 0 条
[B] 轮次3: 覆盖 3 条（触发一次，一次摘到位）| 轮次4: 覆盖 3 条（不触发，零额外调用）
```

**为什么用 `summarized_count` 而不是删消息**：删消息不可逆，万一摘要质量差原始信息就永久丢了。现在做法是"**消息全留着，只是不再发送**"——压缩只作用在发送这一层，保留可回溯性。

### 9. HTTP / SSE 层 `app/api/chat.py` · `app/main.py`

```python
def _frame(event: dict) -> str:
    """SSE 帧格式：data: <json>\\n\\n（空行是帧分隔符）"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

def _event_stream(req: ChatRequest):
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        for event in get_graph().stream(
            {"messages": [HumanMessage(content=req.message)]},
            config,
            stream_mode="custom",          # 只收节点 emit 的事件，不吐 state
        ):
            yield _frame(event)
    except Exception as exc:
        yield _frame({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    yield _frame({"type": "done"})

@router.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_event_stream(req), media_type="text/event-stream")
```

```python
# main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_checkpointer()   # 建表（幂等）
    get_graph()          # 编译图（进程内单例）
    yield
```

**四个要点**：

- `stream_mode="custom"`：只往外发节点主动 `emit()` 的事件，不把内部 state 吐给前端。
- 端点写成同步 `def`：Starlette 检测到同步生成器会**自动丢进线程池**，不阻塞事件循环。
- 异常转成 `error` 事件：不让连接静默断掉，前端能提示、排查有线索。
- `lifespan` 预热：首个请求不因建表/编译图而变慢。

### 10. 前端 `web/index.html`

```javascript
const resp = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: text, thread_id: threadId }),
});
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = '';
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  const frames = buf.split('\n\n');       // SSE 用空行分帧
  buf = frames.pop();                     // 最后一段可能不完整，留到下一轮
  for (const frame of frames) {
    const ev = JSON.parse(frame.slice(6));
    if (ev.type === 'token') bubble.textContent += ev.text;        // 打字机
    else if (ev.type === 'agent_switch') noteEl.textContent = '→ ' + ev.route;
  }
}
```

**为什么不用 `EventSource`**：它只支持 GET——参数只能塞 URL、**不能带请求体**。用 POST + JSON 再自己解析 SSE 帧，不受这些限制。

---

## 五、关键设计决策速查（面试用）

| 决策 | 为什么 |
|---|---|
| Supervisor 模式而非全连接 | 路由可控、故障隔离、每个 Agent 可独立测试 |
| 子图直接作为节点 | 主图不关心子图内部结构，加 ReAct 环不用改主图 |
| 路由 JSON 解析失败兜底到 chat | 路由是 LLM 做的，必须假设它出错，不能让整轮崩 |
| 面试进行中短路（状态优先） | 用户简短答题会被误判成闲聊、打断面试；显式状态优先于模型判断 |
| Skill 渐进披露 | 路由只吃 name+description（~10 token），命中才加载全文 |
| 一个 Skill 目录放多份 prompt 文件 | 不同阶段的指令会互相冲突（见 Bug 1） |
| 阶段切换用计数器不用 LLM 判断 | LLM 自我判断不可复现；关键路径要有确定性 |
| `check_same_thread=False` | SQLite 默认禁止跨线程复用连接，SSE 可能跨线程访问 |
| 摘要用"合并"而非"重新总结" | 保证 `summary` 是固定大小字符串，成本不随轮次增长 |
| 摘要阈值加迟滞（×2.0） | 避免每轮都调一次摘要 LLM |
| 消息全留着只不再发送 | 保留可回溯性，压缩只作用在发送层 |
| `stream_mode="custom"` | 前端要"过程"，不要内部 state |
| 前端 fetch 而非 EventSource | EventSource 只支持 GET，不能带请求体 |

---

## 六、踩过的 4 个真实 bug

### Bug 1：一份 SKILL.md 承担两个冲突阶段

**现象**：面试题数用尽后，本该输出复盘报告，模型却继续追问。

**根因**：复盘阶段注入了整份 `SKILL.md`，里面"**每轮只输出反馈 + 一个问题**"这条规则压过了"输出完整复盘"的指令。

**解法**：把复盘模板拆成同目录的独立文件 `review-template.md`，两个阶段各用各的 prompt。

**教训**：一个 Skill 目录可以放多份针对不同阶段的 prompt，不是只能有一份 SOP。

### Bug 2：历史模式惯性（更隐蔽）

**现象**：拆完文件后**还是追问**。

**根因**：`messages` 里有连续多条"面试官追问"的历史，模型强烈倾向于延续这个模式——**system 指令压不住历史惯性**。

**解法**：在消息**末尾**追加一条 `HumanMessage("面试到此结束，请直接输出复盘报告，不要再提问")`。最后一条消息的权重最高。

**教训**：当模型"不听话"时，先检查历史里有没有它正在模仿的模式。system 里的话往往压不住 few-shot 式的历史惯性。

### Bug 3：Windows 终端 GBK 编码

**现象**：打印含 `✅` / `😊` 的输出时抛 `UnicodeEncodeError: 'gbk' codec can't encode character`。

**根因**：Windows 终端默认 GBK，模型按 SOP 输出 `✅⚠️❌` 时直接崩。

**解法**：入口脚本里统一切 UTF-8：

```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
```

**教训**：Windows 下凡是会输出 emoji/特殊符号的脚本，都要显式设置标准流编码，否则演示现场必崩。

### Bug 4：`curl` 在 PowerShell 下吃掉 JSON 引号

**现象**：用 `curl.exe -d '{"message":"你好"}'` 测 SSE，服务端返回 `JSON decode error`。

**根因**：PowerShell 把单引号当字符串边界，传给 curl 的 JSON 丢了引号。

**解法**：改用 Python `httpx` 写检脚本（`tests/web_check.py`），可控且可复用。

---

## 七、技术栈与运行

| 层 | 选型 | 版本 |
|---|---|---|
| 语言 | Python | 3.10 |
| Agent 编排 | LangGraph | 1.2.12 |
| 持久化 | langgraph-checkpoint-sqlite | 3.1.1 |
| 模型 | DeepSeek（对话）+ 智谱 embedding-3 | openai SDK 3.16.2 |
| Web | FastAPI + Uvicorn | 0.141.1 / 0.53.0 |
| Token 估算 | tiktoken | 0.14.0 |
| 配置 | pydantic-settings | 2.15.0 |
| 前端 | 原生 HTML/JS（fetch + ReadableStream） | 无框架 |

**刻意不用**：LangChain 链式封装（用原生 LangGraph + openai SDK，讲得清）、消息队列/K8s（单机场景硬凑）。

### 启动

```powershell
cd jobpilot
D:\dev\python\python.exe -m uvicorn app.main:app --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

### 自检脚本

| 脚本 | 验证什么 |
|---|---|
| `tests/smoke.py` | 路由分流（chat / jd_analyst） |
| `tests/context_budget_check.py` | 上下文裁剪 + 摘要迟滞 |
| `tests/interview_check.py` | 面试多轮 → 自动复盘 |
| `tests/web_check.py` | SSE 事件流（token / agent_switch / done） |

---

## 八、进度与下一步

### 已完成（约 20%）

- ✅ M1 Agent 骨架：路由 + 2 个 Agent + Skill 机制 + checkpointer + 上下文预算 + SSE + Web

### 待做

| 里程碑 | 内容 |
|---|---|
| M2 检索硬核化 | 真实文档 ingest、BM25+向量+RRF、rerank、**recall@3 评测数字** |
| M3 可靠性与工具层 | ToolRegistry（超时/重试/审计）、MCP Server、`interrupt()` 人工确认、`runs` 表 trace |
| M3 第 3~5 个 Agent | 简历优化、进度管家、公司调研（DeepResearch mini） |
| M4 记忆双轨 | 长期记忆表（用户画像）+ 去重更新、APScheduler 定时任务 |
| M5 工程化 | JWT 多用户、Docker Compose |
| M6 评测与上线 | 评测集（任务成功率/拒答准确率）、README、真部署 |

### 已知局限（别当成已解决）

1. **写放大未解**：`state["messages"]` 仍在增长，checkpointer 每轮全量重写 SQLite。我们只控制了"发给模型的量"，没控制"存盘的量"（解法：`SqliteSaver.prune()` 或 `RemoveMessage`）。
2. **摘要会累积误差**：摘要是"摘要的摘要"，轮次多了有信息损失。
3. **每轮 2~3 次 LLM 调用**（compress 触发时 3 次）：路由可以用小模型或规则前置来省。
