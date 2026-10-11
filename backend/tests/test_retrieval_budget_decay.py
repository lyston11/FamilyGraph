"""U3：检索预算的消费方维度 + 新鲜度（年龄衰减）。

两件事分开钉住：

1. **份额是消费方相关的**（`(consumer_kind, source_type)`），且 assistant 的取值与
   引入该维度之前逐字相同；
2. **新鲜度是独立的排序特征**（`lex-v3`），不是把衰减混进来源类别权重。默认仍是
   `lex-v2` —— 排序是历史回归与评测基线的基准，换默认值要先有评测证据。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.rag import RAG_SOURCE_TYPES
from app.services import memory_rag
from app.services.rag_budget import (
    CONSUMER_TIER_OVERRIDES,
    DEFAULT_TIER_FRACTION,
    SOURCE_TIER_FRACTIONS,
    tier_budget,
    tier_budget_policy,
    tier_fraction,
)
from app.utils.timeutil import utcnow


def test_assistant_shares_are_unchanged_by_the_consumer_dimension():
    """引入消费方维度不得改变 assistant 的份额。"""
    for source_type in RAG_SOURCE_TYPES:
        assert tier_fraction(source_type, "assistant") == SOURCE_TIER_FRACTIONS.get(
            source_type, DEFAULT_TIER_FRACTION
        ), source_type


def test_unknown_consumer_falls_back_to_the_registered_share():
    """未登记的消费方回落到登记份额，不静默变成 0（否则该类来源永远进不来）。"""
    assert tier_fraction("memory", "brand_new_consumer") == SOURCE_TIER_FRACTIONS["memory"]
    assert tier_budget("memory", 2000, "brand_new_consumer") == tier_budget("memory", 2000)


def test_every_consumer_override_targets_a_registered_source_type():
    """覆盖表只能指向已登记来源类别 —— 否则是拼错名字的静默失效。"""
    for consumer_kind, overrides in CONSUMER_TIER_OVERRIDES.items():
        unknown = set(overrides) - set(RAG_SOURCE_TYPES)
        assert not unknown, f"{consumer_kind} 覆盖了未知来源类别: {sorted(unknown)}"
        # 份额必须 < 1：任何单一类别都不该占满整个预算（结构性保证，不是调参）。
        assert all(0 < fraction < 1 for fraction in overrides.values()), consumer_kind


def test_policy_records_the_consumer_kind_and_its_overrides():
    """审计必须能看出实际用了哪一套份额。"""
    policy = tier_budget_policy(2000, "steward")
    assert policy["consumer_kind"] == "steward"
    assert policy["consumer_overrides"] == CONSUMER_TIER_OVERRIDES["steward"]
    assistant = tier_budget_policy(2000, "assistant")
    assert assistant["consumer_overrides"] == {}


def test_recency_buckets_are_ordered_and_bounded():
    """桶边界单调：越新分值越高，且任何时间戳都落在声明值域内。"""
    now = utcnow()
    weights = [
        memory_rag._recency_weight(now - timedelta(days=days), now) for days in (0, 3, 10, 60, 400)
    ]
    assert weights == sorted(weights, reverse=True), weights
    declared = {weight for _days, weight in memory_rag.RECENCY_BUCKETS}
    declared.add(memory_rag.RECENCY_WEIGHT_STALE)
    assert set(weights) <= declared
    # 缺失时间戳按最旧处理：拿不到时间不能反而被抬高。
    assert memory_rag._recency_weight(None, now) == memory_rag.RECENCY_WEIGHT_STALE


def test_future_timestamp_does_not_exceed_the_freshest_bucket():
    """时钟偏差不得造出比最新桶更高的分值。"""
    now = utcnow()
    assert memory_rag._recency_weight(now + timedelta(days=365), now) == max(
        weight for _days, weight in memory_rag.RECENCY_BUCKETS
    )


def _hit(chunk_id: int, *, updated_days_ago: float, source_type: str = "memory"):
    now = utcnow()
    return memory_rag.RAGHit(
        document_id=1,
        chunk_id=chunk_id,
        source_id="same-doc",
        text="西湖区 杭州",
        token_estimate=4,
        rank=0.0,
        citation_handle=f"rag:1:r1:c{chunk_id}",
        scope="private",
        sensitivity="normal",
        revision=1,
        source_type=source_type,
        chunk_index=chunk_id,
        source_updated_at=now - timedelta(days=updated_days_ago),
    )


def test_v3_prefers_the_fresher_source_at_equal_relevance():
    """`lex-v3` 的核心行为：同等相关度下新近更新者优先。"""
    stale = _hit(1, updated_days_ago=400)
    fresh = _hit(2, updated_days_ago=1)
    ordered = memory_rag.rank_candidates(
        [stale, fresh],
        branch_hits={},
        rank_version=memory_rag.RANK_VERSION_RECENCY,
        limit=10,
        query_terms=["西湖区"],
    )
    assert [hit.chunk_id for hit in ordered] == [2, 1]


def test_v2_ignores_recency_so_the_default_is_unchanged():
    """`lex-v2`（默认）不受新鲜度影响 —— 换默认值要有评测证据，不能顺手换。"""
    stale = _hit(1, updated_days_ago=400)
    fresh = _hit(2, updated_days_ago=1)
    ordered = memory_rag.rank_candidates(
        [stale, fresh],
        branch_hits={},
        rank_version=memory_rag.RANK_VERSION_DEFAULT,
        limit=10,
        query_terms=["西湖区"],
    )
    # v2 的次级键是 chunk_index：chunk 1 在前。
    assert [hit.chunk_id for hit in ordered] == [1, 2]


def test_recency_never_overrides_source_type_priority():
    """新鲜度是**类别之内**的信号：旧记忆仍应排在新的公共亲缘之前。"""
    old_memory = _hit(1, updated_days_ago=400, source_type="memory")
    new_kinship = _hit(2, updated_days_ago=0, source_type="public_kinship")
    ordered = memory_rag.rank_candidates(
        [new_kinship, old_memory],
        branch_hits={},
        rank_version=memory_rag.RANK_VERSION_RECENCY,
        limit=10,
        query_terms=["西湖区"],
    )
    assert [hit.source_type for hit in ordered] == ["memory", "public_kinship"]


def test_ranking_is_reproducible_for_a_fixed_now():
    """同一份数据 + 同一 `ranking_now` 必须给出同一顺序（可审计的前提）。"""
    now = utcnow()
    hits = [_hit(index, updated_days_ago=index * 20) for index in range(1, 6)]
    first = memory_rag.rank_candidates(
        hits,
        branch_hits={},
        rank_version=memory_rag.RANK_VERSION_RECENCY,
        limit=10,
        query_terms=["西湖区"],
        ranking_now=now,
    )
    second = memory_rag.rank_candidates(
        list(reversed(hits)),
        branch_hits={},
        rank_version=memory_rag.RANK_VERSION_RECENCY,
        limit=10,
        query_terms=["西湖区"],
        ranking_now=now,
    )
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]


def test_unknown_rank_version_is_refused():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        memory_rag.rank_candidates([], branch_hits={}, rank_version="lex-v9", limit=10)
    assert excinfo.value.status_code == 422
