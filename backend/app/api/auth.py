"""认证路由：login / login/select / refresh / logout（AD-2 全流程）。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    get_db,
    require_authenticated_principal,
)
from app.errors import (
    CHALLENGE_INVALID,
    INVALID_REFRESH_TOKEN,
    UNIFIED_CREDENTIAL_MESSAGE,
    raise_api_error,
)
from app.models.account import Account
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.models.user import User
from app.schemas.auth import (
    ChallengeCandidate,
    ChallengeResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    SelectCandidateRequest,
    TokenPairResponse,
    UserOut,
    public_system_admin_payload,
    public_user_payload,
)
from app.services import (
    audit,
    auth_guard,
)
from app.services import (
    challenge as challenge_service,
)
from app.services import (
    refresh_session as refresh_session_service,
)
from app.utils import security
from app.utils.timeutil import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _token_pair_response(
    session: Session, user: User, account: Account, refresh_raw: str
) -> TokenPairResponse:
    from app.services.platform_roles import is_platform_operator

    access = security.create_access_token(
        user.id, account.token_version, is_platform_operator(session, account)
    )
    return TokenPairResponse(
        access_token=access,
        refresh_token=refresh_raw,
        user=UserOut(**public_user_payload(session, user)),
    )


def _system_token_pair_response(
    admin: SystemAdmin, account: SystemAdminAccount, refresh_raw: str
) -> TokenPairResponse:
    access = security.create_access_token(
        admin.id,
        account.token_version,
        is_platform_operator=True,
        principal_type="system_admin",
    )
    return TokenPairResponse(
        access_token=access,
        refresh_token=refresh_raw,
        user=UserOut(**public_system_admin_payload(admin, account)),
    )


@router.post("/login", response_model=TokenPairResponse | ChallengeResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> TokenPairResponse | ChallengeResponse:
    """名字 + PIN 登录；同名同 PIN 多命中返回 409 challenge。"""
    ip = _client_ip(request)
    system_rows = (
        session.query(SystemAdmin, SystemAdminAccount)
        .join(SystemAdminAccount, SystemAdminAccount.system_admin_id == SystemAdmin.id)
        .filter(SystemAdmin.login_name == payload.name.strip())
        .all()
    )
    system_accounts = [account for _admin, account in system_rows]
    try:
        auth_guard.ensure_not_locked(system_accounts)  # shared lock-window logic
    except auth_guard.AccountLockedError as locked:
        raise_api_error(
            429,
            "ACCOUNT_LOCKED",
            "失败次数过多，账户已临时锁定，请稍后再试",
            detail={"retry_after_seconds": locked.retry_after_seconds},
            headers={"Retry-After": str(locked.retry_after_seconds)},
        )
    rows = (
        session.query(User, Account)
        .join(Account, Account.user_id == User.id)
        .filter(User.name == payload.name.strip())
        .all()
    )
    accounts = [account for _user, account in rows]
    verified_system = [
        (admin, account)
        for admin, account in system_rows
        if security.verify_pin(payload.pin, account.pin_hash)
    ]
    if verified_system:
        admin, account = verified_system[0]
        account.failed_attempts = 0
        account.locked_until = None
        refresh_raw = refresh_session_service.issue_system_admin_refresh_session(
            session, admin, account, rotated_from=None
        )
        audit.write_audit(
            session,
            action="system_admin_login_succeeded",
            actor_id=None,
            target_id=admin.id,
            ip=ip,
            detail={"principal_type": "system_admin"},
        )
        session.commit()
        return _system_token_pair_response(admin, account, refresh_raw)

    if not verified_system and system_accounts and not accounts:
        for account in system_accounts:
            account.failed_attempts += 1
            if account.failed_attempts >= 5:
                account.locked_until = utcnow() + timedelta(minutes=15)
        session.commit()

    # The family account flow remains below. A system principal is never
    # represented as a User or admitted by require_authenticated_user.
    try:
        auth_guard.ensure_not_locked(accounts)
    except auth_guard.AccountLockedError as locked:
        raise_api_error(
            429,
            "ACCOUNT_LOCKED",
            "失败次数过多，账户已临时锁定，请稍后再试",
            detail={"retry_after_seconds": locked.retry_after_seconds},
            headers={"Retry-After": str(locked.retry_after_seconds)},
        )

    if not accounts:
        # 用户名不存在：等开销空校验防时序枚举，统一文案
        security.verify_dummy_pin(payload.pin)
        raise_api_error(401, "AUTH_INVALID_CREDENTIALS", UNIFIED_CREDENTIAL_MESSAGE)

    verified: list[tuple[User, Account]] = [
        (user, account)
        for user, account in rows
        if security.verify_pin(payload.pin, account.pin_hash)
    ]

    if not verified:
        auth_guard.register_failures(session, accounts, ip)
        auth_guard.audit_login_failure_if_needed(session, accounts, ip)
        session.commit()
        raise_api_error(401, "AUTH_INVALID_CREDENTIALS", UNIFIED_CREDENTIAL_MESSAGE)

    if len(verified) > 1:
        challenge = challenge_service.create_challenge(
            session, [user.id for user, _account in verified], ip
        )
        session.commit()

        # 候选提示补代管创建者名（m1a design 兼容项）：一次查询取全 created_by 名字
        creator_ids = {user.created_by for user, _account in verified if user.created_by}
        creator_names: dict[int, str] = {}
        if creator_ids:
            creators = session.query(User).filter(User.id.in_(creator_ids)).all()
            creator_names = {creator.id: creator.name for creator in creators}
        # 同名同 PIN 消歧：HTTP 409 + {challenge_id, candidates}（architecture.md §2）
        response.status_code = 409
        return ChallengeResponse(
            challenge_id=challenge.jti,
            candidates=[
                ChallengeCandidate(
                    id=user.id,
                    name=user.name,
                    created_by_name=creator_names.get(user.created_by) if user.created_by else None,
                )
                for user, _account in verified
            ],
        )

    user, account = verified[0]
    auth_guard.register_success(account)
    refresh_raw = refresh_session_service.issue_refresh_session(session, account, rotated_from=None)
    session.commit()
    return _token_pair_response(session, user, account, refresh_raw)


@router.post("/login/select", response_model=TokenPairResponse)
def select_candidate(
    payload: SelectCandidateRequest, request: Request, session: Session = Depends(get_db)
) -> TokenPairResponse:
    """消歧第二步：单事务原子消费 challenge 后签发 token。"""
    ip = _client_ip(request)
    accepted = challenge_service.consume_challenge(
        session, payload.challenge_id, ip, payload.user_id
    )
    if not accepted:
        # 过期/已用/重放/IP 不符/越权候选：同一拒绝路径 + 审计留痕（PRD 验收）
        audit.write_audit(
            session,
            action="challenge_rejected",
            target_id=payload.user_id,
            ip=ip,
            detail={"reason": "consume_failed"},
        )
        session.commit()
        raise_api_error(401, CHALLENGE_INVALID, "登录校验已失效，请重新登录")

    row = (
        session.query(User, Account)
        .join(Account, Account.user_id == User.id)
        .filter(User.id == payload.user_id)
        .first()
    )
    if row is None:  # 理论不可达：候选集来自真实账号
        session.rollback()
        raise_api_error(401, CHALLENGE_INVALID, "登录校验已失效，请重新登录")
    user, account = row
    auth_guard.register_success(account)
    refresh_raw = refresh_session_service.issue_refresh_session(session, account, rotated_from=None)
    session.commit()
    return _token_pair_response(session, user, account, refresh_raw)


@router.post("/refresh", response_model=TokenPairResponse)
def refresh_tokens(
    payload: RefreshRequest, request: Request, session: Session = Depends(get_db)
) -> TokenPairResponse:
    """轮换 refresh；重用已 revoked token 触发全会话撤销 + 审计。"""
    ip = _client_ip(request)
    try:
        decoded = security.decode_token(payload.refresh_token, security.REFRESH_TOKEN_TYPE)
        if decoded.get("principal_type") == "system_admin":
            admin, system_account, new_refresh_raw = refresh_session_service.rotate_system_admin(
                session, payload.refresh_token, ip
            )
            response = _system_token_pair_response(admin, system_account, new_refresh_raw)
            session.commit()
            return response
    except refresh_session_service.RefreshReuseDetectedError:
        session.commit()
        raise_api_error(401, INVALID_REFRESH_TOKEN, "登录状态已失效，请重新登录")
    except (refresh_session_service.InvalidRefreshTokenError, security.TokenDecodeError):
        session.commit()
        raise_api_error(401, INVALID_REFRESH_TOKEN, "登录状态已失效，请重新登录")

    try:
        account, new_refresh_raw = refresh_session_service.rotate(
            session, payload.refresh_token, ip
        )
    except refresh_session_service.RefreshReuseDetectedError:
        session.commit()  # 撤销与审计必须落库
        raise_api_error(401, INVALID_REFRESH_TOKEN, "登录状态已失效，请重新登录")
    except refresh_session_service.InvalidRefreshTokenError:
        session.commit()
        raise_api_error(401, INVALID_REFRESH_TOKEN, "登录状态已失效，请重新登录")
    user = session.query(User).filter(User.id == account.user_id).one()
    response = _token_pair_response(session, user, account, new_refresh_raw)
    session.commit()
    return response


@router.post("/logout", response_model=LogoutResponse)
def logout(
    payload: LogoutRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] | tuple[SystemAdmin, SystemAdminAccount] = Depends(
        require_authenticated_principal
    ),
) -> LogoutResponse:
    """登出 = 撤销对应 refresh 会话（architecture.md §2）；白名单端点。"""
    principal, _account = identity
    if isinstance(principal, SystemAdmin):
        refresh_session_service.revoke_system_admin_by_raw_token(
            session, principal.id, payload.refresh_token
        )
    else:
        refresh_session_service.revoke_by_raw_token(session, principal.id, payload.refresh_token)
    session.commit()
    return LogoutResponse(success=True)
