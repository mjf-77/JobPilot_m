"""向量库：Chroma 持久化封装。

只拿它做「存向量 + 按向量查」：embedding 由我们自己在 embedder.py 里算，
所以 add/query 都显式传向量，不依赖 Chroma 内置的 embedding function。
"""

import chromadb

from app.config import settings

COLLECTION = "interview_bank"


def _collection():
    #一般在向量库项目里，`_collection()` 是**获取向量集合对象**的私有函数（下划线开头代表内部使用，不要外部直接调用）。
    client = chromadb.PersistentClient(path=str(settings.data_dir / "chroma"))
    return client.get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}#设置向量距离算法：**余弦相似度 cosine**
    )


def upsert(ids: list[str], documents: list[str], metadatas: list[dict], embeddings: list[list[float]]) -> None:
    _collection().upsert(
        ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
    )#`_collection()`：拿到 chroma 集合对象


def count() -> int:
    return _collection().count()


def query_by_vector(vector: list[float], top_k: int = 10) -> list[tuple[str, float]]:
    """返回 [(id, 相似度)]，按相似度降序。
    **vector 就是 embedding 向量，一串浮点数列表**。
    注意：
    Chroma 在 cosine 空间返回的是「距离」，所以相似度 = 1 - distance。
    """
    res = _collection().query(query_embeddings=[vector], n_results=top_k)
    ids = res["ids"][0]#返回结果是一个二维列表，第一维是查询结果，第二维是每个结果里的向量
    distances = res["distances"][0]
    return [(i, 1.0 - d) for i, d in zip(ids, distances)]
