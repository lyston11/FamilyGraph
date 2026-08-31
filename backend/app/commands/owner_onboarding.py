"""Owner onboarding 邀请命令（v2 §0.5，AC-F3）。

- platform_operator 签发：随机单次 token，数据库只存 sha256 hash；短期、可撤销。
- 兑换（claimed 账号）：条件 UPDATE 原子消费 token → 创建独立 LineageSpace +
  owner membership + audit + 事件；兑换者只获得这一个新空间，不获得
  platform_operator 角色，也不连接其他管理员的空间。
- 并发安全：两个并发兑换依赖条件 UPDATE rowcount（WAL busy_timeout 下恰好
  一个胜出），配合 token_hash 唯一约束。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    OWNER_INVITATION_ACCOUNT_NOT_CLAIMED,
    OWNER_INVITATION_INVALID,
    raise_api_error,
)
from app.models.space import FamilySpace, SpaceMember
from app.models.v2_foundation import OwnerInvitation
from app.services import audit
from app.services.domain_events import emit
from app.utils.timeutil import utcnow

ANTI_ENUM_MESSAGE = "邀请链接无效或已失效"


def hash_token(raw_token: str) -> str:
    """token 只存 sha256；原文仅签发响应返回一次。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_owner_invitation(
    session: Session,
    ctx: ActorContext,
    *,
    ttl_minutes: int | None = None,
) -> tuple[OwnerInvitation, str]:
    """operator 签发邀请：返回 (invitation, 明文 token)。"""
    load_actor(session, ctx)  # 授权在路由层 require_platform_operator 完成后进入
    raw_token = secrets.token_urlsafe(32)
    now = utcnow()
    ttl = config.OWNER_INVITATION_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    with command_transaction(session):
        invitation = OwnerInvitation(
            token_hash=hash_token(raw_token),
            created_by=ctx.user_id,
            expires_at=now + timedelta(minutes=ttl),
            created_at=now,
        )
        session.add(invitation)
        session.flush()
        emit(
            session,
            event_type="owner_invitation.created",
            aggregate_type="owner_invitation",
            aggregate_id=invitation.id,
            payload={"created_by": ctx.user_id, "expires_at": invitation.expires_at.isoformat()},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="owner_invitation_created",
            actor_id=ctx.user_id,
            target_id=invitation.id,
            ip=ctx.ip,
            detail={"expires_at": invitation.expires_at.isoformat()},
        )
    return invitation, raw_token


def revoke_owner_invitation(
    session: Session, ctx: ActorContext, invitation_id: int
) -> OwnerInvitation:
    """撤销未使用邀请；已使用/已撤销幂等拒绝（409）。"""
    with command_transaction(session):
        invitation = session.get(OwnerInvitation, invitation_id)
        if invitation is None:
            raise_api_error(404, OWNER_INVITATION_INVALID, ANTI_ENUM_MESSAGE)
        if invitation.used_at is not None or invitation.revoked_at is not None:
            raise_api_error(409, OWNER_INVITATION_INVALID, ANTI_ENUM_MESSAGE)
        invitation.revoked_at = utcnow()
        emit(
            session,
            event_type="owner_invitation.revoked",
            aggregate_type="owner_invitation",
            aggregate_id=invitation.id,
            payload={"revoked_by": ctx.user_id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="owner_invitation_revoked",
            actor_id=ctx.user_id,
            target_id=invitation.id,
            ip=ctx.ip,
            detail={},
        )
    return invitation


def redeem_owner_invitation(
    session: Session,
    ctx: ActorContext,
    *,
    raw_token: str,
) -> FamilySpace:
    """兑换：原子消费 token 并创建独立 LineageSpace（兑换者 = owner）。

    错误统一 404/409 + 同一文案（防枚举）；managed/pin 未改账号 403 引导先认领。
    """
    actor = load_actor(session, ctx)
    if ctx.account_status != "claimed" or actor.account.pin_must_change:
        raise_api_error(
            403,
            OWNER_INVITATION_ACCOUNT_NOT_CLAIMED,
            "请先完成首次登录并修改初始 PIN 后再兑换",
        )

    token_hash = hash_token(raw_token)
    now = utcnow()
    with command_transaction(session):
        invitation = session.scalar(
            select(OwnerInvitation).where(OwnerInvitation.token_hash == token_hash)
        )
        if invitation is None:
            raise_api_error(404, OWNER_INVITATION_INVALID, ANTI_ENUM_MESSAGE)

        # 原子消费：条件 UPDATE 恰好命中一行者胜出（并发/重放/过期/撤销统一在此裁决）
        result = session.execute(
            update(OwnerInvitation)
            .where(
                OwnerInvitation.id == invitation.id,
                OwnerInvitation.used_at.is_(None),
                OwnerInvitation.revoked_at.is_(None),
                OwnerInvitation.expires_at > now,
            )
            .values(used_at=now)
        )
        if result.rowcount != 1:
            raise_api_error(404, OWNER_INVITATION_INVALID, ANTI_ENUM_MESSAGE)

        redeemer = actor
        space = FamilySpace(
            name=f"{redeemer.name}的家族空间",
            owner_id=redeemer.id,
            kind="lineage",
            created_at=now,
        )
        session.add(space)
        session.flush()
        session.add(
            SpaceMember(
                space_id=space.id,
                user_id=redeemer.id,
                added_by=redeemer.id,
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
            payload={"name": space.name, "kind": "lineage", "owner_id": redeemer.id},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        emit(
            session,
            event_type="owner_invitation.redeemed",
            aggregate_type="owner_invitation",
            aggregate_id=invitation.id,
            payload={"redeemed_by_account": ctx.account_id, "space_id": space.id},
            space_id=space.id,
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="owner_invitation_redeemed",
            actor_id=redeemer.id,
            target_id=invitation.id,
            ip=ctx.ip,
            detail={"space_id": space.id},
        )
    return space
