"""用户表：注册与登录校验。

密码用标准库 `hashlib.scrypt` 加盐哈希——scrypt 是抗 GPU 暴力破解的 KDF，
强度足够且不引第三方加密库，少一个依赖也少一处漏洞面。
"""

import hashlib
import hmac
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime

from app.config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    created_at    TEXT NOT NULL
)
"""

# scrypt 参数：n=2^14 在交互式登录场景下耗时约几十毫秒，兼顾安全与体验
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "jobpilot.db")
    conn.execute(_DDL)
    return conn


def _hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=salt.encode(), **_SCRYPT).hex()


def create(username: str, password: str) -> int | None:
    """创建用户；用户名已存在时返回 None。"""
    salt = secrets.token_hex(16)
    with closing(connect()) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    username,
                    _hash(password, salt),
                    salt,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def verify(username: str, password: str) -> int | None:
    """校验用户名密码，成功返回 user_id。

    用 hmac.compare_digest 比较哈希，避免按字符逐位比较带来的时序侧信道。
    """
    with closing(connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, password_hash, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        return None
    if hmac.compare_digest(row["password_hash"], _hash(password, row["salt"])):
        return int(row["id"])
    return None
