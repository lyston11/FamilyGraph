"""家庭空间路由（m1c）：CRUD / 成员管理 / 邀请处理。

v2 D2：写路径全部走应用命令层（app.commands.spaces，AC-F7），路由只做
schema 解析 + 认证 + 命令调用 + 序列化；读路径保持原状。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import spaces as space_commands
from app.commands.context import ActorContext
from app.models.account import Account
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.schemas.space import (
    PositionsPayload,
    SpaceCreate,
    SpaceInviteCreate,
    SpaceMemberOut,
    SpaceOut,
    SpaceProfileRefOut,
    SpaceUpdate,
)
from app.services import space_fsm

router = APIRouter(tags=["spaces"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_active_member(session: Session, space_id: int, user_id: int) -> SpaceMember:
    """读路径守卫：非 active 成员与不存在同一 404（防枚举）。"""
    from app.errors import raise_api_error

    member = space_fsm.find_membership(session, space_id, user_id)
    if member is None or space_fsm.effective_status(member) != "active":
        raise_api_error(404, "SPACE_NOT_FOUND", "家庭空间不存在")
    return member


@router.post("/spaces", status_code=201, response_model=SpaceOut)
def create_space(
    payload: SpaceCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    """创建空间：owner 即 active 成员（自建即同意）；kind 默认 household。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    space = space_commands.create_space(session, ctx, name=payload.name, kind=payload.kind)
    out = SpaceOut.model_validate(space)
    out.member_count = 1
    return out


@router.get("/spaces", response_model=list[SpaceOut])
def list_my_spaces(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceOut]:
    """我 active 成员的空间；附成员数与待处理邀请数。空列表 → 前端引导建默认空间（AD-3）。"""
    actor, _account = identity
    memberships = session.query(SpaceMember).filter(SpaceMember.user_id == actor.id).all()
    active_space_ids = [
        m.space_id for m in memberships if space_fsm.effective_status(m) == "active"
    ]
    spaces = (
        session.query(FamilySpace)
        .filter(FamilySpace.id.in_(active_space_ids))
        .order_by(FamilySpace.created_at.desc())
        .all()
        if active_space_ids
        else []
    )
    outs: list[SpaceOut] = []
    for space in spaces:
        all_members = session.query(SpaceMember).filter(SpaceMember.space_id == space.id).all()
        out = SpaceOut.model_validate(space)
        out.member_count = sum(1 for m in all_members if space_fsm.effective_status(m) == "active")
        out.pending_count = sum(1 for m in all_members if m.status == "pending")
        outs.append(out)
    return outs


@router.patch("/spaces/{space_id}", response_model=SpaceOut)
def rename_space(
    space_id: int,
    payload: SpaceUpdate,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    space = space_commands.rename_space(session, ctx, space_id, name=payload.name)
    return SpaceOut.model_validate(space)


@router.get("/spaces/{space_id}/members", response_model=list[SpaceMemberOut])
def list_space_members(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceMemberOut]:
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    members = (
        session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id)
        .order_by(SpaceMember.id)
        .all()
    )
    return [_member_out_with_name(session, m) for m in members]


def _member_out_with_name(session: Session, m: SpaceMember) -> SpaceMemberOut:
    out = SpaceMemberOut.model_validate(m)
    u = session.get(User, m.user_id)
    out.user_name = u.name if u else None
    return out


@router.get("/spaces/{space_id}/profile-refs", response_model=list[SpaceProfileRefOut])
def list_space_profile_refs(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceProfileRefOut]:
    """待确档最小节点引用（AC-F2 可观测性）：仅 {profile_id, name, added_at}。

    provisional 人物不是 SpaceMember，只以 space_profile_refs 最小引用存在；
    本端点让空间成员能看到这些“待确档”条目。授权：该空间 active 成员（含 guest）；
    其余与不存在同一 404（防枚举）。字段投影恒为最小集，不随可见性放宽。
    """
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    from app.models.space import SpaceProfileRef

    rows = (
        session.query(SpaceProfileRef, User)
        .join(User, User.id == SpaceProfileRef.user_id)
        .filter(SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active")
        .order_by(SpaceProfileRef.id)
        .all()
    )
    return [
        SpaceProfileRefOut(profile_id=ref.user_id, name=user.name, added_at=ref.created_at)
        for ref, user in rows
    ]


@router.get("/spaces/invitations", response_model=list[SpaceMemberOut])
def list_my_invitations(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[SpaceMemberOut]:
    """发给我的 pending 空间邀请（跨全部空间）。"""
    actor, _account = identity
    rows = (
        session.query(SpaceMember)
        .filter(SpaceMember.user_id == actor.id, SpaceMember.status == "pending")
        .order_by(SpaceMember.updated_at.desc())
        .all()
    )
    return [_member_out_with_name(session, m) for m in rows]


@router.post("/spaces/{space_id}/members", status_code=201, response_model=SpaceMemberOut)
def invite_to_space(
    space_id: int,
    payload: SpaceInviteCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """邀请已有账号进空间 → pending（幂等）；managed 直连例外走建档向导组合，不经此端点。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member, _created = space_commands.invite_member(session, ctx, space_id, user_id=payload.user_id)
    session.refresh(member)
    return SpaceMemberOut.model_validate(member)


@router.post("/spaces/join-by-user", status_code=201, response_model=SpaceMemberOut)
def join_by_user(
    payload: JoinByUserPayload,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    """家族视图摘要卡「申请进入 TA 的家庭空间」（join_request 语义，命令化）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member = space_commands.request_join_by_user(
        session, ctx, target_user_id=payload.target_user_id
    )
    session.refresh(member)
    return _member_out_with_name(session, member)


class JoinByUserPayload(BaseModel):
    target_user_id: int = Field(gt=0)


@router.post("/space-memberships/{member_id}/accept", response_model=SpaceMemberOut)
def accept_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    member = space_commands.respond_invitation(session, ctx, member_id, accept=True)
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.post("/space-memberships/{member_id}/reject", response_model=SpaceMemberOut)
def reject_membership(
    member_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    member = space_commands.respond_invitation(session, ctx, member_id, accept=False)
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.delete("/space-memberships/{member_id}", status_code=204)
def remove_or_withdraw_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方可撤回、本人可拒。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    space_commands.leave_or_remove_membership(session, ctx, member_id)
    return Response(status_code=204)


# ---- graph 空间过滤 ----


@router.get("/spaces/{space_id}/positions")
def get_positions(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """画布位置记忆：仅 active 成员可读。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    return space_commands.positions_of(session, ctx, space_id)


@router.put("/spaces/{space_id}/positions")
def put_positions(
    space_id: int,
    payload: PositionsPayload,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """批量 upsert 位置（命令：commands.spaces.save_positions）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    return space_commands.save_positions(session, ctx, space_id, payload.items)
