"""认证依赖：当前用户解析（token_version 比对）与首登改 PIN 全局门禁。

require_pin_changed 以 app 级全局依赖挂载（main.py），对全部已注册路由生效：
- 未认证请求不在此拦截，交由路由自身的 get_current_user 返回 401
- pin_must_change=true 时仅放行白名单 {PUT /me/pin, POST /auth/logout,
  POST /auth/refresh}（architecture.md §1），其余一律 403 PIN_CHANGE_REQUIRED
- /api/health 为公开端点，无认证头时本依赖直接放行
"""

from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from app import logctx
from app.db import SessionLocal
from app.errors import PIN_CHANGE_REQUIRED, UNIFIED_CREDENTIAL_MESSAGE, raise_api_error
from app.models import Account, User
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.services.platform_roles import is_platform_operator
from app.utils import security

Principal = tuple[User, Account] | tuple[SystemAdmin, SystemAdminAccount]


PIN_GATE_WHITELIST: set[tuple[str, str]] = {
    ("PUT", "/api/me/pin"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/refresh"),
    # 「这是我」合并确认：首登门禁内即可调用（F-1 先确认身份，再改 PIN/审清单）
    ("POST", "/api/me/identity/confirm"),
}
# 公开端点：无凭据也放行（health 不经认证依赖管辖，architecture.md §1）
PIN_GATE_PUBLIC: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/login/select"),
    ("GET", "/api/bootstrap/status"),
    ("POST", "/api/bootstrap/initialize"),
}


def get_db(request: Request) -> Session:
    """每个请求复用同一短生命周期会话（request.state 缓存）。"""
    if not hasattr(request.state, "fg_db"):
        request.state.fg_db = SessionLocal()
    return cast(Session, request.state.fg_db)


def close_request_db(request: Request) -> None:
    """中间件在响应结束后调用，释放请求级会话。"""
    session: Session | None = getattr(request.state, "fg_db", None)
    if session is not None:
        session.close()


def resolve_bearer_principal(
    request: Request,
) -> tuple[User, Account] | tuple[SystemAdmin, SystemAdminAccount] | None:
    """解析家庭主体或独立系统主体；主体类型来自签名 JWT + 服务端查表。"""
    if hasattr(request.state, "fg_principal"):
        return cast(Principal | None, request.state.fg_principal)
    request.state.fg_principal = None
    authorization = request.headers.get("Authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return None
    try:
        payload = security.decode_token(raw_token, security.ACCESS_TOKEN_TYPE)
        principal_id = int(payload["sub"])
    except (security.TokenDecodeError, ValueError, TypeError):
        return None
    session = get_db(request)
    principal_type = payload.get("principal_type", "family_user")
    if principal_type == "system_admin":
        system_row = (
            session.query(SystemAdmin, SystemAdminAccount)
            .join(SystemAdminAccount, SystemAdminAccount.system_admin_id == SystemAdmin.id)
            .filter(SystemAdmin.id == principal_id, SystemAdmin.status == "active")
            .first()
        )
        if system_row is None or system_row[1].token_version != payload["ver"]:
            return None
        principal = (system_row[0], system_row[1])
        request.state.fg_principal = principal
        logctx.user_id_var.set(None)
        return principal
    if principal_type != "family_user":
        return None
    user_row = (
        session.query(User, Account)
        .join(Account, Account.user_id == User.id)
        .filter(User.id == principal_id)
        .first()
    )
    if user_row is None or user_row[1].token_version != payload["ver"]:
        return None
    principal = (user_row[0], user_row[1])
    request.state.fg_principal = principal
    logctx.user_id_var.set(user_row[0].id)
    return principal


def resolve_bearer_user(request: Request) -> tuple[User, Account] | None:
    principal = resolve_bearer_principal(request)
    if principal is None or not isinstance(principal[0], User):
        return None
    return principal


def resolve_bearer_system_admin(request: Request) -> tuple[SystemAdmin, SystemAdminAccount] | None:
    principal = resolve_bearer_principal(request)
    if principal is None or not isinstance(principal[0], SystemAdmin):
        return None
    return principal


def require_authenticated_user(request: Request) -> tuple[User, Account]:
    """严格认证：失败统一 401。"""
    resolved = resolve_bearer_user(request)
    if resolved is None:
        raise_api_error(
            401,
            "AUTH_UNAUTHORIZED",
            UNIFIED_CREDENTIAL_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return resolved


def require_authenticated_principal(
    request: Request,
) -> Principal:
    principal = resolve_bearer_principal(request)
    if principal is None:
        raise_api_error(
            401,
            "AUTH_UNAUTHORIZED",
            UNIFIED_CREDENTIAL_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_system_admin(request: Request) -> tuple[SystemAdmin, SystemAdminAccount]:
    resolved = resolve_bearer_system_admin(request)
    if resolved is None:
        raise_api_error(403, "FORBIDDEN_SYSTEM_ADMIN_ONLY", "仅系统管理员可执行该操作")
    return resolved


def require_platform_principal(request: Request) -> Principal:
    principal = require_authenticated_principal(request)
    if isinstance(principal[0], SystemAdmin):
        return principal
    if not is_platform_operator(get_db(request), principal[1]):
        raise_api_error(403, "FORBIDDEN_ADMIN_ONLY", "仅系统管理员可执行该操作")
    return principal


async def require_pin_changed(request: Request) -> None:
    """app 级全局依赖：首登未改 PIN 时仅放行白名单端点。"""
    route = request.scope.get("route")
    key = (request.method, str(getattr(route, "path", request.url.path)))
    # 白名单/公开端点仍解析凭据一次，供路由依赖缓存复用（单次 DB 查询）
    if key in PIN_GATE_WHITELIST or key in PIN_GATE_PUBLIC:
        resolve_bearer_principal(request)
        return
    resolved = resolve_bearer_principal(request)
    if resolved is None:
        return  # 未认证：交给路由自身的严格认证依赖返回 401
    _principal, account = resolved
    if account.pin_must_change:
        raise_api_error(403, PIN_CHANGE_REQUIRED, "请先修改初始 PIN 码后再继续操作")
