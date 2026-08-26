"""空间命令（创建/改名/邀请/响应/退出移除/加入申请/位置保存）。

授权单点：owner/active 成员判定经 services.space_fsm；guest 与 provisional
引用不因本层产生任何 household_detail 权利（可见性仍由 visibility.py 判定）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    USER_NOT_FOUND,
    raise_api_error,
)
from app.models import User
from app.models.node_position import NodePosition
from app.models.space import FamilySpace, SpaceMember
from app.schemas.space import PositionItem
from app.services import audit, space_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def _space_or_404(session: Session, space_id: int) -> FamilySpace:
    space = session.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
    return space


def _require_active_member(session: Session, space_id: int, user_id: int) -> SpaceMember:
    member = space_fsm.find_membership(session, space_id, user_id)
    if member is None or space_fsm.effective_status(member) != "active":
        raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
    return member


def _require_owner(session: Session, space_id: int, user_id: int) -> FamilySpace:
    space = _space_or_404(session, space_id)
    if space.owner_id != user_id:
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅空间所有者可执行该操作")
    return space


def create_space(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    kind: str = "household",
) -> FamilySpace:
    """创建空间：owner 即 active 成员（自建即同意）。"""
    actor = load_actor(session, ctx)
    now = utcnow()
    with command_transaction(session):
        space = FamilySpace(name=name.strip(), owner_id=actor.id, kind=kind, created_at=now)
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
        emit(
            session,
            event_type="space.created",
            aggregate_type="space",
            aggregate_id=space.id,
            payload={"name": space.name, "kind": kind, "owner_id": actor.id},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_created",
            actor_id=actor.id,
            target_id=space.id,
            ip=ctx.ip,
            detail={"name": space.name},
        )
    return space


def rename_space(
    session: Session,
    ctx: ActorContext,
    space_id: int,
    *,
    name: str,
) -> FamilySpace:
    actor = load_actor(session, ctx)
    with command_transaction(session):
        space = _require_owner(session, space_id, actor.id)
        old_name = space.name
        space.name = name.strip()
        emit(
            session,
            event_type="space.updated",
            aggregate_type="space",
            aggregate_id=space.id,
            payload={"old_name": old_name, "name": space.name},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_renamed",
            actor_id=actor.id,
            target_id=space.id,
            ip=ctx.ip,
            detail={"name": space.name},
        )
    return space


def invite_member(
    session: Session,
    ctx: ActorContext,
    space_id: int,
    *,
    user_id: int,
) -> tuple[SpaceMember, bool]:
    """邀请已有账号进空间 → pending（幂等）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        _require_active_member(session, space_id, actor.id)
        space = _space_or_404(session, space_id)
        target = session.get(User, user_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")

        member, created = space_fsm.invite(session, space=space, user_id=user_id, added_by=actor.id)
        if created:
            emit(
                session,
                event_type="space.membership.changed",
                aggregate_type="space",
                aggregate_id=space.id,
                payload={"action": "invited", "user_id": user_id, "by": actor.id},
                space_id=space.id,
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="space_invite_sent",
                actor_id=actor.id,
                target_id=user_id,
                ip=ctx.ip,
                detail={"space_id": space.id},
            )
    return member, created


def respond_invitation(
    session: Session,
    ctx: ActorContext,
    member_id: int,
    *,
    accept: bool,
) -> SpaceMember:
    """受邀人接受/拒绝自己的 pending 邀请。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        member = session.get(SpaceMember, member_id)
        if member is None or member.user_id != actor.id:
            raise_api_error(404, SPACE_NOT_FOUND, "邀请不存在或已处理")
        if member.status != "pending":
            from app.errors import CONNECTION_ALREADY_RESOLVED

            raise_api_error(
                409,
                CONNECTION_ALREADY_RESOLVED,
                "邀请已处理",
                detail={"status": member.status},
            )
        action = "accept" if accept else "reject"
        space_fsm.transition(member, action, actor.id, session)
        emit(
            session,
            event_type="space.membership.changed",
            aggregate_type="space",
            aggregate_id=member.space_id,
            payload={
                "action": "accepted" if accept else "rejected",
                "user_id": member.user_id,
                "by": actor.id,
            },
            space_id=member.space_id,
            actor_account_id=ctx.account_id,
        )
        if accept:
            audit.write_audit(
                session,
                action="space_invite_accepted",
                actor_id=actor.id,
                target_id=member.user_id,
                ip=ctx.ip,
                detail={"space_id": member.space_id},
            )
    return member


def leave_or_remove_membership(session: Session, ctx: ActorContext, member_id: int) -> None:
    """D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方可撤回。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        member = session.get(SpaceMember, member_id)
        if member is None or _space_or_404(session, member.space_id) is None:
            raise_api_error(404, SPACE_NOT_FOUND, "成员记录不存在")
        action = (
            "withdraw" if member.status == "pending" and member.added_by == actor.id else "remove"
        )
        space_fsm.transition(member, action, actor.id, session)
        emit(
            session,
            event_type="space.membership.changed",
            aggregate_type="space",
            aggregate_id=member.space_id,
            payload={"action": action, "user_id": member.user_id, "by": actor.id},
            space_id=member.space_id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_member_left",
            actor_id=actor.id,
            target_id=member.user_id,
            ip=ctx.ip,
            detail={"space_id": member.space_id, "action": action},
        )


def request_join_by_user(
    session: Session,
    ctx: ActorContext,
    *,
    target_user_id: int,
) -> SpaceMember:
    """家族视图摘要卡「申请进入 TA 的家庭空间」（join_request 语义）。

    可见性门禁：viewer 对 target 可见性不得为 none（防枚举 404）。
    """
    from app.services import visibility

    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_user_id)
        if target is None or not visibility.evaluate(session, actor, target).visible:
            raise_api_error(404, USER_NOT_FOUND, "对方不存在或不可见")

        memberships = session.query(SpaceMember).filter(SpaceMember.user_id == target.id).all()
        active_ids = [m.space_id for m in memberships if space_fsm.effective_status(m) == "active"]
        primary_space_id: int | None = None
        owned = (
            session.query(FamilySpace)
            .filter(FamilySpace.owner_id == target.id, FamilySpace.id.in_(active_ids))
            .first()
            if active_ids
            else None
        )
        if owned is not None:
            primary_space_id = owned.id
        elif active_ids:
            primary_space_id = active_ids[0]
        if primary_space_id is None:
            from app.errors import SPACE_JOIN_NO_TARGET_SPACE

            raise_api_error(409, SPACE_JOIN_NO_TARGET_SPACE, "对方尚未建立家庭空间")

        space = _space_or_404(session, primary_space_id)
        member, created = space_fsm.invite(
            session, space=space, user_id=actor.id, added_by=actor.id
        )
        if created:
            emit(
                session,
                event_type="space.membership.changed",
                aggregate_type="space",
                aggregate_id=space.id,
                payload={"action": "join_requested", "user_id": actor.id},
                space_id=space.id,
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="space_join_requested",
                actor_id=actor.id,
                target_id=target.id,
                ip=ctx.ip,
                detail={"space_id": space.id},
            )
    return member


def save_positions(
    session: Session,
    ctx: ActorContext,
    space_id: int,
    items: list[PositionItem],
) -> list[dict[str, float | int]]:
    """批量 upsert 画布位置（active 成员；仅本空间成员坐标可写）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        _require_active_member(session, space_id, actor.id)
        allowed_ids = {
            m.user_id
            for m in session.query(SpaceMember).filter(SpaceMember.space_id == space_id).all()
        }
        for item in items:
            if item.user_id not in allowed_ids:
                from app.errors import VALIDATION_ERROR

                raise_api_error(422, VALIDATION_ERROR, f"user {item.user_id} 不在该空间")
            row = (
                session.query(NodePosition)
                .filter(NodePosition.space_id == space_id, NodePosition.user_id == item.user_id)
                .first()
            )
            if row is None:
                session.add(
                    NodePosition(space_id=space_id, user_id=item.user_id, x=item.x, y=item.y)
                )
            else:
                row.x, row.y = item.x, item.y
        rows = (
            session.query(NodePosition)
            .filter(NodePosition.space_id == space_id)
            .order_by(NodePosition.user_id)
            .all()
        )
        out: list[dict[str, float | int]] = [
            {"user_id": r.user_id, "x": r.x, "y": r.y} for r in rows
        ]
    return out


def positions_of(session: Session, ctx: ActorContext, space_id: int) -> list[dict[str, Any]]:
    """读取空间位置（active 成员）。"""
    actor = load_actor(session, ctx)
    _require_active_member(session, space_id, actor.id)
    rows = session.query(NodePosition).filter(NodePosition.space_id == space_id).all()
    return [{"user_id": r.user_id, "x": r.x, "y": r.y} for r in rows]
