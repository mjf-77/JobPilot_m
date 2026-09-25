"""BM25 关键词检索（中文用 jieba 分词）。
BM25：传统关键词检索算法，**靠词语匹配**，不是向量相似度
RAG 经常做混合检索：向量检索（语义） + BM25（关键词），两者结果合并，效果更好
jieba：中文分词库，把句子切成词语。
例子：`"大模型Agent开发"` → jieba 分词得到 `["大模型","Agent","开发"]`
索引不序列化 BM25Okapi 对象，只存「分词后的语料 + 对应 id」——
几十条量级重建是毫秒级，重新加载比重建更简单可靠。
"""

import json

import jieba
from rank_bm25 import BM25Okapi

from app.config import settings

_INDEX_PATH = settings.data_dir / "bm25_index.json"


def tokenize(text: str) -> list[str]:
    """中文分词，丢掉纯空白项。"""
    return [t for t in jieba.lcut(text) if t.strip()]#列表推导式，丢掉纯空白项，返回分词后的词语列表


def build_index(ids: list[str], corpus: list[str]) -> None:
    """把语料分词后落盘（调用方保证 ids 与 corpus 一一对应）。"""
    payload = {"ids": ids, "tokens": [tokenize(c) for c in corpus]}
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")#字典转 json 字符串


def query(query_text: str, top_k: int = 10) -> list[tuple[str, float]]:
    """返回 [(id, BM25 分数)] 降序。索引不存在时返回空列表。"""
    if not _INDEX_PATH.is_file():
        return []
    payload = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    bm25 = BM25Okapi(payload["tokens"])
    scores = bm25.get_scores(tokenize(query_text))
    ranked = sorted(zip(payload["ids"], scores), key=lambda x: x[1], reverse=True)
    return [(i, float(s)) for i, s in ranked[:top_k]]
