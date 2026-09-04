"""敏感详情访问会话（09-04 RM-F3 / design §4）。

- POST /admin-api/v1/access-sessions 签发：绑定单个 user 或 space，TTL 30 分钟，
  reason 非空/限长/无控制字符（schema 层校验），票据只存 hash；
- 敏感详情端点要求 ``X-Admin-Access-Session``：无效 / 错目标 / 过期 / 撤销
  统一 403，且拒绝尝试必须写审计（防暴力试探无差异响应）；
- 会话不可跨目标复用、不可升级为全后台会话。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import raise_api_error
from app.models.admin_access import AdminAccessSession
from app.models.space import FamilySpace
from app.models.user import User
from app.utils import timeutil

ACCESS_SESSION_TTL_MINUTES = 30
ACCESS_SESSION_HEADER = "X-Admin-Access-Session"
ADMIN_ACCESS_SESSION_INVALID = "ADMIN_ACCESS_SESSION_INVALID"
ADMIN_ACCESS_SESSION_MESSAGE = "访问会话无效或已过期"
ADMIN_TARGET_NOT_FOUND = "ADMIN_TARGET_NOT_FOUND"
ADMIN_TARGET_NOT_FOUND_MESSAGE = "目标不存在或不可访问"

# 允许的敏感 scope：按目标类型固定签发，不接受客户端自定义。
SCOPES_BY_TARGET_TYPE: dict[str, tuple[str, ...]] = {
    "user": ("profile.detail", "avatar.thumbnail", "attachment.metadata"),
    "space": ("member.detail", "relation.detail", "fact.detail"),
}


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _target_exists(session: Session, target_type: str, target_id: int) -> bool:
    if target_type == "user":
        row = session.get(User, target_id)
        return row is not None and row.deleted_at is None
    if target_type == "space":
        return session.get(FamilySpace, target_id) is not None
    return False


def ensure_target(session: Session, target_type: str, target_id: int) -> None:
    """目标必须存在；user/space 统一安全 404（防存在性枚举）。"""
    if not _target_exists(session, target_type, target_id):
        raise_api_error(404, ADMIN_TARGET_NOT_FOUND, ADMIN_TARGET_NOT_FOUND_MESSAGE)


def create_session(
    session: Session,
    *,
    system_admin_id: int,
    target_type: str,
    target_id: int,
    reason: str,
) -> tuple[AdminAccessSession, str]:
    """签发绑定单目标的访问会话；返回 (持久化行, 明文票据)。

    明文票据只出现在本次返回值中（进响应即不再落地）；数据库仅存 SHA-256。
    """
    now = timeutil.utcnow()
    raw_token = secrets.token_urlsafe(32)
    row = AdminAccessSession(
        token_hash=hash_token(raw_token),
        system_admin_id=system_admin_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        scopes_json=list(SCOPES_BY_TARGET_TYPE[target_type]),
        issued_at=now,
        expires_at=now + timedelta(minutes=ACCESS_SESSION_TTL_MINUTES),
    )
    session.add(row)
    session.flush()
    return row, raw_token


def resolve_session(
    session: Session,
    raw_token: str | None,
    *,
    target_type: str,
    target_id: int,
    system_admin_id: int,
) -> AdminAccessSession:
    """校验票据、签发主体与绑定目标；任何失败统一 403（不区分原因，防探测）。

    票据不可跨管理员复用：system_admin_id 与签发行不一致视为无效。
    调用方（路由层）负责把拒绝尝试写审计——本函数只做判定。
    """
    if not raw_token:
        raise_api_error(403, ADMIN_ACCESS_SESSION_INVALID, ADMIN_ACCESS_SESSION_MESSAGE)
    row = session.scalar(
        select(AdminAccessSession).where(AdminAccessSession.token_hash == hash_token(raw_token))
    )
    now = timeutil.utcnow()
    if (
        row is None
        or row.system_admin_id != system_admin_id
        or row.revoked_at is not None
        or row.expires_at < now
        or row.target_type != target_type
        or row.target_id != target_id
    ):
        raise_api_error(403, ADMIN_ACCESS_SESSION_INVALID, ADMIN_ACCESS_SESSION_MESSAGE)
    return row


def revoke_session(session: Session, row: AdminAccessSession) -> None:
    row.revoked_at = timeutil.utcnow()
    session.flush()
