"""Explicit two-person consent commands for cross-lineage view bridges."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import (
    PERSONAL_FAMILY_BRIDGE_CONFLICT,
    PERSONAL_FAMILY_BRIDGE_INVALID,
    PERSONAL_FAMILY_BRIDGE_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.services.domain_events import emit
from app.services.source_facts import FACT_CONFIRMED
from app.utils.timeutil import utcnow


def _require_lineage_space(session: Session, space_id: int) -> FamilySpace:
    space = session.get(FamilySpace, space_id)
    if space is None or space.kind != "lineage":
        raise_api_error(422, PERSONAL_FAMILY_BRIDGE_INVALID, "桥接两侧必须是族谱空间")
    return space


def _active_member(session: Session, *, space_id: int, user_id: int) -> bool:
    return (
        session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == user_id,
                SpaceMember.status == "active",
            )
        )
        is not None
    )


def _claimed_account(session: Session, user_id: int) -> Account | None:
    return session.scalar(
        select(Account).where(Account.user_id == user_id, Account.status == "claimed")
    )


def _normalized_key(space_a: int, anchor_a: int, space_b: int, anchor_b: int) -> str:
    left = f"{space_a}:{anchor_a}"
    right = f"{space_b}:{anchor_b}"
    return "|".join(sorted((left, right)))


def _confirmed_anchor_fact(session: Session, anchor_a: int, anchor_b: int) -> bool:
    return (
        session.scalar(
            select(SourceFact.id).where(
                SourceFact.state == FACT_CONFIRMED,
                or_(
                    (SourceFact.subject_user_id == anchor_a)
                    & (SourceFact.object_user_id == anchor_b),
                    (SourceFact.subject_user_id == anchor_b)
                    & (SourceFact.object_user_id == anchor_a),
                ),
            )
        )
        is not None
    )


def _bridge_for_actor(session: Session, bridge_id: int, account: Account) -> PersonalFamilyBridge:
    bridge = session.get(PersonalFamilyBridge, bridge_id)
    if bridge is None or (
        account.user_id not in {bridge.anchor_a_user_id, bridge.anchor_b_user_id}
        and account.id
        not in {
            bridge.consent_a_account_id,
            bridge.consent_b_account_id,
            bridge.initiated_by_account_id,
        }
    ):
        raise_api_error(404, PERSONAL_FAMILY_BRIDGE_NOT_FOUND, "桥接不存在")
    return bridge


def create_bridge(
    session: Session,
    *,
    account: Account,
    anchor_user_id: int,
    other_space_id: int,
    other_anchor_user_id: int,
    scope: dict[str, Any],
    expires_at: datetime | None = None,
) -> PersonalFamilyBridge:
    """Create a pending bridge; requester gives consent for their own anchor."""
    if anchor_user_id == other_anchor_user_id:
        raise_api_error(422, PERSONAL_FAMILY_BRIDGE_INVALID, "桥接两侧人物不能相同")
    if account.status != "claimed" or account.user_id != anchor_user_id:
        raise_api_error(403, PERSONAL_FAMILY_BRIDGE_INVALID, "只有已认领的 anchor 本人可以发起桥接")
    user_space_id = next(
        (
            row.space_id
            for row in session.scalars(
                select(SpaceMember).where(
                    SpaceMember.user_id == anchor_user_id, SpaceMember.status == "active"
                )
            )
            if session.scalar(select(FamilySpace.kind).where(FamilySpace.id == row.space_id))
            == "lineage"
        ),
        None,
    )
    if user_space_id is None:
        raise_api_error(422, PERSONAL_FAMILY_BRIDGE_INVALID, "发起人没有可用族谱空间")
    _require_lineage_space(session, user_space_id)
    _require_lineage_space(session, other_space_id)
    if not _active_member(session, space_id=other_space_id, user_id=other_anchor_user_id):
        raise_api_error(404, PERSONAL_FAMILY_BRIDGE_NOT_FOUND, "桥接目标不存在")
    if not _confirmed_anchor_fact(session, anchor_user_id, other_anchor_user_id):
        raise_api_error(422, PERSONAL_FAMILY_BRIDGE_INVALID, "缺少已确认的 anchor 关系依据")
    key = _normalized_key(user_space_id, anchor_user_id, other_space_id, other_anchor_user_id)
    if session.scalar(
        select(PersonalFamilyBridge.id).where(PersonalFamilyBridge.normalized_pair_key == key)
    ):
        raise_api_error(409, PERSONAL_FAMILY_BRIDGE_CONFLICT, "该桥接已经存在")
    now = utcnow()
    row = PersonalFamilyBridge(
        lineage_space_a_id=user_space_id,
        lineage_space_b_id=other_space_id,
        anchor_a_user_id=anchor_user_id,
        anchor_b_user_id=other_anchor_user_id,
        normalized_pair_key=key,
        initiated_by_account_id=account.id,
        consent_a_account_id=account.id,
        scope_json=dict(scope),
        status="pending",
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    emit(
        session,
        event_type="personal_family_bridge.created",
        aggregate_type="personal_family_bridge",
        aggregate_id=row.id,
        payload={
            "status": "pending",
            "space_ids": [user_space_id, other_space_id],
        },
        space_id=user_space_id,
        actor_account_id=account.id,
    )
    return row


def consent_bridge(
    session: Session, *, bridge_id: int, account: Account, revision: int
) -> PersonalFamilyBridge:
    bridge = _bridge_for_actor(session, bridge_id, account)
    if bridge.revision != revision or bridge.status != "pending":
        raise_api_error(409, PERSONAL_FAMILY_BRIDGE_CONFLICT, "桥接状态已变化")
    if account.status != "claimed":
        raise_api_error(403, PERSONAL_FAMILY_BRIDGE_INVALID, "账号尚未认领")
    now = utcnow()
    if account.user_id == bridge.anchor_a_user_id:
        bridge.consent_a_account_id = account.id
        bridge.consent_a_at = now
    elif account.user_id == bridge.anchor_b_user_id:
        bridge.consent_b_account_id = account.id
        bridge.consent_b_at = now
    else:
        raise_api_error(404, PERSONAL_FAMILY_BRIDGE_NOT_FOUND, "桥接不存在")
    if bridge.consent_a_account_id is not None and bridge.consent_b_account_id is not None:
        bridge.status = "active"
        event_type = "personal_family_bridge.activated"
    else:
        event_type = "personal_family_bridge.consented"
    bridge.revision += 1
    bridge.updated_at = now
    session.flush()
    emit(
        session,
        event_type=event_type,
        aggregate_type="personal_family_bridge",
        aggregate_id=bridge.id,
        payload={
            "status": bridge.status,
            "revision": bridge.revision,
            "space_ids": [bridge.lineage_space_a_id, bridge.lineage_space_b_id],
        },
        space_id=bridge.lineage_space_a_id,
        actor_account_id=account.id,
    )
    return bridge


def revoke_bridge(
    session: Session, *, bridge_id: int, account: Account, revision: int
) -> PersonalFamilyBridge:
    bridge = _bridge_for_actor(session, bridge_id, account)
    if bridge.revision != revision or bridge.status not in ("pending", "active"):
        raise_api_error(409, PERSONAL_FAMILY_BRIDGE_CONFLICT, "桥接状态已变化")
    if account.user_id not in (bridge.anchor_a_user_id, bridge.anchor_b_user_id):
        raise_api_error(404, PERSONAL_FAMILY_BRIDGE_NOT_FOUND, "桥接不存在")
    now = utcnow()
    bridge.status = "revoked"
    bridge.revoked_at = now
    bridge.revision += 1
    bridge.updated_at = now
    session.flush()
    emit(
        session,
        event_type="personal_family_bridge.revoked",
        aggregate_type="personal_family_bridge",
        aggregate_id=bridge.id,
        payload={
            "status": bridge.status,
            "revision": bridge.revision,
            "space_ids": [bridge.lineage_space_a_id, bridge.lineage_space_b_id],
        },
        space_id=bridge.lineage_space_a_id,
        actor_account_id=account.id,
    )
    return bridge


__all__ = ["consent_bridge", "create_bridge", "revoke_bridge"]
