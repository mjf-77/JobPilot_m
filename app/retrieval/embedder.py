"""向量化：智谱 embedding-3（2048 维）。"""

from app.config import settings
from app.llm import get_embed_client

# 单次请求的批量上限（保守取值，避免超出服务端限制）
_BATCH_SIZE = 16


def embed_texts(texts: list[str]) -> list[list[float]]:#`texts` 就是已经切好的 chunk 片段列表
    """批量向量化，自动分批；返回顺序与输入一致。"""
    vectors: list[list[float]] = []#vectors 是一个列表，每个元素是一个向量，每个向量是一个列表，每个元素是一个浮点数
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        resp = get_embed_client().embeddings.create(
            model=settings.embed_model, input=batch
        )
        vectors.extend(item.embedding for item in resp.data)
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
