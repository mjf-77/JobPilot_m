"""ORM 模型：用户、投递记录、工具调用审计、执行记录。

四张表分两类：
- 业务数据（users / applications）：属于产品本身的数据，生产放 MySQL
- 运行数据（tool_calls / runs）：可观测性数据，与业务同库便于联表排查
"""

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))


class UserProfile(Base):
    """用户画像：跨会话的长期记忆（目标岗位、技术栈、意向城市…）。

    为什么用 KV 表而不是 users 表加一列 JSON：
    画像是「一条一条加上去的」，KV 能按字段增量更新，也不必读改写整个 JSON
    （JSON 列会有并发覆盖的问题）。代价是查询多一条语句，可接受。

    列名用 field 而不是 key —— key 在 MySQL 里是保留字，会被迫到处加引号。
    """

    __tablename__ = "user_profiles"
    __table_args__ = (UniqueConstraint("user_id", "field", name="uq_user_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(32))


class Application(Base):
    """投递记录。唯一键含 user_id —— 多用户隔离靠它，不靠应用层过滤。"""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "company", "role", name="uq_application"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    company: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(32))


class Notification(Base):
    """站内提醒：定时任务主动生成的内容（目前只有每日投递复盘）。

    和 tool_calls / runs 不是一类：那两个是「运行痕迹」（排障用），
    这个是「给用户看的正文」，属于业务数据。

    列名用 is_read 而不是 read —— read 在 MySQL 里是关键字，虽然能用，
    但每次写 SQL 都要确认一下，不值得。
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    is_read: Mapped[int] = mapped_column(Integer, default=0)


class Thread(Base):
    """会话元数据（左栏历史列表用）。

    为什么不直接从 checkpoints 表读：checkpoint 里存的是序列化后的图状态，
    没有「标题」「最后活跃时间」这种业务字段，解析成本高。
    业务元数据自己存一张表，职责更清晰。
    """

    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("user_id", "thread_id", name="uq_thread"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class ToolCall(Base):
    """工具调用审计：参数、结果、耗时逐条留痕。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(32))
    thread_id: Mapped[str] = mapped_column(String(128), default="")
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Run(Base):
    """一次完整请求的汇总：走了哪个 Agent、几次调用、多少 token、多少钱。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(32))
    thread_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    route: Mapped[str] = mapped_column(String(32), default="")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_yuan: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[int] = mapped_column(Integer, default=1)
