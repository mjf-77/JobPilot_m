"""MCP Server 自检：验证 tools/list 与 tools/call 是否符合协议。

它同时是一份「MCP 客户端怎么写」的最小示例——客户端事先**不知道**有哪些工具，
连上之后才拿到清单。这就是「工具注册从硬编码变成运行时发现」。

前置条件：
  1. JobPilot API 已在跑（本地或虚机）
  2. 有可用的 JWT

运行（在 jobpilot/ 目录下）：

    set JOBPILOT_API=http://192.168.100.128:8000
    set JOBPILOT_TOKEN=<登录拿到的 JWT>
    python tests/mcp_check.py

拿 token 的办法：
    curl -X POST http://192.168.100.128:8000/api/auth/login \
         -H "Content-Type: application/json" \
         -d "{\"username\":\"你的用户名\",\"password\":\"你的密码\"}"
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = {"list_applications", "save_application", "delete_application"}

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


async def main() -> int:
    env = {**os.environ}
    if not env.get("JOBPILOT_TOKEN"):
        print("！没有设置 JOBPILOT_TOKEN 环境变量，工具调用会失败（只测发现是够的）")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env=env,
        cwd=str(PROJECT_ROOT),  # 让子进程能 import mcp_server 包
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ---- 1. 工具发现（等价于 JSON-RPC 的 tools/list）----
            print("── 工具发现")
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            for t in tools.tools:
                props = list((t.input_schema or {}).get("properties", {}).keys())
                desc = (t.description or "").strip().splitlines()[0]
                print(f"    {t.name}({', '.join(props)})")
                print(f"      {desc}")

            record("工具清单与期望一致", names == EXPECTED_TOOLS,
                   f"缺失={sorted(EXPECTED_TOOLS - names)} 多余={sorted(names - EXPECTED_TOOLS)}")

            has_schema = all(
                (t.input_schema or {}).get("properties") for t in tools.tools
            )
            record("每个工具都有参数 Schema", has_schema)

            # ---- 2. 只读调用 ----
            print("\n── 只读调用 list_applications()")
            result = await session.call_tool("list_applications", {})
            text = result.content[0].text if result.content else ""
            print("    " + text.replace("\n", "\n    "))
            record("list_applications 返回内容", bool(text.strip()))

            if not env.get("JOBPILOT_TOKEN"):
                print("\n（没有 token，跳过写操作验证）")
                return report()

            # ---- 3. 写入 → 查询 → 删除 闭环 ----
            print("\n── 写操作闭环（自建一条再删掉，不留垃圾数据）")
            await session.call_tool(
                "save_application",
                {"company": "MCP自检公司", "role": "测试岗", "status": "已投递", "note": "自检"},
            )
            after_save = await session.call_tool("list_applications", {"status": "已投递"})
            saved_text = after_save.content[0].text if after_save.content else ""
            record("save_application 后能查到", "MCP自检公司" in saved_text)

            deleted = await session.call_tool(
                "delete_application", {"company": "MCP自检公司", "role": "测试岗"}
            )
            deleted_text = deleted.content[0].text if deleted.content else ""
            record("delete_application 执行成功", "已删除" in deleted_text, deleted_text)

            final = await session.call_tool("list_applications", {})
            final_text = final.content[0].text if final.content else ""
            record("删除后确实查不到了", "MCP自检公司" not in final_text)

            # ---- 4. 参数校验：非法状态应被挡下，而不是写进库 ----
            print("\n── 参数校验")
            bad = await session.call_tool(
                "save_application",
                {"company": "X", "role": "Y", "status": "乱写的状态"},
            )
            bad_text = bad.content[0].text if bad.content else ""
            record("非法 status 被拒绝", "不合法" in bad_text, bad_text[:60])

    return report()


def report() -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 56)
    print(f"  MCP 自检 {passed}/{total}")
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
