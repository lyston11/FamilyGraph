"""关系命令（合并请求创建/决议/撤销/断连）——AD-4 合并语义。

accept 时可选空间成员同事务激活；reject/cancel/revoke 发布关系事件供投影失效。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    CONNECTION_ALREADY_RESOLVED,
    RELATION_NOT_FOUND,
    SPACE_NOT_FOUND,
    USER_NOT_FOUND,
    raise_api_error,
)
from app.models.relation import Relation
from app.models.space import FamilySpace
from app.models.user import User
from app.services import audit, relation_fsm, source_facts, space_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def _relation_event(session: Session, ctx: ActorContext, edge: Relation, action: str) -> None:
    emit(
        session,
        event_type=f"relation.{action}",
        aggregate_type="relation",
        aggregate_id=edge.id,
        payload={
            "from_user": edge.from_user,
            "to_user": edge.to_user,
            "dir_class": edge.dir_class,
            "status": edge.status,
        },
        actor_account_id=ctx.account_id,
    )


def _edge_for_actor(session: Session, edge_id: int, actor_id: int) -> Relation:
    edge = session.get(Relation, edge_id)
    if edge is None or actor_id not in (edge.from_user, edge.to_user):
        # 无权/不存在同一 404 语义（防枚举）
        raise_api_error(404, RELATION_NOT_FOUND, "关系不存在")
    return edge


def create_connection_request(
    session: Session,
    ctx: ActorContext,
    *,
    target_id: int,
    dir_class: str,
    label: str | None,
    space_membership_space_id: int | None = None,
) -> Relation:
    """向已有账号发起合并请求：relation pending（+可选空间成员 pending 同事务）。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")

        # AD-4 合并语义：校验发起人对目标空间是 active 成员（否则无法代发邀请）
        pending_space_id: int | None = None
        if space_membership_space_id is not None:
            space = session.get(FamilySpace, space_membership_space_id)
            membership = (
                space_fsm.find_membership(session, space.id, actor.id)
                if space is not None
                else None
            )
            if (
                space is None
                or membership is None
                or space_fsm.effective_status(membership) != ("active")
            ):
                raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
            pending_space_id = space.id

        edge = relation_fsm.create_relation(
            session,
            from_user=actor.id,
            to_user=target_id,
            dir_class=dir_class,
            label=label,
            status="pending",
        )
        if pending_space_id is not None:
            member, _created = space_fsm.invite(
                session,
                space=session.get(FamilySpace, pending_space_id),  # type: ignore[arg-type]
                user_id=target_id,
                added_by=actor.id,
            )
            edge.pending_space_id = pending_space_id

        _relation_event(session, ctx, edge, "requested")
        audit.write_audit(
            session,
            action="connection_requested",
            actor_id=actor.id,
            target_id=target_id,
            ip=ctx.ip,
            detail={"relation_id": edge.id, "dir_class": dir_class},
        )
    return edge


def decide_connection_request(
    session: Session,
    ctx: ActorContext,
    edge_id: int,
    *,
    accept: bool,
) -> Relation:
    """被请求方 accept/reject；AD-4 合并语义：可选空间成员同事务激活/撤回。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        edge = _edge_for_actor(session, edge_id, actor.id)
        if edge.status != "pending":
            raise_api_error(
                409, CONNECTION_ALREADY_RESOLVED, "该请求已处理", detail={"status": edge.status}
            )
        relation_fsm.transition(edge, "accept" if accept else "reject", actor.id, session)
        if edge.pending_space_id is not None:
            m = space_fsm.find_membership(session, edge.pending_space_id, edge.to_user)
            if m is not None and m.status == "pending":
                m.status = "active" if accept else "withdrawn"
                m.updated_at = utcnow()
            edge.pending_space_id = None

        # E1 生产入口：结构边被双方确认后写 confirmed SourceFact（peer 不映射）。
        # 与 relation 边同事务，避免两套事实源漂移（父任务 AC-P2 单一权威合同）。
        if accept:
            source_facts.map_structural_edge_to_fact(
                session,
                from_user=edge.from_user,
                to_user=edge.to_user,
                dir_class=edge.dir_class,
                asserted_by_account_id=ctx.account_id,
            )

        _relation_event(session, ctx, edge, "accepted" if accept else "rejected")
        audit.write_audit(
            session,
            action="connection_accepted" if accept else "connection_rejected",
            actor_id=actor.id,
            target_id=edge.from_user if actor.id == edge.to_user else edge.to_user,
            ip=ctx.ip,
            detail={"relation_id": edge.id},
        )
    return edge


def cancel_connection(session: Session, ctx: ActorContext, edge_id: int) -> Relation:
    """发起方撤回 pending 请求。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        edge = _edge_for_actor(session, edge_id, actor.id)
        relation_fsm.transition(edge, "cancel", actor.id, session)
        _relation_event(session, ctx, edge, "cancelled")
        audit.write_audit(
            session,
            action="connection_cancelled",
            actor_id=actor.id,
            target_id=edge.to_user,
            ip=ctx.ip,
            detail={"relation_id": edge.id},
        )
    return edge


def revoke_relation(session: Session, ctx: ActorContext, edge_id: int) -> Relation:
    """断连轨（D8）：任一方即可，不动档案。撤权传播经 relation.revoked 事件。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        edge = _edge_for_actor(session, edge_id, actor.id)
        counterpart = edge.to_user if actor.id == edge.from_user else edge.from_user
        relation_fsm.transition(edge, "revoke", actor.id, session)
        # 断连同步失效对应 SourceFact，保证 DerivedFact/回答及时失效（AC-KI8）。
        source_facts.revoke_structural_edge_fact(
            session,
            from_user=edge.from_user,
            to_user=edge.to_user,
            dir_class=edge.dir_class,
            actor_account_id=ctx.account_id,
        )
        _relation_event(session, ctx, edge, "revoked")
        audit.write_audit(
            session,
            action="relation_revoked",
            actor_id=actor.id,
            target_id=counterpart,
            ip=ctx.ip,
            detail={"relation_id": edge.id},
        )
    return edge
