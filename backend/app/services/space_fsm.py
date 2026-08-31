"""SpaceMember FSM 与幂等（architecture.md §4 [AD-4]）。

pending --accept--> active     pending --reject--> rejected（被请求方）
pending --withdraw--> withdrawn（发起方撤回）   pending 超 30 天惰性过期--> withdrawn
active  --remove--> removed    （space owner 或成员本人）

幂等：UNIQUE(space_id,user_id) 兜底 + invite 重复时返回既有 pending 行。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    RELATION_INVALID_TRANSITION,
    raise_api_error,
)
from app.models.relation import Relation
from app.models.space import PENDING_EXPIRY_DAYS, FamilySpace, SpaceMember
from app.utils.timeutil import utcnow


def _now() -> datetime:
    return utcnow()


def is_expired(member: SpaceMember) -> bool:
    """pending 超过 30 天视为 withdrawn（惰性判定，读路径调用）。"""
    return member.status == "pending" and member.updated_at < _now() - timedelta(
        days=PENDING_EXPIRY_DAYS
    )


def effective_status(member: SpaceMember) -> str:
    return "withdrawn" if is_expired(member) else member.status


def active_space_manager(session: Session, space_id: int) -> SpaceMember | None:
    """返回目标空间唯一的 active 本空间管理员。"""
    return session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.role == "space_admin",
            SpaceMember.status == "active",
        )
    )


def is_space_manager(session: Session, space_id: int, user_id: int) -> bool:
    """按 (user_id, space_id) 判断治理权限；不读取 owner_id 或全局角色。"""
    manager = active_space_manager(session, space_id)
    return manager is not None and manager.user_id == user_id


def transition(
    edge_or_member: SpaceMember, action: str, actor_id: int, session: Session
) -> SpaceMember:
    """FSM 校验 + 迁移。action ∈ accept|reject|withdraw|expire|remove。"""
    member = edge_or_member
    is_manager = is_space_manager(session, member.space_id, actor_id)
    is_self = member.user_id == actor_id

    if action == "accept":
        if not is_self:
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "仅受邀人本人可接受")
        if member.status != "pending":
            raise_api_error(
                409,
                RELATION_INVALID_TRANSITION,
                "当前状态不允许接受",
                detail={"status": member.status},
            )
        member.status = "active"
    elif action == "reject":
        if not (is_self or is_manager):
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "无权拒绝该邀请")
        if member.status != "pending":
            raise_api_error(
                409,
                RELATION_INVALID_TRANSITION,
                "当前状态不允许拒绝",
                detail={"status": member.status},
            )
        member.status = "rejected"
    elif action == "withdraw":
        if member.added_by != actor_id:
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "仅邀请发起人可撤回")
        if member.status != "pending":
            raise_api_error(
                409,
                RELATION_INVALID_TRANSITION,
                "当前状态不允许撤回",
                detail={"status": member.status},
            )
        member.status = "withdrawn"
    elif action == "expire":
        if not is_expired(member):
            raise_api_error(409, RELATION_INVALID_TRANSITION, "未到过期时间")
        member.status = "withdrawn"
    elif action == "remove":
        if not (is_manager or is_self):
            raise_api_error(403, "SPACE_FORBIDDEN_ACTOR", "仅空间管理员或本人可移出")
        if member.status != "active":
            raise_api_error(
                409,
                RELATION_INVALID_TRANSITION,
                "仅活跃成员可被移出",
                detail={"status": member.status},
            )
        if member.role == "space_admin":
            raise_api_error(409, "SPACE_MANAGER_TRANSFER_REQUIRED", "请先完成空间管理员交接")
        member.status = "removed"
    else:  # pragma: no cover
        raise_api_error(422, "VALIDATION_ERROR", f"未知动作 {action}")

    member.updated_at = _now()
    session.flush()
    return member


def find_membership(session: Session, space_id: int, user_id: int) -> SpaceMember | None:
    return session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )


def is_active_member(session: Session, space_id: int, user_id: int) -> bool:
    """是否 active 成员（含过期惰性判定）。"""
    member = find_membership(session, space_id, user_id)
    return member is not None and effective_status(member) == "active"


def invite(
    session: Session, *, space: FamilySpace, user_id: int, added_by: int
) -> tuple[SpaceMember, bool]:
    """邀请已有账号进空间：幂等；已 active 幂等返回；重复 pending 返回既有行。

    返回 (member, created)。
    """
    existing = find_membership(session, space.id, user_id)
    if existing is not None:
        if existing.status == "pending":
            return existing, False
        if existing.status == "active":
            return existing, False
        # 终态（rejected/withdrawn/removed）→ 复活为新的 pending 行语义：
        # UNIQUE 限制单行，故原地重置为 pending（保留审计 updated_at 变化）
        existing.status = "pending"
        existing.added_by = added_by
        existing.updated_at = _now()
        session.flush()
        return existing, True

    member = SpaceMember(
        space_id=space.id,
        user_id=user_id,
        added_by=added_by,
        role="member",
        status="pending",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(member)
    session.flush()
    return member, True


def relation_ids_between_active(session: Session, user_a: int, user_b: int) -> list[Relation]:
    """两用户间的活动关系边（供合并请求 accept 时联动校验）。"""
    stmt = select(Relation).where(
        Relation.status == "active",
        ((Relation.from_user == user_a) & (Relation.to_user == user_b))
        | ((Relation.from_user == user_b) & (Relation.to_user == user_a)),
    )
    return list(session.scalars(stmt))
