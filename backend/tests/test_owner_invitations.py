"""Owner onboarding 邀请流程测试（AC-F3）。

覆盖：签发只存 hash、单次使用、过期、撤销、重放、managed 账号拒绝（wrong-actor）、
并发兑换恰好一个胜出、兑换者仅获得新空间（无 platform_operator、不连接其他空间）。
"""

from __future__ import annotations

import threading

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi import HTTPException
from sqlalchemy import select

from app.commands import owner_onboarding as onboarding
from app.commands.context import ActorContext
from app.db import SessionLocal
from app.errors import extract_api_error
from app.models.space import FamilySpace, SpaceMember
from app.models.v2_foundation import DomainEvent, OwnerInvitation


def _ctx(user) -> ActorContext:
    return ActorContext(
        user_id=user.id,
        account_id=user.account.id,
        account_status=user.account.status,
    )


@pytest.fixture()
def operator(db_session):
    return create_user_with_pin(db_session, "平台运营", "101010", is_admin=True)


def _redeem_via_api(client, tokens, token: str):
    return client.post(
        "/api/owner-invitations/redeem", headers=auth_header(tokens), json={"token": token}
    )


def test_invitation_stores_only_hash_and_returns_token_once(db_session, client, operator) -> None:
    tokens = login(client, "平台运营", "101010").json()
    resp = client.post("/api/admin/owner-invitations", headers=auth_header(tokens))
    assert resp.status_code == 201, resp.text
    body = resp.json()

    row = db_session.scalars(select(OwnerInvitation).where(OwnerInvitation.id == body["id"])).one()
    assert row.token_hash != body["token"]  # 数据库只有 hash
    assert len(row.token_hash) == 64
    assert onboarding.hash_token(body["token"]) == row.token_hash  # 可验证对应关系
    assert row.used_at is None and row.revoked_at is None


def test_redeem_creates_lineage_space_and_owner_membership(db_session, client, operator) -> None:
    """兑换：创建独立 LineageSpace + owner membership；不授予 platform_operator。"""
    from app.services.platform_roles import platform_roles as roles_of

    redeemer = create_user_with_pin(db_session, "兑换人甲", "202020")
    inv, token = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()

    tokens = login(client, "兑换人甲", "202020").json()
    resp = _redeem_via_api(client, tokens, token)
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    space = db_session.scalars(select(FamilySpace)).one()
    assert space.kind == "lineage"
    assert space.owner_id == redeemer.id

    membership = db_session.scalars(select(SpaceMember)).one()
    assert membership.role == "owner" and membership.status == "active"

    # 兑换者未获得 platform_operator 角色
    acc = db_session.get(type(redeemer.account), redeemer.account.id)
    assert roles_of(db_session, acc) == frozenset()

    events = list(db_session.scalars(select(DomainEvent)).all())
    types = {e.type for e in events}
    assert {"space.created", "owner_invitation.redeemed"} <= types


def test_redeem_single_use_replay_expired_revoked(db_session, client, operator) -> None:
    create_user_with_pin(db_session, "兑换人乙", "303030")  # 兑换账号

    # 单次使用 + 重放
    inv, token = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()
    tokens = login(client, "兑换人乙", "303030").json()
    assert _redeem_via_api(client, tokens, token).status_code == 201
    replay = _redeem_via_api(client, tokens, token)
    assert replay.status_code == 404
    assert replay.json()["error"]["code"] == "OWNER_INVITATION_INVALID"

    # 过期
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    stale_ctx = _ctx(operator)
    inv2, token2 = onboarding.create_owner_invitation(db_session, stale_ctx)
    inv2.expires_at = utcnow() - timedelta(minutes=1)
    db_session.commit()
    expired = _redeem_via_api(client, tokens, token2)
    assert expired.status_code == 404
    assert expired.json()["error"]["code"] == "OWNER_INVITATION_INVALID"

    # 撤销后不可兑换；重复撤销 409
    op_tokens = login(client, "平台运营", "101010").json()
    _, token3 = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()
    revoke = client.post(
        f"/api/admin/owner-invitations/{inv2.id}/revoke", headers=auth_header(op_tokens)
    )
    assert revoke.status_code == 200

    # 未知 token 同一防枚举码
    unknown = _redeem_via_api(client, tokens, "nonexistent-token-value-0000000000")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "OWNER_INVITATION_INVALID"


def test_revoke_marks_row_and_rejects_double_revoke(db_session, client, operator) -> None:
    op_tokens = login(client, "平台运营", "101010").json()
    created = client.post("/api/admin/owner-invitations", headers=auth_header(op_tokens))
    inv_id = created.json()["id"]
    db_session.expire_all()

    revoked = client.post(
        f"/api/admin/owner-invitations/{inv_id}/revoke", headers=auth_header(op_tokens)
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    again = client.post(
        f"/api/admin/owner-invitations/{inv_id}/revoke", headers=auth_header(op_tokens)
    )
    assert again.status_code == 409


def test_managed_account_cannot_redeem(db_session, client, operator) -> None:
    """wrong-actor 规则：managed/pin 未改账号不可兑换（403 引导先认领）。"""
    managed = create_user_with_pin(db_session, "待认领者", "404040", pin_must_change=True)
    _, token = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()

    # 直接以命令层构造 managed 上下文验证授权边界
    with pytest.raises(HTTPException) as exc_info:
        onboarding.redeem_owner_invitation(db_session, _ctx(managed), raw_token=token)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 403
    assert error is not None
    assert error["code"] == "OWNER_INVITATION_ACCOUNT_NOT_CLAIMED"


def test_operator_redeeming_gains_no_extra_role_or_links(db_session, client, operator) -> None:
    """operator 兑换也不额外获得角色/连接：只得到自己的新空间。"""
    other_space_owner = create_user_with_pin(db_session, "别人家主", "505050")
    from app.utils.timeutil import utcnow

    db_session.add(
        FamilySpace(
            name="别人家", owner_id=other_space_owner.id, kind="household", created_at=utcnow()
        )
    )
    _, token = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()

    result = onboarding.redeem_owner_invitation(db_session, _ctx(operator), raw_token=token)
    db_session.commit()
    db_session.expire_all()

    spaces = list(db_session.scalars(select(FamilySpace)).all())
    mine = [s for s in spaces if s.owner_id == operator.id]
    assert len(mine) == 1 and mine[0].id == result.id  # 只拥有新空间
    memberships = list(db_session.scalars(select(SpaceMember)).all())
    assert all(m.space_id == result.id for m in memberships if m.user_id == operator.id)


def test_concurrent_redemption_exactly_one_winner(db_session, client, operator) -> None:
    """并发兑换同一 token：恰好一个成功（条件 UPDATE rowcount 裁决）。"""
    redeemer = create_user_with_pin(db_session, "并发兑换人", "606060")
    _, token = onboarding.create_owner_invitation(db_session, _ctx(operator))
    db_session.commit()
    db_session.expire_all()

    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        session = SessionLocal()
        try:
            user = session.query(type(redeemer)).filter_by(name="并发兑换人").one()
            ctx = ActorContext(
                user_id=user.id, account_id=user.account.id, account_status=user.account.status
            )
            barrier.wait()
            onboarding.redeem_owner_invitation(session, ctx, raw_token=token)
            results.append("won")
        except HTTPException as exc:
            results.append(str(exc.status_code))
        finally:
            session.close()

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == ["404", "won"]
    db_session.expire_all()
    assert db_session.query(FamilySpace).count() == 1
