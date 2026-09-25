"""把种子面试题库灌入向量库与 BM25 索引。

运行（在 jobpilot/ 目录下）：
    D:\\dev\\python\\python.exe scripts\\ingest.py

可重复执行：向量库用 upsert，BM25 索引整体重建。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402
from app.retrieval import bm25, vector_store  # noqa: E402
from app.retrieval.embedder import embed_texts  # noqa: E402

BANK_PATH = settings.base_dir / "seed" / "interview_bank.json"

"""把 bank.json 的面试题，同时灌入 Chroma 向量库 + 生成 BM25 分词索引，一次性建好两套检索索引。"""
def to_document(row: dict) -> str:
    """构造用于检索的文本：问题 + 考察点（两者都应被召回命中）。"""
    return f"{row['question']}\n考察点：{'；'.join(row['key_points'])}"


def main() -> None:
    rows = json.loads(BANK_PATH.read_text(encoding="utf-8"))#加载面试题库
    ids = [r["id"] for r in rows]
    docs = [to_document(r) for r in rows]
    metas = [{"category": r["category"], "difficulty": r["difficulty"]} for r in rows]

    print(f"题库 {len(rows)} 条，开始向量化…")
    vectors = embed_texts(docs)#把文档列表转换为向量列表
    print(f"向量维度：{len(vectors[0])}")

    vector_store.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vectors)#把文档列表、向量列表、元数据列表、文档列表写入向量库
    print(f"Chroma 已写入：collection={vector_store.COLLECTION}，当前 {vector_store.count()} 条")

    bm25.build_index(ids, docs)#把文档列表、元数据列表写入 BM25 索引
    print("BM25 索引已写入：data/bm25_index.json")


if __name__ == "__main__":
    main()
