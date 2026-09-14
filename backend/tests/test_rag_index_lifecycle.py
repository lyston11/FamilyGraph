"""Workstream D regressions: index lifecycle, tombstone no-resurrection,
versioned chunk identity, bounded maintenance and lease fencing (D-AC1..8).

All content synthetic; runs against the real services and in-session
transactions (concurrency is adjudicated by DB constraints and conditional
updates, not process locks).
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.memory import Memory
from app.models.rag import (
    RAGChunk,
    RAGDocument,
    RAGIndexMaintenanceFailure,
    RAGIndexMaintenanceState,
)
from app.services import memory_rag, memory_sources, rag_maintenance
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


@pytest.fixture()
def rag_on(db_session):
    from app.models.platform_features import PlatformFeatureConfig

    row = db_session.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(
            id=1, memory_enabled=True, rag_enabled=True, updated_at=datetime(2026, 9, 14)
        )
        db_session.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = True
    db_session.flush()


def _confirm_memory(db, user, *, summary, scope, space_id=None):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=user.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope=scope,
        purpose="synthetic dataset",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope=scope,
        space_id=space_id,
    )


@pytest.fixture()
def owner(db_session):
    user, _space = create_agent_fixture(db_session, name="idx-owner")
    return user


# ---- MR-25: invalidated documents are never resurrected ----


def test_invalidated_document_not_resurrected_by_index_or_repair(rag_on, db_session, owner):
    memory = _confirm_memory(db_session, owner, summary="春节在上海聚餐。", scope="private")
    db_session.flush()
    memory_rag.revoke_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    db_session.flush()
    document = db_session.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory", RAGDocument.source_id == str(memory.id)
        )
    )
    assert document is not None and document.status == "invalidated"
    assert document.invalidation_reason == "source_invalidated"

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        memory_rag.index_memory(db_session, memory)
    assert exc_info.value.detail["__api_error__"]["code"] == "RAG_SOURCE_NOT_ALLOWED"
    # FTS repair neither resurrects nor errors.
    count = memory_rag.repair_fts(db_session)
    assert count >= 0
    db_session.flush()
    db_session.refresh(document)
    assert document.status == "invalidated"
    # Maintenance skips it without resurrecting.
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
    db_session.flush()
    db_session.refresh(document)
    assert document.status == "invalidated"
    assert result["skipped_invalid"] >= 1


def test_repeated_rebuild_keeps_single_document_and_stable_chunks(rag_on, db_session, owner):
    memory = _confirm_memory(db_session, owner, summary="外公周日上午去杭州体检。", scope="private")
    db_session.flush()
    first = memory_rag.ensure_memory_index(db_session, memory)
    chunk_ids = [
        c.id
        for c in db_session.scalars(
            select(RAGChunk).where(RAGChunk.document_id == first.id).order_by(RAGChunk.chunk_index)
        ).all()
    ]
    for _ in range(3):
        memory_rag.ensure_memory_index(db_session, memory)
        memory_rag.repair_fts(db_session)
    documents = db_session.scalars(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory", RAGDocument.source_id == str(memory.id)
        )
    ).all()
    assert len([d for d in documents if d.status == "active"]) == 1
    second_ids = [
        c.id
        for c in db_session.scalars(
            select(RAGChunk)
            .where(
                RAGChunk.document_id == first.id,
                RAGChunk.index_version == memory_rag.RAG_INDEX_VERSION,
            )
            .order_by(RAGChunk.chunk_index)
        ).all()
    ]
    assert second_ids == chunk_ids


def test_repeated_ensure_reuses_materialization(rag_on, db_session, owner):
    memory = _confirm_memory(db_session, owner, summary="端午节在苏州住两晚。", scope="private")
    db_session.flush()
    doc_a = memory_rag.ensure_memory_index(db_session, memory)
    # A second executor materializing the same source gets the same projection
    # (no duplicate rows, no chunk churn).
    doc_b = memory_rag.ensure_memory_index(db_session, memory)
    assert doc_a.id == doc_b.id
    chunks = db_session.scalars(
        select(RAGChunk).where(
            RAGChunk.document_id == doc_a.id,
            RAGChunk.index_version == memory_rag.RAG_INDEX_VERSION,
        )
    ).all()
    assert len(chunks) == len(memory_rag._chunk_text(memory.content))


def test_legacy_unverified_never_indexed(rag_on, db_session, owner):
    from app.models.memory import MemoryCandidate
    from conftest import create_agent_message, create_agent_session

    _user, space = create_agent_fixture(db_session, name="legacy-src")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    message = create_agent_message(db_session, session)
    now = utcnow()
    candidate = MemoryCandidate(
        author_account_id=owner.account.id,
        source_message_id=message.id,
        source_span_json={},
        source_kind="agent_message",
        source_verification="unverified",
        source_type="agent_message",
        source_id=str(message.id),
        source_revision=1,
        source_space_id=None,
        source_quote="Legacy unverified text.",
        summary="Legacy unverified text.",
        suggested_scope="private",
        purpose="legacy",
        sensitivity="normal",
        extractor_version="old",
        status="confirmed",
        confirmed_by_account_id=owner.account.id,
        confirmed_at=now,
        decided_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()
    memory = Memory(
        author_account_id=owner.account.id,
        source_candidate_id=candidate.id,
        source_message_id=message.id,
        raw_quote=candidate.source_quote,
        content=candidate.summary,
        scope="private",
        sensitivity="normal",
        purpose="legacy",
        source_verification="unverified",
        confirmed_by_account_id=owner.account.id,
        confirmed_at=now,
        created_at=now,
        updated_at=now,
        status="active",
    )
    db_session.add(memory)
    db_session.flush()
    assert not memory_sources.memory_materializable(db_session, memory)
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
    db_session.flush()
    document = db_session.scalar(select(RAGDocument).where(RAGDocument.source_id == str(memory.id)))
    assert document is None
    assert result["skipped_invalid"] >= 1
    # Original record untouched.
    db_session.refresh(memory)
    assert memory.status == "active" and memory.content == "Legacy unverified text."


def test_revoked_source_not_resurrected_by_maintenance_rounds(rag_on, db_session, owner):
    memory = _confirm_memory(db_session, owner, summary="奶奶喜欢清淡饮食。", scope="private")
    db_session.flush()
    memory_rag.revoke_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    for _ in range(3):
        rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
        db_session.flush()
    document = db_session.scalar(select(RAGDocument).where(RAGDocument.source_id == str(memory.id)))
    assert document is not None
    assert document.status == "invalidated"
    db_session.refresh(memory)
    assert memory.status == "revoked" and memory.raw_quote is not None


# ---- bounded maintenance: closed → confirm → open → backfill (D-AC1) ----


def test_backfill_after_rag_enabled_with_cursor_rounds(db_session, owner):
    from app.models.platform_features import PlatformFeatureConfig

    row = db_session.get(PlatformFeatureConfig, 1)
    if row is None:
        db_session.add(
            PlatformFeatureConfig(
                id=1, memory_enabled=True, rag_enabled=False, updated_at=datetime(2026, 9, 14)
            )
        )
    else:
        row.rag_enabled = False
    db_session.flush()

    closed_memories = [
        _confirm_memory(db_session, owner, summary=f"补建样本 {index} 号。", scope="private")
        for index in range(3)
    ]
    db_session.commit()

    row = db_session.get(PlatformFeatureConfig, 1)
    row.rag_enabled = True
    db_session.flush()

    # RAG off → no work.
    row.rag_enabled = False
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1", batch_size=2)
    assert result == {"skipped": "rag_disabled"}
    row.rag_enabled = True
    db_session.flush()

    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1", batch_size=2)
    assert first["scanned"] == 2
    assert first["materialized"] == 2
    db_session.commit()
    second = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1", batch_size=2)
    assert second["scanned"] == 1
    # A round that scanned fewer than the batch bound completes and restarts.
    assert second["round"] == (first["round"] + 1) if first.get("round") is not None else True
    db_session.commit()
    # Everything legal is now retrievable without re-confirmation.
    for memory in closed_memories:
        document = db_session.scalar(
            select(RAGDocument).where(
                RAGDocument.source_id == str(memory.id), RAGDocument.status == "active"
            )
        )
        assert document is not None
    db_session.refresh(closed_memories[0])
    assert closed_memories[0].status == "active"


def test_failure_ledger_backoff_and_non_blocking(db_session, owner, monkeypatch):
    platform_features_set(rag=True)
    memory = _confirm_memory(db_session, owner, summary="要失败的样本。", scope="private")
    db_session.flush()

    def broken_ensure(_db, _memory):
        raise RuntimeError("synthetic materialization failure")

    monkeypatch.setattr(memory_rag, "ensure_memory_index", broken_ensure)
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
    assert first["failed"] == 1
    ledger = db_session.get(RAGIndexMaintenanceFailure, memory.id)
    assert ledger is not None and ledger.retry_count == 1
    # Backoff: an immediate retry is a no-op for this record.
    second = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
    assert second["scanned"] >= 1 and second["failed"] == 0
    ledger = db_session.get(RAGIndexMaintenanceFailure, memory.id)
    assert ledger.retry_count == 1
    monkeypatch.undo()
    # After the backoff window the record is retried and clears.
    ledger_row = db_session.get(RAGIndexMaintenanceFailure, memory.id)
    ledger_row.next_retry_at = utcnow() - timedelta(seconds=1)
    db_session.flush()
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="w1")
    assert result["failed"] == 0
    assert db_session.get(RAGIndexMaintenanceFailure, memory.id) is None


def platform_features_set(*, rag: bool) -> None:
    # helper used by the failure test before fixtures run its own world
    from app.db import SessionLocal
    from app.models.platform_features import PlatformFeatureConfig

    session = SessionLocal()
    try:
        row = session.get(PlatformFeatureConfig, 1)
        if row is None:
            session.add(
                PlatformFeatureConfig(
                    id=1, memory_enabled=True, rag_enabled=rag, updated_at=datetime(2026, 9, 14)
                )
            )
        else:
            row.rag_enabled = rag
        session.commit()
    finally:
        session.close()


def test_lease_fence_blocks_second_executor_and_expires(db_session, owner, rag_on):
    _confirm_memory(db_session, owner, summary="租约栅栏样本。", scope="private")
    db_session.flush()
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="executor-a")
    assert "skipped" not in first
    state = db_session.get(RAGIndexMaintenanceState, 1)
    assert state.lease_owner == "executor-a"
    # Same-owner re-run allowed; different owner inside lease window loses.
    from app.services.rag_maintenance import MaintenanceLeaseLost

    state.lease_expires_at = utcnow() + timedelta(seconds=60)
    db_session.flush()
    with pytest.raises(MaintenanceLeaseLost):
        rag_maintenance.run_maintenance_batch(db_session, worker_id="executor-b")
    # After expiry the other executor proceeds.
    state.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.flush()
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="executor-b")
    assert "skipped" not in result


def test_stale_executor_cannot_advance_cursor(db_session, owner, rag_on):
    _confirm_memory(db_session, owner, summary="游标推进样本。", scope="private")
    db_session.flush()
    rag_maintenance.run_maintenance_batch(db_session, worker_id="executor-a")
    state = db_session.get(RAGIndexMaintenanceState, 1)
    baseline = state.cursor_memory_id
    # Simulate a stale executor writing with an old attempt via the fenced path.
    from app.services.rag_maintenance import MaintenanceLeaseLost, _renew_lease

    with pytest.raises(MaintenanceLeaseLost):
        _renew_lease(db_session, state, expected_attempt=int(state.attempt) + 5, now=utcnow())
    db_session.refresh(state)
    assert state.cursor_memory_id == baseline


# ---- FTS repair vs materialization separation (D-AC6/AC7) ----


def test_fts_repair_does_not_touch_business_state(rag_on, db_session, owner):
    memory = _confirm_memory(db_session, owner, summary="FTS 修复不影响业务状态。", scope="private")
    db_session.flush()
    memory_rag.revoke_memory(db_session, memory_id=memory.id, account_id=owner.account.id)
    before_status = db_session.get(Memory, memory.id).status
    memory_rag.repair_fts(db_session)
    db_session.flush()
    assert db_session.get(Memory, memory.id).status == before_status == "revoked"
    document = db_session.scalar(select(RAGDocument).where(RAGDocument.source_id == str(memory.id)))
    assert document.status == "invalidated"
    assert document.invalidation_reason == "source_invalidated"


def test_search_reads_only_active_chunk_version(db_session, owner, rag_on):
    memory = _confirm_memory(db_session, owner, summary="活动版本裁定样本春节。", scope="private")
    db_session.flush()
    document = memory_rag.ensure_memory_index(db_session, memory)
    assert document.id > 0
    # Stage a foreign-version chunk with tempting text: the activity pointer
    # must keep it non-recallable.
    db_session.add(
        RAGChunk(
            document_id=document.id,
            chunk_index=99,
            source_revision=document.revision,
            text="春节 staged secret",
            token_estimate=8,
            index_version="fts5-trigram-v1-staged",
            status="active",
            created_at=utcnow(),
        )
    )
    db_session.flush()
    hits = memory_rag.search_rag(
        db_session,
        actor=owner,
        account=owner.account,
        space_id=1,
        query="staged secret",
        agent_kind="assistant",
    )
    assert all(hit.index_version == memory_rag.RAG_INDEX_VERSION for hit in hits)


def test_stage_index_version_flips_only_legal_documents(db_session, owner, rag_on, monkeypatch):
    memory = _confirm_memory(db_session, owner, summary="换版样本春节。", scope="private")
    revoked = _confirm_memory(db_session, owner, summary="换版撤销样本春节。", scope="private")
    db_session.flush()
    memory_rag.revoke_memory(db_session, memory_id=revoked.id, account_id=owner.account.id)
    monkeypatch.setitem(memory_rag.INDEX_CHUNKERS, "fts5-trigram-v2-test", memory_rag._chunk_text)
    result = rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v2-test", worker_id="switcher"
    )
    assert result["staged"] >= 1
    doc = db_session.scalar(
        select(RAGDocument).where(
            RAGDocument.source_id == str(memory.id), RAGDocument.status == "active"
        )
    )
    assert doc.index_version == "fts5-trigram-v2-test"
    # Old chunks retained for exact historical reads.
    old_chunks = db_session.scalars(
        select(RAGChunk).where(
            RAGChunk.document_id == doc.id,
            RAGChunk.index_version == memory_rag.RAG_INDEX_VERSION,
        )
    ).all()
    assert len(old_chunks) >= 1
    revoked_doc = db_session.scalar(
        select(RAGDocument).where(RAGDocument.source_id == str(revoked.id))
    )
    assert revoked_doc.index_version == memory_rag.RAG_INDEX_VERSION
    assert revoked_doc.status == "invalidated"


def test_maintenance_status_has_no_content(rag_on, db_session, owner):
    status = rag_maintenance.maintenance_status(db_session)
    assert set(status).issubset(
        {
            "policy_version",
            "round",
            "cursor_memory_id",
            "upper_memory_id",
            "cursor_document_id",
            "upper_document_id",
            "stage_round",
            "target_index_version",
            "last_success_at",
            "has_failures",
            "active_index_version",
        }
    )
