"""认证自检：注册 → 登录 → 带 token 访问 → 无 token 被拒 → 用户隔离。

本地服务：
    D:\\dev\\python\\python.exe -m uvicorn app.main:app --port 8000
    D:\\dev\\python\\python.exe tests\\auth_check.py

针对部署环境：
    $env:JOBPILOT_BASE="http://192.168.100.128:8000"
    D:\\dev\\python\\python.exe tests\\auth_check.py
"""

import json
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
    name = f"u{uuid.uuid4().hex[:8]}"

    reg = httpx.post(f"{BASE}/api/auth/register", json={"username": name, "password": "pw123456"})
    print("[注册]", reg.status_code)
    token = reg.json().get("token", "")
    print("  token 前缀:", token[:24], "...")

    dup = httpx.post(f"{BASE}/api/auth/register", json={"username": name, "password": "pw123456"})
    print("[重复注册]", dup.status_code, "(期望 409)")

    login = httpx.post(f"{BASE}/api/auth/login", json={"username": name, "password": "pw123456"})
    print("[登录]", login.status_code)
    bad = httpx.post(f"{BASE}/api/auth/login", json={"username": name, "password": "wrong123"})
    print("[错误密码]", bad.status_code, "(期望 401)")

    no_auth = httpx.post(f"{BASE}/api/chat", json={"message": "你好", "thread_id": "t1"})
    print("[无 token 访问 /api/chat]", no_auth.status_code, "(期望 401/403)")

    headers = {"Authorization": f"Bearer {token}"}
    text: list[str] = []
    with httpx.stream(
        "POST",
        f"{BASE}/api/chat",
        json={"message": "帮我记录：投了字节跳动 AI 应用开发岗，状态已投递", "thread_id": "t1"},
        headers=headers,
        timeout=120,
    ) as resp:
        print("[带 token 访问 /api/chat]", resp.status_code)
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "token":
                text.append(event["text"])
            elif event.get("type") == "done":
                print("  done 帧:", event.get("usage"))
    print("  回复内容:", "".join(text)[:300])

    runs = httpx.get(f"{BASE}/api/runs?limit=1", headers=headers)
    print("[/api/runs]", runs.status_code, runs.json()[:1])


if __name__ == "__main__":
    main()
