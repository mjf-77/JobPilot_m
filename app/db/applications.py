"""投递记录（按用户隔离）。

唯一键 (user_id, company, role) 由数据库层保证：同一用户重复记录同一岗位
不会产生脏数据，LLM 反复说「记录一下」也写不乱。
"""

from datetime import datetime

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Application

# 唯一合法的状态取值，按求职漏斗顺序排列（统计输出沿用这个顺序）
STATUSES = ("已投递", "笔试", "面试", "offer", "已拒")


class DuplicateError(Exception):
    """目标「公司 + 岗位」已被另一条记录占用（唯一键冲突）。"""


def save(
    company: str, role: str, status: str, note: str = "", user_id: str = ""
) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    with SessionLocal() as session:
        row = session.scalar(
            select(Application).where(
                Application.user_id == user_id,
                Application.company == company,
                Application.role == role,
            )
        )
        if row is None:
            session.add(
                Application(
                    user_id=user_id,
                    company=company,
                    role=role,
                    status=status,
                    note=note,
                    updated_at=now,
                )
            )
        else:
            row.status, row.note, row.updated_at = status, note, now
        session.commit()
    return f"已记录：{company} · {role} → {status}"


def list_rows(status: str = "", user_id: str = "") -> list[dict]:
    """按用户查询（可选按状态筛选），返回结构化行。

    结构化行给两个消费者共用：REST 接口（前端表格）和工具（Markdown 表格）。
    数据源只有这一个函数，所以两边展示永远一致。
    """
    with SessionLocal() as session:
        stmt = select(Application).where(Application.user_id == user_id)
        if status:
            stmt = stmt.where(Application.status == status)
        rows = session.scalars(stmt.order_by(Application.updated_at.desc())).all()

    return [
        {
            # 前端要按 id 改状态/删除，所以必须带出去；
            # 用 id 而不是 company+role 定位，改名字的场景也不会误伤
            "id": r.id,
            "company": r.company,
            "role": r.role,
            "status": r.status,
            # 库里存的是 ISO 格式（带 T），展示时换成空格并截到分钟
            "updated_at": r.updated_at.replace("T", " ")[:16],
            "note": r.note,
        }
        for r in rows
    ]


def summarize(rows: list[dict]) -> str:
    """状态统计，形如「共 4 条｜已投递 1 · 面试 2 · 已拒 1」。

    REST 接口和 Markdown 表格都调它，避免两处各写一份漏斗统计。
    """
    if not rows:
        return "（暂无投递记录）"

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    # 按固定阶段顺序输出，而不是按 count 排序——用户看的是「漏斗走到哪一步」
    funnel = " · ".join(f"{s} {counts[s]}" for s in STATUSES if s in counts)
    return f"共 {len(rows)} 条｜{funnel}"


def to_markdown(rows: list[dict]) -> str:
    """把投递记录渲染成 Markdown 表格（对话路径用；纯展示，没有操作列）。

    投递进度天然是「表格数据」，以前拼成 `公司 · 岗位 | 状态` 的文本行，
    在聊天气泡里就是一坨竖线、看不出阶段分布，所以改渲染成真表格。
    """
    if not rows:
        return "（暂无投递记录）"

    lines = [
        summarize(rows),
        "",
        "| 公司 | 岗位 | 状态 | 更新时间 | 备注 |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        "| {company} | {role} | **{status}** | {updated_at} | {note} |".format(**r)
        for r in rows
    )
    return "\n".join(lines)


def list_all(status: str = "", user_id: str = "") -> str:
    """工具入口：给 LLM 返回 Markdown 表格。"""
    return to_markdown(list_rows(status, user_id))


def update(
    app_id: int,
    company: str,
    role: str,
    status: str,
    note: str = "",
    user_id: str = "",
) -> bool:
    """整条覆盖更新，返回是否命中。

    和 save() 的区别：save 是按 (公司, 岗位) 找记录，所以改不了名字；
    这里按 id 定位，公司名和岗位名也能改，不用删了重加。

    查询条件必须同时带 user_id：只凭 id 就能改到别人的记录，是个越权漏洞
    （id 是自增的，随便试几个数就命中了）。
    """
    with SessionLocal() as session:
        row = session.scalar(
            select(Application).where(
                Application.id == app_id, Application.user_id == user_id
            )
        )
        if row is None:
            return False

        # 改名可能撞上已有的另一条记录。先查一次再动手，
        # 而不是等数据库抛 IntegrityError——那时事务已经脏了，收尾很麻烦。
        clash = session.scalar(
            select(Application).where(
                Application.user_id == user_id,
                Application.company == company,
                Application.role == role,
                Application.id != app_id,
            )
        )
        if clash is not None:
            raise DuplicateError(f"{company} · {role} 已存在另一条记录")

        row.company, row.role, row.status, row.note = company, role, status, note
        row.updated_at = datetime.now().isoformat(timespec="seconds")
        session.commit()
    return True


def delete_by_id(app_id: int, user_id: str = "") -> bool:
    """按 id 删除（同样必须带 user_id 条件）。"""
    with SessionLocal() as session:
        row = session.scalar(
            select(Application).where(
                Application.id == app_id, Application.user_id == user_id
            )
        )
        if row is None:
            return False
        session.delete(row)
        session.commit()
    return True


def delete(company: str, role: str, user_id: str = "") -> str:
    """删除一条记录（高风险操作，需人工确认）。"""
    with SessionLocal() as session:
        row = session.scalar(
            select(Application).where(
                Application.user_id == user_id,
                Application.company == company,
                Application.role == role,
            )
        )
        if row is None:
            return f"未找到记录：{company} · {role}"
        session.delete(row)
        session.commit()
    return f"已删除：{company} · {role}"
