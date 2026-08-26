"""ActionCard 服务层核心测试（V2.4 Block S1，纯服务层，不测 API）。

覆盖 ST-4 FSM/去重/证据版本/失效合同：
- transition_card 全合法转换 + 终态不可复活 + revision compare-and-set；
- expire_due_cards 把过期 pending/viewed/accepted 转 expired；
- supersede_card 把旧卡置 superseded_by；
- create_card 去重：同证据哈希 active/executed/dismissed 不重复出卡；
  异证据哈希插新卡并 supersede 旧活动卡；仅 expired 历史允许重新出卡。
"""

from __future__ import annotations

import fastapi
import pytest
from conftest import create_user_with_pin
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.space import FamilySpace, SpaceProfileRef
from app.models.steward import ActionCard
from app.services import action_cards
from app.utils import timeutil


def _err_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    assert isinstance(detail, dict) and "__api_error__" in detail, exc
    return str(detail["__api_error__"]["code"])


def _make_space(session: Session) -> tuple[FamilySpace, int, int]:
    """造一个 household 空间 + 两个有 SpaceProfileRef 的可见人物（recipient_account 可用）。"""
    owner = create_user_with_pin(session, "owner", "123456")
    space = FamilySpace(name="S", kind="household", owner_id=owner.id, created_at=owner.created_at)
    session.add(space)
    session.flush()
    now = timeutil.utcnow()
    subject = create_user_with_pin(session, "subj", "123456", gender="m")
    obj = create_user_with_pin(session, "obj", "123456", gender="f")
    for uid in (subject.id, obj.id):
        session.add(
            SpaceProfileRef(space_id=space.id, user_id=uid, status="active", created_at=now)
        )
    session.commit()
    return space, subject.id, obj.id


def _evidence(fact_id: int = 1, revision: int = 1) -> dict[str, object]:
    return {
        "primary_fact_id": fact_id,
        "facts": [{"id": fact_id, "type": "spouse", "revision": revision}],
        "inputs": {"subject_identity_confirmed": True},
    }


def _create(
    session: Session,
    space_id: int,
    recipient_account_id: int,
    subject_id: int,
    object_id: int,
    *,
    evidence: dict[str, object] | None = None,
    kind: str = "household_link",
) -> ActionCard:
    card, _ = action_cards.create_card(
        session,
        kind=kind,
        space_id=space_id,
        recipient_account_id=recipient_account_id,
        subject_user_id=subject_id,
        object_user_id=object_id,
        evidence_json=evidence or _evidence(),
        proposed_action_json={"action": "create_household"},
        reason_text="理由",
        now=timeutil.utcnow(),
    )
    session.commit()
    assert card is not None
    return card


# ---- FSM 全合法转换 ----


def test_transition_full_happy_path_accepted_executed(db_session) -> None:
    """pending→viewed→accepted→executed；每次 revision+1。"""
    space, subj, obj = _make_space(db_session)
    account_id = db_session.get(
        __import__("app.models.user", fromlist=["User"]).User, subj
    ).account.id
    card = _create(db_session, space.id, account_id, subj, obj)

    assert (card.state, card.revision) == ("pending", 1)
    action_cards.transition_card(
        db_session, card, "view", expected_revision=card.revision, actor_account_id=account_id
    )
    assert card.state == "viewed" and card.revision == 2

    action_cards.transition_card(
        db_session, card, "accept", expected_revision=card.revision, actor_account_id=account_id
    )
    assert card.state == "accepted" and card.revision == 3 and card.accepted_at is not None

    action_cards.transition_card(
        db_session,
        card,
        "execute",
        expected_revision=card.revision,
        actor_account_id=account_id,
        executed_event_id=42,
    )
    assert card.state == "executed" and card.revision == 4 and card.executed_event_id == 42


def test_pending_accept_is_directly_available(db_session) -> None:
    """Inbox 的「接受」按钮可直接把 pending 卡置为 accepted。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    card = _create(db_session, space.id, account_id, subj, obj)
    action_cards.transition_card(db_session, card, "accept", expected_revision=card.revision)
    assert card.state == "accepted"
    assert card.revision == 2

    """pending→dismissed、viewed→dismissed 均可达 dismissed 终态。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    card = _create(db_session, space.id, account_id, subj, obj)
    action_cards.transition_card(db_session, card, "dismiss", expected_revision=card.revision)
    assert card.state == "dismissed"

    card2 = _create(db_session, space.id, account_id, subj, obj, evidence=_evidence(fact_id=2))
    action_cards.transition_card(db_session, card2, "view", expected_revision=card2.revision)
    action_cards.transition_card(db_session, card2, "dismiss", expected_revision=card2.revision)
    assert card2.state == "dismissed"


def test_transition_expire_from_each_active_state(db_session) -> None:
    """pending/viewed/accepted 均可 expire→expired。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    for idx, stop_at in enumerate(("pending", "viewed", "accepted"), start=1):
        card = _create(
            db_session, space.id, account_id, subj, obj, evidence=_evidence(fact_id=idx + 10)
        )
        if stop_at != "pending":
            action_cards.transition_card(db_session, card, "view", expected_revision=card.revision)
        if stop_at == "accepted":
            action_cards.transition_card(
                db_session, card, "accept", expected_revision=card.revision
            )
        action_cards.transition_card(db_session, card, "expire", expected_revision=card.revision)
        assert card.state == "expired"


# ---- 终态不可复活 + revision 不匹配 ----


@pytest.mark.parametrize("terminal_state", ["executed", "dismissed", "expired", "superseded"])
def test_terminal_state_cannot_reenter(db_session, terminal_state: str) -> None:
    """任一终态再做任何转换 → 409 CARD_INVALID_TRANSITION。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    card = _create(db_session, space.id, account_id, subj, obj, evidence=_evidence(fact_id=900))
    # 推到目标终态
    if terminal_state == "executed":
        action_cards.transition_card(db_session, card, "view", expected_revision=card.revision)
        action_cards.transition_card(db_session, card, "accept", expected_revision=card.revision)
        action_cards.transition_card(
            db_session,
            card,
            "execute",
            expected_revision=card.revision,
            executed_event_id=7,
        )
    elif terminal_state == "dismissed":
        action_cards.transition_card(db_session, card, "dismiss", expected_revision=card.revision)
    elif terminal_state == "expired":
        action_cards.transition_card(db_session, card, "expire", expected_revision=card.revision)
    else:  # superseded
        action_cards.transition_card(
            db_session, card, "supersede", expected_revision=card.revision, superseded_by_id=888
        )
    assert card.state == terminal_state

    for action in ("view", "accept", "execute", "dismiss", "expire", "supersede"):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            action_cards.transition_card(
                db_session,
                card,
                action,
                expected_revision=card.revision,
                executed_event_id=7,
                superseded_by_id=888,
            )
        assert _err_code(exc_info.value) == "CARD_INVALID_TRANSITION"


def test_revision_mismatch_returns_conflict(db_session) -> None:
    """compare-and-set：expected_revision 不匹配 → 409 CARD_REVISION_CONFLICT。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    card = _create(db_session, space.id, account_id, subj, obj)
    with pytest.raises(fastapi.HTTPException) as exc_info:
        action_cards.transition_card(db_session, card, "view", expected_revision=999)
    assert _err_code(exc_info.value) == "CARD_REVISION_CONFLICT"


def test_execute_requires_executed_event_id(db_session) -> None:
    """execute 未携带 executed_event_id → 422 CARD_INVALID_TRANSITION。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    card = _create(db_session, space.id, account_id, subj, obj)
    action_cards.transition_card(db_session, card, "view", expected_revision=card.revision)
    action_cards.transition_card(
        db_session, card, "accept", expected_revision=card.revision, actor_account_id=account_id
    )
    with pytest.raises(fastapi.HTTPException) as exc_info:
        action_cards.transition_card(
            db_session,
            card,
            "execute",
            expected_revision=card.revision,
            actor_account_id=account_id,
        )
    assert _err_code(exc_info.value) == "CARD_INVALID_TRANSITION"
    assert exc_info.value.status_code == 422


# ---- expire_due_cards ----


def test_expire_due_cards_transitions_each_active_state(db_session) -> None:
    """过期扫描：pending/viewed/accepted 过期 → expired（不触碰未过期与终态卡）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    now = timeutil.utcnow()
    # 三张过期卡（各停在一个活动态）。用三个独立 object 避免 dedupe 冲突。
    objs = []
    for i in range(3):
        o = create_user_with_pin(db_session, f"o{i}", "123456", gender="f")
        db_session.add(
            SpaceProfileRef(space_id=space.id, user_id=o.id, status="active", created_at=now)
        )
        objs.append(o.id)
    db_session.commit()
    c_pending = _create(db_session, space.id, account_id, subj, objs[0], evidence=_evidence(1))
    c_viewed = _create(db_session, space.id, account_id, subj, objs[1], evidence=_evidence(2))
    action_cards.transition_card(db_session, c_viewed, "view", expected_revision=c_viewed.revision)
    c_accepted = _create(db_session, space.id, account_id, subj, objs[2], evidence=_evidence(3))
    action_cards.transition_card(
        db_session, c_accepted, "view", expected_revision=c_accepted.revision
    )
    action_cards.transition_card(
        db_session, c_accepted, "accept", expected_revision=c_accepted.revision
    )
    for card in (c_pending, c_viewed, c_accepted):
        card.expires_at = now.replace(year=now.year - 1)  # 已过
    db_session.commit()
    # 一张未过期卡（不应被触碰）
    fresh_obj = create_user_with_pin(db_session, "fresh", "123456", gender="f")
    db_session.add(
        SpaceProfileRef(space_id=space.id, user_id=fresh_obj.id, status="active", created_at=now)
    )
    db_session.commit()
    c_fresh = _create(db_session, space.id, account_id, subj, fresh_obj.id, evidence=_evidence(4))
    c_fresh.expires_at = now.replace(year=now.year + 1)

    handled = action_cards.expire_due_cards(db_session, space_id=space.id, now=now)
    assert handled == 3
    states = {
        c_pending.id: c_pending.state,
        c_viewed.id: c_viewed.state,
        c_accepted.id: c_accepted.state,
        c_fresh.id: c_fresh.state,
    }
    assert states[c_pending.id] == "expired"
    assert states[c_viewed.id] == "expired"
    assert states[c_accepted.id] == "expired"
    assert states[c_fresh.id] == "pending"


# ---- supersede_card ----


def test_supersede_card_sets_reason_and_target(db_session) -> None:
    """supersede_card 记录 failed_reason 与 superseded_by_id。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    old = _create(db_session, space.id, account_id, subj, obj)
    # 新卡用不同 evidence_version 在同一 (subject,object) 会触发 create_card 自动 supersede，
    # 改用独立 object 避免该副作用，直接验证 supersede_card 系统型调用本身
    replacement_target = create_user_with_pin(db_session, "rep", "123456", gender="f")
    now = timeutil.utcnow()
    db_session.add(
        SpaceProfileRef(
            space_id=space.id, user_id=replacement_target.id, status="active", created_at=now
        )
    )
    db_session.commit()
    action_cards.supersede_card(
        db_session, old, reason="evidence_invalidated", superseded_by_id=12345
    )
    assert old.state == "superseded"
    assert old.superseded_by_id == 12345
    assert old.failed_reason == "evidence_invalidated"


# ---- create_card 去重 / 证据版本 / supersede（AC-ST3 服务层基础）----


def test_create_card_duplicate_same_evidence_active_returns_none(db_session) -> None:
    """同 dedupe_key 且同证据哈希的活动卡存在 → duplicate（不出新卡）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    ev = _evidence()
    first, outcome1 = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=obj,
        evidence_json=ev,
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    assert outcome1 == "created" and first is not None
    second, outcome2 = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=obj,
        evidence_json=ev,
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    assert outcome2 == "duplicate" and second is None
    cards = list(
        db_session.scalars(
            select(ActionCard).where(
                ActionCard.space_id == space.id, ActionCard.kind == "household_link"
            )
        )
    )
    assert len(cards) == 1


def test_create_card_duplicate_blocks_after_executed_and_dismissed(db_session) -> None:
    """executed / dismissed 同证据哈希同样抑制出新卡（不重复骚扰）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id

    # executed 路径
    card_exec = _create(db_session, space.id, account_id, subj, obj, evidence=_evidence(100))
    action_cards.transition_card(
        db_session, card_exec, "view", expected_revision=card_exec.revision
    )
    action_cards.transition_card(
        db_session, card_exec, "accept", expected_revision=card_exec.revision
    )
    action_cards.transition_card(
        db_session,
        card_exec,
        "execute",
        expected_revision=card_exec.revision,
        executed_event_id=5,
    )
    again_exec, outcome = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=obj,
        evidence_json=_evidence(100),
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    assert outcome == "duplicate" and again_exec is None

    # dismissed 路径（独立 key 用不同 object 避免与上面 dedupe 冲突）
    other = create_user_with_pin(db_session, "other", "123456", gender="m")
    now = timeutil.utcnow()
    db_session.add(
        SpaceProfileRef(space_id=space.id, user_id=other.id, status="active", created_at=now)
    )
    db_session.commit()
    card_dis = _create(db_session, space.id, account_id, subj, other.id, evidence=_evidence(200))
    action_cards.transition_card(
        db_session, card_dis, "dismiss", expected_revision=card_dis.revision
    )
    again_dis, outcome = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=other.id,
        evidence_json=_evidence(200),
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    assert outcome == "duplicate" and again_dis is None


def test_create_card_new_evidence_supersedes_old_active(db_session) -> None:
    """异证据哈希 → 插新卡并把旧活动卡置 superseded（version 单调递增）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    old = _create(db_session, space.id, account_id, subj, obj, evidence=_evidence(1, 1))
    new, outcome = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=obj,
        evidence_json=_evidence(1, 2),  # revision 变了 → 哈希不同
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    db_session.commit()
    assert outcome == "superseded_old" and new is not None
    assert new.evidence_version == old.evidence_version + 1
    db_session.refresh(old)
    assert old.state == "superseded" and old.superseded_by_id == new.id


def test_create_card_allows_new_after_only_expired_history(db_session) -> None:
    """仅剩 expired 历史时允许重新出卡（新一轮有效期）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    expired = _create(db_session, space.id, account_id, subj, obj, evidence=_evidence(1, 1))
    action_cards.transition_card(db_session, expired, "expire", expected_revision=expired.revision)
    assert expired.state == "expired"
    new, outcome = action_cards.create_card(
        db_session,
        kind="household_link",
        space_id=space.id,
        recipient_account_id=account_id,
        subject_user_id=subj,
        object_user_id=obj,
        evidence_json=_evidence(1, 1),  # 同证据哈希，但历史仅 expired
        proposed_action_json={"action": "create_household"},
        reason_text="r",
        now=timeutil.utcnow(),
    )
    assert outcome == "created" and new is not None
    assert new.evidence_version == 2  # version 继续递增


def test_create_card_rejects_unknown_kind(db_session) -> None:
    """未知 kind → 422 CARD_INVALID_TRANSITION（fail-closed）。"""
    space, subj, obj = _make_space(db_session)
    from app.models.user import User

    account_id = db_session.get(User, subj).account.id
    with pytest.raises(fastapi.HTTPException) as exc_info:
        action_cards.create_card(
            db_session,
            kind="bogus_kind",
            space_id=space.id,
            recipient_account_id=account_id,
            subject_user_id=subj,
            object_user_id=obj,
            evidence_json=_evidence(),
            proposed_action_json={"action": "x"},
            reason_text="r",
            now=timeutil.utcnow(),
        )
    assert _err_code(exc_info.value) == "CARD_INVALID_TRANSITION"


def test_dedupe_key_format_stable() -> None:
    """dedupe_key = kind:subject:object；object None 折叠为 '-'。"""
    assert action_cards.dedupe_key_for("household_link", 3, 7) == "household_link:3:7"
    assert action_cards.dedupe_key_for("lineage_request", 3, None) == "lineage_request:3:-"


def test_compute_evidence_hash_canonical_and_stable() -> None:
    """证据哈希对键序稳定、对值变化敏感。"""
    a = action_cards.compute_evidence_hash({"a": 1, "b": [2, 3]})
    b = action_cards.compute_evidence_hash({"b": [2, 3], "a": 1})
    assert a == b
    c = action_cards.compute_evidence_hash({"a": 1, "b": [2, 4]})
    assert c != a
