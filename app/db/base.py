"""数据库引擎与会话。

一套代码同时支持 SQLite（本地开发）与 MySQL（部署），只换 DATABASE_URL，
业务代码一行不用改——这正是选 ORM 而不是裸驱动的原因。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# SQLite 是文件库且被跨线程使用（FastAPI 线程池 + 图执行线程），
# 必须关掉同线程校验；MySQL 走网络连接，不需要也不能传这个参数。
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,  # MySQL 长连接可能被服务端掐断，取连接前先探活
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """建表（幂等）。必须先 import models，Base 上才有表定义。"""
    from app.db import models  # noqa: F401  仅为触发模型注册

    Base.metadata.create_all(engine)
