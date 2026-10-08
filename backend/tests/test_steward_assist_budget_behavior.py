"""行为级证明：四类辅助在预算内都不得被结构性饿死。

## 为什么需要行为级，而不只是结构断言

`test_steward_assist_budget_fairness.py` 断言源码结构（两遍预留、保底为 1、
`MAX_CARDS_PER_JOB` < 预算）。但**结构对不代表名额真的分到了每一类**：如果
`candidates_for` 对某类返回空、或去重集合提前吃掉 subject，结构断言仍会通过而
实际行为没变。

## 实测背景（生产，2026-10-08）

预算是 **per job** 的（默认 6），而 explanation **按卡逐个**预留（每张卡一个
attempt）。`explanation 5 + ranking 1 = 6` 之后，terminology 与 candidate
**结构上**拿不到名额——不是被抢占，而是轮到它们时预算已为 0。实测 22 次
terminology 中 16 次 `budget_exhausted`。

本测试直接驱动 `_reserve_plan_attempts` 的**名额分配**，用一个「四类都有活」的
栅栏，断言每一类都拿到至少一个名额，并断言 explanation 不能吃掉全部预算。
"""

from __future__ import annotations

import pytest

from app import config
from app.services import steward_assist
from app.utils.timeutil import utcnow


class _FakeTarget:
    """ranking/explanation 目标卡：只需 `.id` 与 `.kind`。"""

    def __init__(self, id_: int) -> None:
        self.id = id_
        self.kind = "relation"


class _FakePlan:
    def __init__(self, fence: dict) -> None:
        self.id = 1
        self.space_id = 1
        self.policy_version = "test"
        self.fence_json = fence
        self.deadline_at = utcnow()


class _FakeJob:
    def __init__(self) -> None:
        self.id = 1
        self.space_id = 1


@pytest.fixture
def _patched(monkeypatch):
    """把预留依赖全部替换为记录器，只测**名额分配**逻辑。"""
    calls: list[tuple[str, str]] = []
    budget_box: dict[str, dict[str, int]] = {}

    def fake_reserve(
        db,
        *,
        plan,
        job,
        kind,
        subject_key,
        user_content,
        runtime,
        budget,
        now,
        seq_counters,
        viewer_account_id=None,
    ):
        # 模拟真实预算门：超过上限就不再扣预算（真实实现会落 skipped 行）。
        max_calls = config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB
        if budget["calls"] >= max_calls:
            return
        calls.append((kind, subject_key))
        budget["calls"] += 1

    monkeypatch.setattr(steward_assist, "_reserve_attempt", fake_reserve)
    monkeypatch.setattr(steward_assist, "_advance_kind_cursor", lambda *a, **k: None)
    monkeypatch.setattr(steward_assist, "_mark_terminology_reserved", lambda *a, **k: None)
    monkeypatch.setattr(steward_assist, "_visible_context", lambda db, space_id: object())
    monkeypatch.setattr(steward_assist, "candidate_user_content", lambda *a, **k: "c")
    monkeypatch.setattr(steward_assist, "_explanation_user_content", lambda *a, **k: "e")
    monkeypatch.setattr(steward_assist, "_next_seq", lambda *a, **k: 1)
    # ranking 组需要 >= 2 张卡才会被预留。
    monkeypatch.setattr(
        steward_assist, "_ranking_targets", lambda db, ids: [_FakeTarget(i) for i in ids]
    )
    monkeypatch.setattr(
        steward_assist, "_explanation_targets", lambda db, ids: [_FakeTarget(i) for i in ids]
    )
    monkeypatch.setattr(steward_assist.steward_guard, "project_ranking_input", lambda *a, **k: "r")
    monkeypatch.setattr(steward_assist, "terminology_target_retryable", lambda *a, **k: True)

    from app.services import steward_terminology

    monkeypatch.setattr(steward_terminology, "project_terminology_input", lambda *a, **k: "t")
    return calls, budget_box


def _fence_with_all_four_kinds(explain_cards: int, terminology_groups: int) -> dict:
    return {
        "kinds": ["candidate", "ranking", "explanation", "terminology"],
        "cards": [],
        "ranking_groups": [{"recipient_account_id": 1, "card_ids": [10, 11]}],
        "explain_ids": list(range(1, explain_cards + 1)),
        "terminology_groups": [
            {
                "viewer_account_id": 1,
                "root_user_id": 1,
                "digest": f"d{i}",
                "targets": [
                    {
                        "target_user_id": i,
                        "projection_id": None,
                        "semantic_hash": f"s{i}",
                        "request_hash": f"r{i}",
                    }
                ],
            }
            for i in range(terminology_groups)
        ],
    }


def test_every_kind_with_work_gets_a_slot(db_session, monkeypatch, _patched):
    """四类都有活 → 四类都必须拿到至少一个名额。

    修复前：explanation 按卡逐个预留，`MAX_CARDS_PER_JOB`=5 时它加 ranking 就吃满 6 个
    名额，terminology 与 candidate 落 `budget_exhausted`。
    """
    calls, _ = _patched
    fence = _fence_with_all_four_kinds(explain_cards=5, terminology_groups=2)
    plan = _FakePlan(fence)
    budget = {"calls": 0, "tokens": 0}

    steward_assist._reserve_plan_attempts(
        db_session,
        plan=plan,
        job=_FakeJob(),
        kinds=list(steward_assist.ASSIST_KINDS),
        fence=fence,
        runtime=None,
        budget=budget,
        now=utcnow(),
    )

    by_kind: dict[str, int] = {}
    for kind, _subject in calls:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    print(f"  分配={by_kind} 总={budget['calls']}")

    starved = [k for k in steward_assist.ASSIST_KINDS if by_kind.get(k, 0) == 0]
    assert (
        not starved
    ), f"以下 kind 有活却一个名额都没拿到（被其它 kind 挤掉）：{starved}；分配={by_kind}"
    assert budget["calls"] <= config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB


def test_explanation_cannot_take_the_whole_budget(db_session, monkeypatch, _patched):
    """即使 explanation 有大量卡，它也不得占满预算。

    这是「保底名额」与「MAX_CARDS_PER_JOB 上限」共同保证的性质：explanation 的
    卡上限必须小于每 job 预算，否则单类可以独占。
    """
    calls, _ = _patched
    fence = _fence_with_all_four_kinds(explain_cards=50, terminology_groups=2)
    plan = _FakePlan(fence)
    budget = {"calls": 0, "tokens": 0}

    steward_assist._reserve_plan_attempts(
        db_session,
        plan=plan,
        job=_FakeJob(),
        kinds=list(steward_assist.ASSIST_KINDS),
        fence=fence,
        runtime=None,
        budget=budget,
        now=utcnow(),
    )

    explain = sum(1 for kind, _ in calls if kind == "explanation")
    print(f"  explanation={explain} 总={budget['calls']}")
    assert explain < config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB, (
        f"explanation 占了 {explain}/{config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB} 个名额——"
        "单类独占预算，其它三类会被结构性饿死"
    )
    # 且必须给其它三类留出名额。
    assert budget["calls"] == config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB


class _AnyAccount:
    """`db.get(Account, id)` 的替身：任何 id 都返回非 None。

    真实实现需要 viewer Account 存在；本测试只关心名额分配，因此让该查询恒成立。
    """

    def __init__(self, **kwargs) -> None:
        pass


@pytest.fixture(autouse=True)
def _patch_db_get(monkeypatch):
    """`candidates_for` 里的 `db.get(Account, ...)` 必须返回非 None。"""
    from sqlalchemy.orm import Session

    original = Session.get

    def get(self, entity, ident, *args, **kwargs):
        if getattr(entity, "__name__", "") == "Account":
            return _AnyAccount()
        return original(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", get)
