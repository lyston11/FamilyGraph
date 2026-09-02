"""Notifications browser API（读取 + 已读命令，design.md §5.2）。

已读命令只设置 read_at：不调用 ActionCard accept/reject/execute，不写领域
状态事件；ETag 由最终序列化载荷派生，已读后的 unread_count 变化自然换新。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.models.account import Account
from app.models.user import User
from app.schemas.notifications import (
    NotificationReadAllIn,
    NotificationReadAllOut,
    NotificationReadOut,
    NotificationsPageOut,
)
from app.services import family_projection, notifications

router = APIRouter(tags=["notifications"])

_CONTRACT_VERSION = "notifications-v1"


@router.get("/notifications", response_model=NotificationsPageOut)
def list_notifications(
    space_id: int,
    response: Response,
    if_none_match: str | None = Header(default=None),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> NotificationsPageOut | Response:
    """按当前收件人 + space_id 过滤的通知列表；条件请求先复核授权再比较 ETag。"""
    _actor, account = identity
    payload = notifications.list_notifications_page(session, account=account, space_id=space_id)
    etag = family_projection.etag_for_json(
        _CONTRACT_VERSION, NotificationsPageOut.model_validate(payload).model_dump_json()
    )
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return NotificationsPageOut.model_validate(payload)


@router.post("/notifications/{notification_id}/read", response_model=NotificationReadOut)
def mark_notification_read(
    notification_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> NotificationReadOut:
    _actor, account = identity
    row = notifications.mark_notification_read(
        session, account=account, notification_id=notification_id
    )
    return NotificationReadOut.model_validate({"id": row.id, "read_at": row.read_at})


@router.post("/notifications/read-all", response_model=NotificationReadAllOut)
def mark_all_notifications_read(
    request: NotificationReadAllIn,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> NotificationReadAllOut:
    _actor, account = identity
    marked = notifications.mark_all_notifications_read(
        session, account=account, space_id=request.space_id
    )
    return NotificationReadAllOut.model_validate(
        {"space_id": request.space_id, "marked_count": marked}
    )
