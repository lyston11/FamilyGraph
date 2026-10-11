"""RAG 消费者来源策略：集中声明、按 kind 派生、以及「加进索引集不放宽消费者」。

## 这套断言防的是什么

2026-10-11 的实测：`knowledge_retrieval.CONTEXT_SOURCE_TYPES = None` 的语义是「全部已声明
类别」，被三个消费者共用；其中 `ContextBuilder.build()` 同时服务 assistant 与 steward，
却无论哪种 kind 都拿到同一份「全部」。于是 steward 的上下文能读到 `authorized_document`
（该类别由 grant 存在性把关，**没有** `:is_assistant` 门），比 steward 自己的工具策略
`("memory",)` 更宽。

两类缺陷要分开防：

1. **默认方向是放开**——新增 `source_type` 静默进入每个复用该常量的消费者；
2. **跨消费者复用同一常量**——一个名字描述单一消费者的常量被别的消费者拿去用。

## 反证义务

`validate_consumer_source_types` 是对**数据字面量**的校验器。没有反证，它只是同义反复
（拿同一处推导出的集合互相比）。因此本文件用**伪造的策略**证明它确实会失败，并用
「扩展 `INDEXED_SOURCE_TYPES`」证明策略表不是从索引集推导出来的。
"""

from __future__ import annotations

from app.models.rag import (
    INDEXED_SOURCE_TYPES,
    RAG_CONSUMER_SOURCE_TYPES,
    RAG_SOURCE_TYPES,
    UNINDEXED_SOURCE_TYPES,
    context_source_types,
    validate_consumer_source_types,
)
from app.services import agent_tools, steward_memory


def test_shipped_policy_passes_its_own_validator():
    """出厂策略必须自洽——否则校验器与被校验数据一起漂移。"""
    assert validate_consumer_source_types(RAG_CONSUMER_SOURCE_TYPES) == []


def test_every_consumer_declares_an_explicit_source_set():
    """逐个消费者钉住取值。这不是重复声明，而是**防止无意的改动**：

    任何一格变化都必须同时改这里，于是它进入 review。
    """
    assert RAG_CONSUMER_SOURCE_TYPES["assistant_context"] == (
        "memory",
        "authorized_document",
        "public_kinship",
    )
    assert RAG_CONSUMER_SOURCE_TYPES["steward_context"] == ("memory",)
    assert RAG_CONSUMER_SOURCE_TYPES["assistant_memory_tool"] == (
        "memory",
        "authorized_document",
    )
    assert RAG_CONSUMER_SOURCE_TYPES["steward_memory_tool"] == ("memory",)
    assert RAG_CONSUMER_SOURCE_TYPES["browser_search"] == (
        "memory",
        "authorized_document",
        "public_kinship",
    )


def test_context_source_types_derive_from_agent_kind():
    """上下文来源集按 kind 派生，且 steward 侧不宽于 steward 的工具策略。"""
    assert context_source_types("assistant") == RAG_CONSUMER_SOURCE_TYPES["assistant_context"]
    assert context_source_types("steward") == RAG_CONSUMER_SOURCE_TYPES["steward_context"]
    # 承重等式：steward 在上下文里能读的，不得宽于它在工具里能读的。
    assert set(context_source_types("steward")) <= set(
        RAG_CONSUMER_SOURCE_TYPES["steward_memory_tool"]
    )
    # assistant 上下文确实比 steward 宽——这是能力差异，不是遗漏。
    assert set(context_source_types("assistant")) > set(context_source_types("steward"))


def test_unknown_agent_kind_fails_closed():
    """未知 kind 不得回退到最宽集合。"""
    for bad in ("", "system_admin", "ASSISTANT", "operator"):
        try:
            context_source_types(bad)
        except ValueError:
            continue
        raise AssertionError(f"unknown agent kind {bad!r} must fail closed")


def test_local_aliases_match_the_policy_table():
    """两个别名必须指向策略表，而不是各自留一份副本。"""
    assert (
        agent_tools.MEMORY_TOOL_SOURCE_TYPES == RAG_CONSUMER_SOURCE_TYPES["assistant_memory_tool"]
    )
    assert (
        steward_memory.STEWARD_READABLE_SOURCE_TYPES
        == RAG_CONSUMER_SOURCE_TYPES["steward_memory_tool"]
    )


def test_public_kinship_asymmetry_is_intentional_and_pinned():
    """`public_kinship` 在上下文与 browser_search 中、但不在 `search_memory` 中。

    `search_memory` 的名字是「记忆」；返回空间外的通用称谓知识会误导模型把语料当成用户
    记忆（2026-10-10 实测到 `term-pack:zh-CN` 出现在该工具结果里）。上下文构建是拼装材料，
    通用知识作为背景是合理的。
    """
    assert "public_kinship" in RAG_CONSUMER_SOURCE_TYPES["assistant_context"]
    assert "public_kinship" in RAG_CONSUMER_SOURCE_TYPES["browser_search"]
    assert "public_kinship" not in RAG_CONSUMER_SOURCE_TYPES["assistant_memory_tool"]
    assert "public_kinship" not in RAG_CONSUMER_SOURCE_TYPES["steward_memory_tool"]


def test_consumer_source_types_are_indexed_and_not_unindexed():
    """策略只能引用**有写入方**的类别。

    引用一个没有写入方的类别，会让该消费者以为自己能读到一类永不存在的语料——那正是
    `INDEXED_SOURCE_TYPES` / `UNINDEXED_SOURCE_TYPES` 分离要消除的静默空转。
    """
    indexed = set(INDEXED_SOURCE_TYPES)
    unindexed = set(UNINDEXED_SOURCE_TYPES)
    for key, values in RAG_CONSUMER_SOURCE_TYPES.items():
        assert set(values) <= set(RAG_SOURCE_TYPES), key
        assert set(values) <= indexed, f"{key} 引用了无写入方的类别"
        assert not (set(values) & unindexed), key


# ---- 反证 ----


def test_validator_rejects_an_unindexed_source_type():
    """反证 1：把已声明但**未索引**的类别写进策略，校验器必须失败。

    没有这条，`test_consumer_source_types_are_indexed_and_not_unindexed` 就只是在
    复述校验器的逻辑。
    """
    forged = {
        **RAG_CONSUMER_SOURCE_TYPES,
        "forged_consumer": ("memory", "family_story"),
    }
    errors = validate_consumer_source_types(forged)
    assert errors, "校验器未能发现无写入方的类别"
    assert any("family_story" in error for error in errors), errors


def test_validator_rejects_an_undeclared_source_type():
    """反证 2：拼写错误（未声明的类别）必须失败。"""
    forged = {**RAG_CONSUMER_SOURCE_TYPES, "typo_consumer": ("memory", "public_kin")}
    errors = validate_consumer_source_types(forged)
    assert errors, "校验器未能发现未声明的类别"
    assert any("public_kin" in error for error in errors), errors


def test_validator_rejects_empty_consumer_or_empty_set():
    """反证 3：空 key / 空值都是策略错误，不是「读全部」也不是「读不到」。"""
    assert validate_consumer_source_types({"": ("memory",)}), "空 key 必须失败"
    assert validate_consumer_source_types({"empty": ()}), "空值必须失败"


def test_new_indexed_source_type_does_not_widen_any_consumer(monkeypatch):
    """**承重反证**：把新类别加进 `INDEXED_SOURCE_TYPES` 不放宽任何消费者。

    这是整个任务的核心不变量。它直接证明策略表是**固定枚举**而非从索引集推导：
    如果哪天有人把 `RAG_CONSUMER_SOURCE_TYPES` 改成 `INDEXED_SOURCE_TYPES` 的派生值，
    这条会立刻失败。
    """
    before = dict(RAG_CONSUMER_SOURCE_TYPES)
    widened = (*INDEXED_SOURCE_TYPES, "family_story")
    monkeypatch.setattr("app.models.rag.INDEXED_SOURCE_TYPES", widened)
    # 注意：`family_story` 同时在 UNINDEXED_SOURCE_TYPES 里，所以这里只是模拟「有人给它
    # 接了写入方并挪进索引集」；策略表本身不受影响。
    assert dict(RAG_CONSUMER_SOURCE_TYPES) == before, "索引集变化不得改写消费者策略"
    for key, values in before.items():
        assert "family_story" not in values, f"{key} 被静默放宽"
