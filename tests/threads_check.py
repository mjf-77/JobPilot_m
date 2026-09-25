"""会话列表自检：对话 → 会话出现在列表 → 能读回历史消息。

    $env:JOBPILOT_BASE="http://192.168.100.128:8000"
    D:\\dev\\python\\python.exe tests\\threads_check.py
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

BASE = os.environ.get("JOBPILOT_BASE", "http://127.0.0.1:8000")


def main() -> None:
    name = f"t{uuid.uuid4().hex[:8]}"
    token = httpx.post(
        f"{BASE}/api/auth/register", json={"username": name, "password": "pw123456"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    print("[初始会话列表]", httpx.get(f"{BASE}/api/threads", headers=headers).json())

    r = httpx.post(
        f"{BASE}/api/chat",
        json={"message": "你好，我想准备一下秋招", "thread_id": "th1"},
        headers=headers,
        timeout=120,
    )
    print("[发消息]", r.status_code)

    items = httpx.get(f"{BASE}/api/threads", headers=headers).json()
    print("[对话后会话列表]", items)

    if not items:
        print("!! 会话没有出现在列表里")
        return

    tid = items[0]["thread_id"]
    msgs = httpx.get(f"{BASE}/api/threads/{tid}/messages", headers=headers).json()
    print(f"[历史消息] {len(msgs)} 条")
    for msg in msgs:
        print("   ", msg["role"], "|", msg["content"][:40].replace("\n", " "))

    # 按标题能看出是同一会话（首条消息生成标题）
    print("[标题]", items[0]["title"])


if __name__ == "__main__":
    main()
