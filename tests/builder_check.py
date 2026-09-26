"""简历制作自检：多轮累积 → 结构完整 → 渲染出可打印的 HTML。

重点验「多轮累积」——这是这个 Agent 最容易坏的地方：
第二轮补教育经历时，如果把第一轮说的姓名/电话冲掉了，说明合并逻辑错了
（resume_draft 是整字段替换语义，全靠代码把 patch 并进旧值）。

运行（在 jobpilot/ 目录下）：
    python tests/builder_check.py
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage  # noqa: E402

from app import auth  # noqa: E402
from app.api.chat import get_graph  # noqa: E402
from app.db.base import init_db  # noqa: E402
from app.events import set_sink  # noqa: E402
from app.graph.agents.resume_builder import is_ready, missing_labels  # noqa: E402
from app.resume_render import render  # noqa: E402

USER = "buildercheck"

# 模拟一个真实用户：一轮说一类信息
TURNS = [
    "帮我做份简历。我叫蒙锦锋，电话 13217850927，邮箱 2217421563@qq.com",
    "教育经历：广西大学（211），计算机科学与技术，本科，2024.09 到 2028.06",
    "项目：思屿-知识获取与分享社区，我担任后端开发，时间 2025.12-2026.03。"
    "主要做了 JWT 双令牌认证和 Kafka 事件容灾回放。"
    "技术栈是 Java 21、SpringBoot、Redis、Kafka",
    "技能：熟悉 Java 并发编程和 JVM 原理，熟练使用 SpringBoot、MyBatis，掌握 Redis、Kafka。",
]

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def run_turn(text: str, thread: str) -> dict:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread}}
    auth.set_current_user(USER)
    set_sink(lambda _event: None)
    return graph.invoke({"messages": [HumanMessage(content=text)]}, config)


def main() -> int:
    init_db()
    thread = f"buildercheck-{int(time.time())}"
    draft: dict = {}

    print("── 多轮收集")
    for round_no, text in enumerate(TURNS, 1):
        result = run_turn(text, thread)
        draft = result.get("resume_draft") or {}
        print(f"  第 {round_no} 轮后字段：{sorted(draft.keys())}")

        if round_no == 1:
            check("第一轮抽到姓名", draft.get("name") == "蒙锦锋", str(draft.get("name")))
            check("第一轮抽到电话", "13217850927" in str(draft.get("phone", "")))
        elif round_no == 2:
            check("第二轮抽到教育经历", bool(draft.get("education")))
            # ★ 这一条是重点：新信息不能把旧信息冲掉
            check("★ 第二轮没冲掉第一轮的姓名", draft.get("name") == "蒙锦锋")
            check("★ 第二轮没冲掉第一轮的邮箱", "2217421563" in str(draft.get("email", "")))
        elif round_no == 3:
            projects = draft.get("projects") or []
            check("第三轮抽到项目", bool(projects))
            check("★ 前面的基本信息仍在", draft.get("name") == "蒙锦锋")
            if projects:
                first = projects[0]
                check("项目名抽对了", "思屿" in str(first.get("name", "")), str(first.get("name")))
                # 亮点/简介抽没抽出来取决于模型这轮的判断（有时会漏），只观察不判定
                print(f"    （观察）亮点：{first.get('highlights')}｜简介：{first.get('intro')}")
        elif round_no == 4:
            check("第四轮抽到技能", bool(draft.get("skills")))
            check("★ 四项都齐了（可以生成）", is_ready(draft), f"还缺 {missing_labels(draft)}")

    print("\n── 渲染 HTML")
    html = render(draft, missing_labels(draft))
    check("HTML 里有姓名", "蒙锦锋" in html)
    check("章节标题带深蓝样式", 'class="section"' in html)
    check(
        "三个章节都在",
        all(s in html for s in ("教育经历", "项目经历", "个人技能")),
    )
    check("A4 打印样式生效", "@page" in html and "A4" in html)
    # 检查「格式是否统一」而不是写死某个日期：模型抽出来的写法每次可能不同
    check(
        "时间区间里没有半角连字符",
        not re.search(r"\d{4}\.\d{2}\s*[-–]\s*\d{4}", html),
    )
    check("时间区间用了全角波浪号", "～" in html)
    check("屏幕预览有 A4 纸容器", 'class="page"' in html)

    print("\n── 转义（防注入）")
    evil = {"name": "<script>alert(1)</script>", "phone": "138", "email": "a@b.c"}
    evil_html = render(evil)
    check("用户输入被转义，未注入标签", "<script>" not in evil_html and "&lt;script&gt;" in evil_html)

    print("\n── 缺项时的表现（观察）")
    partial = render({"name": "张三"}, missing_labels({"name": "张三"}))
    check("缺信息时页顶提示还缺什么", "这份简历还缺" in partial)

    print("\n── 最终草稿")
    print(json.dumps(draft, ensure_ascii=False, indent=2)[:1500])

    # 把渲染结果落一份，方便人肉打开看版式
    out = Path(__file__).resolve().parent.parent / "data" / "builder_preview.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\n  渲染结果已写入：{out}")

    passed = sum(results)
    print("\n" + "=" * 56)
    print(f"  简历制作自检 {passed}/{len(results)}")
    print("=" * 56)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
