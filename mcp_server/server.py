"""JobPilot 的 MCP Server：把投递管理能力暴露给任意 MCP 客户端。

三个设计决定：

1. **薄代理，不重复实现数据访问**。
   Server 把请求转发给已有的 HTTP API，而不是自己连数据库。多用户隔离
   （JWT + 每条查询都带 user_id 条件）已经在 API 层做完了，在 MCP 层再抄
   一遍只会多出一处可能写错的地方。

2. **身份从环境变量读，不从工具参数读**。
   工具参数是模型填的。如果 token / user_id 走参数，就等于把「改谁的数据」
   交给了模型——和项目里「工具 schema 不出现 user_id」是同一条原则。
   所以这个 Server 是无状态的：谁启动它、带谁的 token，它就只代表谁。

3. **删除的确认位置变了，这是协议决定的，不是偷懒**。
   Web 里删除走图内 interrupt，因为删除意图是**模型推断**出来的，可能理解错。
   在 MCP 里，协议要求客户端在执行工具前获得用户同意（Claude Desktop 会弹出
   工具调用让你点批准），确认职责从服务端移到了客户端 UI 层。
   所以这里的删除不再做二次确认，直接执行。

启动（stdio 传输，由 MCP 客户端作为子进程拉起）：

    set JOBPILOT_TOKEN=<登录拿到的 JWT>
    python -m mcp_server.server

只暴露投递相关的 3 个工具。`search_questions` 是 Agent 内部推理用的题库检索，
对外部客户端没有意义，不暴露。
"""

import os

import httpx
from mcp.server.mcpserver import MCPServer

API_BASE = os.getenv("JOBPILOT_API", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("JOBPILOT_TOKEN", "").strip()

_STATUSES = ("已投递", "笔试", "面试", "offer", "已拒")

mcp = MCPServer("jobpilot")


def _request(method: str, path: str, **kwargs) -> tuple[bool, object]:
    """发一次 API 请求，返回 (是否成功, 数据 或 错误文案)。

    工具是给模型看的，所以失败时返回人能读懂的中文说明，而不是抛异常——
    异常的堆栈对模型没意义，它需要知道「该怎么办」。
    """
    if not TOKEN:
        return False, (
            "缺少 JOBPILOT_TOKEN 环境变量。请在 JobPilot 里登录拿到 token，"
            "再用 JOBPILOT_TOKEN=<token> 重启这个 MCP Server。"
        )
    try:
        with httpx.Client(
            base_url=API_BASE,
            timeout=15,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            resp = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return False, f"连不上 JobPilot API（{API_BASE}）：{exc}"

    if resp.status_code == 401:
        return False, "token 无效或已过期，请重新登录并更新 JOBPILOT_TOKEN。"
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text[:200]
        return False, f"请求失败（HTTP {resp.status_code}）：{detail}"
    return True, resp.json()


def _render(items: list[dict]) -> str:
    """渲染成 Markdown 表格，和 Web 端保持同一种展示形态。"""
    if not items:
        return "（暂无投递记录）"

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    funnel = " · ".join(f"{s} {counts[s]}" for s in _STATUSES if s in counts)

    lines = [
        f"共 {len(items)} 条｜{funnel}",
        "",
        "| 公司 | 岗位 | 状态 | 更新时间 | 备注 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {it['company']} | {it['role']} | **{it['status']}** "
        f"| {it['updated_at']} | {it.get('note') or ''} |"
        for it in items
    ]
    return "\n".join(lines)


@mcp.tool()
def list_applications(status: str = "") -> str:
    """查询我的求职投递记录。

    Args:
        status: 可选筛选。取值：已投递 / 笔试 / 面试 / offer / 已拒；留空返回全部。
    """
    ok, data = _request("GET", "/api/applications")
    if not ok:
        return str(data)

    items = data.get("items", [])
    if status:
        items = [it for it in items if it["status"] == status]
    return _render(items)


@mcp.tool()
def save_application(company: str, role: str, status: str, note: str = "") -> str:
    """记录或更新一条投递记录。同一公司 + 岗位会覆盖更新，不会产生重复。

    Args:
        company: 公司名，例如 "字节跳动"
        role: 岗位名，例如 "AI 应用开发"
        status: 当前状态，取值：已投递 / 笔试 / 面试 / offer / 已拒
        note: 备注，可空
    """
    if status not in _STATUSES:
        return f"状态取值不合法，只能是：{' / '.join(_STATUSES)}"

    ok, data = _request(
        "POST",
        "/api/applications",
        json={"company": company, "role": role, "status": status, "note": note},
    )
    if not ok:
        return str(data)
    return data.get("message", "已记录")


@mcp.tool()
def delete_application(company: str, role: str) -> str:
    """删除一条投递记录（不可恢复）。

    Args:
        company: 公司名
        role: 岗位名
    """
    # API 的删除是按 id 的，所以先查一次拿 id。
    # 对模型来说「公司 + 岗位」才是自然的定位方式，不该逼它记住 id。
    ok, data = _request("GET", "/api/applications")
    if not ok:
        return str(data)

    hit = next(
        (
            it
            for it in data.get("items", [])
            if it["company"] == company and it["role"] == role
        ),
        None,
    )
    if hit is None:
        return f"未找到记录：{company} · {role}"

    ok, err = _request("DELETE", f"/api/applications/{hit['id']}")
    if not ok:
        return str(err)
    return f"已删除：{company} · {role}"


if __name__ == "__main__":
    mcp.run()
