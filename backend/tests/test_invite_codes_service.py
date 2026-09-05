"""services/invite_codes.py 原语与授权单测（09-05 Chunk A）。

覆盖：码生成唯一性/字符集、三种 kind 的 DB CHECK 约束、核销竞态（used_count
并发上界，写锁前置单赢家）、撤销权限（creator vs space_admin）、状态拒绝文案。
"""

import threading
from datetime import timedelta

import pytest
from conftest import create_user_with_pin, seed_space_with_owner
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.commands.context import command_transaction
from app.db import SessionLocal
from app.errors import INVITE_CODE_FORBIDDEN, SPACE_NOT_FOUND
from app.models.invite_code import InviteCode
from app.models.space import SpaceMember
from app.services import invite_codes
from app.utils import timeutil

_SYNC_TIMEOUT = 10.0


def _api_error_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict) and "__api_error__" in detail:
        return str(detail["__api_error__"]["code"])  # type: ignore[union-attr]
    return ""


def _make_member(session, name: str, *, profile_status: str = "identity_confirmed"):
    return create_user_with_pin(session, name, "123456", profile_status=profile_status)


# ---- 1. 码生成：字符集与唯一性 ----


def test_invite_code_generator_charset_and_uniqueness() -> None:
    confusing = set("0O1IL")
    codes = {invite_codes.generate_code() for _ in range(2000)}
    assert len(codes) == 2000  # 2000 次生成无碰撞（31^8 空间）
    for code in codes:
        assert len(code) == invite_codes.DEFAULT_CODE_LENGTH
        assert not (set(code) & confusing), f"码含混淆字符: {code}"
        assert set(code) <= set(invite_codes.CODE_ALPHABET)
    with pytest.raises(ValueError):
        invite_codes.generate_code(7)
    with pytest.raises(ValueError):
        invite_codes.generate_code(11)


# ---- 2. 三种 kind 的 CHECK 约束（DB 层兜底）----


def test_invite_code_kind_constraints(db_session) -> None:
    creator = _make_member(db_session, "约束造数人")
    space = seed_space_with_owner(db_session, creator.id, name="约束空间")
    now = timeutil.utcnow()

    def _insert(**overrides) -> None:
        row = {
            "code": invite_codes.generate_code(),
            "kind": "household",
            "creator_id": creator.id,
            "space_id": space.id,
            "max_uses": 1,
            "used_count": 0,
            "expires_at": now + timedelta(days=7),
            "created_at": now,
        }
        row.update(overrides)
        db_session.add(InviteCode(**row))
        db_session.flush()

    # stranger ⇔ space_id IS NULL（两个方向都拒绝）
    with pytest.raises(IntegrityError):
        _insert(kind="stranger")
    db_session.rollback()
    with pytest.raises(IntegrityError):
        _insert(kind="household", space_id=None)
    db_session.rollback()
    with pytest.raises(IntegrityError):
        _insert(kind="lineage", space_id=None)
    db_session.rollback()
    # household/lineage ⇒ max_uses=1
    with pytest.raises(IntegrityError):
        _insert(kind="household", max_uses=2)
    db_session.rollback()
    with pytest.raises(IntegrityError):
        _insert(kind="household", max_uses=None)
    db_session.rollback()
    # used_count 上界
    with pytest.raises(IntegrityError):
        _insert(kind="household", used_count=2)
    db_session.rollback()
    with pytest.raises(IntegrityError):
        _insert(kind="stranger", space_id=None, max_uses=0)
    db_session.rollback()

    # 合法形状全部落库：一次性家庭码 / 家族码 / 不限次与限次陌生人码
    _insert(kind="household")
    _insert(kind="lineage")
    _insert(kind="stranger", space_id=None, max_uses=None)
    _insert(kind="stranger", space_id=None, max_uses=5)
    db_session.commit()
    assert db_session.query(InviteCode).count() == 4


# ---- 3. 核销竞态：used_count 并发上界（写锁前置单赢家）----


def _consume_with_contract(session, raw_code: str) -> None:
    """按命令层合同组合 resolve+consume（立即事务内），模拟注册/兑换的核销窗口。"""
    with command_transaction(session, immediate=True):
        code = invite_codes.resolve_usable_code(session, raw_code)
        invite_codes.consume_code(session, code)


def test_invite_code_consume_race_one_shot_single_winner(db_session) -> None:
    creator = _make_member(db_session, "核销造数人")
    space = seed_space_with_owner(db_session, creator.id, name="核销空间")
    with command_transaction(db_session):
        code = invite_codes.create_code(
            session=db_session, creator=creator, kind="household", space_id=space.id
        )
    raw = code.code

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=_SYNC_TIMEOUT)
            _consume_with_contract(session, raw)
            result = "won"
        except HTTPException as exc:
            result = f"{exc.status_code}:{_api_error_code(exc)}"
        except Exception as exc:  # noqa: BLE001
            result = f"error:{type(exc).__name__}"
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_SYNC_TIMEOUT)
        assert not thread.is_alive(), "核销线程超时未结束（疑似死锁）"

    assert sorted(outcomes) == sorted(["won", "400:INVITE_CODE_INVALID"]), outcomes
    db_session.expire_all()
    row = db_session.query(InviteCode).filter(InviteCode.code == raw).one()
    assert row.used_count == 1


def test_invite_code_consume_race_respects_stranger_upper_bound(db_session) -> None:
    creator = _make_member(db_session, "上限造数人")
    seed_space_with_owner(db_session, creator.id, name="上限空间")
    with command_transaction(db_session):
        code = invite_codes.create_code(
            session=db_session, creator=creator, kind="stranger", max_uses=2
        )
    raw = code.code

    barrier = threading.Barrier(3)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=_SYNC_TIMEOUT)
            _consume_with_contract(session, raw)
            result = "won"
        except HTTPException as exc:
            result = f"{exc.status_code}:{_api_error_code(exc)}"
        except Exception as exc:  # noqa: BLE001
            result = f"error:{type(exc).__name__}"
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_SYNC_TIMEOUT)
        assert not thread.is_alive(), "核销线程超时未结束（疑似死锁）"

    assert sorted(outcomes) == ["400:INVITE_CODE_INVALID", "won", "won"], outcomes
    db_session.expire_all()
    row = db_session.query(InviteCode).filter(InviteCode.code == raw).one()
    assert row.used_count == 2


# ---- 4. 建码资格与参数校验 ----


def test_invite_code_create_requires_identity_confirmed(db_session) -> None:
    provisional = _make_member(db_session, "未确档人", profile_status="provisional")
    space = seed_space_with_owner(db_session, provisional.id, name="未确档空间")
    with pytest.raises(HTTPException) as exc:
        invite_codes.create_code(
            session=db_session, creator=provisional, kind="household", space_id=space.id
        )
    assert exc.value.status_code == 403
    assert _api_error_code(exc.value) == INVITE_CODE_FORBIDDEN


def test_invite_code_create_household_requires_active_member(db_session) -> None:
    outsider = _make_member(db_session, "空间外人")
    member = _make_member(db_session, "空间内人")
    space = seed_space_with_owner(db_session, member.id, name="成员空间")

    with pytest.raises(HTTPException) as exc:
        invite_codes.create_code(
            session=db_session, creator=outsider, kind="household", space_id=space.id
        )
    assert exc.value.status_code == 404
    assert _api_error_code(exc.value) == SPACE_NOT_FOUND

    with command_transaction(db_session):
        code = invite_codes.create_code(
            session=db_session, creator=member, kind="household", space_id=space.id
        )
    assert code.max_uses == 1
    assert code.space_id == space.id
    assert code.used_count == 0
    # 默认 7 天有效期
    assert code.expires_at - code.created_at == timedelta(days=7)


def test_invite_code_create_stranger_requires_any_active_membership(db_session) -> None:
    member = _make_member(db_session, "有空间人")
    loner = _make_member(db_session, "无空间人")
    seed_space_with_owner(db_session, member.id, name="有空间人的空间")

    with command_transaction(db_session):
        code = invite_codes.create_code(session=db_session, creator=member, kind="stranger")
    assert code.space_id is None
    assert code.max_uses is None  # NULL=不限次

    with pytest.raises(HTTPException) as exc:
        invite_codes.create_code(session=db_session, creator=loner, kind="stranger")
    assert exc.value.status_code == 403


def test_invite_code_create_rejects_bad_params(db_session) -> None:
    member = _make_member(db_session, "参数造数人")
    space = seed_space_with_owner(db_session, member.id, name="参数空间")
    with pytest.raises(HTTPException):
        invite_codes.create_code(
            session=db_session, creator=member, kind="household", space_id=space.id, max_uses=5
        )
    with pytest.raises(HTTPException):
        invite_codes.create_code(
            session=db_session, creator=member, kind="stranger", space_id=space.id
        )
    with pytest.raises(HTTPException):
        invite_codes.create_code(session=db_session, creator=member, kind="galaxy")


# ---- 5. 撤销权限：creator vs space_admin ----


def test_invite_code_revoke_by_creator_and_space_manager(db_session) -> None:
    creator = _make_member(db_session, "建码人")
    manager = _make_member(db_session, "管理员")
    other_member = _make_member(db_session, "普通成员")
    space = seed_space_with_owner(db_session, manager.id, name="撤销空间")
    # creator 是该空间普通 active 成员；other_member 也在空间内但不是管理员
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=creator.id,
            role="member",
            status="active",
            created_at=timeutil.utcnow(),
            updated_at=timeutil.utcnow(),
        )
    )
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=other_member.id,
            role="member",
            status="active",
            created_at=timeutil.utcnow(),
            updated_at=timeutil.utcnow(),
        )
    )
    db_session.commit()
    with command_transaction(db_session):
        code = invite_codes.create_code(
            session=db_session, creator=creator, kind="household", space_id=space.id
        )

    # 无关普通成员（同空间非管理员、非创建者）→ 404 防枚举
    with pytest.raises(HTTPException) as exc:
        invite_codes.revoke_code(session=db_session, code_id=code.id, actor=other_member, ip=None)
    assert exc.value.status_code == 404

    # 空间管理员可撤销他人创建的码
    with command_transaction(db_session):
        revoked = invite_codes.revoke_code(
            session=db_session, code_id=code.id, actor=manager, ip=None
        )
    assert revoked.revoked_at is not None

    # 已撤销终态不可再变
    with pytest.raises(HTTPException) as exc:
        invite_codes.revoke_code(session=db_session, code_id=code.id, actor=creator, ip=None)
    assert exc.value.status_code == 409


def test_invite_code_revoke_stranger_only_creator(db_session) -> None:
    creator = _make_member(db_session, "陌生码创建者")
    someone = _make_member(db_session, "陌生码路人")
    seed_space_with_owner(db_session, creator.id, name="陌生码创建者空间")
    with command_transaction(db_session):
        code = invite_codes.create_code(session=db_session, creator=creator, kind="stranger")

    with pytest.raises(HTTPException) as exc:
        invite_codes.revoke_code(session=db_session, code_id=code.id, actor=someone, ip=None)
    assert exc.value.status_code == 404

    with command_transaction(db_session):
        revoked = invite_codes.revoke_code(
            session=db_session, code_id=code.id, actor=creator, ip=None
        )
    assert revoked.revoked_at is not None


# ---- 6. 状态拒绝文案（仅码本身状态，不泄露创建者信息）----


def test_invite_code_resolve_rejects_expired_revoked_exhausted(db_session) -> None:
    creator = _make_member(db_session, "状态造数人")
    space = seed_space_with_owner(db_session, creator.id, name="状态空间")

    with command_transaction(db_session):
        expired = invite_codes.create_code(
            session=db_session, creator=creator, kind="household", space_id=space.id
        )
    expired.expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        invite_codes.resolve_usable_code(db_session, expired.code)
    assert exc.value.status_code == 400
    assert exc.value.detail["__api_error__"]["message"] == invite_codes.MESSAGE_EXPIRED  # type: ignore[index]
    db_session.rollback()

    with command_transaction(db_session):
        revoked_code = invite_codes.create_code(
            session=db_session, creator=creator, kind="household", space_id=space.id
        )
    revoked_code.revoked_at = timeutil.utcnow()
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        invite_codes.resolve_usable_code(db_session, revoked_code.code)
    assert exc.value.detail["__api_error__"]["message"] == invite_codes.MESSAGE_REVOKED  # type: ignore[index]
    db_session.rollback()

    with command_transaction(db_session):
        exhausted = invite_codes.create_code(
            session=db_session, creator=creator, kind="stranger", max_uses=1
        )
    exhausted.used_count = 1
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        invite_codes.resolve_usable_code(db_session, exhausted.code)
    assert exc.value.detail["__api_error__"]["message"] == invite_codes.MESSAGE_EXHAUSTED  # type: ignore[index]
    db_session.rollback()

    with pytest.raises(HTTPException) as exc:
        invite_codes.resolve_usable_code(db_session, "ZZZZZZZZ")
    assert exc.value.detail["__api_error__"]["message"] == invite_codes.MESSAGE_INVALID  # type: ignore[index]


# ---- 7. join_space_with_code：既有 pending→accept 唯一路径 ----


def test_invite_code_join_activates_membership_and_records_attribution(db_session) -> None:
    from app.models.audit_log import AuditLog

    creator = _make_member(db_session, "家庭码邀请人")
    joiner = _make_member(db_session, "家庭码加入人")
    space = seed_space_with_owner(db_session, creator.id, name="家庭码空间")
    with command_transaction(db_session):
        code = invite_codes.create_code(
            session=db_session, creator=creator, kind="household", space_id=space.id
        )

    with command_transaction(db_session):
        member = invite_codes.join_space_with_code(
            session=db_session,
            code=code,
            user=joiner,
            account_id=joiner.account.id,
            ip=None,
            scene="register",
        )
    assert member.status == "active"
    assert member.added_by == creator.id  # 邀请人归因
    db_session.expire(code, ["used_count"])
    assert code.used_count == 1

    audits = db_session.query(AuditLog).filter(AuditLog.action == "invite_code_redeemed").all()
    assert len(audits) == 1
    detail = audits[0].detail_json
    for fragment in ('"code_id"', '"creator_id"', '"scene": "register"'):
        assert fragment in detail

    # 同一加入人已是 active 成员：不重复核销（409，used_count 不变）
    with pytest.raises(HTTPException) as exc:
        with command_transaction(db_session):
            invite_codes.join_space_with_code(
                session=db_session,
                code=code,
                user=joiner,
                account_id=joiner.account.id,
                ip=None,
                scene="redeem",
            )
    assert exc.value.status_code == 409
    db_session.expire(code, ["used_count"])
    assert code.used_count == 1
