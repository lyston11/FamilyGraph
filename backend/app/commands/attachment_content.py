"""Commands for confirmed attachment content and Assistant grants."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    ATTACHMENT_CONTENT_NOT_ALLOWED,
    ATTACHMENT_CONTENT_NOT_FOUND,
    ATTACHMENT_CONTENT_STATE_CONFLICT,
    ATTACHMENT_NOT_FOUND,
    SPACE_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.attachment import Attachment
from app.models.attachment_content import AgentContentGrant, AttachmentContentVersion
from app.models.space import FamilySpace
from app.models.user import User
from app.services import audit, custody
from app.services.domain_events import emit
from app.services.space_fsm import is_active_member
from app.utils.timeutil import utcnow

MAX_MANUAL_TEXT = 12_000


def _load_attachment(session: Session, attachment_id: int) -> tuple[Attachment, User]:
    row = session.get(Attachment, attachment_id)
    if row is None or row.type not in {"image", "link"}:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    target = session.get(User, row.user_id)
    if target is None:
        raise_api_error(404, ATTACHMENT_NOT_FOUND, "附件不存在")
    return row, target


def _require_claimed_owner(actor: User, target: User) -> Account:
    if actor.id != target.id or target.profile_status != "identity_confirmed":
        raise_api_error(403, ATTACHMENT_CONTENT_NOT_ALLOWED, "只有档案本人可以确认或授权内容")
    account = target.account
    if account is None or account.status != "claimed":
        raise_api_error(403, ATTACHMENT_CONTENT_NOT_ALLOWED, "档案尚未完成认领")
    return account


def create_content_version(
    session: Session,
    ctx: ActorContext,
    *,
    attachment_id: int,
    text: str,
    content_kind: str = "manual_text",
) -> AttachmentContentVersion:
    actor = load_actor(session, ctx)
    attachment, target = _load_attachment(session, attachment_id)
    custody.assert_can_edit(actor, target)
    if content_kind != "manual_text":
        raise_api_error(422, ATTACHMENT_CONTENT_NOT_ALLOWED, "当前只支持用户确认的手工文字")
    normalized = text.strip()
    if not normalized or len(normalized) > MAX_MANUAL_TEXT:
        raise_api_error(422, ATTACHMENT_CONTENT_NOT_ALLOWED, "内容为空或超过长度限制")

    with command_transaction(session, immediate=True):
        current = session.scalar(
            select(func.max(AttachmentContentVersion.revision)).where(
                AttachmentContentVersion.attachment_id == attachment_id
            )
        )
        row = AttachmentContentVersion(
            attachment_id=attachment_id,
            revision=(current or 0) + 1,
            content_kind="manual_text",
            text=normalized,
            content_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            extractor_name="manual",
            extractor_version="v1",
            status="draft",
            created_by=actor.id,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        emit(
            session,
            event_type="attachment.content_version.created",
            aggregate_type="attachment_content_version",
            aggregate_id=row.id,
            payload={"attachment_id": attachment_id, "revision": row.revision},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="attachment_content_version_created",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"attachment_id": attachment_id, "revision": row.revision},
        )
    return row


def confirm_content_version(
    session: Session,
    ctx: ActorContext,
    *,
    version_id: int,
) -> AttachmentContentVersion:
    actor = load_actor(session, ctx)
    row = session.get(AttachmentContentVersion, version_id)
    if row is None:
        raise_api_error(404, ATTACHMENT_CONTENT_NOT_FOUND, "内容版本不存在")
    attachment, target = _load_attachment(session, row.attachment_id)
    account = _require_claimed_owner(actor, target)
    if row.status != "draft":
        raise_api_error(409, ATTACHMENT_CONTENT_STATE_CONFLICT, "内容版本当前不可确认")

    with command_transaction(session, immediate=True):
        row = session.get(AttachmentContentVersion, version_id)
        if row is None or row.status != "draft":
            raise_api_error(409, ATTACHMENT_CONTENT_STATE_CONFLICT, "内容版本当前不可确认")
        confirmed_at = utcnow()
        previous = session.scalars(
            select(AttachmentContentVersion).where(
                AttachmentContentVersion.attachment_id == attachment.id,
                AttachmentContentVersion.status == "confirmed",
                AttachmentContentVersion.id != row.id,
            )
        ).all()
        for old in previous:
            old.status = "superseded"
            old.superseded_at = confirmed_at
            for grant in session.scalars(
                select(AgentContentGrant).where(
                    AgentContentGrant.content_version_id == old.id,
                    AgentContentGrant.status == "active",
                )
            ).all():
                grant.status = "revoked"
                grant.revoked_at = confirmed_at
                grant.revoke_reason = "content_superseded"
                from app.services import memory_rag

                memory_rag.invalidate_source(
                    session,
                    source_type="authorized_document",
                    source_id=(f"attachment:{row.attachment_id}:version:{old.id}:grant:{grant.id}"),
                )
                emit(
                    session,
                    event_type="agent_content_grant.revoked",
                    aggregate_type="agent_content_grant",
                    aggregate_id=grant.id,
                    payload={"reason": "content_superseded"},
                    space_id=grant.space_id,
                    actor_account_id=account.id,
                )
        row.status = "confirmed"
        row.confirmed_by = actor.id
        row.confirmed_at = confirmed_at
        emit(
            session,
            event_type="attachment.content_version.confirmed",
            aggregate_type="attachment_content_version",
            aggregate_id=row.id,
            payload={"attachment_id": row.attachment_id, "revision": row.revision},
            actor_account_id=account.id,
        )
        audit.write_audit(
            session,
            action="attachment_content_version_confirmed",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"attachment_id": row.attachment_id, "revision": row.revision},
        )
    return row


def create_grant(
    session: Session,
    ctx: ActorContext,
    *,
    version_id: int,
    space_id: int,
    agent_kind: str = "assistant",
    purpose: str = "rag",
    grant_scope: str = "whole",
) -> AgentContentGrant:
    actor = load_actor(session, ctx)
    row = session.get(AttachmentContentVersion, version_id)
    if row is None:
        raise_api_error(404, ATTACHMENT_CONTENT_NOT_FOUND, "内容版本不存在")
    _attachment, target = _load_attachment(session, row.attachment_id)
    account = _require_claimed_owner(actor, target)
    if row.status != "confirmed":
        raise_api_error(409, ATTACHMENT_CONTENT_STATE_CONFLICT, "只有已确认内容可以授权")
    if (agent_kind, purpose, grant_scope) != ("assistant", "rag", "whole"):
        raise_api_error(422, ATTACHMENT_CONTENT_NOT_ALLOWED, "当前只支持 Assistant 的整份 RAG 授权")

    with command_transaction(session, immediate=True):
        space = session.get(FamilySpace, space_id)
        if space is None:
            raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")
        if space.kind != "household" or not is_active_member(session, space_id, actor.id):
            raise_api_error(403, ATTACHMENT_CONTENT_NOT_ALLOWED, "只能授权给本人所在的家庭空间")
        existing = session.scalar(
            select(AgentContentGrant).where(
                AgentContentGrant.content_version_id == version_id,
                AgentContentGrant.space_id == space_id,
                AgentContentGrant.agent_kind == agent_kind,
                AgentContentGrant.purpose == purpose,
                AgentContentGrant.grant_scope == grant_scope,
                AgentContentGrant.status == "active",
            )
        )
        if existing is not None:
            return existing
        current = session.scalar(
            select(func.max(AgentContentGrant.authorization_revision)).where(
                AgentContentGrant.content_version_id == version_id,
                AgentContentGrant.space_id == space_id,
            )
        )
        grant = AgentContentGrant(
            content_version_id=version_id,
            space_id=space_id,
            agent_kind=agent_kind,
            purpose=purpose,
            grant_scope=grant_scope,
            grantor_user_id=actor.id,
            grantor_account_id=account.id,
            authorization_revision=(current or 0) + 1,
            status="active",
            created_at=utcnow(),
        )
        session.add(grant)
        session.flush()
        from app.services import memory_rag, platform_features

        if platform_features.is_rag_enabled(session):
            memory_rag.ingest_attachment_content_grant(session, grant.id)
        emit(
            session,
            event_type="agent_content_grant.created",
            aggregate_type="agent_content_grant",
            aggregate_id=grant.id,
            payload={"content_version_id": version_id, "revision": grant.authorization_revision},
            space_id=space_id,
            actor_account_id=account.id,
        )
        audit.write_audit(
            session,
            action="agent_content_grant_created",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"content_version_id": version_id, "space_id": space_id},
        )
    return grant


def revoke_grant(
    session: Session,
    ctx: ActorContext,
    *,
    grant_id: int,
    reason: str | None = None,
) -> AgentContentGrant:
    actor = load_actor(session, ctx)
    grant = session.get(AgentContentGrant, grant_id)
    if grant is None:
        raise_api_error(404, ATTACHMENT_CONTENT_NOT_FOUND, "授权不存在")
    version = session.get(AttachmentContentVersion, grant.content_version_id)
    if version is None:
        raise_api_error(404, ATTACHMENT_CONTENT_NOT_FOUND, "授权不存在")
    _attachment, target = _load_attachment(session, version.attachment_id)
    account = _require_claimed_owner(actor, target)
    if grant.status != "active":
        raise_api_error(409, ATTACHMENT_CONTENT_STATE_CONFLICT, "授权当前不可撤销")

    with command_transaction(session, immediate=True):
        grant = session.get(AgentContentGrant, grant_id)
        if grant is None or grant.status != "active":
            raise_api_error(409, ATTACHMENT_CONTENT_STATE_CONFLICT, "授权当前不可撤销")
        grant.status = "revoked"
        grant.revoked_at = utcnow()
        grant.revoke_reason = (reason or "user_revoked").strip()[:64] or "user_revoked"
        from app.services import memory_rag

        memory_rag.invalidate_source(
            session,
            source_type="authorized_document",
            source_id=(f"attachment:{version.attachment_id}:version:{version.id}:grant:{grant.id}"),
        )
        emit(
            session,
            event_type="agent_content_grant.revoked",
            aggregate_type="agent_content_grant",
            aggregate_id=grant.id,
            payload={"reason": grant.revoke_reason},
            space_id=grant.space_id,
            actor_account_id=account.id,
        )
        audit.write_audit(
            session,
            action="agent_content_grant_revoked",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"grant_id": grant.id, "reason": grant.revoke_reason},
        )
    return grant
