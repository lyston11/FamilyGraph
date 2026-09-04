"""认证依赖：当前用户解析（token_version 比对）与首登改 PIN 全局门禁。

家庭 listener 只解析 family_user 主体（09-04 起系统管理员令牌在独立
admin_app 的签发域签发，家庭依赖拒绝一切非 family_user 主体类型）。
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
from app.services.platform_roles import is_platform_operator
from app.utils import security

Principal = tuple[User, Account]


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


def resolve_bearer_user(request: Request) -> tuple[User, Account] | None:
    """解析家庭主体；主体类型由签名 JWT + 服务端查表共同决定。

    非 family_user 主体类型（含旧 system_admin 声明与伪造 token）一律拒绝：
    系统管理员令牌由独立签发域签发，家庭 listener 永不解析（09-04 SF-F1）。
    """
    if hasattr(request.state, "fg_principal"):
        return cast(tuple[User, Account] | None, request.state.fg_principal)
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
    if payload.get("principal_type", "family_user") != "family_user":
        return None
    session = get_db(request)
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


def require_platform_principal(request: Request) -> Principal:
    """家庭 listener 上的平台运营者依赖：只认 family_user + platform_operator 角色。

    系统管理员主体由独立签发域签发、仅 admin_app 解析（09-04）；本依赖绝不
    接受其他主体类型。旧 admin.py（永久不注册）与 admin_agent 共用此合同。
    """
    principal = require_authenticated_user(request)
    if not is_platform_operator(get_db(request), principal[1]):
        raise_api_error(403, "FORBIDDEN_ADMIN_ONLY", "仅平台运营者可执行该操作")
    return principal


async def require_pin_changed(request: Request) -> None:
    """app 级全局依赖：首登未改 PIN 时仅放行白名单端点。"""
    # 非家庭 API 面（/admin-api、/internal 隔离 catch-all）：保持纯 404 语义，
    # 不解析主体、不触发门禁（家庭端不得从后台路径感知任何门禁存在）。
    if not request.url.path.startswith("/api/"):
        return
    route = request.scope.get("route")
    key = (request.method, str(getattr(route, "path", request.url.path)))
    # 白名单/公开端点仍解析凭据一次，供路由依赖缓存复用（单次 DB 查询）
    if key in PIN_GATE_WHITELIST or key in PIN_GATE_PUBLIC:
        resolve_bearer_user(request)
        return
    resolved = resolve_bearer_user(request)
    if resolved is None:
        return  # 未认证：交给路由自身的严格认证依赖返回 401
    _principal, account = resolved
    if account.pin_must_change:
        raise_api_error(403, PIN_CHANGE_REQUIRED, "请先修改初始 PIN 码后再继续操作")
