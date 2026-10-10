"""来源类别的「声明集」与「活跃索引集」必须一致可证。

## 这套断言防的是什么

2026-10-10 的实测：`RAG_SOURCE_TYPES` 声明五类，实际只有 `memory` 有写入方；
`SOURCE_TIER_FRACTIONS` 五条份额全部登记，因此既有的「所有 RAG_SOURCE_TYPES 都已
登记」断言**照样通过**——它只检查份额存在，不检查写入方存在。

结果是四类空转既无文档也无测试保护：任何人加一个 source_type 都会得到一个静默
空转的枚举值。本测试把「声明了但没人写」变成**显式且可证伪**的决策。

## 反证义务

没有 `test_registry_detects_an_undeclared_source_type` 这条反证，下面的一致性断言
就只是同义反复（把两个集合都从同一处推导出来，永远相等）。反证用一个真实的
`RAG_SOURCE_TYPES` 子集替换，证明断言确实会失败。
"""

from __future__ import annotations

from app.models.rag import (
    INDEXED_SOURCE_TYPES,
    RAG_SOURCE_TYPES,
    UNINDEXED_SOURCE_TYPES,
)
from app.services import rag_budget


def test_indexed_and_unindexed_partition_the_declared_set():
    indexed = set(INDEXED_SOURCE_TYPES)
    unindexed = set(UNINDEXED_SOURCE_TYPES)
    assert not (indexed & unindexed), "同一类别不能既活跃又未索引"
    assert indexed | unindexed == set(
        RAG_SOURCE_TYPES
    ), "新增/删除 source_type 必须同时更新 INDEXED_SOURCE_TYPES 或 UNINDEXED_SOURCE_TYPES"


def test_every_unindexed_source_type_states_a_reason():
    for name, reason in UNINDEXED_SOURCE_TYPES.items():
        assert reason.strip(), f"{name} 必须给出「为什么没有写入方」的理由"


def test_indexed_source_types_are_in_the_declared_set():
    assert set(INDEXED_SOURCE_TYPES) <= set(RAG_SOURCE_TYPES)


def test_indexed_source_types_have_a_tier_fraction():
    """活跃类别必须有份额；未登记会静默落到 DEFAULT_TIER_FRACTION。"""
    for name in INDEXED_SOURCE_TYPES:
        assert name in rag_budget.SOURCE_TIER_FRACTIONS, name


def test_registry_detects_an_undeclared_source_type(monkeypatch):
    """反证：从 RAG_SOURCE_TYPES 里去掉一个未登记的类别，断言必须失败。

    这里直接重放断言逻辑而不是调用被测函数，因为被测对象是**模块级常量**；
    反证的价值在于证明「不一致」这个状态是可被检测的，而不是证明某个函数会返回
    什么。把 `RAG_SOURCE_TYPES` 换成一个多出 `profile_notes` 的集合，等价于
    「有人加了枚举值但没登记」。
    """
    forged = (*RAG_SOURCE_TYPES, "profile_notes")
    indexed = set(INDEXED_SOURCE_TYPES)
    unindexed = set(UNINDEXED_SOURCE_TYPES)
    assert indexed | unindexed != set(forged), "反证失效：断言无法检测未登记的类别"
    monkeypatch.setattr("app.models.rag.RAG_SOURCE_TYPES", forged)
