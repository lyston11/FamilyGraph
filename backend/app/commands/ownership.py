"""Owner 移交命令（v2 §0.5，AC-F5）：pending → accepted | cancelled | expired。

- 接受事务：条件 UPDATE 原子裁决 pending（并发双接受恰好一个成功）→
  复验双方 active 成员资格 → 翻转 owner_id → 双方 membership 调整
  （原 owner 默认降为 space_admin）→ audit + space.transfer.completed 事件。
- pending 超 TTL 惰性过期；expired 是终态事实：在失败路径上也先持久化再报错。
- 同空间同时至多一个 pending（partial unique index）。
- 删除 owner 前显式义务预检：名下仍有空间即 409 OWNER_TRANSFER_REQUIRED 引导移交，
  数据库 owner_id RESTRICT 作为兜底，空间与成员永不被 FK 静默删除。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    OWNER_TRANSFER_INVALID,
    OWNER_TRANSFER_REQUIRED,
    SPACE_NOT_FOUND,
    raise_api_error,
)
from app.models.space import FamilySpace
from app.models.v2_foundation import OwnershipTransfer
from app.services import audit, space_fsm
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def _is_stale(transfer: OwnershipTransfer, now: datetime) -> bool:
    return now - transfer.created_at > timedelta(hours=config.OWNERSHIP_TRANSFER_TTL_HOURS)


def _mark_expired(session: Session, transfer: OwnershipTransfer) -> None:
    """置 expired 终态并留痕（事件 + 审计）。"""
    now = utcnow()
    transfer.status = "expired"
    transfer.decided_at = now
    emit(
        session,
        event_type="space.transfer.expired",
        aggregate_type="ownership_transfer",
        aggregate_id=transfer.id,
        payload={"space_id": transfer.space_id},
        space_id=transfer.space_id,
    )
    audit.write_audit(
        session,
        action="ownership_transfer_expired",
        actor_id=None,
        target_id=transfer.id,
        ip=None,
        detail={"space_id": transfer.space_id},
    )


def create_transfer(
    session: Session,
    ctx: ActorContext,
    *,
    space_id: int,
    to_user_id: int,
) -> OwnershipTransfer:
    """现任 owner 发起移交：目标必须是该空间 active 成员且非自己。"""
    actor = load_actor(session, ctx)
    now = utcnow()
    with command_transaction(session):
        space = session.get(FamilySpace, space_id)
        if space is None or space_fsm.find_membership(session, space_id, actor.id) is None:
            raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
        if space.owner_id != actor.id:
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "仅空间所有者可发起移交")
        if to_user_id == actor.id:
            raise_api_error(409, OWNER_TRANSFER_INVALID, "不能把空间移交给自己")
        target_member = space_fsm.find_membership(session, space_id, to_user_id)
        if target_member is None or space_fsm.effective_status(target_member) != "active":
            raise_api_error(409, OWNER_TRANSFER_INVALID, "移交对象必须是该空间的活跃成员")
        existing = session.scalar(
            select(OwnershipTransfer).where(
                OwnershipTransfer.space_id == space_id, OwnershipTransfer.status == "pending"
            )
        )
        if existing is not None:
            if not _is_stale(existing, now):
                raise_api_error(409, OWNER_TRANSFER_INVALID, "该空间已有待处理的移交")
            existing.status = "expired"
            existing.decided_at = now

        transfer = OwnershipTransfer(
            space_id=space_id,
            from_user=actor.id,
            to_user=to_user_id,
            status="pending",
            created_at=now,
        )
        session.add(transfer)
        session.flush()
        emit(
            session,
            event_type="space.transfer.requested",
            aggregate_type="ownership_transfer",
            aggregate_id=transfer.id,
            payload={"space_id": space_id, "from_user": actor.id, "to_user": to_user_id},
            space_id=space_id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="ownership_transfer_created",
            actor_id=actor.id,
            target_id=transfer.id,
            ip=ctx.ip,
            detail={"space_id": space_id, "to_user": to_user_id},
        )
    return transfer


def accept_transfer(session: Session, ctx: ActorContext, transfer_id: int) -> OwnershipTransfer:
    """受让人接受：原子消费 pending → 复验资格 → 变更所有权与双方角色。

    惰性过期在同一事务内持久化后再返回错误（expired 终态不随失败回滚）。
    """
    actor = load_actor(session, ctx)
    now = utcnow()
    expired_now = False
    won = False
    with command_transaction(session):
        transfer = session.get(OwnershipTransfer, transfer_id)
        if transfer is None or transfer.to_user != actor.id:
            # 非受让人与不存在同一 404（防枚举）
            raise_api_error(404, OWNER_TRANSFER_INVALID, "移交不存在")

        if _is_stale(transfer, now):
            expired_now = True
            _mark_expired(session, transfer)
        else:
            # 原子裁决：仅 pending 行可被接受（并发双接受恰好一个 rowcount=1）
            result = session.execute(
                update(OwnershipTransfer)
                .where(
                    OwnershipTransfer.id == transfer.id,
                    OwnershipTransfer.status == "pending",
                )
                .values(status="accepted", decided_at=now)
            )
            won = result.rowcount == 1

        if won:
            _apply_transfer_ownership(session, transfer, now)
            emit(
                session,
                event_type="space.transfer.completed",
                aggregate_type="ownership_transfer",
                aggregate_id=transfer.id,
                payload={
                    "space_id": transfer.space_id,
                    "from_user": transfer.from_user,
                    "to_user": transfer.to_user,
                },
                space_id=transfer.space_id,
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="ownership_transfer_accepted",
                actor_id=actor.id,
                target_id=transfer.id,
                ip=ctx.ip,
                detail={"space_id": transfer.space_id},
            )

    if expired_now:
        raise_api_error(409, OWNER_TRANSFER_INVALID, "移交已过期")
    if not won:
        raise_api_error(409, OWNER_TRANSFER_INVALID, "移交已被处理")
    return transfer


def _apply_transfer_ownership(session: Session, transfer: OwnershipTransfer, now: datetime) -> None:
    """接受成功后的所有权翻转与双方 membership 调整（同一事务内调用）。"""
    space = session.get(FamilySpace, transfer.space_id)
    if space is None or space.owner_id != transfer.from_user:
        raise_api_error(409, OWNER_TRANSFER_INVALID, "空间所有权状态已变化，移交无法完成")
    heir_membership = space_fsm.find_membership(session, space.id, transfer.to_user)
    from_membership = space_fsm.find_membership(session, space.id, transfer.from_user)
    if heir_membership is None or space_fsm.effective_status(heir_membership) != "active":
        raise_api_error(409, OWNER_TRANSFER_INVALID, "受让人已不是该空间活跃成员")

    space.owner_id = transfer.to_user
    heir_membership.role = "owner"
    heir_membership.updated_at = now
    if from_membership is not None:
        # 原 owner 默认降为 space_admin（保留成员资格便于交接）
        from_membership.role = "space_admin"
        from_membership.updated_at = now


def cancel_transfer(session: Session, ctx: ActorContext, transfer_id: int) -> OwnershipTransfer:
    """发起人或受让人取消 pending 移交；过期裁决同接受路径。"""
    actor = load_actor(session, ctx)
    now = utcnow()
    expired_now = False
    won = False
    with command_transaction(session):
        transfer = session.get(OwnershipTransfer, transfer_id)
        if transfer is None or actor.id not in (transfer.from_user, transfer.to_user):
            raise_api_error(404, OWNER_TRANSFER_INVALID, "移交不存在")

        if _is_stale(transfer, now):
            expired_now = True
            _mark_expired(session, transfer)
        else:
            result = session.execute(
                update(OwnershipTransfer)
                .where(
                    OwnershipTransfer.id == transfer.id,
                    OwnershipTransfer.status == "pending",
                )
                .values(status="cancelled", decided_at=now)
            )
            won = result.rowcount == 1

        if won:
            emit(
                session,
                event_type="space.transfer.cancelled",
                aggregate_type="ownership_transfer",
                aggregate_id=transfer.id,
                payload={"space_id": transfer.space_id, "by": actor.id},
                space_id=transfer.space_id,
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="ownership_transfer_cancelled",
                actor_id=actor.id,
                target_id=transfer.id,
                ip=ctx.ip,
                detail={"space_id": transfer.space_id},
            )

    if expired_now:
        raise_api_error(409, OWNER_TRANSFER_INVALID, "移交已过期")
    if not won:
        raise_api_error(409, OWNER_TRANSFER_INVALID, "移交已被处理")
    return transfer


def list_transfers_for_space(session: Session, space_id: int) -> list[OwnershipTransfer]:
    """空间全部移交记录（倒序）；读路径不做惰性写。"""
    return list(
        session.scalars(
            select(OwnershipTransfer)
            .where(OwnershipTransfer.space_id == space_id)
            .order_by(OwnershipTransfer.id.desc())
        ).all()
    )


def expire_stale_transfers(session: Session) -> int:
    """全量惰性过期清扫（cleanup/后台任务可用）；系统行为，无 actor。返回过期条数。"""
    count = 0
    now = utcnow()
    with command_transaction(session):
        rows = session.scalars(
            select(OwnershipTransfer).where(OwnershipTransfer.status == "pending")
        ).all()
        for row in list(rows):
            if _is_stale(row, now):
                _mark_expired(session, row)
                count += 1
    return count


def assert_no_owner_obligations(session: Session, ctx: ActorContext, user_id: int) -> None:
    """删除/注销 owner 前的义务预检（AC-F5）：名下仍有空间 → 409 引导移交。"""
    owned_ids = list(
        session.scalars(select(FamilySpace.id).where(FamilySpace.owner_id == user_id)).all()
    )
    if owned_ids:
        raise_api_error(
            409,
            OWNER_TRANSFER_REQUIRED,
            "该档案是家庭空间所有者，请先完成 owner 移交后再删除",
            detail={"spaces_requiring_handover": owned_ids},
        )
