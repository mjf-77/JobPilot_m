"""Redis 限流与互斥自检：会话并发锁 + 每日 token 配额。

两种运行结果都有意义，不要只跑一种：
- 连得上 Redis（虚机/容器里）：逐条断言互斥与配额真的生效
- 连不上（本地裸跑）：断言「降级」行为——不锁、不限流，但服务不崩
  这正是设计目标：Redis 是缓存不是权威源，它挂了不该让主流程不可用。

加 --e2e 再多走一段真实 HTTP：注册/登录 → 发一轮对话 → 回头看 Redis 里的
配额是否累加、锁是否已释放。验证的是「代码里调了」和「线上真的生效」的差别。

运行（在 jobpilot/ 目录下）：
    python tests/redis_check.py            # 单元级：锁与配额的逻辑
    python tests/redis_check.py --e2e      # 再加一轮真实对话（会消耗少量 token）
环境变量 JOBPILOT_BASE 可覆盖服务地址（默认 http://127.0.0.1:8000）。
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app import limits  # noqa: E402
from app.config import settings  # noqa: E402

# 假的 user_id / thread：不碰真实账号的配额与会话
TEST_USER = 999_999_998
TEST_THREAD = "redischeck:thread"

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def cleanup(conn) -> None:
    conn.delete(f"lock:thread:{TEST_THREAD}")
    conn.delete(limits._quota_key(TEST_USER))


def e2e(conn) -> None:
    """打真实接口：一轮对话之后，配额该涨、锁该释放。"""
    import httpx

    base = os.environ.get("JOBPILOT_BASE", "http://127.0.0.1:8000")
    creds = {"username": "redischeck-e2e", "password": "redischeck-e2e-pass"}

    print("\n── 端到端：真实 HTTP 走一轮对话")
    httpx.post(f"{base}/api/auth/register", json=creds, timeout=10)
    login = httpx.post(f"{base}/api/auth/login", json=creds, timeout=10)
    token = login.json().get("token")
    check("登录拿到 token", bool(token), f"HTTP {login.status_code}")
    if not token:
        return

    thread_id = f"redischeck-{uuid.uuid4().hex[:6]}"
    with httpx.stream(
        "POST",
        f"{base}/api/chat",
        json={"message": "你好", "thread_id": thread_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    ) as resp:
        frames = "".join(line for line in resp.iter_lines() if line.startswith("data:"))

    check("收到 done 帧（这一轮正常收尾）", '"done"' in frames)
    check("没有错误帧", '"error"' not in frames)

    total = sum(int(conn.get(k) or 0) for k in conn.scan_iter("quota:tokens:*"))
    check("本轮消耗已计入配额（跨请求累计真的生效）", total > 0, f"Redis 里累计 {total} token")
    # 锁没释放的话，这个会话会被白锁到 TTL（600s）才恢复
    check("请求结束后会话锁已释放", not list(conn.scan_iter("lock:thread:*")))


def summary() -> int:
    passed = sum(results)
    print("\n" + "=" * 56)
    print(f"  Redis 自检 {passed}/{len(results)}")
    print("=" * 56)
    return 0 if passed == len(results) else 1


def main() -> int:
    conn = limits.client()

    if conn is None:
        print("── 连不上 Redis：验证降级行为（本地裸跑属正常，虚机上应重跑到全绿）")
        check("会话锁降级为放行", limits.acquire_thread(TEST_THREAD) is True)
        check("配额预检降级为不限流", limits.quota_exceeded(TEST_USER) == (False, 0))
        limits.quota_add(TEST_USER, 123)  # 只要求不抛异常
        check("配额累计降级不报错", True)
        limits.release_thread(TEST_THREAD)  # 同样只要求不抛异常
        check("释放锁降级不报错", True)
        if "--e2e" in sys.argv:
            print("\n（未连上 Redis，端到端部分跳过）")
        return summary()

    print(f"── 已连上 Redis：{settings.redis_url}")
    cleanup(conn)

    print("\n── 会话并发锁")
    check("首个请求拿到锁", limits.acquire_thread(TEST_THREAD))
    ttl = conn.ttl(f"lock:thread:{TEST_THREAD}")
    check("锁带 TTL（进程被 kill 也能自动解锁）", 0 < ttl <= 600, f"TTL={ttl}s")
    check("同会话并发第二个请求被拒", not limits.acquire_thread(TEST_THREAD))
    limits.release_thread(TEST_THREAD)
    check("释放后可再次拿到（上个请求已结束）", limits.acquire_thread(TEST_THREAD))
    limits.release_thread(TEST_THREAD)

    print("\n── 每日 token 配额")
    limits.quota_add(TEST_USER, 1000)
    limits.quota_add(TEST_USER, 500)
    check("多次消耗被累加（并发不覆盖）", limits.quota_used(TEST_USER) == 1500,
          f"实际 {limits.quota_used(TEST_USER)}")
    check("未超限时放行", limits.quota_exceeded(TEST_USER) == (False, 1500))
    quota_ttl = conn.ttl(limits._quota_key(TEST_USER))
    check("配额 key 次日 0 点自动过期", 0 < quota_ttl <= 86400, f"TTL={quota_ttl}s")

    conn.set(limits._quota_key(TEST_USER), settings.daily_token_quota)
    check("达到上限即拒绝", limits.quota_exceeded(TEST_USER)[0],
          f"上限 {settings.daily_token_quota}")

    cleanup(conn)

    if "--e2e" in sys.argv:
        e2e(conn)
    return summary()


if __name__ == "__main__":
    sys.exit(main())
