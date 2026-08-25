"""登录限流与锁定（AD-2）：按 name 连续失败计数，5 次锁 15 分钟。

状态落在 accounts.failed_attempts / accounts.locked_until；同名多账号时
失败同步累加到该 name 的全部账号，任一账号锁定即拒绝整个 name 的登录。
参数经 app.config 可 env 热调；AUTH_LOCKOUT_DISABLED 仅限开发态关闭。
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.services import audit
from app.utils import timeutil


class AccountLockedError(Exception):
    """该 name 当前处于锁定窗口内。携带 Retry-After 秒数供 API 层转 429。"""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(retry_after_seconds, 1)
        super().__init__(f"locked for {retry_after_seconds}s")


def ensure_not_locked(accounts: list[Account]) -> None:
    """同名全部账号任一处于锁定窗口即拒绝（name 级锁定语义）。

    锁定窗口已过的账号顺带归还全新失败预算。
    """
    if config.AUTH_LOCKOUT_DISABLED:
        return
    now = timeutil.utcnow()
    for account in accounts:
        if account.locked_until is not None:
            if account.locked_until > now:
                remaining = int((account.locked_until - now).total_seconds())
                raise AccountLockedError(remaining)
            account.locked_until = None
            account.failed_attempts = 0


def register_failures(session: Session, accounts: list[Account], ip: str | None) -> bool:
    """对同名全部账号累加失败；达到阈值写 locked_until 并审计。

    返回是否本次触发锁定。
    """
    now = timeutil.utcnow()
    triggered = False
    for account in accounts:
        account.failed_attempts += 1
        if (
            not config.AUTH_LOCKOUT_DISABLED
            and account.failed_attempts >= config.AUTH_MAX_FAILED_ATTEMPTS
        ):
            account.locked_until = now + timedelta(minutes=config.AUTH_LOCK_MINUTES)
            triggered = True
            audit.write_audit(
                session,
                action="account_locked",
                actor_id=account.user_id,
                target_id=account.user_id,
                ip=ip,
                detail={"failed_attempts": account.failed_attempts},
            )
    return triggered


def register_success(account: Account) -> None:
    """登录成功清零该账号的失败计数与锁定状态。"""
    account.failed_attempts = 0
    account.locked_until = None


def audit_login_failure_if_needed(
    session: Session, accounts: list[Account], ip: str | None
) -> None:
    """累计失败达 ≥3 时落审计 login_failed（logging-guidelines.md）。"""
    worst = max((a.failed_attempts for a in accounts), default=0)
    if worst >= 3:
        audit.write_audit(
            session,
            action="login_failed",
            target_id=accounts[0].user_id if accounts else None,
            ip=ip,
            detail={"failed_attempts": worst},
        )
