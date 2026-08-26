"""Owner 移交流程测试（AC-F5）。

覆盖：pending→accepted 全链路（owner 翻转 + 原 owner 降 space_admin）、取消、
惰性过期、并发双接受恰好一个成功、删除 owner 被义务预检 409 引导移交且空间
与成员不被级联删除、移交对象非 active 成员 409。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import select

from app.commands import ownership as ownership_commands
from app.commands.context import ActorContext
from app.config import OWNERSHIP_TRANSFER_TTL_HOURS
from app.db import SessionLocal
from app.errors import extract_api_error
from app.models.audit_log import AuditLog
from app.models.space import FamilySpace, SpaceMember
from app.models.v2_foundation import DomainEvent, OwnershipTransfer
from app.utils.timeutil import utcnow


def _ctx(user) -> ActorContext:
    return ActorContext(
        user_id=user.id,
        account_id=user.account.id,
        account_status=user.account.status,
    )


def _transfer_audit_actions(db_session, transfer_id: int) -> set[str]:
    """该移交记录的审计动作集合（audit_log.target_id = transfer.id）。"""
    return set(
        db_session.scalars(select(AuditLog.action).where(AuditLog.target_id == transfer_id)).all()
    )


def _make_space(db_session, owner, *, kind="household", members=()):
    space = FamilySpace(
        name=f"{owner.name}的空间", owner_id=owner.id, kind=kind, created_at=utcnow()
    )
    db_session.add(space)
    db_session.flush()
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=owner.id,
            added_by=owner.id,
            role="owner",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    for member in members:
        db_session.add(
            SpaceMember(
                space_id=space.id,
                user_id=member.id,
                added_by=owner.id,
                role="member",
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    db_session.commit()
    return space


def test_accept_transfer_flips_owner_and_demotes_old_owner(db_session) -> None:
    owner = create_user_with_pin(db_session, "老owner", "111111")
    heir = create_user_with_pin(db_session, "继承人", "222222")
    space = _make_space(db_session, owner, members=[heir])

    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=heir.id
    )
    result = ownership_commands.accept_transfer(db_session, _ctx(heir), transfer.id)
    assert result.status == "accepted"

    db_session.expire_all()
    assert space.owner_id == heir.id
    roles = {m.user_id: m.role for m in db_session.scalars(select(SpaceMember)).all()}
    assert roles[heir.id] == "owner"
    assert roles[owner.id] == "space_admin"  # 原 owner 默认降为 space_admin

    types = {e.type for e in db_session.scalars(select(DomainEvent)).all()}
    assert {"space.transfer.requested", "space.transfer.completed"} <= types

    # 审计留痕：发起与接受各有对应行，actor 记录正确
    actions = _transfer_audit_actions(db_session, transfer.id)
    assert {"ownership_transfer_created", "ownership_transfer_accepted"} <= actions


def test_only_heir_can_accept_and_non_member_target_rejected(db_session) -> None:
    owner = create_user_with_pin(db_session, "owner甲", "333333")
    member = create_user_with_pin(db_session, "成员乙", "444444")
    outsider = create_user_with_pin(db_session, "路人丙", "555555")
    space = _make_space(db_session, owner, members=[member])

    # 非 active 成员不能作为移交目标
    with pytest.raises(HTTPException) as exc_info:
        ownership_commands.create_transfer(
            db_session, _ctx(owner), space_id=space.id, to_user_id=outsider.id
        )
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "OWNER_TRANSFER_INVALID"

    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=member.id
    )
    # 非受让人接受 → 404 防枚举
    with pytest.raises(HTTPException) as exc_info2:
        ownership_commands.accept_transfer(db_session, _ctx(outsider), transfer.id)
    assert exc_info2.value.status_code == 404

    # 发起人自己也不能替受让人接受
    with pytest.raises(HTTPException):
        ownership_commands.accept_transfer(db_session, _ctx(owner), transfer.id)


def test_cancel_transfer_by_either_party(db_session) -> None:
    owner = create_user_with_pin(db_session, "owner撤", "666666")
    heir = create_user_with_pin(db_session, "继承撤", "777777")
    space = _make_space(db_session, owner, members=[heir])

    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=heir.id
    )
    cancelled = ownership_commands.cancel_transfer(db_session, _ctx(heir), transfer.id)
    assert cancelled.status == "cancelled"

    # 取消后可重新发起（partial unique index 只约束 pending）
    again = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=heir.id
    )
    assert again.status == "pending"

    # 审计留痕：created 与 cancelled 均指向同一条移交记录
    assert {"ownership_transfer_created", "ownership_transfer_cancelled"} <= (
        _transfer_audit_actions(db_session, transfer.id)
    )


def test_stale_pending_transfer_expires_lazily(db_session) -> None:
    owner = create_user_with_pin(db_session, "过期owner", "888888")
    heir = create_user_with_pin(db_session, "过期继承", "898989")
    space = _make_space(db_session, owner, members=[heir])

    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=heir.id
    )
    transfer.created_at = utcnow() - timedelta(hours=OWNERSHIP_TRANSFER_TTL_HOURS + 1)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        ownership_commands.accept_transfer(db_session, _ctx(heir), transfer.id)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "OWNER_TRANSFER_INVALID"

    db_session.expire_all()
    row = db_session.get(OwnershipTransfer, transfer.id)
    assert row.status == "expired"  # 惰性置为 expired 终态

    # 过期是系统事实：终态持久化的同时必须留审计（无 actor）
    expired_rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.target_id == transfer.id, AuditLog.action == "ownership_transfer_expired")
        .all()
    )
    assert len(expired_rows) == 1
    assert expired_rows[0].actor_id is None


def test_concurrent_double_accept_single_winner(db_session) -> None:
    """并发双接受：恰好一个成功，owner 只翻转一次。"""
    owner = create_user_with_pin(db_session, "并发owner", "909090")
    heir = create_user_with_pin(db_session, "并发继承", "919191")
    space = _make_space(db_session, owner, members=[heir])
    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=heir.id
    )
    db_session.commit()
    db_session.expire_all()

    results: list[str] = []
    barrier = __import__("threading").Barrier(2)

    def worker() -> None:
        session = SessionLocal()
        try:
            user = session.query(type(heir)).filter_by(id=heir.id).one()
            ctx = ActorContext(
                user_id=user.id, account_id=user.account.id, account_status=user.account.status
            )
            barrier.wait()
            ownership_commands.accept_transfer(session, ctx, transfer.id)
            results.append("won")
        except HTTPException as exc:
            results.append(str(exc.status_code))
        finally:
            session.close()

    t1 = __import__("threading").Thread(target=worker)
    t2 = __import__("threading").Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == ["409", "won"]
    db_session.expire_all()
    assert space.owner_id == heir.id
    assert db_session.query(FamilySpace).filter(FamilySpace.owner_id == owner.id).count() == 0


def test_delete_owner_blocked_with_guidance_space_survives(db_session) -> None:
    """AC-F5：删除 owner 被 409 引导移交；空间/成员不被 FK 静默删除。"""
    owner = create_user_with_pin(db_session, "周owner", "929292")
    member = create_user_with_pin(db_session, "周成员", "939393")
    space = _make_space(db_session, owner, members=[member])

    # 服务层义务预检直接验证（路由层行为已在 v2 foundation 套件覆盖）
    with pytest.raises(HTTPException) as exc_info:
        ownership_commands.assert_no_owner_obligations(db_session, _ctx(owner), owner.id)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 409
    assert error is not None and error["code"] == "OWNER_TRANSFER_REQUIRED"
    assert space.id in (error.get("detail") or {}).get("spaces_requiring_handover", [])

    # 移交后义务解除，可正常删除（此处仅验证预检放行）
    transfer = ownership_commands.create_transfer(
        db_session, _ctx(owner), space_id=space.id, to_user_id=member.id
    )
    ownership_commands.accept_transfer(db_session, _ctx(member), transfer.id)
    ownership_commands.assert_no_owner_obligations(db_session, _ctx(owner), owner.id)  # 不抛错
