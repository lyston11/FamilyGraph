"""V2 Foundation 合同测试（PRD AC-F1/AC-F2/AC-F5 + implement.md 验证清单）。

覆盖：
- 三条独立单向状态机（Account/Profile/FactReview）及互不联动
- 迁移约束逐项（CHECK 枚举、表达唯一索引、partial unique index、RESTRICT）
- owner 删除保护：RESTRICT 转译 409，空间不被级联删除
- domain_events 单调 id 与 append-only 约定
- 推荐资格与 identity_confirmed 绑定
"""

from __future__ import annotations

import pytest
from conftest import create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import text

from app.errors import extract_api_error
from app.services import identity_fsm

# ---- 状态机 ----


def test_account_claim_single_direction(db_session) -> None:
    account = create_user_with_pin(db_session, "甲", "111111", pin_must_change=True).account
    assert account.status == "managed"

    identity_fsm.claim_account(db_session, account)
    assert account.status == "claimed"
    assert account.claimed_at is not None

    with pytest.raises(HTTPException) as exc_info:
        identity_fsm.claim_account(db_session, account)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "IDENTITY_INVALID_TRANSITION"


def test_profile_confirm_single_direction_and_no_auto_linkage(db_session) -> None:
    """claimed 不自动确认 profile：两条状态机独立完成。"""
    user = create_user_with_pin(
        db_session, "乙", "222222", pin_must_change=True, profile_status="provisional"
    )
    assert user.account.status == "managed"
    assert user.profile_status == "provisional"

    # Account 认领后 Profile 保持 provisional
    identity_fsm.claim_account(db_session, user.account)
    db_session.expire_all()
    assert user.profile_status == "provisional"

    identity_fsm.confirm_profile_identity(db_session, user)
    assert user.profile_status == "identity_confirmed"
    assert user.profile_confirmed_at is not None

    with pytest.raises(HTTPException) as exc_info:
        identity_fsm.confirm_profile_identity(db_session, user)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "IDENTITY_INVALID_TRANSITION"


def test_fact_review_proposed_to_confirmed_or_disputed_terminal(db_session) -> None:
    from app.models.v2_foundation import ProfileFactReview
    from app.utils.timeutil import utcnow

    user = create_user_with_pin(db_session, "丙", "333333")
    review = ProfileFactReview(
        profile_id=user.id,
        item_type="name",
        item_ref_json={"field": "name", "value": "丙"},
        proposed_by=user.id,
        status="proposed",
        created_at=utcnow(),
    )
    db_session.add(review)
    db_session.commit()

    identity_fsm.decide_fact_review(db_session, review, "confirmed", user.id)
    assert review.status == "confirmed"
    assert review.decided_at is not None and review.decided_by == user.id

    # 终态不可再转
    with pytest.raises(HTTPException) as exc_info:
        identity_fsm.decide_fact_review(db_session, review, "disputed", user.id)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "IDENTITY_INVALID_TRANSITION"

    review2 = ProfileFactReview(
        profile_id=user.id,
        item_type="birth",
        item_ref_json={"field": "birth"},
        proposed_by=user.id,
        status="proposed",
        created_at=utcnow(),
    )
    db_session.add(review2)
    db_session.commit()
    identity_fsm.decide_fact_review(db_session, review2, "disputed", user.id)
    assert review2.status == "disputed"

    # 未知决议 422
    review3 = ProfileFactReview(
        profile_id=user.id,
        item_type="gender",
        item_ref_json={"field": "gender"},
        proposed_by=user.id,
        status="proposed",
        created_at=utcnow(),
    )
    db_session.add(review3)
    db_session.commit()
    with pytest.raises(HTTPException) as val_exc:
        identity_fsm.decide_fact_review(db_session, review3, "maybe", user.id)
    assert val_exc.value.status_code == 422


def test_recommendation_eligible_requires_identity_confirmed(db_session) -> None:
    provisional = create_user_with_pin(db_session, "丁", "444444", profile_status="provisional")
    confirmed = create_user_with_pin(db_session, "戊", "555555")
    assert identity_fsm.recommendation_eligible(provisional) is False  # AC-F2
    assert identity_fsm.recommendation_eligible(confirmed) is True


# ---- 迁移与约束（沿用 conftest 已跑真实迁移链的数据库）----


def _raw_exec(session, sql: str, params: dict | None = None):
    return session.execute(text(sql), params or {})


def test_migration_constraint_enforcement(db_session) -> None:
    """空库迁移后的 CHECK/唯一索引逐项可验证（AC-F1）。"""
    from sqlalchemy.exc import IntegrityError

    u = create_user_with_pin(db_session, "己", "666666")

    # family_spaces.kind CHECK
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO family_spaces (name, owner_id, kind, created_at)"
                " VALUES ('x', :oid, 'clan', CURRENT_TIMESTAMP)"
            ),
            {"oid": u.id},
        )
    db_session.rollback()

    # accounts.status CHECK
    from app.models.account import Account
    from app.models.user import User

    row = db_session.query(User).filter(User.id == u.id).one()
    db_session.add(Account(user_id=row.id + 900000, pin_hash="x", status="zombie"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # disclosure_preferences：scope/space 配对 CHECK
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO disclosure_preferences"
                " (profile_id, category, scope, space_id, allowed, updated_at)"
                " VALUES (:pid, 'bio', 'space', NULL, 1, CURRENT_TIMESTAMP)"
            ),
            {"pid": u.id},
        )
    db_session.rollback()

    # 全局行唯一（COALESCE 表达式索引）：第一条提交，第二条拒绝
    db_session.execute(
        text(
            "INSERT INTO disclosure_preferences"
            " (profile_id, category, scope, space_id, allowed, updated_at)"
            " VALUES (:pid, 'bio', 'global', NULL, 1, CURRENT_TIMESTAMP)"
        ),
        {"pid": u.id},
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO disclosure_preferences"
                " (profile_id, category, scope, space_id, allowed, updated_at)"
                " VALUES (:pid, 'bio', 'global', NULL, 1, CURRENT_TIMESTAMP)"
            ),
            {"pid": u.id},
        )
    db_session.rollback()


def test_ownership_transfer_single_pending_per_space(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models.space import FamilySpace
    from app.models.v2_foundation import OwnershipTransfer
    from app.utils.timeutil import utcnow

    owner = create_user_with_pin(db_session, "庚", "777777")
    heir = create_user_with_pin(db_session, "继承人", "818181")
    space = FamilySpace(name="庚家", owner_id=owner.id, kind="household", created_at=utcnow())
    db_session.add(space)
    db_session.commit()  # 先固化空间，避免后续 rollback 连带撤销
    now = utcnow()
    db_session.add(
        OwnershipTransfer(
            space_id=space.id,
            from_user=owner.id,
            to_user=heir.id,
            status="pending",
            created_at=now,
        )
    )
    db_session.add(
        OwnershipTransfer(
            space_id=space.id,
            from_user=owner.id,
            to_user=heir.id,
            status="pending",
            created_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # 非 pending 不受 partial index 限制
    db_session.add(
        OwnershipTransfer(
            space_id=space.id,
            from_user=owner.id,
            to_user=heir.id,
            status="cancelled",
            created_at=now,
        )
    )
    db_session.flush()


def test_owner_delete_blocked_space_not_cascaded(db_session, client) -> None:
    """AC-F5：删除空间所有者被阻止并引导移交；空间不被 FK 静默删除。"""
    from conftest import auth_header, login
    from fastapi.testclient import TestClient

    del TestClient
    from app.models.space import FamilySpace
    from app.utils.timeutil import utcnow

    owner = create_user_with_pin(db_session, "周owner", "919191")
    db_session.add(
        FamilySpace(name="周家", owner_id=owner.id, kind="household", created_at=utcnow())
    )
    db_session.commit()

    tokens = login(client, "周owner", "919191").json()
    r = client.delete(
        f"/api/users/{owner.id}",
        headers=auth_header(tokens),
        params={"confirm_name": "周owner"},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"]["code"] == "OWNER_TRANSFER_REQUIRED"

    # 空间仍在，所有者仍在
    db_session.expire_all()
    spaces = db_session.query(FamilySpace).filter(FamilySpace.owner_id == owner.id).count()
    assert spaces == 1
    assert db_session.query(FamilySpace).filter_by(name="周家").count() == 1


def test_owner_invitation_token_unique(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models.v2_foundation import OwnerInvitation
    from app.utils.timeutil import utcnow

    u = create_user_with_pin(db_session, "辛", "828282")
    now = utcnow()
    db_session.add(
        OwnerInvitation(
            token_hash="a" * 64,
            created_by=u.id,
            expires_at=now,
            created_at=now,
        )
    )
    db_session.add(
        OwnerInvitation(
            token_hash="a" * 64,
            created_by=u.id,
            expires_at=now,
            created_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_domain_events_monotonic_append_only(db_session) -> None:
    from app.models.v2_foundation import DomainEvent
    from app.utils.timeutil import utcnow

    u = create_user_with_pin(db_session, "壬", "939393")
    now = utcnow()
    for i in range(3):
        db_session.add(
            DomainEvent(
                type="profile.updated",
                aggregate_type="profile",
                aggregate_id=u.id,
                payload={"seq": i},
                actor_account_id=u.account.id,
                created_at=now,
            )
        )
    db_session.commit()
    rows = (
        db_session.query(DomainEvent)
        .filter(DomainEvent.aggregate_type == "profile", DomainEvent.aggregate_id == u.id)
        .order_by(DomainEvent.id)
        .all()
    )
    assert [r.payload["seq"] for r in rows] == [0, 1, 2]
    assert rows[0].id < rows[1].id < rows[2].id  # 单调递增
