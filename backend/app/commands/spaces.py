"""空间命令（创建/改名/邀请/响应/退出移除/加入申请/位置保存）。

授权单点：owner/active 成员判定经 services.space_fsm；provisional
引用不因本层产生任何 household_detail 权利（可见性仍由 visibility.py 判定）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    SPACE_FORBIDDEN_ACTOR,
    SPACE_LINEAGE_ACCESS_UNAVAILABLE,
    SPACE_NOT_FOUND,
    USER_NOT_FOUND,
    VALIDATION_ERROR,
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


def _require_space_manager(session: Session, space_id: int, user_id: int) -> FamilySpace:
    space = _space_or_404(session, space_id)
    if not space_fsm.is_space_manager(session, space_id, user_id):
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅当前空间管理员可执行该操作")
    return space


def _resolve_lineage_target(session: Session, actor_id: int, lineage_space_id: int) -> FamilySpace:
    """校验家族配对目标：存在、kind=lineage、操作者是其 active 成员。

    非成员与不存在统一 404（防枚举）；目标不是 lineage 走 422。
    """
    lineage = _space_or_404(session, lineage_space_id)
    if lineage.kind != "lineage":
        raise_api_error(422, VALIDATION_ERROR, "关联目标必须是家族空间")
    _require_active_member(session, lineage_space_id, actor_id)
    return lineage


def create_space(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    kind: str = "household",
    lineage_space_id: int | None = None,
    commit: bool = True,
) -> FamilySpace:
    """创建空间：owner 即 active 成员（自建即同意）。

    ``commit=False`` 供上层应用命令（如注册命令的陌生人码分支）把空间创建
    合并进同一个短事务，与 create_shared_household 的组合惯例一致。
    ``lineage_space_id`` 仅 household 可用：创建即挂入指定家族空间（须为其
    active 成员），命令层校验，数据库不靠 CHECK 兜底。
    """
    actor = load_actor(session, ctx)
    now = utcnow()
    if lineage_space_id is not None:
        if kind != "household":
            raise_api_error(422, VALIDATION_ERROR, "仅家庭空间可以关联家族空间")
        _resolve_lineage_target(session, actor.id, lineage_space_id)
    with command_transaction(session, commit=commit):
        space = FamilySpace(
            name=name.strip(),
            owner_id=actor.id,
            kind=kind,
            lineage_space_id=lineage_space_id,
            created_at=now,
        )
        session.add(space)
        session.flush()
        session.add(
            SpaceMember(
                space_id=space.id,
                user_id=actor.id,
                added_by=actor.id,
                role="space_admin",
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
        space = _require_space_manager(session, space_id, actor.id)
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


def set_lineage_link(
    session: Session,
    ctx: ActorContext,
    space_id: int,
    *,
    lineage_space_id: int | None,
) -> FamilySpace:
    """设置/解除家庭空间的所属家族配对（仅该空间管理员；清除传 None）。

    授权：household 的 space_admin；同时要求操作者是目标 lineage 的 active
    成员（不能把自家空间挂进自己进不去的家族）。家族侧的解绑不在此处：
    删除/移交 lineage 是独立治理流程。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        space = _require_space_manager(session, space_id, actor.id)
        if space.kind != "household":
            raise_api_error(422, VALIDATION_ERROR, "仅家庭空间可以关联家族空间")
        old_link = space.lineage_space_id
        if lineage_space_id is not None:
            _resolve_lineage_target(session, actor.id, lineage_space_id)
        space.lineage_space_id = lineage_space_id
        emit(
            session,
            event_type="space.updated",
            aggregate_type="space",
            aggregate_id=space.id,
            payload={"lineage_space_id": lineage_space_id, "old_lineage_space_id": old_link},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_lineage_link_updated",
            actor_id=actor.id,
            target_id=space.id,
            ip=ctx.ip,
            detail={"lineage_space_id": lineage_space_id},
        )
    return space


def _require_inviter(session: Session, space_id: int, user_id: int) -> SpaceMember:
    """空间邀请由当前 active 成员发起，受邀人仍需本人接受。

    这是共享领域命令层的授权边界，不能依赖前端按钮隐藏；
    platform_operator 也不会因平台角色获得任何家庭空间写权限。
    """
    return _require_active_member(session, space_id, user_id)


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
        _require_inviter(session, space_id, actor.id)
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
    """pending 行决议：普通邀请由受邀人接受；本人申请加入由该空间管理员（即被
    加入空间的那个人）审核。申请人不得自批，只能撤回自己的 pending 行。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        member = session.get(SpaceMember, member_id)
        if member is None or not (
            member.user_id == actor.id
            or space_fsm.is_space_manager(session, member.space_id, actor.id)
        ):
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
                detail={
                    "space_id": member.space_id,
                    # 09-19：区分「本人接受邀请」与「管理员批准加入申请」。
                    "by_manager": member.user_id != actor.id,
                },
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
    """向目标本人 household 提交 pending 加入申请。

    household membership 与配对 lineage 完全分离：申请只产生目标 household 的
    pending 行，由 target 本人批准；批准后申请人仍需另行提交 lineage 申请，不能
    因 household membership 读取对方家族树。
    """
    from app.errors import SPACE_JOIN_NO_RELATION, SPACE_JOIN_NO_TARGET_SPACE
    from app.services import visibility

    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_user_id)
        if target is None or not visibility.evaluate(session, actor, target).visible:
            raise_api_error(404, USER_NOT_FOUND, "对方不存在或不可见")

        memberships = session.query(SpaceMember).filter(SpaceMember.user_id == target.id).all()
        active_ids = [m.space_id for m in memberships if space_fsm.effective_status(m) == "active"]
        primary_space_id: int | None = None
        if active_ids:
            # owner 优先，其次 target 任管理员的 space_admin 空间；order_by 保证
            # 同一 target 的解析结果确定。
            owned = (
                session.query(FamilySpace)
                .filter(
                    FamilySpace.owner_id == target.id,
                    FamilySpace.kind == "household",
                    FamilySpace.id.in_(active_ids),
                )
                .order_by(FamilySpace.id)
                .first()
            )
            if owned is not None:
                primary_space_id = owned.id
            else:
                managed = (
                    session.query(FamilySpace)
                    .join(SpaceMember, SpaceMember.space_id == FamilySpace.id)
                    .filter(
                        SpaceMember.user_id == target.id,
                        SpaceMember.role == "space_admin",
                        SpaceMember.status == "active",
                        FamilySpace.kind == "household",
                        FamilySpace.id.in_(active_ids),
                    )
                    .order_by(FamilySpace.id)
                    .first()
                )
                primary_space_id = managed.id if managed is not None else None
        if primary_space_id is None:
            raise_api_error(409, SPACE_JOIN_NO_TARGET_SPACE, "对方尚未建立家庭空间")

        space = _space_or_404(session, primary_space_id)
        if space.kind != "household":
            raise_api_error(409, SPACE_JOIN_NO_TARGET_SPACE, "对方尚未建立家庭空间")
        # 准入边界：只有与目标同属至少一个 active 家族空间（lineage）的用户才能
        # 自行申请加入对方的家庭空间。同 lineage 可见性只是提交 pending 的资格，
        # 不授予任何家族树读取权；批准与否由 target 本人决定。
        lineage_ids = set(
            session.scalars(
                select(SpaceMember.space_id)
                .join(FamilySpace, FamilySpace.id == SpaceMember.space_id)
                .where(
                    SpaceMember.user_id == actor.id,
                    SpaceMember.status == "active",
                    FamilySpace.kind == "lineage",
                )
            ).all()
        )
        shares_lineage = bool(lineage_ids) and (
            session.scalar(
                select(SpaceMember.id)
                .where(
                    SpaceMember.user_id == target.id,
                    SpaceMember.status == "active",
                    SpaceMember.space_id.in_(lineage_ids),
                )
                .limit(1)
            )
            is not None
        )
        if not shares_lineage:
            raise_api_error(403, SPACE_JOIN_NO_RELATION, "你与该账号不在同一个家族空间")
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


def create_shared_household(
    session: Session,
    ctx: ActorContext,
    *,
    other_user_id: int,
    name: str | None = None,
    commit: bool = True,
) -> tuple[FamilySpace, int]:
    """共同创建 HouseholdSpace，并把双方作为 active 成员加入。

    返回新空间和 membership 领域事件 id；``commit=False`` 仅供上层应用命令
    把空间创建、卡片执行状态和审计合并进同一个短事务。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session, commit=commit, immediate=True):
        other = session.get(User, other_user_id)
        if other is None:
            raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")
        if other.id == actor.id:
            raise_api_error(422, VALIDATION_ERROR, "不能与自己创建共同家庭空间")
        if (
            actor.profile_status != "identity_confirmed"
            or other.profile_status != "identity_confirmed"
        ):
            raise_api_error(409, VALIDATION_ERROR, "双方档案尚未完成身份确认")

        now = utcnow()
        left = aliased(SpaceMember)
        right = aliased(SpaceMember)
        existing = (
            session.query(FamilySpace)
            .join(left, left.space_id == FamilySpace.id)
            .join(right, right.space_id == FamilySpace.id)
            .filter(
                FamilySpace.kind == "household",
                left.user_id == actor.id,
                left.status == "active",
                right.user_id == other.id,
                right.status == "active",
            )
            .order_by(FamilySpace.id)
            .first()
        )
        if existing is not None:
            event = emit(
                session,
                event_type="space.membership.changed",
                aggregate_type="space",
                aggregate_id=existing.id,
                payload={
                    "action": "household_link_reused",
                    "user_ids": [actor.id, other.id],
                    "by": actor.id,
                },
                space_id=existing.id,
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="space_reused",
                actor_id=actor.id,
                target_id=existing.id,
                ip=ctx.ip,
                detail={"kind": "household", "linked_user_id": other.id},
            )
            session.flush()
            return existing, event.id

        space_name = (name or "").strip() or f"{actor.name} 与 {other.name}的家庭"
        space = FamilySpace(
            name=space_name,
            owner_id=actor.id,
            kind="household",
            created_at=now,
        )
        session.add(space)
        session.flush()
        session.add_all(
            [
                SpaceMember(
                    space_id=space.id,
                    user_id=actor.id,
                    added_by=actor.id,
                    role="space_admin",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                SpaceMember(
                    space_id=space.id,
                    user_id=other.id,
                    added_by=actor.id,
                    role="member",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.flush()
        emit(
            session,
            event_type="space.created",
            aggregate_type="space",
            aggregate_id=space.id,
            payload={"name": space.name, "kind": "household", "owner_id": actor.id},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        membership_event = emit(
            session,
            event_type="space.membership.changed",
            aggregate_type="space",
            aggregate_id=space.id,
            payload={
                "action": "household_link_activated",
                "user_ids": [actor.id, other.id],
                "by": actor.id,
            },
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_created",
            actor_id=actor.id,
            target_id=space.id,
            ip=ctx.ip,
            detail={"name": space.name, "kind": "household", "linked_user_id": other.id},
        )
        session.flush()
    return space, membership_event.id


def request_lineage_membership(
    session: Session,
    ctx: ActorContext,
    *,
    target_space_id: int,
    target_user_id: int,
    commit: bool = True,
) -> tuple[SpaceMember, int]:
    """向指定 LineageSpace 发起 pending 加入申请，不自动激活成员资格。"""
    from app.services import visibility

    actor = load_actor(session, ctx)
    with command_transaction(session, commit=commit):
        target_space = _space_or_404(session, target_space_id)
        if target_space.kind != "lineage":
            raise_api_error(422, VALIDATION_ERROR, "加入申请目标必须是家族空间")
        target = session.get(User, target_user_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")
        if target.id == actor.id:
            raise_api_error(422, VALIDATION_ERROR, "不能申请加入自己的家族空间")
        if target.profile_status != "identity_confirmed":
            raise_api_error(409, VALIDATION_ERROR, "对方档案尚未完成身份确认")
        if not visibility.evaluate(session, actor, target).visible:
            raise_api_error(404, USER_NOT_FOUND, "对方不存在或不可见")
        _require_active_member(session, target_space.id, target.id)
        if space_fsm.is_active_member(session, target_space.id, actor.id):
            raise_api_error(409, VALIDATION_ERROR, "你已经是该家族空间成员")

        member, _created = space_fsm.invite(
            session, space=target_space, user_id=actor.id, added_by=actor.id
        )
        event = emit(
            session,
            event_type="space.membership.changed",
            aggregate_type="space",
            aggregate_id=target_space.id,
            payload={
                "action": "lineage_join_requested",
                "user_id": actor.id,
                "member_id": member.id,
            },
            space_id=target_space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_join_requested",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"space_id": target_space.id, "member_id": member.id},
        )
        session.flush()
    return member, event.id


def request_lineage_access(
    session: Session, ctx: ActorContext, *, household_space_id: int
) -> tuple[SpaceMember, int]:
    """家庭空间成员申请读取该家庭所属家族空间（独立的第二条申请）。

    家庭空间成员资格不等于家族空间成员资格：本命令只产生目标 lineage 的
    pending 行，由该家族空间的管理员（即被加入的那个人）审核；批准前不能读取
    任何家族树。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        household = _space_or_404(session, household_space_id)
        if household.kind != "household":
            raise_api_error(422, VALIDATION_ERROR, "只有家庭空间可以申请所属家族空间")
        _require_active_member(session, household.id, actor.id)
        lineage_id = household.lineage_space_id
        if lineage_id is None:
            raise_api_error(
                409,
                SPACE_LINEAGE_ACCESS_UNAVAILABLE,
                "该家庭空间尚未关联家族空间",
            )
        lineage = _space_or_404(session, lineage_id)
        if lineage.kind != "lineage":
            raise_api_error(409, SPACE_LINEAGE_ACCESS_UNAVAILABLE, "该家庭空间尚未关联家族空间")
        if space_fsm.is_active_member(session, lineage.id, actor.id):
            raise_api_error(409, VALIDATION_ERROR, "你已经是该家族空间成员")
        # 审批人 = 该家族空间的 active 管理员（即被加入的那个人）；没有可审批的
        # 管理员时申请无处可送，按 409 明确拒绝而不是落一条无人处理的 pending。
        approver = space_fsm.active_space_manager(session, lineage.id)
        if approver is None or approver.user_id == actor.id:
            raise_api_error(409, SPACE_LINEAGE_ACCESS_UNAVAILABLE, "该家族空间暂无可审批的成员")

        member, _created = space_fsm.invite(
            session, space=lineage, user_id=actor.id, added_by=actor.id
        )
        event = emit(
            session,
            event_type="space.membership.changed",
            aggregate_type="space",
            aggregate_id=lineage.id,
            payload={
                "action": "lineage_access_requested",
                "user_id": actor.id,
                "approver_user_id": approver.user_id,
                "member_id": member.id,
            },
            space_id=lineage.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="space_lineage_access_requested",
            actor_id=actor.id,
            target_id=approver.user_id,
            ip=ctx.ip,
            detail={"space_id": lineage.id, "household_space_id": household.id},
        )
        session.flush()
    return member, event.id


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
