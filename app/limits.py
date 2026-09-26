"""跨请求的互斥与配额：会话并发锁 + 每日 token 上限。

为什么这两件事必须放 Redis：
它们的状态生命周期是「跨请求」的——一个请求上的锁要让并发的另一个请求看见；
今天累计的 token 要让今天之后的所有请求看见。进程内的 dict 做不到
（多 worker 各持一份，等于没锁），所以要一个外部共享、自带 TTL 的计数器。

Redis 在这里是**缓存，不是权威源**：连不上时全部降级为「不锁、不限流」，
服务照常可用。业务数据在 MySQL、会话态在 checkpoint，Redis 挂了不丢任何东西。
"""

import logging
import time
import uuid
from datetime import datetime, timedelta

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# 会话锁兜底 TTL：进程被 kill 时来不及释放，靠 TTL 自动解锁，避免会话被永久锁死
_LOCK_TTL = 600
# 探活失败后的重试间隔：避免 Redis 宕机期间每个请求都白等一次连接超时
_RETRY_AFTER = 30.0

_client: redis.Redis | None = None
_next_probe = 0.0


def client() -> redis.Redis | None:
    """惰性建连并探活。连不上返回 None，调用方一律走降级分支。"""
    global _client, _next_probe
    if _client is not None:
        return _client
    if time.monotonic() < _next_probe:
        return None
    _next_probe = time.monotonic() + _RETRY_AFTER
    try:
        _client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        _client.ping()
        logger.info("Redis 已连接：%s", settings.redis_url)
    except Exception as exc:
        _client = None
        logger.warning("Redis 不可用，降级为不锁不限流（%s 秒后重试）：%s", _RETRY_AFTER, exc)
    return _client


def acquire_thread(scoped_thread: str) -> bool:
    """抢占会话锁。

    SET NX 保证只有一个请求拿到；value 存随机串，释放时校验，
    避免「自己 TTL 过期后误删了别人刚拿到的锁」。
    拿不到锁说明同一会话已有请求在跑——并发写 checkpoint 会互相覆盖，
    所以这里宁可拒绝也不要放两个进去。
    """
    conn = client()
    if conn is None:
        return True  # 降级：宁可并发，也不因为缓存故障让服务不可用
    try:
        return bool(
            conn.set(f"lock:thread:{scoped_thread}", uuid.uuid4().hex, nx=True, ex=_LOCK_TTL)
        )
    except Exception as exc:
        logger.warning("会话锁获取失败，本次放行：%s", exc)
        return True


def release_thread(scoped_thread: str) -> None:
    """释放会话锁：只删自己持有的那把。"""
    conn = client()
    if conn is None:
        return
    try:
        conn.delete(f"lock:thread:{scoped_thread}")
    except Exception:
        pass  # 释放失败无所谓，TTL 到点自动解锁


def _quota_key(user_id: int) -> str:
    # 容器已设 TZ=Asia/Shanghai，按本地日期分桶，跨天自然重置
    return f"quota:tokens:{user_id}:{datetime.now():%Y%m%d}"


def _next_midnight() -> int:
    """次日 0 点的时间戳：给配额 key 设过期，避免 key 无限堆积。"""
    tomorrow = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(tomorrow.timestamp())


def quota_used(user_id: int) -> int | None:
    """今日已用 token；Redis 不可用返回 None（表示不限流）。"""
    conn = client()
    if conn is None:
        return None
    try:
        return int(conn.get(_quota_key(user_id)) or 0)
    except Exception:
        return None


def quota_exceeded(user_id: int) -> tuple[bool, int]:
    """超限预检。用在请求入口：超了就拒绝，省下这轮的模型花费。"""
    used = quota_used(user_id)
    if used is None:
        return False, 0
    return used >= settings.daily_token_quota, used


def quota_add(user_id: int, tokens: int) -> None:
    """一轮结束后累计消耗（INCRBY 是原子的，并发请求不会互相覆盖）。"""
    if tokens <= 0:
        return
    conn = client()
    if conn is None:
        return
    try:
        key = _quota_key(user_id)
        with conn.pipeline() as pipe:
            pipe.incrby(key, tokens)
            pipe.expireat(key, _next_midnight())
            pipe.execute()
    except Exception as exc:
        logger.warning("配额累计失败（不影响本轮结果）：%s", exc)
