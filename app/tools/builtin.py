"""内置工具集：注册到全局 registry，供各 Agent 通过 Function Calling 调用。"""

from app import auth
from app.db import applications, profiles
from app.retrieval.service import search
from app.tools.registry import ToolRegistry, ToolSpec

registry = ToolRegistry()


def _search_questions(query: str, top_k: int = 3) -> str:
    hits = search(query, top_k=top_k)
    if not hits:
        return "（题库未命中）"
    return "\n".join(
        f"- [{h['id']}] {h['question']}（考察点：{'；'.join(h['key_points'])}）"
        for h in hits
    )


# 下面三个是薄包装：把「当前用户」注入业务函数。
# 这样做的好处是工具对 LLM 的 schema 保持干净（不出现 user_id 参数）——
# 用户身份是系统事实，不该由模型来传。
def _save_application(company: str, role: str, status: str, note: str = "") -> str:
    # 空值校验放在工具层，而不是只写在 SOP 里——数据完整性不能指望模型每次都听话。
    # 为什么非拦不可：唯一键是 (公司, 岗位)，一条空岗位的占位记录会和之后
    # 补全的真实岗位变成两条不同记录，用户补一句"是 Java 开发"就多出一条脏数据。
    if not company.strip() or not role.strip():
        return "记录失败：公司和岗位都不能为空。请先向用户问清楚缺的那一项，不要凭空猜测。"
    return applications.save(
        company.strip(), role.strip(), status, note, user_id=auth.current_user_id()
    )


def _list_applications(status: str = "") -> str:
    return applications.list_all(status, user_id=auth.current_user_id())


def _delete_application(company: str, role: str) -> str:
    return applications.delete(company, role, user_id=auth.current_user_id())


def _remember_profile(field: str, value: str) -> str:
    return profiles.set_one(auth.current_user_id(), field, value)


registry.register(
    ToolSpec(
        name="search_questions",
        description="从面试题库中检索与关键词相关的题目，返回题目与考察点。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，如「RAG 评测」「并发」"},
                "top_k": {"type": "integer", "description": "返回条数，默认 3"},
            },
            "required": ["query"],
        },
        func=_search_questions,
    )
)

registry.register(
    ToolSpec(
        name="save_application",
        description="记录或更新一条投递记录。同一公司+岗位会覆盖更新（幂等），不会产生重复数据。",
        parameters={
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名"},
                "role": {"type": "string", "description": "岗位名"},
                "status": {
                    "type": "string",
                    "description": "当前状态：已投递 / 笔试 / 面试 / offer / 已拒",
                },
                "note": {"type": "string", "description": "备注，可空"},
            },
            "required": ["company", "role", "status"],
        },
        func=_save_application,
    )
)

registry.register(
    ToolSpec(
        name="list_applications",
        description=(
            "查询投递记录，返回 Markdown 表格（含状态统计），请原样输出给用户。"
            "status 传空字符串则返回全部。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "可选筛选：已投递 / 笔试 / 面试 / offer / 已拒",
                }
            },
        },
        func=_list_applications,
    )
)

registry.register(
    ToolSpec(
        name="delete_application",
        description="删除一条投递记录，不可恢复。",
        parameters={
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名"},
                "role": {"type": "string", "description": "岗位名"},
            },
            "required": ["company", "role"],
        },
        func=_delete_application,
        requires_confirmation=True,  # 高风险：删除不可逆，执行前必须人工确认
    )
)

registry.register(
    ToolSpec(
        name="remember_profile",
        description=(
            "记住关于用户的长期信息，跨会话生效：目标岗位方向、技术栈、意向城市、"
            "求职阶段这类「下次还用得上」的事实。value 传空字符串表示忘掉这条。"
            "一次性的事实（某场面试的时间、某家公司的状态）不要记，那些属于投递记录。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "字段名，如「目标岗位」「技术栈」「意向城市」「求职阶段」",
                },
                "value": {
                    "type": "string",
                    "description": "字段值；传空字符串表示删除这条",
                },
            },
            "required": ["field", "value"],
        },
        func=_remember_profile,
    )
)
