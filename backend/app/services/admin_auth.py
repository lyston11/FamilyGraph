"""管理员凭据与会话服务（09-04 SF-F2/F4/F5）。

- 登录：username + 强密码；失败统一 ADMIN_INVALID_CREDENTIALS 文案（不泄露
  账号存在性），达到阈值锁定并 429 + Retry-After（仍用统一文案）；
- refresh：轮换 + 绝对有效期（轮换不续期）；重用已 revoked token 按攻击处置，
  撤销该管理员全部活跃会话并审计；
- 密码/用户名变更：需当前密码，成功后 password_version+1 并撤销全部会话；
- 凭据文件清理：首次改密事务提交后删除 0600 bootstrap 文件；删除失败写安全
  告警且回置 password_must_change（不标记初始化完成）。

脱敏红线：密码/令牌明文永不进入日志、审计 detail 或异常消息。
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    ACCOUNT_LOCKED,
    ADMIN_CREDENTIAL_MESSAGE,
    ADMIN_INVALID_CREDENTIALS,
    ADMIN_PASSWORD_TOO_WEAK,
    ADMIN_USERNAME_TAKEN,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.system_admin import SystemAdmin, SystemAdminAccount, SystemAdminRefreshSession
from app.services import admin_bootstrap, audit
from app.utils import admin_security, security, timeutil

logger = logging.getLogger(__name__)

USERNAME_MIN_LENGTH = 3
PASSWORD_MIN_LENGTH = 12

_USERNAME_NO_WHITESPACE = re.compile(r"^\S+$")
# 强密码基线：≥12 位且同时含小写、大写与数字（bootstrap 随机密码满足）
_PASSWORD_LOWERCASE = re.compile(r"[a-z]")
_PASSWORD_UPPERCASE = re.compile(r"[A-Z]")
_PASSWORD_DIGIT = re.compile(r"[0-9]")


class AdminAccountLockedError(Exception):
    """管理员账号处于锁定窗口内；携带 Retry-After 秒数供路由转 429。"""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(retry_after_seconds, 1)
        super().__init__(f"admin locked for {retry_after_seconds}s")


class AdminRefreshInvalidError(Exception):
    """admin refresh 无效（签名/过期/版本不符/主体缺失）。"""


class AdminRefreshReuseDetectedError(AdminRefreshInvalidError):
    """提交了已 revoked 的 admin refresh token——按重用攻击处置。"""


def load_account(session: Session, admin_id: int) -> tuple[SystemAdmin, SystemAdminAccount] | None:
    row = (
        session.query(SystemAdmin, SystemAdminAccount)
        .join(SystemAdminAccount, SystemAdminAccount.system_admin_id == SystemAdmin.id)
        .filter(SystemAdmin.id == admin_id)
        .first()
    )
    return (row[0], row[1]) if row else None


def _ensure_not_locked(account: SystemAdminAccount) -> None:
    """锁定窗口内拒绝；窗口已过顺带归还失败预算（与家庭 auth_guard 同语义）。"""
    if config.AUTH_LOCKOUT_DISABLED:
        return
    if account.locked_until is not None:
        now = timeutil.utcnow()
        if account.locked_until > now:
            raise AdminAccountLockedError(int((account.locked_until - now).total_seconds()))
        account.locked_until = None
        account.failed_attempts = 0


def _register_failure(
    session: Session, admin: SystemAdmin, account: SystemAdminAccount, ip: str | None
) -> None:
    account.failed_attempts += 1
    lockout_enabled = not config.AUTH_LOCKOUT_DISABLED
    if lockout_enabled and account.failed_attempts >= config.AUTH_MAX_FAILED_ATTEMPTS:
        # SF-F4：锁定立即撤销全部 admin refresh/access 会话——refresh 行全部
        # 置 revoked，access 经 password_version+1 版本失效（无状态 token 的
        # 唯一撤销手段）。
        account.password_version += 1
        account.locked_until = timeutil.utcnow() + timedelta(minutes=config.AUTH_LOCK_MINUTES)
        audit.write_audit(
            session,
            action="admin_account_locked",
            target_id=admin.id,
            ip=ip,
            detail={"principal_type": admin_security.ADMIN_PRINCIPAL_TYPE},
        )
        revoke_all_sessions(session, admin.id, ip, "admin_lockout_sessions_revoked")
    elif account.failed_attempts >= 3:
        audit.write_audit(
            session,
            action="admin_login_failed",
            target_id=admin.id,
            ip=ip,
            detail={"failed_attempts": account.failed_attempts},
        )


def issue_refresh_session(
    session: Session,
    admin: SystemAdmin,
    account: SystemAdminAccount,
    *,
    rotated_from: int | None,
    absolute_expiry: datetime,
) -> str:
    """签发 admin refresh 会话；token exp 与行 expires_at 同为绝对有效期。"""
    now = timeutil.utcnow()
    ttl_seconds = max(int((absolute_expiry - now).total_seconds()), 1)
    jti = secrets.token_urlsafe(32)
    raw_token = admin_security.create_admin_refresh_token(
        admin.id, account.password_version, jti, ttl_seconds=ttl_seconds
    )
    session.add(
        SystemAdminRefreshSession(
            system_admin_id=admin.id,
            token_hash=security.hash_token(raw_token),
            rotated_from=rotated_from,
            expires_at=absolute_expiry,
            revoked_at=None,
            created_at=now,
            last_seen_at=now,
        )
    )
    return raw_token


def authenticate(
    session: Session, *, username: str, password: str, ip: str | None
) -> tuple[SystemAdmin, SystemAdminAccount, str, str]:
    """管理员登录：成功返回 (admin, account, access_token, refresh_token)。

    失败路径（不存在/密码错/锁定）统一 401/429 + ADMIN_CREDENTIAL_MESSAGE，
    不区分账号存在性；未知用户名执行等开销 dummy 校验防时序枚举。
    """
    row = (
        session.query(SystemAdmin, SystemAdminAccount)
        .join(SystemAdminAccount, SystemAdminAccount.system_admin_id == SystemAdmin.id)
        .filter(SystemAdmin.username == username.strip())
        .first()
    )
    if row is None:
        security.verify_dummy_password(password)
        raise_api_error(401, ADMIN_INVALID_CREDENTIALS, ADMIN_CREDENTIAL_MESSAGE)
    admin, account = row[0], row[1]
    try:
        _ensure_not_locked(account)
    except AdminAccountLockedError as locked:
        raise_api_error(
            429,
            ACCOUNT_LOCKED,
            ADMIN_CREDENTIAL_MESSAGE,
            detail={"retry_after_seconds": locked.retry_after_seconds},
            headers={"Retry-After": str(locked.retry_after_seconds)},
        )
    if not security.verify_password(password, account.password_hash):
        _register_failure(session, admin, account, ip)
        session.flush()
        raise_api_error(401, ADMIN_INVALID_CREDENTIALS, ADMIN_CREDENTIAL_MESSAGE)
    account.failed_attempts = 0
    account.locked_until = None
    refresh_raw = issue_refresh_session(
        session,
        admin,
        account,
        rotated_from=None,
        absolute_expiry=timeutil.utcnow()
        + timedelta(seconds=config.ADMIN_REFRESH_TOKEN_TTL_SECONDS),
    )
    access = admin_security.create_admin_access_token(admin.id, account.password_version)
    audit.write_audit(
        session,
        action="admin_login_succeeded",
        target_id=admin.id,
        ip=ip,
        detail={"principal_type": admin_security.ADMIN_PRINCIPAL_TYPE},
    )
    return admin, account, access, refresh_raw


def rotate_refresh(
    session: Session, raw_token: str, ip: str | None
) -> tuple[SystemAdmin, SystemAdminAccount, str]:
    """轮换 admin refresh；重用已 revoked token 撤销全部会话并审计。"""
    try:
        payload = admin_security.decode_admin_token(
            raw_token, admin_security.ADMIN_REFRESH_TOKEN_TYPE
        )
    except security.TokenDecodeError as exc:
        raise AdminRefreshInvalidError(str(exc)) from None
    row = session.scalar(
        select(SystemAdminRefreshSession).where(
            SystemAdminRefreshSession.token_hash == security.hash_token(raw_token)
        )
    )
    if row is None or not row.is_active:
        if row is not None:
            revoke_all_sessions(session, row.system_admin_id, ip, "admin_refresh_reuse_detected")
        raise AdminRefreshReuseDetectedError("revoked admin refresh token submitted")
    loaded = load_account(session, row.system_admin_id)
    if loaded is None:
        raise AdminRefreshInvalidError("principal missing")
    admin, account = loaded
    if admin.status != "active":
        raise AdminRefreshInvalidError("admin not active")
    if account.password_version != payload[admin_security.VERSION_CLAIM]:
        raise AdminRefreshInvalidError("password version mismatch")
    now = timeutil.utcnow()
    if row.expires_at <= now:
        raise AdminRefreshInvalidError("expired")
    row.revoked_at = now
    row.last_seen_at = now
    new_raw = issue_refresh_session(
        session, admin, account, rotated_from=row.id, absolute_expiry=row.expires_at
    )
    session.flush()
    return admin, account, new_raw


def revoke_by_raw_token(session: Session, admin_id: int, raw_token: str | None) -> bool:
    """登出撤销对应 refresh 会话；无 token 时撤销全部（登出幂等）。"""
    if raw_token is None or not raw_token.strip():
        return revoke_all_sessions(session, admin_id, None, "admin_logout_all")
    row = session.scalar(
        select(SystemAdminRefreshSession).where(
            SystemAdminRefreshSession.token_hash == security.hash_token(raw_token.strip()),
            SystemAdminRefreshSession.system_admin_id == admin_id,
            SystemAdminRefreshSession.revoked_at.is_(None),
        )
    )
    if row is None:
        return False
    row.revoked_at = timeutil.utcnow()
    session.flush()
    return True


def revoke_all_sessions(session: Session, admin_id: int, ip: str | None, reason: str) -> bool:
    """撤销该管理员全部活跃 refresh 会话；reason 进审计（改密/改用户名/恢复/锁定）。"""
    rows = session.scalars(
        select(SystemAdminRefreshSession).where(
            SystemAdminRefreshSession.system_admin_id == admin_id,
            SystemAdminRefreshSession.revoked_at.is_(None),
        )
    ).all()
    now = timeutil.utcnow()
    for row in rows:
        row.revoked_at = now
    audit.write_audit(
        session,
        action=reason,
        target_id=admin_id,
        ip=ip,
        detail={
            "principal_type": admin_security.ADMIN_PRINCIPAL_TYPE,
            "revoked_count": len(rows),
        },
    )
    session.flush()
    return bool(rows)


def validate_password_strength(password: str) -> None:
    """新密码强度基线：≥12 位且含小写/大写/数字；不满足 422（429 之外的唯一策略入口）。"""
    if (
        len(password) < PASSWORD_MIN_LENGTH
        or not _PASSWORD_LOWERCASE.search(password)
        or not _PASSWORD_UPPERCASE.search(password)
        or not _PASSWORD_DIGIT.search(password)
    ):
        raise_api_error(
            422,
            ADMIN_PASSWORD_TOO_WEAK,
            f"新密码至少 {PASSWORD_MIN_LENGTH} 位，且需同时包含大写字母、小写字母和数字",
        )


def validate_username(username: str) -> str:
    """用户名规范：3-100 字符、非空白字符；返回 strip 后的值。"""
    candidate = username.strip()
    if (
        len(candidate) < USERNAME_MIN_LENGTH
        or len(candidate) > 100
        or not _USERNAME_NO_WHITESPACE.match(candidate)
    ):
        raise_api_error(
            422,
            VALIDATION_ERROR,
            f"用户名须为 {USERNAME_MIN_LENGTH}-100 个字符且不含空白",
        )
    return candidate


def change_password(
    session: Session,
    admin: SystemAdmin,
    account: SystemAdminAccount,
    *,
    current_password: str,
    new_password: str,
    ip: str | None,
) -> None:
    """修改管理员密码：需当前密码；成功后版本+1 并撤销全部会话。

    不在此提交：调用方（路由）提交后须调用 finalize_credential_file 完成
    bootstrap 凭据文件清理（删除失败回置 password_must_change）。
    """
    if not security.verify_password(current_password, account.password_hash):
        raise_api_error(401, ADMIN_INVALID_CREDENTIALS, ADMIN_CREDENTIAL_MESSAGE)
    validate_password_strength(new_password)
    now = timeutil.utcnow()
    account.password_hash = security.hash_password(new_password)
    account.password_version += 1
    account.password_must_change = False
    if account.status == "managed":
        account.status = "claimed"
        account.claimed_at = now
    account.updated_at = now
    admin.updated_at = now
    revoke_all_sessions(session, admin.id, ip, "admin_password_change_sessions_revoked")
    audit.write_audit(
        session,
        action="admin_password_changed",
        target_id=admin.id,
        ip=ip,
        detail={"principal_type": admin_security.ADMIN_PRINCIPAL_TYPE},
    )


def change_username(
    session: Session,
    admin: SystemAdmin,
    account: SystemAdminAccount,
    *,
    current_password: str,
    new_username: str,
    ip: str | None,
) -> None:
    """修改管理员用户名：需当前密码；成功后版本+1 并撤销全部会话。"""
    if not security.verify_password(current_password, account.password_hash):
        raise_api_error(401, ADMIN_INVALID_CREDENTIALS, ADMIN_CREDENTIAL_MESSAGE)
    candidate = validate_username(new_username)
    taken = (
        session.query(SystemAdmin.id)
        .filter(SystemAdmin.username == candidate, SystemAdmin.id != admin.id)
        .first()
    )
    if taken is not None:
        raise_api_error(409, ADMIN_USERNAME_TAKEN, "该用户名已被占用")
    now = timeutil.utcnow()
    admin.username = candidate
    admin.updated_at = now
    account.updated_at = now
    account.password_version += 1
    revoke_all_sessions(session, admin.id, ip, "admin_username_change_sessions_revoked")
    audit.write_audit(
        session,
        action="admin_username_changed",
        target_id=admin.id,
        ip=ip,
        detail={"principal_type": admin_security.ADMIN_PRINCIPAL_TYPE},
    )


def finalize_credential_file(session: Session, admin_id: int) -> None:
    """改密事务提交后调用：删除 bootstrap 凭据文件（SF-F3）。

    删除失败：写安全告警日志 + 审计，并回置 password_must_change=true
    （初始化未完成）；该回置使用独立事务提交，不抛出以免掩盖改密成功。
    """
    if not admin_bootstrap.credentials_file_exists():
        return
    try:
        admin_bootstrap.delete_credentials_file()
    except OSError:
        logger.error(
            "SECURITY: admin bootstrap credentials file could not be deleted; "
            "password_must_change restored until the file is removed"
        )
        loaded = load_account(session, admin_id)
        if loaded is None:
            return
        _admin, account = loaded
        account.password_must_change = True
        audit.write_audit(
            session,
            action="admin_credential_file_delete_failed",
            target_id=admin_id,
            detail={"principal_type": admin_security.ADMIN_PRINCIPAL_TYPE},
        )
        session.commit()
