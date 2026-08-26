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
from app.utils import security

# 白名单：(method, 路由模板路径)。路径含 /api 前缀（include_router 时注入）。
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


def resolve_bearer_user(request: Request) -> tuple[User, Account] | None:
    """解析 Authorization 头；无效/缺失返回 None（不抛错）。

    结果缓存在 request.state，同一请求内（全局门禁 + 路由依赖）只解析一次。
    token_version 比对：改 PIN 等敏感操作后旧 access 即刻失效（PRD 验收）。
    """
    if hasattr(request.state, "fg_resolved"):
        resolved: tuple[User, Account] | None = request.state.fg_resolved
        return resolved
    request.state.fg_resolved = None
    authorization = request.headers.get("Authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return None
    try:
        payload = security.decode_token(raw_token, security.ACCESS_TOKEN_TYPE)
    except security.TokenDecodeError:
        return None
    session = get_db(request)
    row = (
        session.query(User, Account)
        .join(Account, Account.user_id == User.id)
        .filter(User.id == int(payload["sub"]))
        .first()
    )
    if row is None:
        return None
    user, account = row
    if account.token_version != payload["ver"]:
        return None
    request.state.fg_resolved = (user, account)
    # 结构化日志 user_id 字段回填（logging-guidelines.md）；请求任务隔离，无跨请求泄漏
    logctx.user_id_var.set(user.id)
    return user, account


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


async def require_pin_changed(request: Request) -> None:
    """app 级全局依赖：首登未改 PIN 时仅放行白名单端点。

    app dependencies 在路由匹配后执行，scope["route"] 必然存在。
    """
    route = request.scope.get("route")
    key = (request.method, str(getattr(route, "path", request.url.path)))
    # 白名单/公开端点仍解析凭据一次，供路由依赖缓存复用（单次 DB 查询）
    if key in PIN_GATE_WHITELIST or key in PIN_GATE_PUBLIC:
        resolve_bearer_user(request)
        return
    resolved = resolve_bearer_user(request)
    if resolved is None:
        return  # 未认证：交给路由自身的严格认证依赖返回 401
    _user, account = resolved
    if account.pin_must_change:
        raise_api_error(403, PIN_CHANGE_REQUIRED, "请先修改初始 PIN 码后再继续操作")
