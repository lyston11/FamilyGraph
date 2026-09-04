"""admin_app 认证依赖：独立签发域校验（09-04 SF-F4/F5）。

校验顺序：签名（ADMIN_JWT_SECRET）→ issuer/audience → principal_type →
主体 active → password_version 比对。家庭令牌（家庭 SECRET_KEY、无 iss/aud）
在此一律解析失败，不回退任何主体；首登改密门禁（require_admin_ready）对
me/username 生效，password/logout/refresh 属白名单（design §6）。
"""

from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from app import logctx
from app.api.deps import get_db
from app.errors import (
    ADMIN_PASSWORD_CHANGE_REQUIRED,
    ADMIN_SESSION_MESSAGE,
    ADMIN_UNAUTHORIZED,
    raise_api_error,
)
from app.models.admin_access import AdminAccessSession
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.services import admin_audit
from app.services.admin_access_sessions import (
    ACCESS_SESSION_HEADER,
    resolve_session,
)
from app.services.admin_auth import load_account
from app.utils import admin_security, security

AdminPrincipal = tuple[SystemAdmin, SystemAdminAccount]


def resolve_admin_principal(request: Request) -> AdminPrincipal | None:
    """解析 admin access token；无效/跨域/版本不符一律 None（不区分原因）。"""
    if hasattr(request.state, "fg_admin_principal"):
        return cast(AdminPrincipal | None, request.state.fg_admin_principal)
    request.state.fg_admin_principal = None
    authorization = request.headers.get("Authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return None
    try:
        payload = admin_security.decode_admin_token(
            raw_token, admin_security.ADMIN_ACCESS_TOKEN_TYPE
        )
        admin_id = int(payload["sub"])
    except (security.TokenDecodeError, ValueError, TypeError, KeyError):
        return None
    session = get_db(request)
    loaded = load_account(session, admin_id)
    if loaded is None:
        return None
    admin, account = loaded
    if admin.status != "active":
        return None
    if account.password_version != payload[admin_security.VERSION_CLAIM]:
        return None
    principal: AdminPrincipal = (admin, account)
    request.state.fg_admin_principal = principal
    logctx.user_id_var.set(None)
    return principal


def require_admin_principal(request: Request) -> AdminPrincipal:
    """严格管理员认证：失败统一 401 ADMIN_UNAUTHORIZED。"""
    resolved = resolve_admin_principal(request)
    if resolved is None:
        raise_api_error(
            401,
            ADMIN_UNAUTHORIZED,
            ADMIN_SESSION_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return resolved


def require_admin_ready(request: Request) -> AdminPrincipal:
    """admin 业务路由统一门禁：无效令牌 401；首登未改密白名单外一律 403。

    09-04 子任务 2 起 /admin-api/v1 全部路由经本依赖进入（替代旧
    require_system_admin）：password_must_change=true 时除密码/refresh/logout
    白名单外不可触达任何业务读端点（design §6 / spec §12.4）。
    """
    admin, account = require_admin_principal(request)
    if account.password_must_change:
        raise_api_error(
            403,
            ADMIN_PASSWORD_CHANGE_REQUIRED,
            "请先修改初始管理员密码后再继续操作",
        )
    return admin, account


def enforce_access_session(
    request: Request,
    session: Session,
    identity: AdminPrincipal,
    *,
    target_type: str,
    target_id: int,
    endpoint: str,
) -> AdminAccessSession:
    """敏感详情票据门禁（RM-F3）：无效/错目标/过期/撤销统一 403 并审计拒绝。

    每次成功使用同样写审计（access_session.used / access_session.denied）；
    审计提交后异常原样抛出，响应与随机失败不可区分。
    """
    admin, _account = identity
    raw_token = request.headers.get(ACCESS_SESSION_HEADER)
    try:
        session_row = resolve_session(
            session,
            raw_token,
            target_type=target_type,
            target_id=target_id,
            system_admin_id=admin.id,
        )
    except Exception:
        admin_audit.record_access(
            session,
            action="access_session.denied",
            endpoint=endpoint,
            system_admin_id=admin.id,
            target_type=target_type,
            target_id=target_id,
            ip=request.client.host if request.client else None,
        )
        session.commit()
        raise
    admin_audit.record_access(
        session,
        action="access_session.used",
        endpoint=endpoint,
        system_admin_id=admin.id,
        session_row=session_row,
        target_type=target_type,
        target_id=target_id,
        ip=request.client.host if request.client else None,
    )
    return session_row
