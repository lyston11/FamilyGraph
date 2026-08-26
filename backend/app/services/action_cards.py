"""ActionCard 状态机与去重/证据版本/失效（V2.4 Block S1）。

FSM（单向，终态不可复活）：
    pending --view/accept--> viewed/accepted
    viewed --accept--> accepted --execute--> executed
    pending/viewed --dismiss--> dismissed
    pending/viewed/accepted --expire--> expired
    pending/viewed/accepted --supersede--> superseded

并发合同：所有转换基于 compare-and-set revision —— 调用方传入读取时的
revision，锁内复核不一致即 409 CARD_REVISION_CONFLICT；每次转换 revision+1。
生命周期每次转换写 domain_events（card.*），由调用方事务统一提交。

去重合同（AC-ST3）：同 (space, kind, subject, object) 即同 dedupe_key 下：
- 存在 active/executed/dismissed 且 evidence_hash 相同的卡 → 不再出新卡
  （相同证据不重复骚扰；executed 表示动作已完成，dismissed 表示用户已拒绝）；
- 存在 active 且 evidence_hash 不同（证据变化）→ 新卡插入后把旧活动卡置
  superseded，superseded_by_id 指向新卡，evidence_version 单调递增；
- 仅剩 expired 历史 → 允许重新出卡（新一轮有效期）。

红线：本模块绝不写 SourceFact、不发送任何申请命令、不修改空间成员资格；
accepted 卡的执行由 S2 后端命令重新校验后回调 transition_card(action="execute")。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import STEWARD_CARD_TTL_DAYS
from app.errors import (
    CARD_INVALID_TRANSITION,
    CARD_NOT_FOUND,
    CARD_REVISION_CONFLICT,
    raise_api_error,
)
from app.models.steward import (
    CARD_ACTIVE_STATES,
    CARD_TERMINAL_STATES,
    ActionCard,
)
from app.services import steward_events
from app.services.domain_events import emit as emit_domain_event
from app.utils.timeutil import utcnow

# ---- 状态与动作 ----
STATE_PENDING = "pending"
STATE_VIEWED = "viewed"
STATE_ACCEPTED = "accepted"
STATE_EXECUTED = "executed"
STATE_DISMISSED = "dismissed"
STATE_EXPIRED = "expired"
STATE_SUPERSEDED = "superseded"

ACTION_VIEW = "view"
ACTION_ACCEPT = "accept"
ACTION_EXECUTE = "execute"
ACTION_DISMISS = "dismiss"
ACTION_EXPIRE = "expire"
ACTION_SUPERSEDE = "supersede"

# (当前状态, 动作) -> 新状态；终态不在键中即天然拒绝「复活」
TRANSITIONS: dict[tuple[str, str], str] = {
    (STATE_PENDING, ACTION_VIEW): STATE_VIEWED,
    (STATE_PENDING, ACTION_DISMISS): STATE_DISMISSED,
    (STATE_PENDING, ACTION_EXPIRE): STATE_EXPIRED,
    (STATE_PENDING, ACTION_SUPERSEDE): STATE_SUPERSEDED,
    (STATE_PENDING, ACTION_ACCEPT): STATE_ACCEPTED,
    (STATE_VIEWED, ACTION_ACCEPT): STATE_ACCEPTED,
    (STATE_VIEWED, ACTION_DISMISS): STATE_DISMISSED,
    (STATE_VIEWED, ACTION_EXPIRE): STATE_EXPIRED,
    (STATE_VIEWED, ACTION_SUPERSEDE): STATE_SUPERSEDED,
    (STATE_ACCEPTED, ACTION_EXECUTE): STATE_EXECUTED,
    (STATE_ACCEPTED, ACTION_EXPIRE): STATE_EXPIRED,
    (STATE_ACCEPTED, ACTION_SUPERSEDE): STATE_SUPERSEDED,
}

# 卡片种类展示元数据（模板文案；LLM 只能润色不能改变动作集）
CARD_KIND_META: dict[str, dict[str, str]] = {
    "household_link": {
        "label": "共同家庭空间",
        "privacy_effect": (
            "接受后将进入确认页：创建或加入共同家庭空间，空间成员可见你的名字与关系标签；"
            "档案详情仍按可见性策略控制。"
        ),
    },
    "lineage_request": {
        "label": "家族空间申请",
        "privacy_effect": (
            "申请将显示给对方家族空间的 owner 审批；通过之前对方只能看到名字与称谓等" "最小信息。"
        ),
    },
}


def dedupe_key_for(kind: str, subject_user_id: int, object_user_id: int | None) -> str:
    """唯一键：card_type + subject/object + space（space 由查询/索引携带）。"""
    return f"{kind}:{subject_user_id}:{object_user_id if object_user_id is not None else '-'}"


def compute_evidence_hash(evidence_json: dict[str, Any]) -> str:
    """证据快照指纹：规范化 JSON（排序键）的 sha256。"""
    canonical = json.dumps(evidence_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_expires_at(now: datetime | None = None) -> datetime:
    """卡片默认有效期（ST-4：卡片必须带有效期）。"""
    return (now or utcnow()) + timedelta(days=STEWARD_CARD_TTL_DAYS)


def get_card(session: Session, card_id: int) -> ActionCard | None:
    return session.get(ActionCard, card_id)


def require_card(session: Session, card_id: int) -> ActionCard:
    card = session.get(ActionCard, card_id)
    if card is None:
        raise_api_error(404, CARD_NOT_FOUND, "卡片不存在", detail={"card_id": card_id})
    return card


def _emit_card_event(
    session: Session,
    *,
    event_type: str,
    card: ActionCard,
    from_state: str,
    actor_account_id: int | None,
) -> None:
    emit_domain_event(
        session,
        event_type=event_type,
        aggregate_type=steward_events.AGGREGATE_ACTION_CARD,
        aggregate_id=card.id,
        payload={
            "card_id": card.id,
            "kind": card.kind,
            "space_id": card.space_id,
            "subject_user_id": card.subject_user_id,
            "object_user_id": card.object_user_id,
            "from_state": from_state,
            "to_state": card.state,
            "revision": card.revision,
        },
        space_id=card.space_id,
        actor_account_id=actor_account_id,
    )


def transition_card(
    session: Session,
    card: ActionCard,
    action: str,
    *,
    expected_revision: int,
    actor_account_id: int | None = None,
    executed_event_id: int | None = None,
    superseded_by_id: int | None = None,
    now: datetime | None = None,
) -> ActionCard:
    """compare-and-set 状态转换：revision 不一致 409；非法转换/终态再入 409。

    execute 必须提供 executed_event_id（执行命令产生的 DomainEvent）；
    supersede 建议提供 superseded_by_id（取代卡 id；证据失效型取代可为空）。
    """
    moment = now or utcnow()
    if card.revision != expected_revision:
        raise_api_error(
            409,
            CARD_REVISION_CONFLICT,
            "卡片已被其他操作更新，请刷新后重试",
            detail={
                "card_id": card.id,
                "expected_revision": expected_revision,
                "current_revision": card.revision,
            },
        )
    new_state = TRANSITIONS.get((card.state, action))
    if new_state is None:
        raise_api_error(
            409,
            CARD_INVALID_TRANSITION,
            "卡片当前状态不允许该操作",
            detail={"card_id": card.id, "current_state": card.state, "action": action},
        )
    if action == ACTION_EXECUTE and executed_event_id is None:
        raise_api_error(
            422,
            CARD_INVALID_TRANSITION,
            "执行动作必须携带执行的领域事件 id",
            detail={"card_id": card.id},
        )
    from_state = card.state
    card.state = new_state
    card.revision += 1
    if action == ACTION_ACCEPT:
        card.accepted_at = moment
    elif action == ACTION_EXECUTE:
        card.executed_event_id = executed_event_id
    elif action == ACTION_SUPERSEDE:
        card.superseded_by_id = superseded_by_id
    session.flush()
    _emit_card_event(
        session,
        event_type=steward_events.CARD_EVENT_BY_ACTION[action],
        card=card,
        from_state=from_state,
        actor_account_id=actor_account_id,
    )
    return card


def find_cards_for_key(
    session: Session,
    *,
    space_id: int,
    kind: str,
    subject_user_id: int,
    object_user_id: int | None,
    states: tuple[str, ...] | None = None,
) -> list[ActionCard]:
    """同 dedupe_key 的历史卡（按 version 升序）；states=None 表示全部。"""
    stmt = select(ActionCard).where(
        ActionCard.space_id == space_id,
        ActionCard.dedupe_key == dedupe_key_for(kind, subject_user_id, object_user_id),
    )
    if states is not None:
        stmt = stmt.where(ActionCard.state.in_(states))
    rows = list(session.scalars(stmt.order_by(ActionCard.evidence_version.asc())))
    return rows


def create_card(
    session: Session,
    *,
    kind: str,
    space_id: int,
    recipient_account_id: int,
    subject_user_id: int,
    object_user_id: int | None,
    evidence_json: dict[str, Any],
    proposed_action_json: dict[str, Any],
    reason_text: str,
    privacy_effect: str | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[ActionCard | None, str]:
    """去重出卡：返回 (card, outcome)，outcome ∈ created|duplicate|superseded_old。

    - 同 key 且存在 active/executed/dismissed 同证据哈希卡 → duplicate（不出新卡）；
    - 同 key 且存在 active 异证据卡 → 先插新卡再 supersede 旧活动卡
      （outcome=superseded_old；旧卡 superseded_by_id 指向新卡）；
    - 仅 expired 历史 → 直接出新卡（created）。
    """
    if kind not in CARD_KIND_META:
        raise_api_error(422, CARD_INVALID_TRANSITION, "未知卡片种类", detail={"kind": kind})
    moment = now or utcnow()
    evidence_hash = compute_evidence_hash(evidence_json)
    history = find_cards_for_key(
        session,
        space_id=space_id,
        kind=kind,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
    )
    for row in history:
        if row.evidence_hash == evidence_hash and row.state in (
            *CARD_ACTIVE_STATES,
            STATE_EXECUTED,
            STATE_DISMISSED,
        ):
            return None, "duplicate"

    max_version = max((row.evidence_version for row in history), default=0)
    stale_active = [row for row in history if row.state in CARD_ACTIVE_STATES]
    card = ActionCard(
        kind=kind,
        space_id=space_id,
        recipient_account_id=recipient_account_id,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        evidence_json=evidence_json,
        evidence_hash=evidence_hash,
        evidence_version=max_version + 1,
        dedupe_key=dedupe_key_for(kind, subject_user_id, object_user_id),
        proposed_action_json=proposed_action_json,
        reason_text=reason_text,
        privacy_effect=privacy_effect or CARD_KIND_META[kind]["privacy_effect"],
        state=STATE_PENDING,
        revision=1,
        expires_at=expires_at if expires_at is not None else default_expires_at(moment),
        created_at=moment,
    )
    session.add(card)
    session.flush()
    for row in stale_active:
        transition_card(
            session,
            row,
            ACTION_SUPERSEDE,
            expected_revision=row.revision,
            superseded_by_id=card.id,
            now=moment,
        )
    return card, ("superseded_old" if stale_active else "created")


def expire_due_cards(
    session: Session,
    *,
    space_id: int,
    now: datetime | None = None,
) -> int:
    """在单一空间内惰性过期活动卡（每次转换写事件）。"""
    moment = now or utcnow()
    due = list(
        session.scalars(
            select(ActionCard).where(
                ActionCard.space_id == space_id,
                ActionCard.state.in_(CARD_ACTIVE_STATES),
                ActionCard.expires_at.is_not(None),
                ActionCard.expires_at < moment,
            )
        )
    )
    for card in due:
        transition_card(session, card, ACTION_EXPIRE, expected_revision=card.revision, now=moment)
    return len(due)


def supersede_card(
    session: Session,
    card: ActionCard,
    *,
    reason: str | None = None,
    superseded_by_id: int | None = None,
    now: datetime | None = None,
) -> ActionCard:
    """证据失效/资格丧失时的系统型取代（actor=None；failed_reason 记录原因）。"""
    card.failed_reason = reason
    return transition_card(
        session,
        card,
        ACTION_SUPERSEDE,
        expected_revision=card.revision,
        superseded_by_id=superseded_by_id,
        now=now,
    )


def active_cards_in_space(session: Session, space_id: int) -> list[ActionCard]:
    return list(
        session.scalars(
            select(ActionCard).where(
                ActionCard.space_id == space_id, ActionCard.state.in_(CARD_ACTIVE_STATES)
            )
        )
    )


def terminal_states() -> tuple[str, ...]:
    return CARD_TERMINAL_STATES
