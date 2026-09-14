"""通知最小投影与已读命令（任务 09-02-personal-family-view-followup，design.md §5）。

边界：
- 通知行只保存 收件人账号 + 空间 + 领域引用 + 最小安全文案 + read_at；
  ``domain_status`` 与 ActionCard ``revision`` 在读取时从被引用领域对象实时
  投影（单一真源），已读命令绝不触碰 ActionCard / SpaceMember 状态。
- 生成只在真实领域事件发生时发生（ActionCard 出卡、空间成员 pending 行
  创建）；没有自然来源的 kind（bridge/relation）不产生行，不造假数据。
- 查询严格按 recipient_account_id + space_id 过滤，且先做当前空间授权复核；
  引用不匹配（领域行被级联删除/换空间）的行 fail-closed 不返回。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.commands.context import command_transaction
from app.errors import NOTIFICATION_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.notification import Notification
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import ActionCard
from app.models.steward_suggestion import StewardSuggestion
from app.models.user import User
from app.services import visibility
from app.services.action_cards import CARD_KIND_META
from app.services.family_projection import authorized_space_or_404
from app.utils.timeutil import utcnow

MASKED_ACTOR: dict[str, bool] = {"__masked__": True}

# ActionCard state → 前端冻结的跨领域最小状态集（NotificationDomainStatus）
_CARD_DOMAIN_STATUS: dict[str, str] = {
    "pending": "pending",
    "viewed": "pending",
    "accepted": "accepted",
    "executed": "done",
    "dismissed": "rejected",
    "expired": "expired",
    "superseded": "revoked",
}

SUGGESTION_KIND_TITLES: dict[str, str] = {
    "relation_proposal": "Steward 有关系线索待核实",
    "term_preference": "管家称谓建议",
    "identity_duplicate": "发现疑似重复档案待核实",
    "missing_information": "发现资料缺口待核实",
}

_INVITE_TITLE = "你有新的家庭空间邀请"
_JOIN_REQUEST_TITLE = "有新的空间加入申请"


# ---- 生成（由领域命令同事务调用）----


def _account_id_of(session: Session, user_id: int | None) -> int | None:
    if user_id is None:
        return None
    return session.scalar(select(Account.id).where(Account.user_id == user_id))


def _add_notification(
    session: Session,
    *,
    kind: str,
    space_id: int,
    recipient_account_id: int,
    title: str,
    action_card_id: int | None = None,
    space_member_id: int | None = None,
    suggestion_id: int | None = None,
    actor_user_id: int | None = None,
    summary: str | None = None,
    now: datetime | None = None,
) -> None:
    session.add(
        Notification(
            kind=kind,
            space_id=space_id,
            recipient_account_id=recipient_account_id,
            action_card_id=action_card_id,
            space_member_id=space_member_id,
            suggestion_id=suggestion_id,
            actor_user_id=actor_user_id,
            title=title,
            summary=summary,
            created_at=now or utcnow(),
        )
    )
    session.flush()


def record_action_card_notification(session: Session, card: ActionCard) -> None:
    """ActionCard 出卡 → 收件人通知（create_card 幂等去重，一卡至多一条）。"""
    _add_notification(
        session,
        kind="action_card",
        space_id=card.space_id,
        recipient_account_id=card.recipient_account_id,
        action_card_id=card.id,
        actor_user_id=card.subject_user_id,
        title=f"{CARD_KIND_META[card.kind]['label']}推荐待确认",
    )


def record_suggestion_notification(
    session: Session, *, suggestion: StewardSuggestion, recipient_account_id: int
) -> None:
    """Steward 建议产生 → 收件人站内通知（固定模板 title，UNIQUE 去重兜底）。

    summary 不复制任何未经校验的模型 rationale：留空，详情由建议端点投影。
    """
    _add_notification(
        session,
        kind="steward_suggestion",
        space_id=suggestion.space_id,
        recipient_account_id=recipient_account_id,
        suggestion_id=suggestion.id,
        actor_user_id=suggestion.subject_user_id,
        title=SUGGESTION_KIND_TITLES[suggestion.kind],
    )


def record_membership_request_notification(
    session: Session, *, space: FamilySpace, member: SpaceMember
) -> None:
    """空间成员 pending 行创建时的自然映射：
    - 邀请（user != added_by）→ 通知受邀人；
    - 本人申请加入（user == added_by）→ 通知空间当前 active 管理员。
    """
    from app.services import space_fsm

    if member.user_id != member.added_by:
        recipient_account_id = _account_id_of(session, member.user_id)
        actor_user_id = member.added_by
        title = _INVITE_TITLE
    else:
        manager = space_fsm.active_space_manager(session, space.id)
        if manager is None:
            return
        recipient_account_id = _account_id_of(session, manager.user_id)
        actor_user_id = member.user_id
        title = _JOIN_REQUEST_TITLE
    if recipient_account_id is None:
        return
    _add_notification(
        session,
        kind="space_membership",
        space_id=space.id,
        recipient_account_id=recipient_account_id,
        space_member_id=member.id,
        actor_user_id=actor_user_id,
        title=title,
    )


# ---- 读取投影 ----


def _project_item(
    session: Session,
    viewer: User,
    account: Account,
    row: Notification,
    space_name: str,
) -> dict[str, Any] | None:
    domain_status: str | None
    suggestion_state: str | None = None
    action_card: dict[str, int] | None = None
    title, summary = row.title, row.summary
    if row.kind == "action_card":
        card = session.get(ActionCard, row.action_card_id) if row.action_card_id else None
        if (
            card is None
            or card.space_id != row.space_id
            or card.recipient_account_id != row.recipient_account_id
        ):
            return None  # 引用损坏（级联删除/换空间）：fail-closed 丢弃该行
        domain_status = _CARD_DOMAIN_STATUS.get(card.state)
        if domain_status is None:  # pragma: no cover - FSM 枚举扩展时的防线
            return None
        action_card = {"card_id": card.id, "revision": card.revision}
    elif row.kind == "steward_suggestion":
        from app.services import steward_suggestions as suggestion_service

        suggestion = (
            session.get(StewardSuggestion, row.suggestion_id) if row.suggestion_id else None
        )
        if suggestion is None or suggestion.space_id != row.space_id:
            return None  # 引用损坏（级联删除/换空间）：fail-closed 丢弃该行
        if not suggestion_service.suggestion_visible(
            session,
            viewer=viewer,
            account=account,
            suggestion=suggestion,
        ):
            return None
        detail = suggestion_service.get_suggestion_detail(
            session,
            account=account,
            space_id=row.space_id,
            suggestion_id=suggestion.id,
        )
        suggestion_state = str(detail["state"])
        domain_status = suggestion_service.SUGGESTION_DOMAIN_STATUS.get(suggestion_state)
        if domain_status is None:
            return None
        presentation = detail.get("presentation")
        if isinstance(presentation, dict):
            summary = presentation.get("summary")
        if suggestion.kind == "term_preference":
            title = "管家称谓建议"
            # Optional preferences never enter the domain-action pending count.
            if domain_status == "pending":
                domain_status = "done"
        elif suggestion.kind == "relation_proposal":
            title = "关系线索待核实" if suggestion_state == "proposed" else "关系线索处理进展"
    elif row.kind == "space_membership":
        from app.services import space_fsm

        member = session.get(SpaceMember, row.space_member_id) if row.space_member_id else None
        if member is None or member.space_id != row.space_id:
            return None
        domain_status = space_fsm.effective_status(member)
    else:
        # bridge/relation 暂无自然来源；出现即视为脏数据，不返回
        return None

    actor_name: str | dict[str, bool] | None = None
    if row.actor_user_id is not None:
        actor = session.get(User, row.actor_user_id)
        if actor is not None:
            decision = visibility.evaluate(
                session,
                viewer,
                actor,
                space_context=row.space_id,
                purpose=visibility.PURPOSE_PROFILE,
            )
            actor_name = (
                visibility.payload_from_decision(decision, actor).get("name")
                if decision.visible
                else dict(MASKED_ACTOR)
            )

    return {
        "id": row.id,
        "space_id": row.space_id,
        "kind": row.kind,
        "payload": {
            "title": title,
            "summary": summary,
            "actor_name": actor_name,
            "space_name": space_name,
        },
        "domain_status": domain_status,
        "action_card": action_card,
        "suggestion": (
            {
                "suggestion_id": row.suggestion_id,
                "state": suggestion_state,
            }
            if row.kind == "steward_suggestion" and row.suggestion_id
            else None
        ),
        "created_at": row.created_at,
        "read_at": row.read_at,
    }


def list_notifications_page(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    """按当前收件人 + 空间过滤并投影；授权复核失败安全 404。"""
    space, viewer = authorized_space_or_404(session, account=account, space_id=space_id)
    rows = session.scalars(
        select(Notification)
        .where(
            Notification.recipient_account_id == account.id,
            Notification.space_id == space_id,
        )
        .order_by(Notification.id.desc())
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _project_item(session, viewer, account, row, space.name)
        if item is not None:
            items.append(item)
    unread = sum(1 for item in items if item["read_at"] is None)
    return {"space_id": space_id, "items": items, "unread_count": unread}


# ---- 已读命令（只改 read_at）----


def mark_notification_read(
    session: Session, *, account: Account, notification_id: int
) -> Notification:
    """单条已读：收件人隔离；BEGIN IMMEDIATE 串行化后首个 read_at 胜出，幂等。"""
    with command_transaction(session, immediate=True):
        row = session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_account_id == account.id,
            )
        )
        if row is None:
            raise_api_error(404, NOTIFICATION_NOT_FOUND, "通知不存在")
        if row.read_at is None:
            row.read_at = utcnow()
    return row


def mark_all_notifications_read(session: Session, *, account: Account, space_id: int) -> int:
    """全部已读：仅限当前账号 + 指定空间的未读行。"""
    if space_id <= 0:
        raise_api_error(422, "VALIDATION_ERROR", "空间参数不合法")
    with command_transaction(session, immediate=True):
        result = session.execute(
            update(Notification)
            .where(
                Notification.recipient_account_id == account.id,
                Notification.space_id == space_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=utcnow())
        )
        marked = int(result.rowcount or 0)
    return marked


__all__ = [
    "list_notifications_page",
    "mark_all_notifications_read",
    "mark_notification_read",
    "record_action_card_notification",
    "record_suggestion_notification",
    "record_membership_request_notification",
]
