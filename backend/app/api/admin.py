"""管理员后台路由（m4b，A4 三职责 + 审计只读）。is_admin only。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import raise_api_error
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import audit
from app.utils import security

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(identity: tuple[User, Account]) -> User:
    actor, _account = identity
    if not actor.is_admin:
        raise_api_error(403, "FORBIDDEN_ADMIN_ONLY", "仅管理员可执行该操作")
    return actor


@router.get("/users")
def admin_list_users(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """全量用户列表（管理视图）。"""
    _require_admin(identity)
    users = session.query(User).order_by(User.id).all()
    out = []
    for u in users:
        acc = session.scalar(select(Account).where(Account.user_id == u.id))
        out.append(
            {
                "id": u.id,
                "name": u.name,
                "is_admin": u.is_admin,
                "gender": u.gender,
                "privacy_mode": u.privacy_mode,
                "claim_status": u.claim_status,
                "created_by": u.created_by,
                "locked_until": acc.locked_until.isoformat() if acc and acc.locked_until else None,
                "created_at": u.created_at.isoformat(),
            }
        )
    return out


class ResetPinPayload(BaseModel):
    confirm: bool = Field(description="二次确认标志")


@router.post("/users/{user_id}/reset-pin")
def admin_reset_pin(
    user_id: int,
    payload: ResetPinPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, str]:
    """重置任意用户 PIN（A4）：新随机 PIN 一次性返回；旧会话即刻失效。"""
    actor = _require_admin(identity)
    if not payload.confirm:
        raise_api_error(422, "VALIDATION_ERROR", "缺少二次确认")

    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, "USER_NOT_FOUND", "用户不存在")
    account = session.scalar(select(Account).where(Account.user_id == user_id))
    if account is None:
        raise_api_error(404, "USER_NOT_FOUND", "用户无登录凭据")

    new_pin = security.generate_pin()
    account.pin_hash = security.hash_pin(new_pin)
    account.pin_must_change = True  # 强制首登再改，管理员也不应知道其长期 PIN
    account.token_version += 1
    account.failed_attempts = 0
    account.locked_until = None

    from app.services import refresh_session as refresh_service

    refresh_service.revoke_all_active(session, user_id, ip=None, reason="admin_reset")

    ip = request.client.host if request.client else None
    audit.write_audit(
        session,
        action="pin_reset",
        actor_id=actor.id,
        target_id=user_id,
        ip=ip,
        detail={"admin_action": True},
    )
    session.commit()
    return {"pin": new_pin}


class AdminUpdateUserPayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    privacy_mode: str | None = Field(default=None, pattern="^(perpetual|handover)$")
    transfer_custody_to: int | None = Field(default=None, gt=0)


@router.patch("/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: AdminUpdateUserPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """数据兜底修正：改名 / 改归属模式 / 转移代管权。全部走 audit。"""
    actor = _require_admin(identity)
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, "USER_NOT_FOUND", "用户不存在")

    changes: dict[str, Any] = {}
    if payload.name is not None:
        target.name = payload.name.strip()
        changes["name"] = target.name
    if payload.privacy_mode is not None:
        target.privacy_mode = payload.privacy_mode
        changes["privacy_mode"] = payload.privacy_mode
    if payload.transfer_custody_to is not None:
        new_guardian = session.get(User, payload.transfer_custody_to)
        if new_guardian is None:
            raise_api_error(404, "USER_NOT_FOUND", "新代管人不存在")
        target.created_by = new_guardian.id
        changes["transferred_to"] = new_guardian.id
    if not changes:
        raise_api_error(422, "VALIDATION_ERROR", "未提供任何修改项")

    ip = request.client.host if request.client else None
    audit.write_audit(
        session,
        action="admin_user_updated",
        actor_id=actor.id,
        target_id=user_id,
        ip=ip,
        detail={"changes": changes},
    )
    session.commit()
    return {"id": target.id, **changes}


@router.get("/audit-logs")
def admin_audit_logs(
    limit: int = 200,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """审计日志只读列表（倒序，默认最近 200 条）。"""
    _require_admin(identity)
    rows = session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            "action": r.action,
            "target_id": r.target_id,
            "ip": r.ip,
            "detail_json": r.detail_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
