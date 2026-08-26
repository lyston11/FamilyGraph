"""Behavioral contracts for explicit memory confirmation and scope isolation."""

import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import select

from app import config
from app.models.agent import AgentJob
from app.models.rag import RAGChunk
from app.models.space import FamilySpace
from app.models.v2_foundation import DomainEvent
from app.services.domain_events import emit
from app.services.memory_rag import (
    confirm_candidate,
    ingest_authorized_document,
    propose_candidate,
    revoke_memory,
    search_rag,
)


def test_candidate_is_not_retrievable_until_explicit_confirmation(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-owner")
    create_space_member(db_session, space.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_document_ref="authorized-note-1",
        source_quote="Our family recipe is kept in the blue notebook.",
        summary="The family recipe is kept in the blue notebook.",
        suggested_scope="household",
        purpose="family reference",
    )
    db_session.commit()

    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="blue notebook",
        )
        == []
    )

    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=f"household:{space.id}",
    )
    db_session.commit()

    hits = search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="blue notebook",
    )
    assert len(hits) == 1
    assert hits[0].source_id == str(memory.id)


def test_private_memory_is_available_to_assistant_context(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-private-rag")
    create_space_member(db_session, space.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_document_ref="private-note-rag",
        source_quote="The private note mentions a silver locket.",
        summary="A silver locket is mentioned in a private note.",
        suggested_scope="private",
        purpose="private reference",
    )
    db_session.commit()

    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="private",
    )
    db_session.commit()

    hits = search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="silver locket",
        agent_kind="assistant",
    )
    assert [hit.source_id for hit in hits] == [str(memory.id)]


def test_shared_memory_scope_matches_space_kind(db_session):
    owner, household = create_agent_fixture(db_session, name="memory-scope-kind")
    create_space_member(db_session, household.id, owner.id, role="owner")
    lineage = FamilySpace(
        name="memory-scope-kind-lineage",
        kind="lineage",
        owner_id=owner.id,
        created_at=owner.created_at,
    )
    db_session.add(lineage)
    db_session.commit()
    create_space_member(db_session, lineage.id, owner.id, role="owner")
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_document_ref="scope-kind-note",
        source_quote="A scope-kind test note.",
        summary="A scope-kind test note.",
        suggested_scope="household",
        purpose="scope validation",
    )
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        confirm_candidate(
            db_session,
            candidate_id=candidate.id,
            confirmer=owner,
            confirmer_account=owner.account,
            scope=f"household:{lineage.id}",
        )
    assert error.value.status_code == 422


def test_steward_cannot_retrieve_public_rag_documents(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-steward-public")
    create_space_member(db_session, space.id, owner.id, role="owner")
    ingest_authorized_document(
        db_session,
        source_type="authorized_document",
        source_id="steward-public-1",
        text_value="A public document for assistant use only.",
        author_account_id=None,
        scope="public",
        space_id=None,
    )
    db_session.commit()

    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="assistant use only",
            agent_kind="steward",
        )
        == []
    )


def test_private_memory_event_does_not_enqueue_steward_job(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="memory-private-event")
    create_space_member(db_session, space.id, owner.id, role="owner")

    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_document_ref="private-session-1",
        source_quote="A private note that must not become Steward input.",
        summary="A private note.",
        suggested_scope="private",
        purpose="private reflection",
    )
    db_session.commit()

    event = db_session.scalar(
        select(DomainEvent).where(
            DomainEvent.type == "memory.candidate.proposed",
            DomainEvent.aggregate_id == candidate.id,
        )
    )
    assert event is not None
    assert (
        db_session.scalar(
            select(AgentJob).where(AgentJob.kind == "steward", AgentJob.space_id == space.id)
        )
        is None
    )


def test_public_rag_requires_active_space_membership(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-public-owner")
    create_space_member(db_session, space.id, owner.id, role="owner")
    outsider = create_user_with_pin(db_session, "memory-public-outsider", "123456")
    db_session.commit()

    ingest_authorized_document(
        db_session,
        source_type="authorized_document",
        source_id="public-rag-1",
        text_value="This public document is visible only through an authorized space.",
        author_account_id=None,
        scope="public",
        space_id=None,
    )
    db_session.commit()

    assert search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="public document",
    )
    assert (
        search_rag(
            db_session,
            actor=outsider,
            account=outsider.account,
            space_id=space.id,
            query="public document",
        )
        == []
    )


def test_high_public_document_is_rejected(db_session):
    with pytest.raises(HTTPException) as error:
        ingest_authorized_document(
            db_session,
            source_type="authorized_document",
            source_id="public-high-1",
            text_value="must not be indexed",
            author_account_id=None,
            scope="public",
            space_id=None,
            sensitivity="high",
        )
    assert error.value.status_code == 422


def test_domain_event_tombstones_rag_document_and_chunks(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-delete-event")
    create_space_member(db_session, space.id, owner.id, role="owner")
    document = ingest_authorized_document(
        db_session,
        source_type="profile",
        source_id=str(owner.id),
        text_value="Profile text to remove from retrieval.",
        author_account_id=owner.account.id,
        scope="household",
        space_id=space.id,
        sensitivity="normal",
    )
    db_session.commit()
    chunk = db_session.scalar(select(RAGChunk).where(RAGChunk.document_id == document.id))
    assert chunk is not None

    emit(
        db_session,
        event_type="profile.deleted",
        aggregate_type="profile",
        aggregate_id=owner.id,
        space_id=space.id,
        actor_account_id=owner.account.id,
        payload={"reason": "user_request"},
    )
    db_session.commit()

    db_session.refresh(document)
    db_session.refresh(chunk)
    assert document.status == "invalidated"
    assert chunk.status == "invalidated"
    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="Profile text",
        )
        == []
    )


def test_shared_memory_isolation_and_revoke_tombstone(db_session):
    owner, space = create_agent_fixture(db_session, name="memory-owner-2")
    create_space_member(db_session, space.id, owner.id, role="owner")
    other = create_user_with_pin(db_session, "memory-other", "123456")
    db_session.commit()
    candidate = propose_candidate(
        db_session,
        author_account_id=owner.account.id,
        source_document_ref="authorized-note-2",
        source_quote="The private family archive uses a cedar box.",
        summary="The family archive uses a cedar box.",
        suggested_scope="household",
        purpose="family reference",
    )
    db_session.commit()
    memory = confirm_candidate(
        db_session,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope="household",
        space_id=space.id,
    )
    db_session.commit()

    assert (
        search_rag(
            db_session,
            actor=other,
            account=other.account,
            space_id=space.id,
            query="cedar box",
        )
        == []
    )
    assert search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=space.id,
        query="cedar box",
    )

    revoke_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    db_session.commit()
    assert (
        search_rag(
            db_session,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query="cedar box",
        )
        == []
    )
