"""V2.5 persistence contract and FTS invalidation regressions."""

from __future__ import annotations

from conftest import create_agent_fixture, create_agent_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.memory_rag import RAGChunk, RAGDocument
from app.utils import timeutil


def test_rag_fts_is_trigram_index_but_lifecycle_filter_is_required(db_session):
    user, space = create_agent_fixture(db_session, name="rag-owner")
    session = create_agent_session(
        db_session, account_id=user.account.id, space_id=space.id, kind="assistant"
    )
    now = timeutil.utcnow()
    document = RAGDocument(
        source_type="family_story",
        source_id="101",
        revision=1,
        author_account_id=user.account.id,
        space_id=space.id,
        scope="household",
        sensitivity="normal",
        confirmation_status="confirmed",
        visibility_snapshot={},
        visibility_snapshot_key="visibility-v1",
        index_version="fts-v1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(document)
    db_session.flush()
    chunk = RAGChunk(
        document_id=document.id,
        chunk_index=0,
        text="grandmother keeps the family recipe book",
        token_estimate=6,
        created_at=now,
    )
    db_session.add(chunk)
    db_session.commit()

    matches = (
        db_session.execute(
            text(
                "SELECT c.id FROM rag_chunks_fts f "
                "JOIN rag_chunks c ON c.id = f.rowid "
                "JOIN rag_documents d ON d.id = c.document_id "
                "WHERE rag_chunks_fts MATCH :query "
                "AND c.status = 'active' AND d.status = 'active' "
                "AND d.space_id = :space_id"
            ),
            {"query": "recipe", "space_id": space.id},
        )
        .scalars()
        .all()
    )
    assert matches == [chunk.id]

    # A tombstone/invalidation makes the old chunk unservable immediately; the
    # asynchronous physical projector is not part of the security predicate.
    document.status = "invalidated"
    document.invalidated_at = timeutil.utcnow()
    db_session.commit()
    assert (
        db_session.execute(
            text(
                "SELECT c.id FROM rag_chunks_fts f "
                "JOIN rag_chunks c ON c.id = f.rowid "
                "JOIN rag_documents d ON d.id = c.document_id "
                "WHERE rag_chunks_fts MATCH :query "
                "AND c.status = 'active' AND d.status = 'active' "
                "AND d.space_id = :space_id"
            ),
            {"query": "recipe", "space_id": space.id},
        )
        .scalars()
        .all()
        == []
    )

    # The model is still attached to a run/session boundary in the test setup;
    # the RAG query itself never derives authorization from that boundary.
    assert session.space_id == space.id


def test_rag_document_rejects_unconfirmed_projection(db_session):
    user, space = create_agent_fixture(db_session, name="unconfirmed-rag")
    now = timeutil.utcnow()
    db_session.add(
        RAGDocument(
            source_type="profile",
            source_id=str(user.id),
            revision=1,
            author_account_id=user.account.id,
            space_id=space.id,
            scope="household",
            sensitivity="normal",
            confirmation_status="pending",
            visibility_snapshot={},
            visibility_snapshot_key="visibility-v1",
            index_version="fts-v1",
            created_at=now,
            updated_at=now,
        )
    )
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
    else:  # pragma: no cover - the database constraint is the subject of this test
        raise AssertionError("unconfirmed RAG projection was persisted")
