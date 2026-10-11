from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.commands.attachment_content import (
    confirm_content_version,
    create_content_version,
    create_grant,
    revoke_grant,
)
from app.commands.context import ActorContext
from app.models.attachment import Attachment
from app.models.space import FamilySpace, SpaceMember
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_user_with_pin


def _ctx(user):
    return ActorContext.from_identity(user, user.account)


def _attachment(db, user, *, kind="link"):
    row = Attachment(
        user_id=user.id,
        type=kind,
        url_or_path="https://example.com/family" if kind == "link" else "photo.png",
        title="资料",
        description="说明",
        uploaded_by=user.id,
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def test_content_version_is_immutable_and_confirmation_supersedes_old(db_session):
    user, _space = create_agent_fixture(db_session, name="内容主人")
    attachment = _attachment(db_session, user)

    draft = create_content_version(
        db_session, _ctx(user), attachment_id=attachment.id, text="第一次确认的资料"
    )
    assert draft.status == "draft"
    assert draft.revision == 1
    assert len(draft.content_sha256) == 64

    confirmed = confirm_content_version(db_session, _ctx(user), version_id=draft.id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_by == user.id

    second = create_content_version(
        db_session, _ctx(user), attachment_id=attachment.id, text="第二次确认的资料"
    )
    assert second.revision == 2
    confirm_content_version(db_session, _ctx(user), version_id=second.id)
    db_session.refresh(confirmed)
    assert confirmed.status == "superseded"

    with pytest.raises(HTTPException) as exc_info:
        confirm_content_version(db_session, _ctx(user), version_id=confirmed.id)
    assert exc_info.value.status_code == 409


def test_only_claimed_profile_owner_can_confirm(db_session):
    owner = create_user_with_pin(
        db_session,
        "待认领档案",
        "111111",
        claim_status="managed",
        profile_status="identity_confirmed",
    )
    creator = create_user_with_pin(db_session, "代管人", "222222")
    owner.created_by = creator.id
    db_session.commit()
    attachment = _attachment(db_session, owner)
    draft = create_content_version(
        db_session, _ctx(creator), attachment_id=attachment.id, text="代管人填写的文字"
    )

    with pytest.raises(HTTPException) as exc_info:
        confirm_content_version(db_session, _ctx(creator), version_id=draft.id)
    assert exc_info.value.status_code == 403


def test_household_assistant_grant_is_reversible_and_not_reused(db_session):
    user, space = create_agent_fixture(db_session, name="授权主人")
    attachment = _attachment(db_session, user, kind="image")
    draft = create_content_version(
        db_session, _ctx(user), attachment_id=attachment.id, text="可授权的图片说明"
    )
    version = confirm_content_version(db_session, _ctx(user), version_id=draft.id)

    grant = create_grant(db_session, _ctx(user), version_id=version.id, space_id=space.id)
    assert grant.status == "active"
    assert grant.agent_kind == "assistant"
    assert grant.purpose == "rag"
    assert grant.grant_scope == "whole"
    assert (
        create_grant(db_session, _ctx(user), version_id=version.id, space_id=space.id).id
        == grant.id
    )

    revoked = revoke_grant(db_session, _ctx(user), grant_id=grant.id, reason="不再使用")
    assert revoked.status == "revoked"
    with pytest.raises(HTTPException) as exc_info:
        revoke_grant(db_session, _ctx(user), grant_id=grant.id)
    assert exc_info.value.status_code == 409

    regrant = create_grant(db_session, _ctx(user), version_id=version.id, space_id=space.id)
    assert regrant.id != grant.id
    assert regrant.authorization_revision == grant.authorization_revision + 1


def test_grant_requires_household_and_active_membership(db_session):
    user, household = create_agent_fixture(db_session, name="空间授权人")
    attachment = _attachment(db_session, user)
    draft = create_content_version(db_session, _ctx(user), attachment_id=attachment.id, text="文字")
    version = confirm_content_version(db_session, _ctx(user), version_id=draft.id)

    lineage = FamilySpace(name="lineage", kind="lineage", owner_id=user.id, created_at=utcnow())
    db_session.add(lineage)
    db_session.flush()
    db_session.add(
        SpaceMember(
            space_id=lineage.id,
            user_id=user.id,
            added_by=user.id,
            role="space_admin",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        create_grant(db_session, _ctx(user), version_id=version.id, space_id=lineage.id)
    assert exc_info.value.status_code == 403

    outsider = create_user_with_pin(db_session, "外部", "333333")
    db_session.commit()
    inactive = FamilySpace(
        name="other", kind="household", owner_id=outsider.id, created_at=utcnow()
    )
    db_session.add(inactive)
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        create_grant(db_session, _ctx(user), version_id=version.id, space_id=inactive.id)
    assert exc_info.value.status_code == 403
