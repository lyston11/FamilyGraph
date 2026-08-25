"""services/auth_guard.py 分支覆盖（implement.md 清单 #3）。"""

from datetime import timedelta

import pytest
from conftest import create_user_with_pin

from app import config
from app.services import auth_guard
from app.utils import timeutil


@pytest.fixture()
def account(db_session):
    """返回 Account（auth_guard 服务层签名）。"""
    return create_user_with_pin(db_session, "张三", "111111").account


def _freeze_now(monkeypatch: pytest.MonkeyPatch):
    real = timeutil.utcnow()

    class Clock:
        offset = 0.0

        def __call__(self):
            return real + timedelta(seconds=self.offset)

    clock = Clock()
    monkeypatch.setattr(timeutil, "utcnow", clock)
    return clock


def test_no_lock_initially(db_session, account) -> None:
    auth_guard.ensure_not_locked([account])  # 不抛异常即通过


def test_failures_below_threshold_do_not_lock(db_session, account) -> None:
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS - 1):
        triggered = auth_guard.register_failures(db_session, [account], ip="1.2.3.4")
        assert not triggered
    db_session.commit()
    auth_guard.ensure_not_locked([account])
    assert account.failed_attempts == config.AUTH_MAX_FAILED_ATTEMPTS - 1


def test_threshold_failure_locks_account(db_session, account) -> None:
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        triggered = auth_guard.register_failures(db_session, [account], ip=None)
    assert triggered
    assert account.locked_until is not None
    assert account.locked_until > timeutil.utcnow()
    with pytest.raises(auth_guard.AccountLockedError):
        auth_guard.ensure_not_locked([account])


def test_lock_expires_and_budget_resets(
    db_session, account, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = timeutil.utcnow()
    monkeypatch.setattr(timeutil, "utcnow", lambda: base)
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        auth_guard.register_failures(db_session, [account], ip=None)
    db_session.commit()
    # 窗口过后：解锁并归还全新失败预算
    monkeypatch.setattr(
        timeutil,
        "utcnow",
        lambda: base + timedelta(minutes=config.AUTH_LOCK_MINUTES, seconds=1),
    )
    auth_guard.ensure_not_locked([account])
    assert account.locked_until is None
    assert account.failed_attempts == 0


def test_register_success_clears_state(db_session, account) -> None:
    auth_guard.register_failures(db_session, [account], ip=None)
    auth_guard.register_success(account)
    assert account.failed_attempts == 0
    assert account.locked_until is None


def test_lockout_disabled_env_skips_locking(
    db_session, account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AUTH_LOCKOUT_DISABLED", True)
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS * 2):
        triggered = auth_guard.register_failures(db_session, [account], ip=None)
    assert not triggered
    assert account.locked_until is None


def test_audit_login_failed_written_at_third_failure(db_session, account) -> None:
    from app.models.audit_log import AuditLog

    # 两次失败：未达 ≥3 阈值，不落 login_failed 审计
    auth_guard.register_failures(db_session, [account], ip=None)
    auth_guard.register_failures(db_session, [account], ip=None)
    auth_guard.audit_login_failure_if_needed(db_session, [account], ip="9.9.9.9")
    db_session.flush()
    rows = db_session.query(AuditLog).filter(AuditLog.action == "login_failed").all()
    assert rows == []

    # 第三次失败达到阈值：审计留痕
    auth_guard.register_failures(db_session, [account], ip=None)
    auth_guard.audit_login_failure_if_needed(db_session, [account], ip="9.9.9.9")
    db_session.flush()
    rows = db_session.query(AuditLog).filter(AuditLog.action == "login_failed").all()
    assert len(rows) == 1
    assert rows[0].detail["failed_attempts"] >= 3


def test_account_locked_audited_on_trigger(db_session, account) -> None:
    from app.models.audit_log import AuditLog

    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        auth_guard.register_failures(db_session, [account], ip=None)
    db_session.flush()
    rows = db_session.query(AuditLog).filter(AuditLog.action == "account_locked").all()
    assert len(rows) == 1
