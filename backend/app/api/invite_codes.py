"""邀请码路由（09-05 Chunk C，全部挂在 8000 家庭 listener）。

薄路由惯例（AC-F7）：schema 解析 + 认证上下文构造 → 命令层 → 序列化；
授权、FSM、写入、事件与审计在命令层同一短事务内完成（命令在
commands/registration.py，码原语在 services/invite_codes.py）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import registration as registration_commands
from app.commands.context import ActorContext
from app.models.account import Account
from app.models.invite_code import InviteCode
from app.models.user import User
from app.schemas.invite_code import (
    CreateInviteCodeRequest,
    InviteCodeOut,
    RedeemInviteCodeRequest,
)
from app.services import invite_codes

router = APIRouter(tags=["invite-codes"])


def _ctx(request: Request, identity: tuple[User, Account]) -> ActorContext:
    actor, account = identity
    ip = request.client.host if request.client else None
    return ActorContext.from_identity(actor, account, ip=ip)


def _serialize(session: Session, codes: list[InviteCode]) -> list[InviteCodeOut]:
    """码投影：批量补空间名（列表展示）；不含 creator_id 等多余字段。"""
    names = invite_codes.space_names(session, codes)
    return [
        InviteCodeOut.model_validate(
            {
                "id": code.id,
                "code": code.code,
                "kind": code.kind,  # DB CHECK 保证枚举合法；model_validate 运行时复核
                "space_id": code.space_id,
                "space_name": names.get(code.space_id) if code.space_id is not None else None,
                "max_uses": code.max_uses,
                "used_count": code.used_count,
                "expires_at": code.expires_at,
                "revoked_at": code.revoked_at,
                "created_at": code.created_at,
            }
        )
        for code in codes
    ]


@router.get("/invite-codes", response_model=list[InviteCodeOut])
def list_my_codes(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[InviteCodeOut]:
    """我的码列表（创建者视角；分享用明文码随行，渲染 …/register?code=XXX）。"""
    codes = registration_commands.list_my_invite_codes(
        session, ActorContext.from_identity(identity[0], identity[1])
    )
    return _serialize(session, codes)


@router.post("/invite-codes", response_model=InviteCodeOut, status_code=201)
def create_code(
    payload: CreateInviteCodeRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> InviteCodeOut:
    """创建码：household/lineage 须为该空间 active 成员；stranger 任意已登录账号。

    无身份确认门槛（PRD 决策 13 修订）；资格判定在码原语内完成。
    """
    code = registration_commands.create_my_invite_code(
        session,
        _ctx(request, identity),
        kind=payload.kind,
        space_id=payload.space_id,
        max_uses=payload.max_uses,
        ttl_days=payload.ttl_days,
    )
    return _serialize(session, [code])[0]


@router.delete("/invite-codes/{code_id}", response_model=InviteCodeOut)
def revoke_code(
    code_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> InviteCodeOut:
    """撤销码：创建者本人，或该码所在空间的 active 空间管理员（陌生人码仅创建者）。"""
    code = registration_commands.revoke_my_invite_code(session, _ctx(request, identity), code_id)
    return _serialize(session, [code])[0]


@router.post("/me/invite-codes/redeem", response_model=InviteCodeOut)
def redeem_code(
    payload: RedeemInviteCodeRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> InviteCodeOut:
    """填码加入空间：与注册填码同一加入语义（决策 8）；陌生人码 400 明确提示。

    兑换者是当前登录用户本人；响应回已核销码行（used_count 已含本次，
    space_name 供前端展示「已加入哪个空间」），不泄露创建者信息。
    """
    code = registration_commands.redeem_invite_code(
        session,
        _ctx(request, identity),
        raw_code=payload.code,
        relation_label=payload.relation_label,
    )
    return _serialize(session, [code])[0]
