"""定时任务自检：每日投递复盘能不能真的生成、写库、被读到。

不用等到早上 9 点——直接调 run_daily_digest() 跑一次。
所以这个脚本同时也是「手动触发」的入口。

构造两种场景，看模型分不分得清：
  A. 明显停滞（投了几周没动静）→ 应该产出提醒
  B. 一切正常（刚投的、都很新）→ 预期回「无需提醒」（这一条只观察，不作断言，
     因为模型可能保守地说一句"可以主动跟进"）

运行（在 jobpilot/ 目录下）：
    python tests/scheduler_check.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import delete  # noqa: E402

from app import scheduler  # noqa: E402
from app.db import notifications  # noqa: E402
from app.db.base import SessionLocal, init_db  # noqa: E402
from app.db.models import Application, Notification  # noqa: E402

STALE_USER = "schedcheck-stale"
FRESH_USER = "schedcheck-fresh"

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat(timespec="seconds")


def wipe(user_id: str) -> None:
    with SessionLocal() as session:
        session.execute(delete(Application).where(Application.user_id == user_id))
        session.execute(delete(Notification).where(Notification.user_id == user_id))
        session.commit()


def seed(user_id: str, rows: list[dict]) -> None:
    with SessionLocal() as session:
        for row in rows:
            session.add(Application(user_id=user_id, **row))
        session.commit()


def main() -> int:
    init_db()
    wipe(STALE_USER)
    wipe(FRESH_USER)

    # 场景 A：投了 2~3 周，全卡在早期状态 → 该被提醒
    seed(
        STALE_USER,
        [
            {"company": "字节跳动", "role": "AI 应用开发", "status": "已投递", "note": "", "updated_at": days_ago(21)},
            {"company": "腾讯", "role": "后端开发", "status": "已投递", "note": "", "updated_at": days_ago(18)},
            {"company": "美团", "role": "Java 开发", "status": "笔试", "note": "", "updated_at": days_ago(12)},
            {"company": "阿里巴巴", "role": "AI 应用开发", "status": "已投递", "note": "", "updated_at": days_ago(9)},
        ],
    )
    # 场景 B：刚投的，状态都很新 → 不该被打扰
    seed(
        FRESH_USER,
        [
            {"company": "字节跳动", "role": "AI 应用开发", "status": "已投递", "note": "", "updated_at": days_ago(0)},
            {"company": "腾讯", "role": "后端开发", "status": "已投递", "note": "", "updated_at": days_ago(1)},
        ],
    )

    print("── 生成提醒（限定到测试用户，不动真实数据）")
    created = scheduler.run_daily_digest([STALE_USER, FRESH_USER])
    print(f"    实际写入 {created} 条")

    stale_items = notifications.list_for(STALE_USER)
    fresh_items = notifications.list_for(FRESH_USER)

    check("停滞场景生成了提醒", len(stale_items) == 1)
    if stale_items:
        content = stale_items[0]["content"]
        print(f"    提醒内容：{content}")
        check("提醒内容有实质长度（不是敷衍）", len(content) >= 20, f"{len(content)} 字")
        check("提醒里提到了库里真实存在的公司",
              any(c in content for c in ("字节", "腾讯", "美团", "阿里")))

    print("\n── 正常场景（观察项，不作断言）")
    print(f"    写入 {len(fresh_items)} 条"
          + (f"：{fresh_items[0]['content']}" if fresh_items else "（模型判断无需打扰）"))

    print("\n── 未读数与已读")
    check("未读数正确", notifications.unread_count(STALE_USER) == len(stale_items))
    updated = notifications.mark_all_read(STALE_USER)
    check("标记已读后未读归零", notifications.unread_count(STALE_USER) == 0, f"标记了 {updated} 条")

    wipe(STALE_USER)
    wipe(FRESH_USER)

    passed = sum(results)
    print("\n" + "=" * 56)
    print(f"  定时任务自检 {passed}/{len(results)}")
    print("=" * 56)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
