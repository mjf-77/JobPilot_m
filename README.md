# JobPilot · AI 求职助手（多 Agent）

> 面向秋招场景的多 Agent 系统。LangGraph Supervisor 编排 4 个专职 Agent，
> 完整技术栈：Skill 渐进披露 · 混合检索 · 工具网关 · 人工确认 · 多用户隔离 · Docker 部署。
>
> 代码：https://github.com/mjf-77/JobPilot_m ｜ 部署形态：单机 Docker Compose，浏览器访问

---

## 一、它解决什么问题

秋招期间的信息过载：搜岗位、判断 JD 值不值得投、对照简历改、练面试、记投递进度——全是重复劳动。

JobPilot 把这些做成一个多 Agent 系统。**场景是真实的**（我自己每天在做的事），这是它和"教程项目"最根本的区别——不是先想"做个 Agent 练手"，而是先有一个真实的、每天在重复的痛点。

| Agent | 做什么 | 结构 |
|---|---|---|
| `jd_analyst` | 逐条比对 JD 与候选人，输出匹配度、差距清单、投递建议 | 单节点子图 |
| `resume_coach` | 诊断**已有**简历的问题，输出结构化改写建议 | 单节点 + 结构化输出 |
| `resume_builder` | **从零**收集口述信息，攒成结构化草稿并渲染成可打印的简历 | 单节点 + 多轮累积 |
| `mock_interviewer` | 扮演面试官连续追问，题数用尽后产出结构化复盘 | 两节点 + 条件边 |
| `progress_tracker` | 记录/查询/删除投递进度（**唯一会写库的 Agent**） | ReAct 环 + interrupt |
| `chat` | 寒暄、能力咨询兜底（不是 Agent，是兜底分支） | 单节点 |

> 讲的时候说 **5 个专职 Agent + 1 个兜底**，别把 chat 算成 Agent。

---

## 二、系统架构

### 2.1 四层架构总图

```
┌───────────────────────────────────────────────────────────────────────────┐
│  ① 展示层   web/index.html （单文件，零依赖）                                │
│                                                                            │
│  ┌─ 会话视图 ──────────────┐   ┌─ 投递视图（#apps，hash 路由）─────────┐    │
│  │ 消息流 · 打字机渲染      │   │ 投递表卡片 · 行内编辑 · 弹窗新增      │    │
│  │ 复制 · loading · 折叠日志│   │ 行内发起面试 · toast 反馈            │    │
│  └────────────────────────┘   └──────────────────────────────────────┘    │
│  自写 Markdown 渲染器（内网无法引 CDN）· SSE 帧解析 · token 存 localStorage   │
└───────────────────────────────┬───────────────────────────────────────────┘
                                │  HTTP / SSE
┌───────────────────────────────▼───────────────────────────────────────────┐
│  ② 接入层   app/main.py · app/api/*                                        │
│                                                                            │
│  chat.py      POST /api/chat（SSE）· /api/chat/confirm（interrupt 恢复）     │
│  applications.py  GET/POST/PUT/DELETE /api/applications                    │
│  threads.py   /api/threads（列表 / 历史消息 / 删除）                        │
│  resume.py    POST /api/resume/upload（PDF 解析入 state）                   │
│  auth.py      /api/auth/login · /api/auth/register                         │
│                                                                            │
│  职责：鉴权（JWT）· 参数校验（Pydantic 422）· 会话隔离（thread_id 加前缀）     │
│       SSE 分帧 · worker 线程 + queue（同步图 → 异步响应）                    │
└───────────────────────────────┬───────────────────────────────────────────┘
                                │  graph.invoke(state, config)
┌───────────────────────────────▼───────────────────────────────────────────┐
│  ③ 编排层   app/graph/                                                     │
│                                                                            │
│  supervisor.py  主图：compress → router →（条件边）→ 子图                    │
│  agents/        4 个子图，各自可独立编译与测试                                │
│  state.py       AgentState 状态契约（主图与子图共享）                         │
│  checkpointer.py SqliteSaver（thread_id 索引全量 state）                     │
└───────────────────────────────┬───────────────────────────────────────────┘
                                │
┌───────────────────────────────▼───────────────────────────────────────────┐
│  ④ 能力层                                                                   │
│                                                                            │
│  skills.py     Skill 渐进披露（路由只读 name+description，命中才注入全文）      │
│  context.py    上下文预算：tiktoken 计数 + 渐进摘要 + 迟滞阈值                 │
│  retrieval/    混合检索：智谱 embedding + Chroma + jieba/BM25 + RRF          │
│  tools/        ToolRegistry 网关：超时 / 重试 / 审计 / 权限分级               │
│  llm.py        LLM 工厂（chat / stream / stream_with_tools）                 │
│  events.py     事件通道（自建 sink，contextvars）                             │
│  auth.py       JWT + scrypt 加盐 + contextvar 传当前用户                      │
│  metering.py   token 计量与成本核算                                          │
│  parsing.py    PDF 文本抽取                                                  │
│  scheduler.py  定时任务：每日投递复盘（Agent 主动发起，不是被动响应）           │
└───────────────────────────────┬───────────────────────────────────────────┘
                                │
┌───────────────────────────────▼───────────────────────────────────────────┐
│  ⑤ 持久层   app/db/*（SQLAlchemy 2.0 ORM，一套代码跑 SQLite / MySQL）         │
│                                                                            │
│  users         用户（scrypt 加盐密码）                                       │
│  user_profiles 用户画像（跨会话长期记忆，KV 结构）                             │
│  notifications 站内提醒（定时任务生成，前端只读）                              │
│  applications  投递记录（唯一键 user_id+company+role）                        │
│  threads       会话元数据（标题、活跃时间，供左栏列表）                          │
│  tool_calls    工具调用审计（参数 / 结果 / 耗时 / 成败）                        │
│  runs          请求级 trace（路由 / 调用次数 / token / 成本 / 延迟）            │
│                                                                            │
│  另有 LangGraph checkpoints（SqliteSaver）—— 图状态，与业务表分开存            │
└───────────────────────────────────────────────────────────────────────────┘
```

**为什么分两类存储**：`users` / `applications` 是业务数据（生产放 MySQL）；`checkpoints` 是图内部状态（固定用 SQLite）。混在一起会让"换数据库"变成"动整个系统"。

### 2.2 Agent 编排图

```
                          START
                            │
                    ┌───────▼────────┐
                    │ load_profile   │  长期画像：跨会话记住「你是谁」
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │   compress     │  上下文预算：够长才压缩（迟滞阈值）
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │    router      │  轻量 LLM + JSON 兜底
                    └───────┬────────┘
                            │
        ┌───────────┬───────┼────────┬───────────────┐
        │           │       │        │               │
   ┌────▼────┐ ┌────▼────┐ ┌▼─────┐ ┌▼──────────┐ ┌──▼──────────────┐
   │  chat   │ │jd_analyst│ │resume│ │  mock     │ │ progress_tracker│
   │(兜底直答)│ │(单节点)  │ │coach │ │interviewer│ │  (ReAct 环)     │
   └────┬────┘ └────┬────┘ └┬─────┘ └─────┬─────┘ └──┬──────────────┘
        │           │       │             │            │
        └───────────┴───────┴─────────────┴────────────┘
                            │
                           END

子图内部结构：

  jd_analyst          START → analyze → END                （单节点：检索 + 生成）

  resume_coach        START → diagnose → END               （单节点：结构化输出 + 校验重试）

  mock_interviewer    START → interviewer ─┬─(还有题)→ END
                                           └─(题数用尽)→ review → END
                      阶段切换由 asked 计数驱动，不让 LLM 自己判断"该收尾了"

  progress_tracker    START → agent ─┬─(要调工具)→ tools ─┐
                                     └─(给出答复)→ END     │
                                            ▲             │
                                            └─────────────┘
                      高风险工具在执行前 interrupt() 挂起，等用户在前端确认
```

### 2.3 数据模型

```
users ──1:N──> applications        投递记录（按用户隔离）
      ──1:N──> threads             会话元数据
      ──1:N──> runs                请求 trace
                  tool_calls       工具审计（按 thread 关联）

applications 唯一键：(user_id, company, role)
  → 同一岗位重复记录是「幂等覆盖」，不会产生脏数据
```

### 2.4 部署拓扑

```
┌─────────────────────────── 虚机 192.168.100.128 (CentOS 7) ────────────────┐
│                                                                            │
│  ┌────────────────────────┐         ┌──────────────────────────┐          │
│  │  jobpilot-app-1        │  TCP    │  jobpilot-mysql-1        │          │
│  │  Python 3.11 / Uvicorn │────────>│  MySQL 8.0               │          │
│  │  非 root（UID 1000）    │  3306   │  volume: mysql_data      │          │
│  │  HEALTHCHECK /health   │         └──────────────────────────┘          │
│  │  ./data 挂载（持久化）   │                                                │
│  └───────────┬────────────┘                                                │
│              │ 8000                                                        │
└──────────────┼─────────────────────────────────────────────────────────────┘
               │
        浏览器访问 http://192.168.100.128:8000
```

`./data` 挂载的是 checkpointer 的 SQLite 与 Chroma 向量库——**不挂载的话每次重建容器，会话记忆和知识库索引全部丢失**。

---

## 三、一次请求的完整生命周期

### 路径 A：普通对话（以「帮我模拟面试」为例）

```
① 前端          index.html :: send(text)
   POST /api/chat  {message, thread_id}
   Authorization: Bearer <jwt>
                 ↓
② 接入层        api/chat.py
   解析 JWT → user_id
   scoped_thread = f"{user_id}:{thread_id}"     ← 防越权：别人的 thread 读不到
   threads.touch(user_id, thread_id, 首条消息)   ← 自动建会话、用首句当标题
   开 worker 线程 + queue.Queue，返回 StreamingResponse
                 ↓
③ 图入口        checkpointer
   按 scoped_thread 取出历史 state（messages / summary / interview / resume_text）
                 ↓
④ compress      context.py
   未摘要消息数 > keep_recent × 2.0 ？
   → 是：把最旧一批压进摘要（渐进合并，摘要大小恒定）
   → 否：什么都不做
                 ↓
⑤ router        supervisor.py
   interview.status == "ongoing" → 短路直接进面试官（不调 LLM）
   否则：技能清单 + 判断示例 → LLM 输出 JSON → 解析 route
   emit("agent_switch")   ← 前端显示「→ mock_interviewer」
                 ↓
⑥ 子图          mock_interviewer.py
   _retrieve_questions()：用最近对话去题库混合检索，召回相关题目与考察点
   system = SOP + 进度（第 N 题 / 共 M 题）+ 召回题目
   messages = build_messages(system, state)   ← 统一预算裁剪 + 注入摘要
   chat_streaming() → 每个增量 emit("token")
                 ↓
⑦ 条件边        asked >= max_questions ？→ review 节点出复盘 ： END 等用户作答
                 ↓
⑧ 落盘          checkpointer 把新 state 写回 SQLite
   runs.save() 记录本轮：路由、LLM 次数、token、成本、延迟
                 ↓
⑨ 前端          token 累加 → 打字机；收到 agent_switch → 显示折叠的「执行详情」
```

### 路径 B：工具调用 + 人工确认（以「把腾讯那条删掉」为例）

```
①~⑤ 同上，router 判定 → progress_tracker
                 ↓
⑥ agent 节点    stream_chat_with_tools()
   流式输出 content（照常推 token）+ 按 index 累积 tool_calls 分片
   模型决定调用 delete_application
                 ↓
⑦ tools 节点    查 ToolRegistry：该工具 requires_confirmation = True
   emit("confirm", tool=..., args=...)    ← 通知前端「要确认」
   interrupt({...})                        ← 挂起整张图！
   状态存进 checkpointer，worker 线程结束响应
                 ↓
⑧ 用户在前端点「确认」
   POST /api/chat/confirm  {thread_id, approved: true}
   graph.invoke(Command(resume={"approved": True}), config)
                 ↓
⑨ 从断点恢复    真正执行 registry.execute(...)
   超时 / 重试 / 写审计表 tool_calls
   ToolMessage 回灌模型 → 模型生成最终答复
                 ↓
⑩ 落盘 + 前端渲染
```

**关键点**：`interrupt()` 是**恢复现场继续跑**，不是重新发一次请求。整个图的执行位置、已累积的消息、工具参数都在 checkpointer 里，`Command(resume=...)` 把它们取回来接着执行。这跟"前后端约定一个回调接口做二次确认"是两个层次的东西。

---

## 四、四个 Agent 的设计差异

每个子图的形状都不一样，这是刻意的——按任务特性选结构，而不是套同一个模板。

### 4.1 `jd_analyst`：单节点 + 检索增强

```
START → analyze → END
```

`analyze` 节点：拿 JD 文本 → 检索面试题库里相关考察点 → 连同 SOP 一起构造 prompt → 流式生成分析报告。

**为什么单节点**：一次输入一次输出，没有状态机、没有工具、没有多轮，套复杂结构只是增加理解成本。

### 4.2 `resume_coach`：结构化输出 + 校验重试

```
START → diagnose → END
```

与其它三个最本质的区别：**它要求模型输出 JSON，再用 Pydantic 校验，失败就把错误原文回灌让它重试（≤3 次）**。

```python
class ResumeReview(BaseModel):
    summary: str
    score: int = Field(ge=0, le=100)      # 越界直接判失败
    strengths: list[str]
    rewrites: list[Rewrite]
    priorities: list[str]

for _ in range(_MAX_ATTEMPTS):
    raw = chat(messages, response_format={"type": "json_object"})
    try:
        review = ResumeReview.model_validate(json.loads(raw))
        emit("token", text=_render(review))    # 校验通过才渲染成 Markdown 表格
        return {"messages": [AIMessage(content=rendered)]}
    except (json.JSONDecodeError, ValidationError) as exc:
        last_error = str(exc)                  # 把「具体哪里不对」回灌重试
```

**三个细节**：

1. **错误原文回灌**比笼统说"格式错了"有效得多——模型能看到"score 超出 0-100 范围"这种具体信息。
2. **校验失败不把半成品丢给用户**。宁可重试，也不要渲染一个缺字段的报告。
3. **简历原文走 `state["resume_text"]` 而不是聊天记录**。放 system 里能保证每次诊断都拿到完整原文，也不会被上下文压缩吃掉。

### 4.3 `mock_interviewer`：两节点 + 条件边

```
START → interviewer ─┬─(还有题)→ END
                     └─(题数用尽)→ review → END
```

**为什么拆两个节点**：面试对话是"逐轮交互"，复盘报告是"一次性结构化产出"，两者的 prompt、输出形态、触发条件都不同。混在一个节点里，prompt 会互相干扰——这是踩过坑才明白的（见第九节 Bug 1）。

**为什么阶段切换用计数器而不是让 LLM 判断**：LLM 的自我判断既不可复现也不可靠（可能聊两句就要收尾，也可能追问十轮不肯停）。**把不确定性从关键路径上移走**。

**复盘阶段的坑（Bug 2）**：光在 system 里写"输出复盘、不要提问"没用——历史里连续多条"面试官追问"形成了很强的模式惯性。解法是在消息**末尾**追加一条 `HumanMessage("面试到此结束，请直接输出复盘报告")`。**最后一条消息的权重最高**。

### 4.4 `progress_tracker`：ReAct 环 + 人工确认

```
START → agent ─┬─(模型要求调工具)→ tools ─┐
               └─(给出最终答复)→ END      │
                        ▲                │
                        └────────────────┘
```

这是**唯一会写数据库的 Agent**，也是 human-in-the-loop 的落点。

**难点：流式 + 工具调用要同时支持**。用非流式的话，模型不会推 token 事件，前端最终答复是空白（实测踩过）。解法是"流式 + 累积 tool_calls 分片"——content 分片照常推给前端，tool_calls 按 `index` 累积到完整后再执行。

**权限分级**：工具在注册时标 `requires_confirmation`，只有删除类工具标了。执行前 `interrupt()` 挂起等用户确认。

**工具 schema 里没有 `user_id` 参数**（见 `tools/builtin.py`）：

```python
def _save_application(company: str, role: str, status: str, note: str = "") -> str:
    return applications.save(company, role, status, note, user_id=auth.current_user_id())
```

当前用户是从 **contextvar** 注入的，不是让模型传。**用户身份是系统事实，不该由模型来决定**——否则就是一个"模型可以随便指定改谁的记录"的越权漏洞。

---

## 五、核心机制

### 5.1 Skill 渐进披露

```
skills/
├── jd-analysis/SKILL.md
├── resume-coach/SKILL.md
├── progress-tracker/SKILL.md
└── mock-interview/
    ├── SKILL.md                  # 面试推进 SOP
    └── review-template.md        # 复盘模板（独立文件，见 Bug 1）
```

| 阶段 | 加载什么 | 成本 |
|---|---|---|
| 路由 | 只有 `name + description` | 每技能约 10 token |
| 命中后 | `SKILL.md` 全文注入该子 Agent | 按需 |
| 特定阶段 | `review-template.md` 等附加文件 | 按需 |

对比"把所有 SOP 全文塞进 system prompt"：那样 4 个 Skill 就是几千 token 常驻，还会互相干扰指令。

**一个 Skill = 一个目录**，SKILL.md 是主 SOP，其余文件是按阶段取的附加资源。

**行为与代码解耦**：改 SOP 只改 Markdown，不碰代码。前端展示的措辞、篇幅约束、输出格式要求全在 SKILL.md 里。

### 5.2 两层记忆：上下文预算 + 长期画像

**第一层：上下文预算（会话内）**

**问题**：路由节点只截最近 N 条，执行节点却发全量历史——两套裁剪策略并存。后果是多轮对话累计 token 成本近似 n²。

**解法**：把所有"发给模型的消息"收敛到一个出口 `build_messages()`，并加一个图入口节点做渐进摘要。

```python
def build_messages(system, state, budget=None) -> list[BaseMessage]:
    """system（必留）→ 历史摘要 → 预算内最近的原文历史"""
    remaining = budget - count_tokens([system])
    prefix = [SystemMessage(content=f"<history_summary>...</history_summary>")] if state.get("summary") else []
    remaining -= count_tokens(prefix)
    pending = state["messages"][state.get("summarized_count", 0):]   # 已摘要的不再发原文
    return [system, *prefix, *_fit(pending, max(remaining, 0))]      # _fit：从最近往前装
```

**两种压缩手段**：

1. **摘要（优先）**：渐进合并——prompt 是「已有摘要 + 新对话 → 合并去重」，所以 `summary` 永远是固定大小的字符串，**不随轮次增长**。
2. **硬裁剪（保底）**：`_fit()` 从最近往前装，装不下的丢最旧的。

**迟滞（hysteresis）**：触发阈值是 `keep_recent × 2.0`，且触发时一次摘到只剩 `keep_recent` 条。若一超阈值就摘，等于每轮多一次 LLM 调用；攒够了再摘，之后若干轮都不触发。

> 和缓存淘汰、GC 分代阈值、TCP 拥塞控制是同一个思想：给阈值加缓冲带，避免在临界点附近高频抖动。

**为什么用 `summarized_count` 而不是删消息**：删消息不可逆，摘要质量差就永久丢信息。现在是"**消息全留着，只是不再发送**"——压缩只作用在发送层，保留可回溯性。

**第二层：长期画像（跨会话）**

上下文预算是「一次会话内」的记忆，换个会话就没了。但"我的目标岗位是 AI 应用开发"这种信息，下次开新会话还应该记得。

实现是一张 KV 表 `user_profiles` + 图入口节点：

```
START → load_profile → compress → router → …
        （读长期画像）  （压短期历史）
```

```python
def load_profile(state) -> dict:
    """每个请求现读一次画像——单表查询很便宜，且用户改了下一个请求就生效，
    不用担心 checkpointer 里存着旧值（画像属于「用户」，不属于「这个会话」）。"""
    user_id = auth.current_user_id()
    return {"user_profile": profiles.get_all(user_id)} if user_id else {}
```

注入顺序：`system → <user_profile> → <history_summary> → 最近历史`。画像排在摘要**前面**，因为它是"这个人是谁"，比"这次聊了什么"更基础，而且不随会话增长（成本恒定，就几条 KV）。

**为什么用 KV 表而不是 users 表加一列 JSON**：画像是"一条一条加上去的"，KV 能按字段增量更新，也不必读改写整个 JSON（JSON 列会有并发覆盖问题）。

**谁来写画像**：`remember_profile(field, value)` 工具（挂给 progress_tracker），配合 SOP 引导——"聊投递时用户透露了长期有效的信息，顺手记下来"。**没做"每轮自动抽取"**：那要多一次 LLM 调用，而且抽错了用户根本不知道。`value` 传空字符串即删除该字段，所以不用单独设计删除工具。

**验收**（`tests/profile_check.py`，7/7）：重点在最后一环——**新开一个 thread** 问"我的目标岗位是什么"。前面几环（写入、读取、注入 prompt）都可能看着对但实际断掉，只有换了会话还答得出来才算数。

### 5.3 混合检索

```
query ─┬─> 智谱 embedding-3（2048 维）→ Chroma 向量检索 ─┐
       │                                                ├─> RRF 融合（k=60）→ top_k
       └─> jieba 分词 → BM25 关键词检索 ─────────────────┘
```

**为什么必须混合**：向量检索擅长语义相近（"并发问题" ≈ "线程安全"），但对手册里的**专有名词、精确术语**容易漏；BM25 正好相反。RRF 不用调权，只按排名融合，工程上最省心。

**RRF 的核心**：`score(d) = Σ 1/(k + rank_i(d))`，只看排名不看分数——所以两路检索的分数量纲不同也没关系。

**实测**（`evals/run_eval.py`，20 条评测 query）：

| 方案 | recall@3 |
|---|---|
| 纯向量 | 18/20 |
| 纯 BM25 | 18/20 |
| **RRF 融合** | **20/20** |

**降级设计**：检索是"让出题更靠谱"的增强，不是关键路径。索引缺失或 API 异常时返回占位文案，面试照常进行。

### 5.4 ToolRegistry 网关

所有工具调用经过一个统一网关，四件事在一个地方做掉：

```python
class ToolSpec:
    name: str
    description: str
    parameters: dict
    func: Callable
    requires_confirmation: bool = False    # 权限标记
    timeout: float = 30.0
    max_retries: int = 1
```

| 机制 | 解决什么 |
|---|---|
| 超时 | 工具卡住不能拖死整轮对话 |
| 重试 | 网络类偶发失败不必让用户重说 |
| 审计 | 每次调用写 `tool_calls` 表（参数/结果/耗时/成败），排障有据 |
| 权限标记 | 高风险工具走 interrupt 人工确认 |

**最容易踩的坑**：工具跑在 `ThreadPoolExecutor` 里（为了做超时），而当前用户存在 contextvar 里——**contextvar 不跨线程**，导致 `user_id` 写进去是空串。解法：

```python
ctx = contextvars.copy_context()
future = _EXECUTOR.submit(ctx.run, spec.func, **args)
```

`copy_context()` 把当前线程的 context 复制一份带过去。这个坑在三个地方都出现过（见第九节 Bug 6）。

### 5.5 事件通道（为什么自建 sink）

LangGraph 提供了 `get_stream_writer()` 往 custom stream 写事件，直观写法是：

```python
def _interview(state):
    get_stream_writer()({"type": "token", "text": chunk})   # 看起来没问题
```

**实际现象**：除闲聊外的所有 Agent 在浏览器里**回复空白**。

**根因**：`get_stream_writer()` 在**子图节点**里写入的事件到不了主图的 custom stream——子图有自己的写入上下文，主图 `stream_mode="custom"` 收不到。

**解法**：不用框架的 writer，自己用 contextvars 存一个回调：

```python
# app/events.py
_sink: contextvars.ContextVar = contextvars.ContextVar("event_sink", default=None)

def set_sink(fn) -> None:  _sink.set(fn)
def emit(event_type: str, **payload) -> None:
    sink = _sink.get()
    if sink is None:
        return                    # 不在流式上下文（脚本里直接 invoke）→ 静默忽略
    sink({"type": event_type, **payload})
```

`api/chat.py` 的 worker 线程里 `set_sink(lambda e: events_queue.put(("event", e)))`。

**好处**：节点代码不用写任何"我是否在流式环境"的判断；在脚本里 `invoke` 时静默忽略；子图嵌套多深都能冒泡到出口。

### 5.6 多用户隔离

两层隔离，缺一不可：

| 层 | 手段 |
|---|---|
| 数据层 | `users` 表 + `applications.user_id` 外键列；所有查询强制带 `user_id` 条件 |
| 会话层 | `thread_id` 加用户前缀：`f"{user_id}:{thread_id}"` |

**为什么 thread_id 要加前缀**：checkpointer 只认 `thread_id` 字符串。如果直接用前端传来的 thread_id，用户 A 构造一个别人用过的 id 就能读到对方的历史。加前缀后，命名空间天然隔离。

**密码存储**：`hashlib.scrypt` 加盐 + `hmac.compare_digest` 恒定时间比较（不用 `==`，避免时序侧信道）。

**改/删接口的越权防护**：`PUT/DELETE /api/applications/{id}` 的查询条件必须同时带 `id` 和 `user_id`——只凭 id 的话，id 是自增的，随便试几个数就能改别人的记录。别人的记录返回 **404 而不是 403**（不透露"这条存在但不属于你"）。

### 5.7 SSE 与前端渲染

**为什么不用 `EventSource`**：它只支持 GET——参数只能塞 URL、不能带请求体，也没法带 `Authorization` 头。改用 `fetch` + `ReadableStream` 手动解析 SSE 帧。

```javascript
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = '';
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  const frames = buf.split('\n\n');    // SSE 用空行分帧
  buf = frames.pop();                  // 最后一段可能不完整，留到下一轮
  for (const frame of frames) {
    if (!frame.startsWith('data: ')) continue;
    const ev = JSON.parse(frame.slice(6));
    if (ev.type === 'token') raw += ev.text;          // 累加 → 重新渲染 Markdown
  }
}
```

**为什么自写 Markdown 渲染器**（不用 marked.js）：部署环境在内网，引 CDN 直接加载失败。自己写的只覆盖 Agent 实际输出的语法（标题/列表/表格/加粗/行内代码），约 40 行。

**安全细节**：先 `escapeHtml` 再替换标记。反过来的话，模型输出里的尖括号会变成注入点。

### 5.8 MCP Server

把投递管理能力用 MCP 协议暴露出去，任何 MCP 客户端（Claude Desktop / Cursor / 自研）都能直接调用。

```
MCP 客户端（Claude Desktop 等）
        │  stdio（JSON-RPC：tools/list · tools/call）
┌───────▼────────────────────────────┐   3 个工具：
│  mcp_server/server.py              │   list_applications
│  无状态薄代理                        │   save_application
│  身份来自环境变量 JOBPILOT_TOKEN      │   delete_application
└───────┬────────────────────────────┘
        │  HTTP（带 Authorization 头）
┌───────▼────────────────────────────┐
│  JobPilot API（已有的那套）          │   鉴权 + 多用户隔离在这里
└────────┬───────────────────────────┘
         │
      MySQL
```

**三个设计决定**：

1. **薄代理，不重复实现数据访问**。Server 把请求转发给已有 HTTP API，而不是自己连库。多用户隔离（JWT + 每条查询带 `user_id`）已经在 API 层做完了，在 MCP 层再抄一遍只会多出一处可能写错的地方。
2. **身份从环境变量读，不从工具参数读**。工具参数是模型填的——如果 token / user_id 走参数，就等于把「改谁的数据」交给了模型。这和「工具 schema 里不出现 user_id」是同一条原则。所以 Server 是无状态的：谁启动它、带谁的 token，它就只代表谁。
3. **删除的确认位置变了，这是协议决定的**。Web 里删除走图内 `interrupt`（删除意图是**模型推断**的，可能理解错）；MCP 协议要求客户端在执行工具前获得用户同意（Claude Desktop 会弹窗让你点批准），确认职责从服务端移到了客户端 UI 层。所以这里的 delete 不做二次确认。

**为什么只暴露 3 个工具**：`search_questions` 是 Agent 内部推理用的题库检索，对外部客户端没有意义。

**跑法**：

```bash
# 1. 拿 token（登录接口返回的字段）
curl -X POST http://127.0.0.1:8000/api/auth/login -H "Content-Type: application/json" \
     -d '{"username":"你的用户名","password":"你的密码"}'

# 2. 起 Server（stdio，正常由 MCP 客户端作为子进程拉起）
set JOBPILOT_TOKEN=<上一步拿到的 token>
python -m mcp_server.server

# 3. 自检：工具发现 → 只读调用 → 写-查-删闭环 → 参数校验
python tests/mcp_check.py
```

`tests/mcp_check.py` 本身也是一份「MCP 客户端怎么写」的最小示例——客户端事先**不知道**有哪些工具，连上之后才拿到清单，这就是「工具注册从硬编码变成运行时发现」。自检实测 7/7。

### 5.9 定时任务（Agent 主动干活）

前面所有能力都是**被动**的——用户问了才响应。定时任务让它主动发起：

```
每天 09:00
    │
    ▼
遍历「有投递记录的用户」
    │
    ▼
投递记录 + 长期画像 ──> 模型生成一条复盘提醒
    │                        │
    │                 「无需提醒」→ 直接跳过，不写库
    ▼                        │
写入 notifications 表 ◀───────┘
    │
    ▼
前端 chat-head 的 🔔 显示未读红点
```

**三个设计决定**：

1. **用 APScheduler，不用 cron / Celery**。任务里要直接用应用内的资源（数据库会话、LLM 客户端）。用 cron 得起第二个进程再连一遍库；Celery 还要额外一套 broker。**代价是多实例部署时每个实例都会跑一遍**——到那时得换成「分布式锁 + 单实例执行」或外部调度器。
2. **让模型自己判断「今天值不值得提醒」**。记录都是新投的、没有停滞时，模型回「无需提醒」，就不写库。**每日推送最容易死于「天天说废话」**。
3. **任务里显式设置当前用户**。后台线程没有请求上下文、contextvar 是空的。现在这个任务不调工具所以用不上，但哪天让它调工具，漏了这步就会写出 user_id 为空的脏数据（和 Bug 6 同一个坑）。

**实测**（`tests/scheduler_check.py`，5/5）。喂它一批「投了几周没动静」的记录，产出的是：

> 有两条投递已停滞超两周：腾讯后端停在"已投递"18 天，字节 AI 应用停在"已投递"21 天，均无任何进展。建议先确认这两家的投递是否已进入筛选流程（如官网状态、是否有笔试通知），若确无回应，可考虑通过内推或重新投递其他组来激活。美团笔试已过 12 天仍未更新结果，可留意是否该跟进。

喂它「刚投的、状态都很新」的记录 → 写入 **0 条**（模型判断无需打扰）。

**它不是拼字符串，是真在算停滞天数、分层给建议**——这才叫 Agent 主动干活。

顺带一个部署上的坑：CentOS 7 的 `sed`（GNU 4.2.2）会把 `sed -i "s/\r$//"` 里的 `\r` 当字面量 `r`，**删掉每行结尾的 `r`**（把 `from app import scheduler` 改成 `schedule`）。而 Write 工具产出的脚本本来就是 LF（不含 CR），**那一句根本不需要**。现在传脚本一律不再做这层转换。

### 5.10 简历制作（从零生成）

其他 Agent 的产物都是一段话，**这个的产物是一个文件**。

```
多轮口述 ──> 每轮抽取字段（patch）──> 代码合并进 state["resume_draft"]
                                          │
                                必填项齐了？── 否 ──> 继续问下一类
                                          │ 是
                                          ▼
                                Jinja2 渲染 templates/resume.html
                                          │
                                          ▼
                                浏览器预览 ──> Ctrl+P 存成 PDF
```

**四个设计决定**：

1. **模型每轮只输出「本轮抽到的字段」（patch），不是完整草稿**。让它每轮重写整份草稿，它迟早会漏掉前面说过的内容。合并交给代码：列表字段按各自的主键（学校／项目名／技能名）合并，同一条做更新、新的才追加。
2. **「收齐了没有」由代码判断**，不让模型说了算——和 mock_interviewer 的题数控制一个思路，关键路径上的判断要可复现。
3. **收集期间路由短路**。用户第二轮说的"教育经历：广西大学…"本身没有意图，交给 LLM 判会被判成闲聊、把收集流程打断。所以只要 `resume_draft` 存在且没收满，就直接进 resume_builder——和"面试进行中短路"是同一个手法。
4. **渲染走 HTML，不在服务端直出 PDF**。服务端生成 PDF 得往镜像里塞中文字体，缺字体就是一片方块；交给浏览器渲染则用系统字体，这个坑直接消失，而且用户能先预览再打印。

**模板对齐的版式**：深蓝章节标题 + 通栏下划线、姓名居中、三栏对齐行（左项目名／中角色／右时间）、`•` 条目、A4 单页。

**踩到的坑**：三栏一开始写成 `flex + space-between` 配 `flex: 1` 的左列，左列把剩余空间吃光，中列被挤到右边（实测偏 300+px）。改成 `grid-template-columns: 1fr auto 1fr` + `justify-self` 后中列精确居中（偏差 0.0px）。

**验收**（`tests/builder_check.py`，19/19）：重点是**多轮累积**——第二轮补教育经历时不能把第一轮说的姓名冲掉。

---

## 六、前端

### 6.1 页面结构

```
┌ 左栏 240px ┬────────── 主区域（≤1160px 居中）─────────────┐
│ JobPilot    │  ┌─ chat-head ─────────────────────────┐   │
│ + 新会话     │  │ [← 返回会话]  标题                    │   │
│ 快捷功能     │  ├─────────────────────────────────────┤   │
│  · 分析 JD   │  │  会话视图 #viewChat                  │   │
│  · 优化简历  │  │    消息流（AI 白卡片 / 用户蓝气泡）     │   │
│  · 模拟面试  │  │    ▸ 执行详情（默认折叠）              │   │
│  · 投递进度  │  │                                     │   │
│ 历史会话     │  │  ── 或（hash = #apps）──             │   │
│  ▌会话一     │  │  投递视图 #viewApps                   │   │
│   会话二     │  │    ┌ 投递记录卡片 ──────────────┐     │   │
│ 头像 用户名  │  │    │ 共 4 条｜已投递 2 · ...  [+新增]│    │   │
│ [切换账号]   │  │    │ 表格（行内编辑/删除/面试）    │     │   │
│             │  │    └──────────────────────────┘     │   │
│             │  └─────────────────────────────────────┘   │
│             │  [上传简历] [输入消息…] [发送]               │
│             │      Enter 发送 / Shift + Enter 换行        │
└─────────────┴────────────────────────────────────────────┘
```

### 6.2 两个视图的关系

投递记录**不是对话内容**，所以从消息流里剥出来做成独立页面：

- 点左栏「投递进度」→ `location.hash = 'apps'` → 切到投递视图
- **用 hash 而不是普通 JS 变量**——这样浏览器的后退键也能返回会话
- 投递视图里底部输入框整体隐藏，避免"两套输入框打架"
- 点某行的 🎤 → 自动切回会话视图（面试过程是对话内容，否则用户看不到任何反馈）

### 6.3 几个交互细节的设计理由

| 细节 | 为什么这么做 |
|---|---|
| 表格用 DOM 渲染而非 Markdown | 表格里要放 `<select>` 和按钮，Markdown 字符串塞不进可交互控件 |
| 改状态无需点保存，改公司名要点保存 | 改状态是最高频操作（值得省一步）；改名是低频且有冲突风险 |
| 表格点 × 只需一次 confirm | 用户自己点的，意图明确 |
| 对话说"删掉腾讯"要走 interrupt + 恢复 | 删除意图是**模型推断**的，可能理解错 |
| 投递页操作反馈走 toast | 投递页里聊天区是隐藏的，往消息流插提示等于没提示 |
| 操作后就地重绘表格而非追加新气泡 | 否则点几次消息区就被表格刷屏 |
| 复制按钮的 `execCommand` 回退 | `http://` 页面不是安全上下文，`navigator.clipboard` 不可用 |

**同一个删除操作，两条路径的确认强度不同**——因为风险等级不只看操作本身，还要看**意图是谁产生的**。这比"所有删除都加重确认"合理：否则点个按钮还要走一遍图挂起恢复，纯属自找麻烦。

---

## 七、技术栈

| 层 | 选型 | 版本 |
|---|---|---|
| 语言 | Python | 3.11（镜像）/ 3.10（本地） |
| Agent 编排 | LangGraph | 1.2.12 |
| 图状态持久化 | langgraph-checkpoint-sqlite | 3.1.1 |
| 对话模型 | DeepSeek | openai SDK 3.16.2 |
| Embedding | 智谱 embedding-3（2048 维） | — |
| 向量库 | Chroma | — |
| 关键词检索 | jieba + rank-bm25 | — |
| Web 框架 | FastAPI + Uvicorn | — |
| ORM | SQLAlchemy 2.0（`Mapped` + `mapped_column`） | — |
| 业务库 | SQLite（本地）/ MySQL 8.0（生产） | — |
| PDF 解析 | pypdf | — |
| 鉴权 | PyJWT + hashlib.scrypt | — |
| Token 估算 | tiktoken | 0.14.0 |
| 配置 | pydantic-settings | — |
| 前端 | 原生 HTML/JS（单文件，零依赖） | — |
| 部署 | Docker 多阶段构建 + Compose | Docker 26.1.4 / Compose v2.27.1 |

**刻意不用**：LangChain 链式封装（用原生 LangGraph + openai SDK，讲得清）、消息队列 / K8s（单机场景硬凑）。

---

## 八、关键设计决策速查（面试用）

| 决策 | 为什么 |
|---|---|
| Supervisor 模式而非全连接 | 路由可控、故障隔离、每个 Agent 可独立测试 |
| 子图直接作为节点 | 主图不关心子图内部结构；往子图加 ReAct 环，主图一行不用改 |
| 按任务特性选子图形状 | 单节点 / 双节点 / ReAct 环各不相同，不套模板 |
| 路由 JSON 解析失败兜底到 chat | 路由是 LLM 做的，必须假设它出错，不能让整轮崩 |
| 面试进行中短路（状态优先于模型） | 用户简短答题会被误判成闲聊、打断面试；有显式状态时用状态 |
| 阶段切换用计数器不用 LLM 判断 | LLM 自我判断不可复现；关键路径要有确定性 |
| Skill 渐进披露 | 路由只吃 name+description（约 10 token），命中才注入全文 |
| 一个 Skill 目录放多份 prompt | 不同阶段的指令会互相冲突（Bug 1） |
| 用 `copy_context()` 传 contextvar | 工具跑在线程池里，contextvar 不跨线程（Bug 6） |
| 自建事件 sink 不用 `get_stream_writer()` | 子图节点写的事件到不了主图 custom stream（Bug 5） |
| 结构化输出 + 错误原文回灌重试 | 笼统说"格式错了"模型改不对；具体错误才能定位 |
| 简历原文走 state 不走聊天记录 | 保证每次诊断拿到完整原文，且不被上下文压缩吃掉 |
| 摘要用「合并」而非「重新总结」 | 保证 summary 是固定大小字符串，成本不随轮次增长 |
| 摘要阈值加迟滞（×2.0） | 避免每轮都调一次摘要 LLM |
| 消息全留着只不再发送 | 保留可回溯性，压缩只作用在发送层 |
| 向量 + BM25 + RRF | 向量擅长语义、BM25 擅长专名，RRF 只按排名融合免调权 |
| 检索可降级 | 它是增强不是关键路径，失败不能影响主流程 |
| 工具 schema 不出现 user_id | 用户身份是系统事实，不能让模型传（越权漏洞） |
| 工具查询条件强制带 user_id | id 自增，只凭 id 可越权改他人数据 |
| thread_id 加用户前缀 | checkpointer 只认字符串，不加前缀可跨用户读历史 |
| 越权返回 404 而非 403 | 不透露"这条存在但不属于你" |
| `check_same_thread=False` | SQLite 默认禁跨线程复用连接，SSE 可能跨线程访问 |
| 接入层用 worker 线程 + queue | 图是同步的、响应要异步流式，用队列把两者解耦 |
| 前端 fetch 而非 EventSource | EventSource 只支持 GET，不能带请求体，也带不了 Authorization 头 |
| 自写 Markdown 渲染器 | 内网无法引 CDN；先转义再替换标记防注入 |
| 投递表用 DOM 而非 Markdown | 要放 select 和按钮，Markdown 塞不进交互控件 |
| 视图切换用 hash 路由 | 浏览器后退键天然可用，无需自己维护历史栈 |
| 状态改完不用点保存，改名字要点 | 高频操作省一步；低频操作有过期风险，值得显式确认 |

---

## 九、踩过的真实 bug

这些都是**调试出来的**，不是推理出来的。每条的现象 → 根因 → 教训。

### Bug 1：一份 SKILL.md 承担两个冲突阶段

**现象**：面试题数用尽后，本该输出复盘报告，模型却继续追问。

**根因**：复盘阶段注入了整份 `SKILL.md`，里面"每轮只输出反馈 + 一个问题"这条规则压过了"输出完整复盘"的指令。

**解法**：把复盘模板拆成同目录的独立文件 `review-template.md`，两个阶段各用各的 prompt。

**教训**：一个 Skill 目录可以放多份针对不同阶段的 prompt，不是只能有一份 SOP。

### Bug 2：历史模式惯性（比 Bug 1 更隐蔽）

**现象**：拆完文件后**还是追问**。

**根因**：`messages` 里有连续多条"面试官追问"的历史，模型强烈倾向于延续这个模式——**system 指令压不住历史惯性**。

**解法**：在消息**末尾**追加一条 `HumanMessage("面试到此结束，请直接输出复盘报告，不要再提问")`。最后一条消息的权重最高。

**教训**：模型"不听话"时，先检查历史里有没有它正在模仿的模式。

### Bug 3：Windows 终端 GBK 编码

**现象**：打印含 `✅` / `😊` 的输出时抛 `UnicodeEncodeError: 'gbk' codec can't encode character`。

**根因**：Windows 终端默认 GBK，模型按 SOP 输出 emoji 时直接崩。

**解法**：入口脚本统一切 UTF-8 —— `sys.stdout.reconfigure(encoding="utf-8")`。

**教训**：Windows 下凡是会输出 emoji 的脚本都要显式设标准流编码，否则演示现场必崩。

### Bug 4：`curl` 在 PowerShell 下吃掉 JSON 引号

**现象**：`curl.exe -d '{"message":"你好"}'` 测 SSE，服务端返回 JSON decode error。

**根因**：PowerShell 把单引号当字符串边界，传给 curl 的 JSON 丢了引号。

**解法**：改用 Python `httpx` 写自检脚本（`tests/web_check.py`），可控可复用。

### Bug 5：子图里 `get_stream_writer()` 失效（最严重的一个）

**现象**：除闲聊外**所有 Agent 在浏览器里回复空白**。前端不报错，后端也不报错。

**排查过程**：一开始怀疑是前端 SSE 解析问题、后来又怀疑是模型没输出。最后用最小复现定位到：`get_stream_writer()` 在**子图节点**里写入的事件到不了主图的 custom stream。

**根因**：子图有自己的写入上下文，主图 `stream_mode="custom"` 收不到子图节点写的事件。这个行为在文档里没有明确说明。

**解法**：弃用框架的 writer，自己用 contextvars 存一个回调（见 5.5）。

**教训**：现象是"前端空白"时，不要只盯着前端。**先确认后端到底有没有产出事件**——我是靠打印事件流才定位到的。

### Bug 6：contextvar 跨线程丢失（同类问题踩了三次）

**现象**：`applications.user_id` 写入的是空串，导致投递记录查不出来。

**根因**：工具为了做超时跑在 `ThreadPoolExecutor` 里，而当前用户存在 contextvar 里——**contextvar 不跨线程**，新线程读到的是默认值。

**解法**：提交任务时带上 context —— `ctx = contextvars.copy_context(); _EXECUTOR.submit(ctx.run, func, **args)`。

**同一根因的三个落点**：

| 落点 | 表现 |
|---|---|
| FastAPI 逐块迭代同步生成器 | 生成器在别的线程跑，sink 丢了 |
| LangGraph 节点线程 | 同上 |
| ToolRegistry 线程池 | user_id 空串 |

**教训**：**凡是"跨线程/跨上下文"的地方，contextvar 都要显式传递**。看到"值莫名其妙变默认值"，先想是不是换了线程。

### Bug 7：部署时 volume 覆盖导致 SQLite 打不开

**现象**：容器启动报 `sqlite3.OperationalError: unable to open database file`。

**根因**：compose 的 `./data` bind mount 覆盖了镜像内已 `chown` 的目录。宿主上的 `data/` 属主是 root，而容器内进程是 UID 1000，没有写权限。

**解法**：`mkdir -p data && chown -R 1000:1000 data`。

**教训**：bind mount 会让镜像内的权限设置**全部失效**。非 root 容器 + bind mount，宿主目录属主必须匹配。

### Bug 8：tiktoken 运行时联网下载编码文件失败

**现象**：本地跑得好好的，重建容器后启动即失败。

**根因**：tiktoken 首次使用需要下载编码文件，而虚机访问不了 `openaipublic.blob.core.windows.net`。**之前能跑只是因为碰巧有缓存，重建就丢**。

**解法**：把编码文件复制到 `jobpilot/.tiktoken/`，Dockerfile 里设 `TIKTOKEN_CACHE_DIR` 并 `COPY` 进镜像。

**教训**：**"本地能跑"不等于"重建能跑"**——凡是运行时下载的资源，都要预置进镜像。

### Bug 9：`docker cp` 热更导致容器与源码不一致

**现象**：容器里跑的是最新代码，但宿主机 `web/index.html` 和容器内的 md5 不一致，`app/api/` 下还缺一个后来新增的文件。

**根因**：为了快速迭代，改完直接 `docker cp` 进容器，**没有同步回宿主机源码目录**。镜像构建时 `COPY` 的是宿主机源码，所以重建容器会退回旧代码。

**解法**：同步源码 → `docker compose up -d --build` 重建，并核对容器内与本地文件 md5 一致。

**教训**：**热更新是临时的，源码才是唯一真相**。快迭代可以 `docker cp`，但必须在某个节点回写源码，否则迟早会丢。

### Bug 10：工具日志折叠框永远是空的

**现象**：「▸ 执行详情」默认折叠是对的，但展开后**没有内容**。

**根因**：

```javascript
if (traceEl) traceEl.set(label);
else traceEl = addTrace();     // ← 首个事件只建了空盒子，从没写入
```

一轮对话通常只发**一个** `agent_switch` 事件，所以那个框永远空着。

**解法**：先创建再写入 —— `if (!traceEl) traceEl = addTrace(); traceEl.set(label);`。

**教训**：`if (x) use(x); else create()` 这种写法，**要检查"创建之后是否还需要使用"**。这类 bug 单元测试很容易漏（DOM 存在、无报错），只有真点开看才发现。

### Bug 11：`min_length` 挡不住全空格

**现象**：接口传 `{"company": "   "}`，`Field(min_length=1)` 校验通过，`strip()` 之后变成空字符串写进库——出现一条没有公司名的记录。

**根因**：Pydantic 的 `min_length` 校验的是**原始字符串长度**，全空格长度是 3，合法。

**解法**：去空格后再判空。

```python
company, role = payload.company.strip(), payload.role.strip()
if not company or not role:
    raise HTTPException(status_code=422, detail="公司和岗位不能为空")
```

**教训**：**校验要作用在"清洗后的值"上**。只要代码里有 `strip()`，就必须考虑"strip 之后为空"的情况。

---

## 十、效果数据与进度

### 效果数据（可复现）

两个层次都有评测脚本，跑一遍就能复现，不是"感觉还行"。

**检索层**（`evals/run_eval.py`，20 条 query）

| 方案 | recall@3 |
|---|---|
| 纯向量 | 18/20 |
| 纯 BM25 | 18/20 |
| **RRF 融合** | **20/20** |

**Agent 层**（`evals/run_agent_eval.py`，32 条用例）

| 维度 | 结果 | 测什么 |
|---|---|---|
| 路由准确率 | **20/20 = 100%** | 5 个分支的意图识别，含省略说法（"把腾讯那条删掉"→progress_tracker）与边界（"你好"→chat） |
| 工具调用 / 任务完成 | **8/8 = 100%** | 真写库后校验副作用：新增 / 改状态（幂等不新增）/ 按状态筛 / 删除+确认 / 删除+取消 / 一次说两条 / 信息不全不乱写 |
| 拒答 / 边界 | **4/4 = 100%** | 没简历不瞎评、不越权查他人数据、不声称能代投、不预测 offer |

跑法：

```bash
python evals/run_agent_eval.py            # 全部 32 条，约 40 秒
python evals/run_agent_eval.py routing    # 只跑路由，约 13 秒
```

**评测本身也踩了坑，而且它暴露了两个真问题：**

1. **thread_id 必须每次运行唯一**。checkpointer 是跨运行持久化的，一开始我把 thread 固定成 `eval-t05`，第二次跑就带着第一次的记忆——模型回答"这条我刚才已经删过了"，**结果全是假的**。现在用运行时间戳做命名空间。
2. **评测当回归测试用**，这是它最有价值的地方：
   - t08 发现真 bug：用户只说"投了阿里巴巴"（没给岗位），模型直接写了一条空岗位记录。唯一键是 `(公司, 岗位)`，这条占位记录会让用户之后补全时变成两条脏数据。修法是工具层直接拒绝空值（不指望模型每次都听话）。
   - 改完 t08 后 **t05 立刻挂了**：模型把"记录必须给岗位"泛化到了删除，导致"把美团那条删了"（该司只有一条）变成反问岗位。改 SOP 让它先查一眼、只有一条就直接删。
   - 这一来一回说明：**提示词的改动是有副作用的，没有回归测试就只能靠运气**。

### 已完成

| 里程碑 | 内容 |
|---|---|
| **M1** Agent 骨架 | 路由 + Skill 机制 + checkpointer + 上下文预算 + SSE + Web |
| **M2** 检索硬核化 | 题库 ingest、向量 + BM25 + RRF、recall@3 评测（20/20） |
| **M3** 可靠性与工具层 | ToolRegistry（超时/重试/审计/权限）、interrupt 人工确认、runs trace、4 个 Agent 全部上线、**MCP Server** |
| **M4** 记忆 | 两层记忆：会话内渐进摘要（tiktoken 预算 + 迟滞）+ 跨会话长期画像；**定时任务**每日主动复盘 |
| **M5** 工程化 | JWT 多用户、SQLAlchemy ORM 迁移、Docker 部署上线 |
| **M6** | 前端完整重构（会话/投递双视图）、Agent 层评测集（32 条）、自检脚本 14 个 |

### 待做

| 项 | 内容 |
|---|---|
| 验证 | 用真实简历 PDF 端到端跑一遍 |
| 评测 | 把用例扩到 100+ 条；拒答判定目前靠关键词，考虑换 LLM 评判 |
| 画像 | 工具只挂给 progress_tracker，`chat` 分支还没有工具环，说"记住 XXX"时可能覆盖不到 |
| 多实例 | 定时任务目前每个实例都会跑，上多实例时需加分布式锁 |

### 已知局限（别当成已解决）

1. **写放大未解**：`state["messages"]` 仍在增长，checkpointer 每轮全量重写 SQLite。我们只控制了"发给模型的量"，没控制"存盘的量"（解法：`prune()` 或 `RemoveMessage`）。
2. **摘要会累积误差**：摘要是"摘要的摘要"，轮次多了有信息损失。
3. **客户端断开时 worker 不停止**：缺一个取消信号（`threading.Event`），用户关页面后后端还在跑。
4. **`runs.save` 缺异常保护**：trace 写失败会影响主流程。
5. **扫描件 PDF 不支持**：纯图片 PDF 抽不出文字，需要 OCR。
6. **每轮 2~3 次 LLM 调用**：路由可以用小模型或规则前置来省。

---

## 十一、运行与部署

### 本地

```powershell
cd jobpilot
cp .env.example .env          # 填 DEEPSEEK_API_KEY / ZHIPU_API_KEY / JWT_SECRET
python -m uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000
```

### Docker

```bash
cd jobpilot
mkdir -p data && chown -R 1000:1000 data     # 非 root 容器 + bind mount，这步不能省
docker compose up -d --build
```

镜像里的几个工程细节：

| 细节 | 为什么 |
|---|---|
| 多阶段构建 | 构建依赖不进最终镜像 |
| 非 root（UID 1000） | 容器逃逸的爆炸半径更小 |
| `HEALTHCHECK /health` | 编排系统能判断"活着"还是"能用" |
| `TIKTOKEN_CACHE_DIR` + 预置 | 运行时无法下载编码文件（Bug 8） |
| `PIP_INDEX_URL` 构建参数 | 国内网络下 pypi 不稳定，可切清华源 |
| BuildKit 缓存挂载 | 依赖没变时不重复下载 |

### 自检脚本

| 脚本 | 验证什么 |
|---|---|
| `tests/smoke.py` | 路由分流 |
| `tests/context_budget_check.py` | 上下文裁剪 + 摘要迟滞 |
| `tests/interview_check.py` | 面试多轮 → 自动复盘 |
| `tests/web_check.py` | SSE 事件流 |
| `tests/auth_check.py` | JWT 鉴权与越权 |
| `tests/route_check.py` | 5 个分支的路由准确率 |
| `tests/interrupt_check.py` | 高风险工具挂起与恢复 |
| `tests/tools_check.py` | 工具网关四机制 + 审计落库 |
| `tests/resume_check.py` | 结构化输出与校验重试 |
| `tests/threads_check.py` | 会话列表与历史消息 |
| `tests/progress_check.py` | 投递记录增删改查 |
| `tests/mcp_check.py` | MCP 协议：工具发现 + 写-查-删闭环 + 参数校验 |
| `tests/profile_check.py` | 长期画像：写入 → 注入 prompt → 换会话仍答得出来 |
| `tests/scheduler_check.py` | 定时任务：停滞场景出提醒 / 正常场景不打扰 |
| `tests/builder_check.py` | 简历制作：多轮累积不丢字段 + HTML 渲染 + 转义 |
| `evals/run_eval.py` | 检索层 recall@3（向量 / BM25 / 混合三路对比） |
| `evals/run_agent_eval.py` | Agent 层 32 条用例（路由 / 工具调用 / 拒答） |

---

## 附：这份文档对应的完整能力清单

```
✅ LangGraph Supervisor 多 Agent 编排（4 专职 + 1 兜底）
✅ Skill 机制（Markdown SOP + 渐进披露 + 多文件按阶段加载）
✅ 两层记忆（会话内：tiktoken 计数 + 渐进摘要 + 迟滞阈值；跨会话：用户画像）
✅ 混合检索（向量 + BM25 + RRF，含 recall@3 评测）
✅ Agent 层评测（32 条用例：路由 20 / 工具调用 8 / 拒答 4，全部可复现）
✅ 工具调用（ToolRegistry：超时/重试/审计/权限分级）
✅ MCP Server（3 个工具，薄代理复用 HTTP API，协议自检 7/7）
✅ 定时任务（每日投递复盘：Agent 主动发起，无事不打扰）
✅ 人工确认（interrupt 挂起 + Command(resume) 恢复现场）
✅ 会话持久化（SqliteSaver + thread_id 隔离）
✅ 多用户鉴权（JWT + scrypt 加盐 + 数据层隔离）
✅ 简历从零制作（多轮口述 → 结构化草稿 → HTML 模板 → 浏览器打印成 PDF）
✅ 结构化输出（Pydantic 校验 + 错误回灌重试）
✅ SSE 流式（自建事件 sink 打通子图）
✅ PDF 解析入库（上传简历 → state）
✅ 运行可观测（tool_calls 审计表 + runs trace 表）
✅ 前端（零依赖单文件、双视图 hash 路由、自写 Markdown 渲染、行内编辑）
✅ 部署（多阶段镜像、非 root、健康检查、Compose）
```
