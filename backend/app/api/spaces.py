"""家庭空间路由（m1c）：CRUD / 成员管理 / 邀请处理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    USER_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.schemas.space import (
    PositionsPayload,
    SpaceCreate,
    SpaceInviteCreate,
    SpaceMemberOut,
    SpaceOut,
    SpaceUpdate,
)
from app.services import audit, space_fsm
from app.utils.timeutil import utcnow

router = APIRouter(tags=["spaces"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _space_or_404(session: Session, space_id: int) -> FamilySpace:
    space = session.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, "SPACE_NOT_FOUND", "家庭空间不存在")
    return space


def _require_active_member(session: Session, space_id: int, user_id: int) -> SpaceMember:
    member = space_fsm.find_membership(session, space_id, user_id)
    if member is None or space_fsm.effective_status(member) != "active":
        raise_api_error(404, "SPACE_NOT_FOUND", "家庭空间不存在")
    return member


def _require_owner(session: Session, space_id: int, user_id: int) -> FamilySpace:
    space = _space_or_404(session, space_id)
    if space.owner_id != user_id:
        raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "仅空间所有者可执行该操作")
    return space


@router.post("/spaces", status_code=201, response_model=SpaceOut)
def create_space(
    payload: SpaceCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceOut:
    """创建空间：owner 即 active 成员（自建即同意，AD-4 新建例外语义）。"""
    actor, _account = identity
    now = utcnow()
    space = FamilySpace(name=payload.name.strip(), owner_id=actor.id, created_at=now)
    session.add(space)
    session.flush()
    session.add(
        SpaceMember(
            space_id=space.id,
            user_id=actor.id,
            added_by=actor.id,
            role="owner",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    audit.write_audit(
        session,
        action="space_created",
        actor_id=actor.id,
        target_id=space.id,
        ip=_client_ip(request),
        detail={"name": space.name},
    )
    session.commit()
    session.refresh(space)
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
    actor, _account = identity
    space = _require_owner(session, space_id, actor.id)
    space.name = payload.name.strip()
    session.commit()
    session.refresh(space)
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
    actor, _account = identity
    space = _require_active_member_space(session, space_id, actor.id)
    target = session.get(User, payload.user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")

    member, created = space_fsm.invite(
        session, space=space, user_id=payload.user_id, added_by=actor.id
    )
    if created:
        audit.write_audit(
            session,
            action="space_invite_sent",
            actor_id=actor.id,
            target_id=payload.user_id,
            ip=_client_ip(request),
            detail={"space_id": space.id},
        )
        session.commit()
    session.refresh(member)
    return SpaceMemberOut.model_validate(member)


def _require_active_member_space(session: Session, space_id: int, user_id: int) -> FamilySpace:
    _member = _require_active_member(session, space_id, user_id)
    return _space_or_404(session, space_id)


@router.post("/space-memberships/{member_id}/accept", response_model=SpaceMemberOut)
def accept_membership(
    member_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, _account = identity
    member = _get_pending_membership_for_requestee(session, member_id, actor.id)
    space_fsm.transition(member, "accept", actor.id, session)
    audit.write_audit(
        session,
        action="space_invite_accepted",
        actor_id=actor.id,
        target_id=member.user_id,
        ip=_client_ip(request),
        detail={"space_id": member.space_id},
    )
    session.commit()
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.post("/space-memberships/{member_id}/reject", response_model=SpaceMemberOut)
def reject_membership(
    member_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceMemberOut:
    actor, _account = identity
    member = _get_pending_membership_for_requestee(session, member_id, actor.id)
    space_fsm.transition(member, "reject", actor.id, session)
    session.commit()
    session.refresh(member)
    return _member_out_with_name(session, member)


@router.delete("/space-memberships/{member_id}", status_code=204)
def remove_or_withdraw_membership(
    member_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方可撤回、本人可拒。"""
    actor, _account = identity
    member = session.get(SpaceMember, member_id)
    if member is None:
        raise_api_error(404, "SPACE_NOT_FOUND", "成员记录不存在")
    _space_or_404(session, member.space_id)
    action = "withdraw" if member.status == "pending" and member.added_by == actor.id else "remove"
    space_fsm.transition(member, action, actor.id, session)
    audit.write_audit(
        session,
        action="space_member_left",
        actor_id=actor.id,
        target_id=member.user_id,
        ip=None,
        detail={"space_id": member.space_id, "action": action},
    )
    session.commit()
    return Response(status_code=204)


def _get_pending_membership_for_requestee(
    session: Session, member_id: int, actor_id: int
) -> SpaceMember:
    member = session.get(SpaceMember, member_id)
    if member is None or member.user_id != actor_id:
        # 无关者 404 防枚举；owner 审批 join_request 属 M2
        raise_api_error(404, "SPACE_NOT_FOUND", "邀请不存在或已处理")
    if member.status != "pending":
        raise_api_error(
            409, "CONNECTION_ALREADY_RESOLVED", "邀请已处理", detail={"status": member.status}
        )
    return member


# ---- graph 空间过滤 ----


@router.get("/spaces/{space_id}/positions")
def get_positions(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """画布位置记忆：仅 active 成员可读。"""
    _require_active_member(session, space_id, identity[0].id)
    from app.models.node_position import NodePosition

    rows = session.query(NodePosition).filter(NodePosition.space_id == space_id).all()
    return [{"user_id": r.user_id, "x": r.x, "y": r.y} for r in rows]


@router.put("/spaces/{space_id}/positions")
def put_positions(
    space_id: int,
    payload: PositionsPayload,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, float | int]]:
    """批量 upsert 位置（active 成员可写；仅允许保存自己所在空间的成员坐标）。"""
    actor, _account = identity
    _require_active_member(session, space_id, actor.id)
    from app.models.node_position import NodePosition

    allowed_ids = {
        m.user_id for m in session.query(SpaceMember).filter(SpaceMember.space_id == space_id).all()
    }
    for item in payload.items:
        if item.user_id not in allowed_ids:
            raise_api_error(422, "VALIDATION_ERROR", f"user {item.user_id} 不在该空间")
        row = (
            session.query(NodePosition)
            .filter(NodePosition.space_id == space_id, NodePosition.user_id == item.user_id)
            .first()
        )
        if row is None:
            session.add(NodePosition(space_id=space_id, user_id=item.user_id, x=item.x, y=item.y))
        else:
            row.x, row.y = item.x, item.y
    session.commit()
    rows = session.query(NodePosition).filter(NodePosition.space_id == space_id).all()
    return [{"user_id": r.user_id, "x": r.x, "y": r.y} for r in rows]
