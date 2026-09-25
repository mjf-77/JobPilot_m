"""RetrievalService：对 Agent 暴露的统一检索入口。

Agent 只依赖这一层，不关心下面是向量、关键词还是融合——
以后换向量库、加 rerank，都在这层内部完成，上层代码不动。
"""

import json

from app.config import settings
from app.retrieval import bm25, hybrid, vector_store
from app.retrieval.embedder import embed_query

_BANK_PATH = settings.base_dir / "seed" / "interview_bank.json"

# 每路召回的候选数：先多召回，融合后再截到 top_k
_CANDIDATES = 10


def _load_bank() -> dict[str, dict]:#加载面试题库
    if not _BANK_PATH.is_file():
        return {}
    rows = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}#字典推导式：把列表转字典，key 是`id`，value 是整条 row


def search(query: str, top_k: int = 5, mode: str = "hybrid") -> list[dict]:
    """检索面试题。

    mode：
      - "vector"  只用向量召回（作为对照基线）
      - "bm25"    只用关键词召回
      - "hybrid"  两路召回 + RRF 融合（默认）

    返回 [{"id", "score", "question", "category", "difficulty", "key_points", "followups"}]
    """
    bank = _load_bank()
    vector_hits: list[tuple[str, float]] = []
    bm25_hits: list[tuple[str, float]] = []

    if mode in ("vector", "hybrid"):#如果 mode 等于`vector` **或者**等于`hybrid`
        vector_hits = vector_store.query_by_vector(embed_query(query), top_k=_CANDIDATES)

    if mode in ("bm25", "hybrid"):
        bm25_hits = bm25.query(query, top_k=_CANDIDATES)

    if mode == "hybrid":
        # RRF 只吃「排名」，所以把两路结果各自拍平成 id 列表再融合
        fused = hybrid.rrf_fuse(
            [[i for i, _ in vector_hits], [i for i, _ in bm25_hits]]#把两路结果各自拍平成 id 列表再融合
        )
        ordered = [i for i, _ in fused[:top_k]]
        scores = dict(fused)
    else:
        single = vector_hits if mode == "vector" else bm25_hits
        ordered = [i for i, _ in single[:top_k]]
        scores = dict(single)

    results: list[dict] = []
    for doc_id in ordered:
        row = bank.get(doc_id)
        if row is None:
            continue
        results.append({**row, "score": round(scores.get(doc_id, 0.0), 4)})#把 row 和 score 合并，保留 4 位小数，round是四舍五入
    return results
