"""admin_app 认证依赖：独立签发域校验（09-04 SF-F4/F5）。

校验顺序：签名（ADMIN_JWT_SECRET）→ issuer/audience → principal_type →
主体 active → password_version 比对。家庭令牌（家庭 SECRET_KEY、无 iss/aud）
在此一律解析失败，不回退任何主体；首登改密门禁（require_admin_ready）对
me/username 生效，password/logout/refresh 属白名单（design §6）。
"""

from typing import cast

from fastapi import Request

from app import logctx
from app.api.deps import get_db
from app.errors import (
    ADMIN_PASSWORD_CHANGE_REQUIRED,
    ADMIN_SESSION_MESSAGE,
    ADMIN_UNAUTHORIZED,
    raise_api_error,
)
from app.models.system_admin import SystemAdmin, SystemAdminAccount
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
    """首登改密门禁：password_must_change=true 时白名单外一律 403（SF-F5）。"""
    admin, account = require_admin_principal(request)
    if account.password_must_change:
        raise_api_error(
            403,
            ADMIN_PASSWORD_CHANGE_REQUIRED,
            "请先修改初始管理员密码后再继续操作",
        )
    return admin, account


def require_system_admin(request: Request) -> AdminPrincipal:
    """后台业务路由的治理依赖（子任务 2 挂载到 admin_app 时使用）。

    与家庭可见性链完全无关：非管理员主体（含 family token）一律 403。
    """
    resolved = resolve_admin_principal(request)
    if resolved is None:
        raise_api_error(403, "FORBIDDEN_SYSTEM_ADMIN_ONLY", "仅系统管理员可执行该操作")
    return resolved
