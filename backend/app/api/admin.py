"""平台运营后台路由（v2 §0.2：platform_operator 专属 + 审计只读）。

operator 角色仅管理系统代码/Provider/白名单/安全策略；本后台不提供
家庭数据浏览权。数据兑底（更正决议、争议决议）属 break-glass：理由必填 +
完整审计，且仅返回请求本身的最小必要数据，不产生日常浏览权。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, require_authenticated_user, require_platform_principal
from app.commands import admin as admin_commands
from app.commands import data_rights as data_right_commands
from app.commands import manager_applications as manager_application_commands
from app.commands import owner_onboarding as onboarding_commands
from app.commands.context import ActorContext
from app.errors import raise_api_error
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.system_admin import SystemAdmin
from app.models.user import User
from app.models.v2_foundation import ClaimDispute, DataRightRequest, OwnerInvitation
from app.schemas.space import ManagerApplicationDecision, ManagerApplicationOut
from app.schemas.v2_foundation import (
    DataRightRequestOut,
    OperatorResolveCorrection,
    OwnerInvitationCreated,
    OwnerInvitationOut,
)
from app.services import audit
from app.services.platform_roles import is_platform_operator, require_platform_operator
from app.utils import security

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(identity: tuple[User, Account], session: Session) -> User:
    actor, account = identity
    require_platform_operator(session, account)
    return actor


@router.get("/users")
def admin_list_users(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """平台账号列表（系统管理视图；仅平台元数据，不含家庭姓名/性别/出生）。

    operator 只管理账号生命周期（认领/确档/锁定）与系统配置；浏览或兜底家庭
    资料必须走带原因、带审计的 break-glass 通道（见下方 lookup / PATCH）。
    """
    _require_admin(identity, session)
    users = session.query(User).order_by(User.id).all()
    out = []
    for u in users:
        acc = session.scalar(select(Account).where(Account.user_id == u.id))
        out.append(
            {
                "id": u.id,
                "is_admin": is_platform_operator(session, acc),
                "claim_status": acc.status if acc else None,
                "profile_status": u.profile_status,
                "locked_until": acc.locked_until.isoformat() if acc and acc.locked_until else None,
                "created_at": u.created_at.isoformat(),
            }
        )
    return out


@router.get("/users/lookup")
def admin_user_lookup(
    request: Request,
    name: str = Query(min_length=1, max_length=100),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """break-glass 账号检索：按名字前缀还原账号标识（审计留痕）。

    仅供重置 PIN / 数据兑底等人工兜底定位账号使用；不提供家庭字段浏览。
    """
    actor = _require_admin(identity, session)
    rows = (
        session.query(User)
        .filter(User.name.like(f"{name.strip()}%"))
        .order_by(User.id)
        .limit(20)
        .all()
    )
    out = []
    for u in rows:
        acc = session.scalar(select(Account).where(Account.user_id == u.id))
        out.append(
            {
                "id": u.id,
                "name": u.name,
                "claim_status": acc.status if acc else None,
                "profile_status": u.profile_status,
            }
        )
    audit.write_audit(
        session,
        action="operator_name_lookup",
        actor_id=actor.id,
        ip=_client_ip(request),
        detail={"prefix": name.strip(), "hits": len(out)},
    )
    session.commit()
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
    """重置任意账号 PIN（break-glass 前身：全部审计留痕）；旧会话即刻失效。"""
    actor = _require_admin(identity, session)
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
    note: str = Field(min_length=1, max_length=1000)


@router.patch("/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: AdminUpdateUserPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """数据兜底修正（break-glass）：理由必填；授权/写入/事件/审计在命令层单事务。"""
    ctx = ActorContext.from_identity(identity[0], identity[1], ip=_client_ip(request))
    target, changes = admin_commands.admin_update_user(
        session,
        ctx,
        user_id,
        name=payload.name,
        privacy_mode=payload.privacy_mode,
        transfer_custody_to=payload.transfer_custody_to,
        note=payload.note,
    )
    return {"id": target.id, **changes}


@router.get("/audit-logs")
def admin_audit_logs(
    limit: int = 200,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """审计日志只读列表（倒序，默认最近 200 条）。"""
    _require_admin(identity, session)
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


# ---- owner onboarding 邀请管理（AC-F3；兑换端点在 governance 路由）----


@router.post("/owner-invitations", status_code=201, response_model=OwnerInvitationCreated)
def create_owner_invitation(
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> OwnerInvitationCreated:
    """签发短期单次可撤销邀请；token 明文仅本次响应返回，服务端只存 hash。"""
    actor = _require_admin(identity, session)
    ctx = ActorContext.from_identity(actor, identity[1], ip=_client_ip(request))
    invitation, raw_token = onboarding_commands.create_owner_invitation(session, ctx)
    out = OwnerInvitationOut.model_validate(invitation).model_dump()
    return OwnerInvitationCreated(token=raw_token, **out)


@router.get("/owner-invitations", response_model=list[OwnerInvitationOut])
def list_owner_invitations(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[OwnerInvitationOut]:
    _require_admin(identity, session)
    rows = (
        session.scalars(select(OwnerInvitation).order_by(OwnerInvitation.id.desc()).limit(200))
        .unique()
        .all()
    )
    return [OwnerInvitationOut.model_validate(r) for r in rows]


@router.post("/owner-invitations/{invitation_id}/revoke", response_model=OwnerInvitationOut)
def revoke_owner_invitation(
    invitation_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> OwnerInvitationOut:
    actor = _require_admin(identity, session)
    ctx = ActorContext.from_identity(actor, identity[1], ip=_client_ip(request))
    row = onboarding_commands.revoke_owner_invitation(session, ctx, invitation_id)
    return OwnerInvitationOut.model_validate(row)


# ---- 数据权利 operator 决议（break-glass：最小数据 + 理由必填 + 审计）----


@router.get("/data-rights", response_model=list[DataRightRequestOut])
def admin_list_data_rights(
    status: str | None = None,
    type: str | None = None,  # noqa: A002 - 查询参数名与列名一致
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[DataRightRequestOut]:
    """待处理请求列表（仅请求行本身；不含任何其他家庭数据）。"""
    _require_admin(identity, session)
    stmt = select(DataRightRequest).order_by(DataRightRequest.id.desc()).limit(200)
    if status is not None:
        stmt = stmt.where(DataRightRequest.status == status)
    if type is not None:
        stmt = stmt.where(DataRightRequest.type == type)
    rows = list(session.scalars(stmt).all())
    return [DataRightRequestOut.model_validate(r) for r in rows]


@router.post("/data-rights/{request_id}/resolve-correction", response_model=DataRightRequestOut)
def resolve_correction(
    request_id: int,
    payload: OperatorResolveCorrection,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> DataRightRequestOut:
    """决议更正申请：批准时按 payload.fields 应用白名单字段（审计含 break_glass 标记）。"""
    actor = _require_admin(identity, session)
    ctx = ActorContext.from_identity(actor, identity[1], ip=_client_ip(request))
    row = data_right_commands.resolve_correction_request(
        session, ctx, request_id, approve=payload.approve, note=payload.note
    )
    return DataRightRequestOut.model_validate(row)


@router.get("/claim-disputes")
def admin_list_claim_disputes(
    status: str | None = None,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """争议列表（最小披露：profile_id / 状态 / 时间；证据原文仅决议时经命令层读取）。"""
    _require_admin(identity, session)
    stmt = select(ClaimDispute).order_by(ClaimDispute.id.desc()).limit(200)
    if status is not None:
        stmt = stmt.where(ClaimDispute.status == status)
    rows = list(session.scalars(stmt).all())
    return [
        {
            "id": r.id,
            "profile_id": r.profile_id,
            "raised_by_account_id": r.raised_by_account_id,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "resolution_note": r.resolution_note,
        }
        for r in rows
    ]


class DisputeResolvePayload(BaseModel):
    outcome: str = Field(pattern="^(resolved_claim|resolved_reject)$")
    note: str = Field(min_length=1, max_length=1000)


@router.post("/claim-disputes/{dispute_id}/resolve")
def resolve_claim_dispute(
    dispute_id: int,
    body: DisputeResolvePayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """决议认领争议：evidence 原文永不覆盖；理由必填 + break-glass 审计。"""
    actor = _require_admin(identity, session)
    ctx = ActorContext.from_identity(actor, identity[1], ip=_client_ip(request))
    dispute = data_right_commands.resolve_claim_dispute(
        session, ctx, dispute_id, outcome=body.outcome, note=body.note
    )
    return {
        "id": dispute.id,
        "status": dispute.status,
        "resolution_note": dispute.resolution_note,
    }


# ---- 空间管理者申请审批（任务 08-30-space-manager-approval；require_platform_operator）----


@router.get("/manager-applications", response_model=list[ManagerApplicationOut])
def admin_list_manager_applications(
    status: str | None = None,
    session: Session = Depends(get_db),
    identity: Principal = Depends(require_platform_principal),
) -> list[ManagerApplicationOut]:
    """审批队列（仅治理所需的申请人与目标空间元数据）。"""
    if isinstance(identity[0], User):
        _require_admin(identity, session)
    rows = manager_application_commands.list_applications(session, status=status)
    return [manager_application_commands.serialize_application(session, row) for row in rows]


@router.post(
    "/manager-applications/{application_id}/decision", response_model=ManagerApplicationOut
)
def decide_manager_application(
    application_id: int,
    payload: ManagerApplicationDecision,
    request: Request,
    session: Session = Depends(get_db),
    identity: Principal = Depends(require_platform_principal),
) -> ManagerApplicationOut:
    """裁决申请；系统管理员批准已有管理员的申请必须先发出交接工单。"""
    if isinstance(identity[0], SystemAdmin):
        row = manager_application_commands.decide_manager_application_as_system_admin(
            session,
            application_id,
            decision=payload.decision,
            note=payload.note,
            system_admin_id=identity[0].id,
            ip=_client_ip(request),
        )
        return manager_application_commands.serialize_application(session, row)
    actor = _require_admin(identity, session)
    ctx = ActorContext.from_identity(actor, identity[1], ip=_client_ip(request))
    row = manager_application_commands.decide_manager_application(
        session,
        ctx,
        application_id,
        decision=payload.decision,
        note=payload.note,
        decided_by=actor.id,
    )
    return manager_application_commands.serialize_application(session, row)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
