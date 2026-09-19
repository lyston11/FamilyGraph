"""管家候选负向判据的确定性回归（09-19-steward-recommendation-correctness）。

覆盖：直接亲子（双向）、收养/继亲、生物祖先长链（双向）、环输入、无冲突对照、
guardian/spouse/partner 不构成同辈排斥，以及预算 fail-closed。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services import steward_candidate_policy as policy


@dataclass(frozen=True)
class _Fact:
    fact_type: str
    subject_user_id: int
    object_user_id: int


def _index(*facts: _Fact) -> policy.CandidateConflictIndex:
    return policy.build_conflict_index_from_facts(facts)


def test_direct_parent_conflicts_in_both_directions() -> None:
    index = _index(_Fact("biological_parent", 1, 3))
    assert index.sibling_conflict_reason(1, 3) == policy.CONFLICT_PARENT_CHILD
    assert index.sibling_conflict_reason(3, 1) == policy.CONFLICT_PARENT_CHILD
    # 兄弟本人之间没有冲突（父母→两个孩子的直接边不是这对的冲突）
    two_children = _index(_Fact("biological_parent", 1, 2), _Fact("biological_parent", 1, 3))
    assert two_children.sibling_conflict_reason(2, 3) is None


@pytest.mark.parametrize("fact_type", ["adoptive_parent", "step_parent"])
def test_adoptive_and_step_parent_also_suppress(fact_type: str) -> None:
    """模型不得把收养/继亲亲子改写成同辈；这是推荐抑制，不是关系互斥。"""
    index = _index(_Fact(fact_type, 1, 3))
    assert index.sibling_conflict_reason(1, 3) == policy.CONFLICT_PARENT_CHILD
    assert index.sibling_conflict_reason(3, 1) == policy.CONFLICT_PARENT_CHILD


def test_biological_ancestry_conflicts_in_both_directions() -> None:
    index = _index(_Fact("biological_parent", 1, 2), _Fact("biological_parent", 2, 3))
    assert index.sibling_conflict_reason(1, 3) == policy.CONFLICT_BIOLOGICAL_ANCESTRY
    assert index.sibling_conflict_reason(3, 1) == policy.CONFLICT_BIOLOGICAL_ANCESTRY


def test_biological_ancestry_walks_multiple_generations() -> None:
    index = _index(
        _Fact("biological_parent", 1, 2),
        _Fact("biological_parent", 2, 3),
        _Fact("biological_parent", 3, 4),
    )
    assert index.sibling_conflict_reason(1, 4) == policy.CONFLICT_BIOLOGICAL_ANCESTRY
    # 同一祖先的两支后代之间仍是合法同辈
    branched = _index(
        _Fact("biological_parent", 1, 2),
        _Fact("biological_parent", 2, 4),
        _Fact("biological_parent", 2, 5),
    )
    assert branched.sibling_conflict_reason(4, 5) is None


def test_mixed_adoptive_chain_is_not_treated_as_blood_ancestry() -> None:
    """收养/继亲/监护混合长路径不归约为血亲，不落入祖先规则。"""
    index = _index(
        _Fact("biological_parent", 1, 2),
        _Fact("adoptive_parent", 2, 3),
    )
    assert index.sibling_conflict_reason(1, 3) is None


def test_unrelated_facts_do_not_conflict() -> None:
    index = _index(_Fact("spouse", 1, 2), _Fact("partner", 3, 4))
    assert index.sibling_conflict_reason(1, 2) is None
    assert index.sibling_conflict_reason(7, 8) is None


@pytest.mark.parametrize("fact_type", ["guardian", "spouse", "partner"])
def test_non_parent_kinds_do_not_suppress_siblings(fact_type: str) -> None:
    """不同类型关系本身不构成同辈矛盾：不引入「任意不同关系互斥」假设。"""
    index = _index(_Fact(fact_type, 1, 2))
    assert index.sibling_conflict_reason(1, 2) is None


def test_cycles_and_self_pairs_are_safe() -> None:
    cyclic = _index(_Fact("biological_parent", 1, 2), _Fact("biological_parent", 2, 1))
    assert cyclic.sibling_conflict_reason(1, 2) == policy.CONFLICT_PARENT_CHILD
    # 自环不产生无限循环，也不把同一人判为自身同辈
    self_loop = _index(_Fact("biological_parent", 1, 1))
    assert self_loop.sibling_conflict_reason(1, 1) is None


def test_non_sibling_kinds_are_not_evaluated() -> None:
    index = _index(_Fact("biological_parent", 1, 3))
    assert index.conflicts(relation_kind="spouse", subject_user_id=1, object_user_id=3) is None
    assert (
        index.conflicts(relation_kind="direct_sibling", subject_user_id=1, object_user_id=3)
        == policy.CONFLICT_PARENT_CHILD
    )


def test_malformed_rows_are_skipped() -> None:
    index = policy.build_conflict_index_from_facts(
        [
            _Fact("biological_parent", 1, 2),
            {"fact_type": "biological_parent", "subject_user_id": "1", "object_user_id": 2},
        ]
    )
    assert index.sibling_conflict_reason(1, 2) == policy.CONFLICT_PARENT_CHILD
    assert index.sibling_conflict_reason(1, 3) is None


def test_ancestry_budget_exhaustion_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """超出预算不得当作「已证明无冲突」，按冲突处理（fail-closed）。"""
    monkeypatch.setattr(policy, "MAX_ANCESTRY_NODES", 3)
    facts = [_Fact("biological_parent", node + 1, node) for node in range(1, 10)]
    index = _index(*facts)
    assert index.sibling_conflict_reason(10, 1) == policy.CONFLICT_ANCESTRY_BUDGET_EXCEEDED


def test_empty_index_is_permissive() -> None:
    assert policy.CandidateConflictIndex.empty().sibling_conflict_reason(1, 2) is None
