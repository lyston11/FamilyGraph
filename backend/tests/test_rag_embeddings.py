"""C6：embedding 抽象、filter-then-ANN 与候选融合。

## 本文件守护什么

1. **未配置时 fail-closed**：不伪造命中、不静默退化为「无结果」；
2. **filter-then-ANN 而不是 post-filter**：过滤必须在 ANN 之前（实测 post-filter
   在低选择性下静默返回不足 k）；
3. **RRF 融合的确定性**：同分候选顺序可复现，不依赖字典插入顺序；
4. **`DeterministicEmbedder` 不得被当成生产实现**：它没有语义相似性。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import rag_embeddings as emb


def test_unconfigured_fails_closed():
    """未配置时抛 `EmbeddingUnavailable`，而不是返回空向量或抛通用异常。

    调用方据此**回退到词法路径**；若返回空向量，会得到「全是零距离」的伪命中；
    若抛通用异常，调用方难以区分「未配置」与「运行故障」。
    """
    config = emb.EmbeddingConfig(provider="", model="", dimension=0)
    assert config.configured is False
    with pytest.raises(emb.EmbeddingUnavailable):
        emb.resolve_embedder(config)


def test_partially_configured_is_not_configured():
    """缺任一项都算未配置——半配置会让维度与模型不一致。"""
    for provider, model, dim in [
        ("openai", "", 1536),
        ("", "text-embedding-3-small", 1536),
        ("openai", "text-embedding-3-small", 0),
    ]:
        assert (
            emb.EmbeddingConfig(provider=provider, model=model, dimension=dim).configured is False
        )


def test_unimplemented_provider_does_not_silently_use_deterministic():
    """未实现的 provider 必须报错，**不得**回落到 `DeterministicEmbedder`。

    它用 token 哈希投影，没有语义相似性——"叔叔" 与 "伯父" 不会被判为相近。
    回落会让系统看起来「已启用向量检索」，实际退化成比词法更差的字面检索。
    """
    config = emb.EmbeddingConfig(provider="some-new-provider", model="m", dimension=8)
    with pytest.raises(emb.EmbeddingUnavailable, match="尚未实现"):
        emb.resolve_embedder(config)


def test_deterministic_embedder_is_reproducible_but_not_semantic():
    """确定性 embedder 可复现，但**没有**语义相似性（这是它的定义，不是缺陷）。"""
    e = emb.DeterministicEmbedder(dimension=32)

    async def _run():
        return await e.embed(["uncle", "uncle", "aunt"])

    first, second, third = asyncio.run(_run())
    assert first == second, "同一输入必须给出同一向量（否则不可复现）"
    assert first != third
    # 归一化：便于余弦距离比较
    assert abs(sum(v * v for v in first) ** 0.5 - 1.0) < 1e-9


def test_filter_then_ann_puts_filter_before_ordering():
    """过滤必须出现在 CTE 内，ANN 排序在外层。

    这是本模块最重要的结构断言：post-filter 形态（先 ORDER BY 再 WHERE）在低选择性
    下会静默返回不足 k，而 RAG 无法区分「无相关内容」与「被过滤掉」。
    """
    sql = str(
        emb.build_vector_candidates(
            dimension=8,
            eligibility="c.status = 'active' AND d.status = 'active'",
            model="m1",
        )
    )
    assert "WITH authorized AS" in sql, "缺少先过滤的 CTE"
    # model 过滤必须存在：不同模型的向量空间不可比较，不过滤会返回随机排序。
    assert "s.model = :model" in sql, "缺少 model 过滤：不同向量空间的向量会被互相比较"
    # 第二个 CTE 以逗号分隔（`WITH a AS (...), ranked AS (...)`）。
    assert "ranked AS" in sql, "缺少去重用的 ranked CTE"

    # ① 过滤必须在**第一个** CTE 内：`authorized` 只做过滤，不做排序。
    authorized = sql.split("WITH authorized AS")[1].split("ranked AS")[0]
    assert "c.status = 'active'" in authorized, "过滤条件不在 authorized CTE 内（变成 post-filter）"
    assert "<=>" not in authorized, "authorized CTE 不应包含距离排序（过滤与排序必须分离）"

    # ② 距离运算符必须在 ranked 内（每个分段只算一次），而不是在外层重复计算。
    ranked = sql.split("ranked AS")[1].split("SELECT chunk_id, document_id")[0]
    assert "<=>" in ranked, "缺少向量距离运算符"
    assert (
        "PARTITION BY chunk_id" in ranked
    ), "缺少 PARTITION BY：同一 chunk 的多个分段会重复出现在结果里"
    assert "row_number()" in ranked, "缺少每 chunk 取最佳分段的去重"

    # ③ 外层必须用**已算好**的 distance 排序，不重复计算距离。
    outer = sql.split("SELECT chunk_id, document_id")[1]
    assert "ORDER BY distance ASC" in outer
    assert "<=>" not in outer, "外层重复计算距离（应在 ranked 内算一次）"
    assert "WHERE rn = 1" in outer, "外层未按每 chunk 最佳分段过滤"


def test_vector_query_rejects_bad_parameters():
    """参数错误 fail-loud：维度 0 会让 vector(0) 非法，over_fetch<1 会让 LIMIT 失效。"""
    with pytest.raises(ValueError):
        emb.build_vector_candidates(dimension=0, eligibility="1=1", model="m")
    with pytest.raises(ValueError):
        emb.build_vector_candidates(dimension=8, eligibility="1=1", model="m", over_fetch=0)
    with pytest.raises(ValueError):
        emb.pgvector_ddl(0)
    # 空 model 必须拒绝：不过滤 model 会让不同向量空间的向量互相比较，
    # 余弦距离在那里**没有意义**，会返回看似合理但实际随机的排序且不报错。
    with pytest.raises(ValueError, match="model"):
        emb.build_vector_candidates(dimension=8, eligibility="1=1", model="")


def test_pgvector_ddl_binds_dimension_and_carries_scope_columns():
    """DDL 必须绑定维度，并带 revision/scope 列。

    维度不匹配时插入会失败（刻意的 fail-loud）；revision/scope 列是检索过滤的依据
    ——撤权后向量**不会**自动消失（实测），可见性完全靠这些列。
    """
    ddl = emb.pgvector_ddl(1536)
    assert "vector(1536)" in ddl
    assert "revision int NOT NULL" in ddl
    assert "scope text NOT NULL" in ddl
    assert "ON DELETE CASCADE" in ddl, "chunk 删除后向量必须随之删除"


def test_rrf_fusion_is_deterministic_for_ties():
    """同分候选的顺序必须可复现（按 chunk_id 升序兜底）。

    否则同分候选的相对顺序取决于字典插入顺序——同一查询两次可能给出不同顺序，
    引用列表与用户可见结果随之漂移。
    """
    lexical = [{"chunk_id": 5}, {"chunk_id": 3}]
    vector = [{"chunk_id": 3}, {"chunk_id": 5}]
    first = emb.fuse_candidates(lexical=lexical, vector=vector, limit=10)
    second = emb.fuse_candidates(lexical=lexical, vector=vector, limit=10)
    assert [h["chunk_id"] for h in first] == [h["chunk_id"] for h in second]
    # 两路都命中的 3 与 5 分数相同，按 chunk_id 升序 → 3 在前
    assert [h["chunk_id"] for h in first] == [3, 5]


def test_rrf_rewards_candidates_hit_by_both_paths():
    """两路都命中的候选应排在只被一路命中的前面。"""
    lexical = [{"chunk_id": 1}, {"chunk_id": 2}]
    vector = [{"chunk_id": 1}, {"chunk_id": 3}]
    fused = emb.fuse_candidates(lexical=lexical, vector=vector, limit=3)
    assert fused[0]["chunk_id"] == 1, "两路都命中的候选未排首位"


def test_rrf_does_not_compare_raw_scores():
    """融合只看名次，不看分数——bm25 与余弦距离不同量纲，比较会随分布漂移。"""
    # 两路的「分数」值完全不同，但名次相同 → 结果必须相同
    lexical = [{"chunk_id": 1, "rank": 0.001}, {"chunk_id": 2, "rank": 999.0}]
    vector = [{"chunk_id": 1, "distance": 0.9}, {"chunk_id": 2, "distance": 0.1}]
    fused = emb.fuse_candidates(lexical=lexical, vector=vector, limit=2)
    assert [h["chunk_id"] for h in fused] == [1, 2]


def test_rrf_limit_is_bounded():
    lexical = [{"chunk_id": i} for i in range(1, 6)]
    assert len(emb.fuse_candidates(lexical=lexical, vector=[], limit=3)) == 3
    with pytest.raises(ValueError):
        emb.fuse_candidates(lexical=[], vector=[], limit=0)
