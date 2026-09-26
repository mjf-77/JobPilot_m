"""定时任务：让 Agent 主动干活，而不是只在被问到时才响应。

目前只有一件事——每天早晨为每个有投递记录的用户生成一条「投递复盘」。

三个设计决定：

1. **用 APScheduler，不用 cron / Celery**。
   任务里要直接用应用内的资源（数据库会话、LLM 客户端）。用 cron 得起第二个进程
   再连一遍库；Celery 还要额外一套 broker。代价是**多实例部署时每个实例都会跑一遍**，
   到那时得换成「分布式锁 + 单实例执行」或外部调度器。

2. **让模型自己判断「今天值不值得提醒」，而不是无脑推送**。
   记录都是新投的、没有停滞时，模型回「无需提醒」，我们就不写库。
   每日推送最容易死于「天天说废话」。

3. **任务里显式设置当前用户**。
   后台线程没有请求上下文，contextvar 是空的。现在这个任务不调工具所以用不上，
   但哪天让它调工具（比如自动发跟进提醒），漏了这步就会写出 user_id 为空的脏数据
   ——和 Bug 6 是同一个坑。
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from langchain_core.messages import HumanMessage, SystemMessage

from app import auth
from app.config import settings
from app.db import applications, notifications, profiles
from app.llm import chat

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# 模型用它表示「今天没什么可说的」
_SKIP = "无需提醒"

_DIGEST_SYSTEM = """你负责每天早上为用户做一次求职投递复盘，写一条提醒给他。

规则：
1. 只在**确实有事值得说**时才写：有记录卡在某个状态太久、整体进展异常
   （例如投了不少却一个面试都没有）、或某条需要马上采取行动。
2. 如果一切正常（都是新投的、没有停滞、没有异常），**只输出四个字**：无需提醒
3. 需要提醒时输出 2~4 句：先给结论，再说建议先做哪件事。不要客套话，不要罗列。
4. 只能依据给定的记录，**不要编造**——不要假设某家公司会回复、不要虚构面试时间。
"""


def compose_digest(items: list[dict], profile: dict[str, str]) -> str:
    """把投递记录 + 长期画像交给模型，产出今天的提醒；无事可说返回空串。"""
    table = "\n".join(
        f"- {it['company']} · {it['role']}：{it['status']}（更新于 {it['updated_at']}）"
        + (f"｜备注：{it['note']}" if it.get("note") else "")
        for it in items
    )
    profile_text = "\n".join(f"- {k}：{v}" for k, v in profile.items()) or "（暂无）"

    prompt = (
        f"今天是 {datetime.now():%Y-%m-%d}。\n\n"
        f"用户画像：\n{profile_text}\n\n"
        f"当前投递记录（共 {len(items)} 条）：\n{table}\n\n"
        f"请给出今天的提醒；如果没什么值得说的，只回复「{_SKIP}」。"
    )
    try:
        answer = chat(
            [SystemMessage(content=_DIGEST_SYSTEM), HumanMessage(content=prompt)]
        )
    except Exception:
        # 定时任务失败不能把调度器搞崩，记日志跳过这个用户
        logger.exception("生成投递复盘失败")
        return ""

    text = (answer or "").strip()
    # 把「无需提醒」的各种写法（带标点、被扩写成一句话）都识别掉
    if not text.replace(_SKIP, "").strip("。.！!，, "):
        return ""
    return text


def run_daily_digest(user_ids: list[str] | None = None) -> int:
    """遍历有投递记录的用户逐个生成提醒，返回实际写入的条数。

    user_ids 只在自检时传（限定到测试用户，避免给真实用户发提醒）；
    APScheduler 无参调用它，此时为 None，遍历全部。
    """
    targets = user_ids if user_ids is not None else applications.list_user_ids()
    created = 0
    for user_id in targets:
        items = applications.list_rows(user_id=user_id)
        if not items:
            continue

        auth.set_current_user(user_id)  # 后台线程没有请求上下文，必须显式设
        text = compose_digest(items, profiles.get_all(user_id))
        if not text:
            continue

        notifications.add(user_id, text)
        created += 1
        logger.info("已为用户 %s 生成投递复盘", user_id)
    return created


def start() -> None:
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("定时任务未启用（SCHEDULER_ENABLED=false）")
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        run_daily_digest,
        CronTrigger(hour=settings.digest_hour, minute=settings.digest_minute),
        id="daily_digest",
        replace_existing=True,
        # 恰好在重启时会错过执行，1 小时内仍补跑一次
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "定时任务已启动：每天 %02d:%02d 生成投递复盘",
        settings.digest_hour,
        settings.digest_minute,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
