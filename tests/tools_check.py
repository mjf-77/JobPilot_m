"""工具网关自检：四个内置工具 + 审计 + 失败重试 + 高风险标记。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe tests\\tools_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.db import audit  # noqa: E402
from app.tools.builtin import registry  # noqa: E402
from app.tools.registry import ToolSpec  # noqa: E402


def main() -> None:
    print("== 1. 工具清单 ==")
    print(registry.names())

    print("\n== 2. 检索题库 ==")
    print(registry.execute("search_questions", {"query": "RAG 评测", "top_k": 2}))

    print("\n== 3. 写入投递记录（连写两次，验证幂等：应更新而非新增）==")
    print(registry.execute("save_application", {"company": "示例公司", "role": "AI 应用开发", "status": "已投递"}))
    print(registry.execute("save_application", {"company": "示例公司", "role": "AI 应用开发", "status": "笔试"}))

    print("\n== 4. 查询 ==")
    print(registry.execute("list_applications", {}))

    print("\n== 5. 删除（高风险工具，此处直接执行以验证功能）==")
    print(registry.execute("delete_application", {"company": "示例公司", "role": "AI 应用开发"}))

    print("\n== 6. 调用不存在的工具（应返回错误文本而不是抛异常）==")
    print(registry.execute("not_exist", {}))

    print("\n== 7. 失败重试（临时注册一个必定抛异常的工具，max_retries=2）==")
    registry.register(
        ToolSpec(
            name="_boom",
            description="测试用",
            parameters={"type": "object", "properties": {}},
            func=lambda: 1 / 0,
            max_retries=2,
        )
    )
    print(registry.execute("_boom", {}))

    print("\n== 8. 审计记录（最近 5 条）==")
    for row in audit.recent(5):
        print(f"  {row['created_at']} | {row['tool']:20s} | ok={row['ok']} | {row['duration_ms']}ms | {row['error'] or ''}")

    print("\n== 9. 高风险标记 ==")
    spec = registry.get("delete_application")
    print("delete_application.requires_confirmation =", spec.requires_confirmation)
    print("search_questions.requires_confirmation =", registry.get("search_questions").requires_confirmation)


if __name__ == "__main__":
    main()
