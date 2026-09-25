"""Web / SSE 端点自检：验证 /health 与 /api/chat 的事件流。

需要先启动服务：
    D:\\dev\\python\\python.exe -m uvicorn app.main:app --port 8000
再运行：
    D:\\dev\\python\\python.exe tests\\web_check.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"


def main() -> None:
    health = httpx.get(f"{BASE}/health", timeout=10)
    print("[/health]", health.status_code, health.json())

    counts: dict[str, int] = {}
    text: list[str] = []

    with httpx.stream(
        "POST",
        f"{BASE}/api/chat",
        json={"message": "你好，你能帮我做什么？", "thread_id": "web-check"},
        timeout=120,
    ) as resp:
        print("[/api/chat]", resp.status_code, resp.headers.get("content-type"))
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            etype = event.get("type", "?")
            counts[etype] = counts.get(etype, 0) + 1
            if etype == "agent_switch":
                print(f"  agent_switch → {event.get('route')} ({event.get('skill')})")
            elif etype == "token":
                text.append(event["text"])
            elif etype == "done":
                print("  done.usage =", json.dumps(event.get("usage"), ensure_ascii=False))
            elif etype == "error":
                print("  error:", event)

    print("[事件统计]", counts)
    print("[拼出的回答前 200 字]\n" + "".join(text)[:200])


if __name__ == "__main__":
    main()
