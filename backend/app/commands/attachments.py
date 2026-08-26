"""附件命令（图片上传/外链添加/删除）——AD-7 安全边界保持不变。

校验/重编码/落盘沿用 services.attachments；删除的物理文件清理由命令返回
路径、调用方在事务提交后执行（外部 I/O 不进事务）。删除/撤权传播发布
attachments.invalidated 事件（§0.6 合同）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import ATTACHMENT_NOT_FOUND, USER_NOT_FOUND, raise_api_error
from app.models.attachment import Attachment
from app.models.user import User
from app.services import attachments as att_service
from app.services import audit, custody
from app.services.domain_events import emit
from app.utils.timeutil import utcnow


def _editable_target(session: Session, ctx: ActorContext, user_id: int) -> tuple[Any, User]:
    """授权前置：目标存在 + 当前主体有编辑权。"""
    actor = load_actor(session, ctx)
    target = session.get(User, user_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "档案不存在")
    custody.assert_can_edit(actor, target)
    return actor, target


def add_image_attachment(
    session: Session,
    ctx: ActorContext,
    *,
    user_id: int,
    filename: str,
    data: bytes,
    title: str = "",
) -> Attachment:
    """图片上传：白名单/解码校验/PIL 重编码 → 落盘 → DB 行 + 审计。

    CPU 重编码非网络 I/O；文件先于提交落盘，回滚残留由孤儿清扫兜底。
    """
    actor, _target = _editable_target(session, ctx, user_id)
    att_service.validate_image_upload(filename, data)
    clean, _ext = att_service.reencode_strip_metadata(data)
    path = att_service.save_image(user_id, clean)

    with command_transaction(session):
        row = Attachment(
            user_id=user_id,
            type="image",
            url_or_path=path.name,
            title=title or None,
            uploaded_by=actor.id,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        audit.write_audit(
            session,
            action="attachment_uploaded",
            actor_id=actor.id,
            target_id=user_id,
            ip=ctx.ip,
            detail={"id": row.id},
        )
    return row


def add_link_attachment(
    session: Session,
    ctx: ActorContext,
    *,
    user_id: int,
    url: str,
    title: str | None,
    description: str | None,
) -> Attachment:
    actor, _target = _editable_target(session, ctx, user_id)
    att_service.validate_link_url(url)

    with command_transaction(session):
        row = Attachment(
            user_id=user_id,
            type="link",
            url_or_path=url.strip(),
            title=title,
            description=description,
            uploaded_by=actor.id,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        audit.write_audit(
            session,
            action="attachment_link_added",
            actor_id=actor.id,
            target_id=user_id,
            ip=ctx.ip,
            detail={"id": row.id},
        )
    return row


def delete_attachment(
    session: Session,
    ctx: ActorContext,
    attachment_id: int,
) -> str | None:
    """删除附件行并发布失效事件；返回待清理的物理文件名（image 类型）。"""
    with command_transaction(session):
        row = session.get(Attachment, attachment_id)
        if row is None:
            raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
        actor, _target = _editable_target(session, ctx, row.user_id)

        path_name = row.url_or_path if row.type == "image" else None
        owner_id = row.user_id
        session.delete(row)
        emit(
            session,
            event_type="attachments.invalidated",
            aggregate_type="profile",
            aggregate_id=owner_id,
            payload={"attachment_id": attachment_id, "path": path_name},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="attachment_deleted",
            actor_id=actor.id,
            target_id=owner_id,
            ip=ctx.ip,
            detail={"path": path_name},
        )
    return path_name
