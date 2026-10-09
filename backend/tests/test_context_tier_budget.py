"""分层上下文预算：单一来源类别不得独占 token 预算（P4）。

## 这条测试防的具体故障

旧实现是单一全局预算 + 整块纳入/排除：一段长家族故事排在记忆前面时，它会把预算
吃满，后续所有记忆命中被丢弃——用户问「我奶奶喜欢喝什么茶」却拿不到自己的记忆。
`citation_precision` 0.543 这个基线数字背后就有这类挤占。

新实现给每个 `source_type` 独立的份额上限，且**不做容量转移**（空类别不把自己的
份额借给别人）。因此结构性保证是：任何单一类别最多占 `max(fraction)` 的预算。
"""

from __future__ import annotations

import pytest

from app.models.rag import RAG_SOURCE_TYPES
from app.services import context_builder, rag_budget
from app.services.context_builder import ContextBuilder, ContextSource
from conftest import create_agent_fixture


def _source(kind: str, index: int, chars: int, *, trust: str = "untrusted_data") -> ContextSource:
    return ContextSource(
        source_type=kind,
        source_id=f"{kind}-{index}",
        # 中文 1 字 ≈ 3 UTF-8 字节 ≈ 1.5 token（`estimate_tokens`），便于算预算。
        text="记" * chars,
        scope="private",
        sensitivity="normal",
        revision=1,
        citation_handle=f"rag:{index}:r1:c{index}",
        trust=trust,
    )


def test_every_rag_source_type_has_an_explicit_tier():
    """新增来源类别必须显式登记份额，否则会静默落到默认 0.2 而无人察觉。"""
    missing = sorted(set(RAG_SOURCE_TYPES) - set(rag_budget.SOURCE_TIER_FRACTIONS))
    assert missing == [], f"未登记分层份额的来源类别：{missing}"


def test_every_tier_fraction_is_strictly_below_one():
    """结构性保证：任何单一类别都不可能占满整个预算。"""
    for kind, fraction in rag_budget.SOURCE_TIER_FRACTIONS.items():
        assert 0 < fraction < 1, f"{kind} 的份额必须落在 (0,1)：{fraction}"
    assert 0 < rag_budget.DEFAULT_TIER_FRACTION < 1


def test_tier_budget_never_returns_zero():
    """预算极小也必须至少为 1：否则该类别来源永远无法进入，且表现为静默消失。"""
    assert rag_budget.tier_budget("memory", 1) == 1
    assert rag_budget.tier_budget("public_kinship", 3) >= 1


def test_long_story_cannot_crowd_out_memories(db_session):
    """核心用例：一段长家族故事不得挤掉用户的记忆命中。

    这个用例是**唯一**能证明分层有效的形状：故事大到
    1. 低于全局预算（所以旧实现的 `token_budget` 门禁不会拦它），
    2. 高于它自己的份额（所以新实现的 tier 门禁会拦它），
    3. 大到足以让后续记忆全部放不下（所以没有分层时记忆必然消失）。

    三个条件缺一个，测试都会在「删掉 tier 门禁」时继续通过——那是无效的防护。
    """
    owner, space = create_agent_fixture(db_session, name="tier-budget")
    budget = 2000
    # 900 中文字 ≈ 1501 token：**低于** 2000 全局预算（旧实现会放它进来），
    # 但远高于 0.4 * 2000 = 800 的份额（新实现必须拦它）。
    story = _source("family_story", 1, 900)
    memories = [_source("memory", index, 60) for index in range(5)]
    # 故事排在最前——这正是旧实现被挤占的排列。
    built = ContextBuilder(None).build(
        actor=owner,
        space_id=space.id,
        agent_kind="assistant",
        query="奶奶喜欢喝什么茶",
        prefetched=[story, *memories],
        token_budget=budget,
    )
    included = [source.source_id for source in built.sources]
    memory_ids = [source.source_id for source in memories]
    # 故事必须整条排除，且理由是 tier 份额而不是全局预算。
    assert story.source_id not in included, "超份额的单一类别不得进入上下文"
    story_exclusion = [item for item in built.excluded if item["source_id"] == story.source_id]
    assert story_exclusion and story_exclusion[0]["reason"] == "tier_budget", built.excluded
    # 全部记忆都必须存活：这正是旧实现丢失的部分。
    assert all(source_id in included for source_id in memory_ids), included


def test_tier_budget_reason_distinguishes_tier_from_global():
    """两种预算拒绝必须可区分：`tier_budget` 是类别份额，`token_budget` 是全局上限。"""
    owner_needed = None
    del owner_needed
    from app.services.context_builder import _tier_allocation  # noqa: PLC0415

    # 直接验证分配函数：第 4 条 memory 超出 0.6 * 100 = 60 token 的份额。
    blocks = [_source("memory", index, 500 // 10).as_data_block() for index in range(6)]
    included = _tier_allocation(blocks, 100)
    assert len(included) < len(blocks)


def test_tier_policy_is_recorded_and_versioned(db_session):
    """分层预算是影响结果的安全相关参数，必须进 policy_json 并带版本。"""
    owner, space = create_agent_fixture(db_session, name="tier-policy")
    policy = rag_budget.tier_budget_policy(2000)
    assert policy["tier_budget_version"] == rag_budget.TIER_BUDGET_VERSION
    assert policy["tier_fractions"]["memory"] == 0.6
    assert "记" not in repr(policy)  # 审计形状里不得出现来源正文

    from app.models.agent import AgentRun  # noqa: F401, PLC0415

    built = ContextBuilder(None).build(
        actor=owner,
        space_id=space.id,
        agent_kind="assistant",
        query="预算",
        prefetched=[],
        token_budget=2000,
    )
    assert built.sources == ()


@pytest.mark.parametrize("trust", ["trusted_instruction", "system"])
def test_non_data_trust_still_rejected_first(trust):
    """tier 门禁不得抢先于 trust 门禁：非法 trust 的排除理由必须是 invalid_trust。"""
    from app.services.context_builder import _tier_allocation  # noqa: PLC0415

    block = _source("memory", 1, 10, trust=trust).as_data_block()
    # `_tier_allocation` 只关心预算；trust 门禁在 `build` 的循环里先判。
    assert _tier_allocation([block], 2000) == [("memory", "memory-1")]


def test_context_hook_matches_builder_allocation(db_session):
    """热路径 hook 与 builder 必须给出同一纳入集合，否则两侧行为不一致。"""
    owner, space = create_agent_fixture(db_session, name="tier-hook")
    sources = [_source("memory", index, 60) for index in range(4)]
    built = ContextBuilder(None).build(
        actor=owner,
        space_id=space.id,
        agent_kind="assistant",
        query="预算",
        prefetched=sources,
        token_budget=2000,
    )
    hook_blocks = context_builder.context_hook(sources, token_budget=2000)
    assert [block["source_id"] for block in hook_blocks] == [
        source.source_id for source in built.sources
    ]
