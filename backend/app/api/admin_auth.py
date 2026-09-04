"""/admin-api 认证路由（09-04：独立 admin_app，仅 8002 listener 可达）。

路由面固定为：POST /auth/login、POST /auth/refresh、POST /auth/logout、
GET /auth/me、PUT /auth/password、PUT /auth/username（+ GET /health，
见 main.py）。家庭 listener 对 /admin-api/* 一律普通 404（main.py catch-all）。

首登改密白名单（design §6）：password / refresh / logout；me 与 username 在
password_must_change=true 时 403 ADMIN_PASSWORD_CHANGE_REQUIRED。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_principal, require_admin_ready
from app.api.deps import get_db
from app.errors import (
    ADMIN_SESSION_MESSAGE,
    ADMIN_UNAUTHORIZED,
    raise_api_error,
)
from app.schemas.admin_auth import (
    AdminLoginRequest,
    AdminPasswordChangeRequest,
    AdminRefreshRequest,
    AdminSessionOut,
    AdminTokenPairResponse,
    AdminUsernameChangeRequest,
    admin_session_payload,
)
from app.schemas.auth import LogoutRequest, LogoutResponse
from app.services import admin_auth
from app.utils import admin_security

router = APIRouter(prefix="/admin-api", tags=["admin-auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/auth/login", response_model=AdminTokenPairResponse)
def login(
    payload: AdminLoginRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> AdminTokenPairResponse:
    """管理员登录：username + 强密码；统一文案不泄露账号存在性。"""
    ip = _client_ip(request)
    try:
        admin, account, access, refresh_raw = admin_auth.authenticate(
            session, username=payload.username, password=payload.password, ip=ip
        )
    except HTTPException:
        # 失败计数/锁定/审计必须落库后原样抛出（与家庭登录同合同）
        session.commit()
        raise
    session.commit()
    return AdminTokenPairResponse(
        access_token=access,
        refresh_token=refresh_raw,
        admin=AdminSessionOut(**admin_session_payload(admin, account)),
    )


@router.post("/auth/refresh", response_model=AdminTokenPairResponse)
def refresh_tokens(
    payload: AdminRefreshRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> AdminTokenPairResponse:
    """轮换 admin refresh：绝对有效期；旧 token 重用触发全会话撤销 + 审计。"""
    ip = _client_ip(request)
    try:
        admin, account, new_refresh_raw = admin_auth.rotate_refresh(
            session, payload.refresh_token, ip
        )
    except admin_auth.AdminRefreshReuseDetectedError:
        # 重用攻击：全会话撤销与审计必须落库；拒绝文案与会话失效统一
        session.commit()
        raise_api_error(401, ADMIN_UNAUTHORIZED, ADMIN_SESSION_MESSAGE)
    except admin_auth.AdminRefreshInvalidError:
        session.commit()
        raise_api_error(401, ADMIN_UNAUTHORIZED, ADMIN_SESSION_MESSAGE)
    session.commit()
    return AdminTokenPairResponse(
        access_token=admin_security.create_admin_access_token(admin.id, account.password_version),
        refresh_token=new_refresh_raw,
        admin=AdminSessionOut(**admin_session_payload(admin, account)),
    )


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(
    payload: LogoutRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_principal),
) -> LogoutResponse:
    """登出 = 撤销对应 admin refresh 会话；白名单端点（首登未改密可用）。"""
    admin, _account = identity
    admin_auth.revoke_by_raw_token(session, admin.id, payload.refresh_token)
    session.commit()
    return LogoutResponse(success=True)


@router.get("/auth/me", response_model=AdminSessionOut)
def me(identity: AdminPrincipal = Depends(require_admin_ready)) -> AdminSessionOut:
    """当前管理员会话投影（白名单字段；首登未改密 403）。"""
    admin, account = identity
    return AdminSessionOut(**admin_session_payload(admin, account))


@router.put("/auth/password", response_model=AdminSessionOut)
def change_password(
    payload: AdminPasswordChangeRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_principal),
) -> AdminSessionOut:
    """修改密码：需当前密码；成功后撤销全部会话并清理凭据文件（SF-F3/F5）。"""
    admin, account = identity
    admin_auth.change_password(
        session,
        admin,
        account,
        current_password=payload.current_password,
        new_password=payload.new_password,
        ip=_client_ip(request),
    )
    session.commit()
    # 事务提交后再删除 bootstrap 凭据文件；删除失败回置 password_must_change
    admin_auth.finalize_credential_file(session, admin.id)
    return AdminSessionOut(**admin_session_payload(admin, account))


@router.put("/auth/username", response_model=AdminSessionOut)
def change_username(
    payload: AdminUsernameChangeRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AdminSessionOut:
    """修改用户名：需当前密码；成功后撤销全部会话（SF-F5）。"""
    admin, account = identity
    admin_auth.change_username(
        session,
        admin,
        account,
        current_password=payload.current_password,
        new_username=payload.username,
        ip=_client_ip(request),
    )
    session.commit()
    return AdminSessionOut(**admin_session_payload(admin, account))
