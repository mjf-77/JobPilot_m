"""检索评测：对比 vector / bm25 / hybrid 三路的 recall@k。

评测集里每条是「用户口语化提问 → 正确题目 id」。刻意用不同说法提问，
才能区分「真理解了语义」和「只匹配到了关键词」。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe evals\\run_eval.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.retrieval.service import search  # noqa: E402

EVAL_PATH = Path(__file__).resolve().parent / "interview_eval.json"
MODES = ["vector", "bm25", "hybrid"]
KS = [1, 3, 5]


def main() -> None:
    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    print(f"评测集 {len(cases)} 条\n")

    for mode in MODES:
        hits = {k: 0 for k in KS}
        misses: list[tuple[str, str, list[str]]] = []

        for case in cases:
            gold = case["gold"]
            results = search(case["query"], top_k=max(KS), mode=mode)
            ids = [r["id"] for r in results]
            for k in KS:
                if gold in ids[:k]:
                    hits[k] += 1
            if gold not in ids[:3]:
                misses.append((case["query"], gold, ids[:3]))

        summary = "  ".join(f"recall@{k}={hits[k]}/{len(cases)}" for k in KS)
        print(f"[{mode:6s}] {summary}")

        if mode == "hybrid" and misses:
            print("  未命中的 case：")
            for query, gold, top3 in misses:
                print(f"    - {query}（gold={gold}）→ 实际 {top3}")
    print()


if __name__ == "__main__":
    main()
