"""当前用户路由：GET /me、PUT /me/name、PUT /me/pin。

PUT /me/pin 在 pin_must_change 白名单内（deps.PIN_GATE_WHITELIST），
改毕 token_version+1 使旧 access/refresh 全部即刻失效。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import UNIFIED_CREDENTIAL_MESSAGE, raise_api_error
from app.models import Account, User
from app.schemas.auth import ChangeNameRequest, ChangePinRequest, UserOut, public_user_payload
from app.services import audit
from app.services import refresh_session as refresh_session_service
from app.utils import security

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
def get_me(
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    user, _account = identity
    return UserOut(**public_user_payload(user))


@router.put("/me/name", response_model=UserOut)
def change_name(
    payload: ChangeNameRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改名（锁定决策 A1：随时可改；Q2 待定默认不限频）。不改名不失效会话。"""
    user, _account = identity
    old_name = user.name
    user.name = payload.name.strip()
    audit.write_audit(
        session,
        action="name_changed",
        actor_id=user.id,
        target_id=user.id,
        ip=request.client.host if request.client else None,
        detail={"old_name": old_name},
    )
    session.commit()
    return UserOut(**public_user_payload(user))


@router.put("/me/pin", response_model=UserOut)
def change_pin(
    payload: ChangePinRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> UserOut:
    """改 PIN：验旧 PIN → 新哈希 → pin_must_change=false（首登认领）→ 版本+1。"""
    user, account = identity
    if not security.verify_pin(payload.old_pin, account.pin_hash):
        # 旧 PIN 错误同样走防枚举统一文案
        raise_api_error(401, "AUTH_INVALID_CREDENTIALS", UNIFIED_CREDENTIAL_MESSAGE)

    account.pin_hash = security.hash_pin(payload.new_pin)
    account.pin_must_change = False
    account.token_version += 1
    account.failed_attempts = 0
    account.locked_until = None
    # 全部旧 refresh 会话一并作废（PRD：refresh 无法再换新）
    refresh_session_service.revoke_all_active(session, user.id, ip=None, reason="pin_change")
    audit.write_audit(
        session,
        action="pin_changed",
        actor_id=user.id,
        target_id=user.id,
        ip=request.client.host if request.client else None,
        detail={},
    )
    session.commit()
    return UserOut(**public_user_payload(user))
