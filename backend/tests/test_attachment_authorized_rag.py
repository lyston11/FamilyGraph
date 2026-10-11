from __future__ import annotations

from sqlalchemy import select
from test_attachment_content import _attachment, _ctx

from app.commands.attachment_content import (
    confirm_content_version,
    create_content_version,
    create_grant,
    revoke_grant,
)
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument
from app.services import memory_rag
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _enable_rag(db):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def test_authorized_document_is_indexed_with_grant_and_revoked_before_cleanup(db_session):
    _enable_rag(db_session)
    owner, space = create_agent_fixture(db_session, name="RAG附件主人")
    attachment = _attachment(db_session, owner, kind="link")
    draft = create_content_version(
        db_session,
        _ctx(owner),
        attachment_id=attachment.id,
        text="祖屋照片说明：院子里的桂花树。",
    )
    version = confirm_content_version(db_session, _ctx(owner), version_id=draft.id)
    grant = create_grant(db_session, _ctx(owner), version_id=version.id, space_id=space.id)

    source_id = f"attachment:{attachment.id}:version:{version.id}:grant:{grant.id}"
    document = db_session.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "authorized_document",
            RAGDocument.source_id == source_id,
        )
    )
    assert document is not None
    assert document.scope == "household"
    assert document.space_id == space.id
    assert document.owner_user_id == owner.id

    hits = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="祖屋 桂花",
        source_types=("authorized_document",),
        for_model=False,
    )
    assert hits
    assert hits[0].source_type == "authorized_document"
    assert hits[0].citation_handle.startswith("rag:attachment:")

    revoked = revoke_grant(db_session, _ctx(owner), grant_id=grant.id, reason="撤销测试")
    assert revoked.status == "revoked"
    db_session.commit()
    assert (
        db_session.scalar(select(RAGDocument.status).where(RAGDocument.id == document.id))
        == "invalidated"
    )
    assert (
        memory_rag.search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="祖屋 桂花",
            source_types=("authorized_document",),
            for_model=False,
        )
        == []
    )


def test_authorized_document_requires_explicit_source_type_filter_for_memory_tool():
    # Contract-level proof is kept beside the real integration test: callers must
    # opt into this category; `None` remains the low-level compatibility default.
    from app.services import agent_tools

    assert agent_tools.MEMORY_TOOL_SOURCE_TYPES == ("memory", "authorized_document")
