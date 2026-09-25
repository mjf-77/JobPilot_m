"""简历上传自检。

用法（可选传入一个真实 PDF 路径）：
    D:\\dev\\python\\python.exe tests\\resume_check.py "C:\\path\\to\\resume.pdf"

不给路径时，只验证「鉴权」和「非 PDF 被拒」。
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
THREAD = "resume-t1"


def main() -> None:
    name = f"r{uuid.uuid4().hex[:8]}"
    reg = httpx.post(
        f"{BASE}/api/auth/register", json={"username": name, "password": "pw123456"}
    )
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = httpx.post(
        f"{BASE}/api/resume/upload",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        data={"thread_id": THREAD},
    )
    print("[无 token 上传]", r.status_code, "(期望 401/403)")

    r = httpx.post(
        f"{BASE}/api/resume/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"thread_id": THREAD},
        headers=headers,
    )
    print("[上传 .txt]", r.status_code, r.json().get("detail"), "(期望 400)")

    if len(sys.argv) < 2:
        print("（未提供 PDF 路径，跳过解析与诊断）")
        return

    pdf = Path(sys.argv[1]).read_bytes()
    r = httpx.post(
        f"{BASE}/api/resume/upload",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"thread_id": THREAD},
        headers=headers,
    )
    print("[上传 PDF]", r.status_code)
    if r.status_code != 200:
        print("  ", r.text[:200])
        return

    data = r.json()
    print("  解析字符数:", data["chars"])
    print("  预览:", data["preview"][:120].replace("\n", " "))

    # 关键验证：上传后 Agent 能否读到简历（而不是让用户粘贴）
    with httpx.stream(
        "POST",
        f"{BASE}/api/chat",
        json={"message": "帮我优化简历", "thread_id": THREAD},
        headers=headers,
        timeout=180,
    ) as resp:
        chunks = []
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            import json

            ev = json.loads(line[6:])
            if ev.get("type") == "token":
                chunks.append(ev["text"])
            elif ev.get("type") == "done":
                print("[用量]", ev.get("usage"))

    text = "".join(chunks)
    print("[Agent 回复前 240 字]", text[:240].replace("\n", " "))


if __name__ == "__main__":
    main()
