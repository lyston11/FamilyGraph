"""refresh 会话持久化：签发 / 轮换 / 重用检测 / 全会话撤销（AD-2）。

- 登录/选中候选后写入 refresh_sessions 行（只存 token_hash）
- 每次刷新：旧行置 revoked_at + 新行 rotated_from 链，同事务提交
- 提交已 revoked 的 token = 重用攻击 → 撤销该用户全部活跃会话 + 审计
"""

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.refresh_session import RefreshSession
from app.services import audit
from app.utils import security, timeutil


class InvalidRefreshTokenError(Exception):
    """refresh 无效（签名错/过期/行缺失/版本不符）。"""


class RefreshReuseDetectedError(InvalidRefreshTokenError):
    """提交了已 revoked 的 refresh token——按重用攻击处置。"""


def issue_refresh_session(session: Session, account: Account, rotated_from: int | None) -> str:
    """为账号签发新 refresh token 并落库；返回原始 token（仅本次响应可见）。"""
    jti = secrets.token_urlsafe(32)
    raw_token = security.create_refresh_token(account.user_id, account.token_version, jti)
    row = RefreshSession(
        user_id=account.user_id,
        token_hash=security.hash_token(raw_token),
        rotated_from=rotated_from,
        expires_at=timeutil.utcnow() + timedelta(seconds=config.REFRESH_TOKEN_TTL_SECONDS),
        revoked_at=None,
    )
    session.add(row)
    return raw_token


def rotate(session: Session, raw_token: str, ip: str | None) -> tuple[Account, str]:
    """校验并轮换 refresh：返回 (account, 新 refresh token)。

    - 已 revoked 行 → 重用攻击：撤销该用户全部活跃会话 + 审计 + 异常
    - 过期/未知/版本不符 → 普通 InvalidRefreshTokenError
    """
    try:
        payload = security.decode_token(raw_token, security.REFRESH_TOKEN_TYPE)
    except security.TokenDecodeError as exc:
        raise InvalidRefreshTokenError(str(exc)) from None

    token_hash = security.hash_token(raw_token)
    row = session.query(RefreshSession).filter(RefreshSession.token_hash == token_hash).first()
    if row is None:
        raise InvalidRefreshTokenError("unknown refresh session")
    if not row.is_active:
        # 重用攻击：全会话撤销 + 审计告警（AD-2 硬性合同）
        revoke_all_active(session, row.user_id, ip=ip, reason="refresh_reuse_detected")
        raise RefreshReuseDetectedError("revoked refresh token submitted")

    account = session.query(Account).filter(Account.user_id == row.user_id).one_or_none()
    if account is None or account.token_version != payload["ver"]:
        # 版本不符：PIN 已变更等敏感操作之后，旧链路全部作废
        if row.revoked_at is None:
            row.revoked_at = timeutil.utcnow()
            session.flush()
        raise InvalidRefreshTokenError("token version mismatch")

    now = timeutil.utcnow()
    if row.expires_at <= now:
        raise InvalidRefreshTokenError("expired")

    old_id = row.id
    row.revoked_at = now
    new_raw = issue_refresh_session(session, account, rotated_from=old_id)
    session.flush()
    return account, new_raw


def revoke_by_raw_token(session: Session, user_id: int, raw_token: str | None) -> bool:
    """登出撤销：撤销指定 token 对应且属于该用户的行；无 token 时撤销全部。

    返回是否撤销了至少一行。无效 token 静默忽略（登出幂等，不泄露信息）。
    """
    if raw_token is None or not raw_token.strip():
        return revoke_all_active(session, user_id, ip=None, reason="logout_all")
    token_hash = security.hash_token(raw_token.strip())
    row = (
        session.query(RefreshSession).filter(RefreshSession.token_hash == token_hash).one_or_none()
    )
    if row is None or row.user_id != user_id or not row.is_active:
        return False
    row.revoked_at = timeutil.utcnow()
    session.flush()
    return True


def revoke_all_active(session: Session, user_id: int, ip: str | None, reason: str) -> bool:
    """撤销该用户全部活跃 refresh 会话；reason 进审计 detail。"""
    now = timeutil.utcnow()
    rows = (
        session.query(RefreshSession)
        .filter(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        row.revoked_at = now
    audit.write_audit(
        session,
        action=reason if reason.startswith(("refresh_", "logout")) else "sessions_revoked",
        actor_id=user_id,
        target_id=user_id,
        ip=ip,
        detail={"reason": reason, "revoked_count": len(rows)},
    )
    session.flush()
    return len(rows) > 0
