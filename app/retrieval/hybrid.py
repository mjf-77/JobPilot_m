"""RRF（Reciprocal Rank Fusion）：把多路检索结果合成一个排序。

向量分数范围 0‑1；BM25 分数没有上限，两者**不能直接相加**。
RRF：**抛弃原始分数，只看每条文档在各路结果里排第几名**

公式：score(d) = Σ 1 / (k + rank_i(d))，k 通常取 60。

为什么用 RRF 而不是加权求和：
向量相似度（0~1 的余弦）和 BM25 分数（无上界）量纲完全不同，
加权求和要先做归一化、还要为每一路调权重；RRF 只看「排在第几名」，
天然可比、几乎不需要调参，是工业界的默认做法。
"""

RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """输入多路「按序排列的 id 列表」，返回融合后的 [(id, score)] 降序。

    ranked_lists = [
    ["id1", "id2", "id3"],   # 向量检索输出，已经排好序
    ["id2", "id1", "id4"]    # BM25检索输出，已经排好序
]

    在任意一路里排名靠前都会加分，多路都靠前的文档得分最高——
    这正是「混合检索」比单路更稳的原因。
    """
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
