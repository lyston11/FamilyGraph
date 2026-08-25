"""services/refresh_session.py：轮换链、重用检测触发全会话撤销（implement.md #4）。"""

from datetime import timedelta

import pytest
from conftest import create_user_with_pin

from app import config
from app.models.audit_log import AuditLog
from app.models.refresh_session import RefreshSession
from app.services import refresh_session as rss
from app.utils import security, timeutil


@pytest.fixture()
def account(db_session):
    """返回 Account（服务层签名）；user 经 .user 关系访问。"""
    return create_user_with_pin(db_session, "李四", "222222").account


def _issued_pair(session, account):
    raw = rss.issue_refresh_session(session, account, rotated_from=None)
    session.commit()
    return raw


def _row_by_raw(db_session, raw: str) -> RefreshSession:
    row: RefreshSession | None = (
        db_session.query(RefreshSession)
        .filter_by(token_hash=security.hash_token(raw))
        .one_or_none()
    )
    assert row is not None
    return row


def test_issue_persists_hash_only(db_session, account) -> None:
    raw = _issued_pair(db_session, account)
    rows = db_session.query(RefreshSession).all()
    assert len(rows) == 1
    assert rows[0].token_hash == security.hash_token(raw)
    assert raw != rows[0].token_hash  # 原始 token 不落库
    assert rows[0].revoked_at is None
    assert rows[0].rotated_from is None


def test_rotate_revokes_old_and_links_chain(db_session, account) -> None:
    first = _issued_pair(db_session, account)
    rotated_account, second = rss.rotate(db_session, first, ip=None)
    db_session.commit()

    old_row = _row_by_raw(db_session, first)
    new_row = _row_by_raw(db_session, second)
    assert old_row.revoked_at is not None
    assert new_row.revoked_at is None
    assert new_row.rotated_from == old_row.id
    assert rotated_account.user_id == account.user_id


def test_reuse_of_revoked_token_revokes_all_sessions_and_audits(db_session, account) -> None:
    """提交已 revoked 的 refresh → 全会话撤销 + 审计（AD-2）。"""
    first = _issued_pair(db_session, account)
    _account, second = rss.rotate(db_session, first, ip=None)
    db_session.commit()

    with pytest.raises(rss.RefreshReuseDetectedError):
        rss.rotate(db_session, first, ip="8.8.8.8")
    db_session.commit()

    active = (
        db_session.query(RefreshSession)
        .filter(RefreshSession.user_id == account.user_id)
        .filter(RefreshSession.revoked_at.is_(None))  # type: ignore[attr-defined]
        .all()
    )
    assert active == []  # 轮换产生的新 token 也被撤销：全会话失效
    audit_rows = (
        db_session.query(AuditLog).filter(AuditLog.action == "refresh_reuse_detected").all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].detail["reason"] == "refresh_reuse_detected"


def test_expired_refresh_rejected(db_session, account) -> None:
    raw = _issued_pair(db_session, account)
    stored = _row_by_raw(db_session, raw)
    stored.expires_at = timeutil.utcnow() - timedelta(seconds=config.REFRESH_TOKEN_TTL_SECONDS + 5)
    db_session.commit()
    with pytest.raises(rss.InvalidRefreshTokenError):
        rss.rotate(db_session, raw, ip=None)


def test_version_mismatch_invalidates(db_session, account) -> None:
    """token_version 变更后旧 refresh 无法换新（PRD 验收）。"""
    raw = _issued_pair(db_session, account)
    account.token_version += 1
    db_session.commit()
    with pytest.raises(rss.InvalidRefreshTokenError):
        rss.rotate(db_session, raw, ip=None)
    assert _row_by_raw(db_session, raw).revoked_at is not None


def test_logout_by_raw_token(db_session, account) -> None:
    raw = _issued_pair(db_session, account)
    assert rss.revoke_by_raw_token(db_session, account.user_id, raw)
    db_session.commit()
    assert _row_by_raw(db_session, raw).revoked_at is not None
    # 幂等：再次撤销同一 token 返回 False，不抛错
    assert not rss.revoke_by_raw_token(db_session, account.user_id, raw)


def test_logout_ignores_foreign_or_unknown_token(db_session, account) -> None:
    other = create_user_with_pin(db_session, "王五", "333333").account
    other_raw = rss.issue_refresh_session(db_session, other, rotated_from=None)
    db_session.commit()
    # user 提交别人的/未知的 token：不撤销、不报错（登出幂等）
    assert not rss.revoke_by_raw_token(db_session, account.user_id, other_raw)
    assert not rss.revoke_by_raw_token(db_session, account.user_id, "unknown-token")
