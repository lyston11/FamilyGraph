"""Independent D review probes; synthetic data, no production services or network."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models.memory import Memory
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument, RAGIndexMaintenanceFailure
from app.models.space import SpaceMember
from app.services import memory_rag, memory_sources, rag_maintenance
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def set_rag(db, enabled):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(
            id=1, memory_enabled=True, rag_enabled=enabled, updated_at=utcnow()
        )
        db.add(row)
    else:
        row.rag_enabled = enabled
    db.flush()


def save_memory(db, user, body, *, source=None, quote=None):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=user.account.id,
        source=source or {"kind": "manual"},
        source_quote=body if quote is None else quote,
        summary=body,
        suggested_scope="private",
        purpose="independent D review",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope="private",
    )


def projection(db, memory):
    return db.scalar(
        select(RAGDocument)
        .where(RAGDocument.source_type == "memory", RAGDocument.source_id == str(memory.id))
        .execution_options(populate_existing=True)
    )


def chunks(db, document):
    return list(
        db.scalars(
            select(RAGChunk)
            .where(RAGChunk.document_id == document.id)
            .order_by(RAGChunk.id)
            .execution_options(populate_existing=True)
        )
    )


def snapshot(db):
    result = {}
    for name in (
        "rag_documents", "rag_chunks", "rag_index_maintenance_state",
        "rag_index_maintenance_failures", "memories", "memory_candidates",
    ):
        result[name] = db.execute(text(f"SELECT * FROM {name} ORDER BY 1")).all()
    result["fts"] = db.execute(
        text("SELECT rowid, chunk_id, text FROM rag_chunks_fts ORDER BY rowid")
    ).all()
    return result


@pytest.fixture()
def world(db_session):
    user, space = create_agent_fixture(db_session, name="D-independent-synthetic")
    set_rag(db_session, True)
    return user, space


@pytest.mark.parametrize("entry", ["ensure", "batch", "stage"])
def test_known_index_superseded_restore(db_session, world, entry):
    """Old explicitly recoverable state, with complete matching evidence and live source."""
    user, _ = world
    memory = save_memory(db_session, user, "orchidgrove intact legacy index")
    document = projection(db_session, memory)
    old_identity = [(row.id, row.text, row.index_version) for row in chunks(db_session, document)]
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    db_session.commit()
    assert memory_sources.memory_materializable(db_session, memory)
    assert document.content_sha256 == memory_rag.query_hash(memory.content)

    if entry == "ensure":
        memory_rag.ensure_memory_index(db_session, memory)
    elif entry == "batch":
        observed = rag_maintenance.run_maintenance_batch(db_session, worker_id="review")
        assert observed["materialized"] == 1, observed
    else:
        observed = rag_maintenance.stage_index_version(
            db_session, target_version="fts5-trigram-v1", worker_id="review"
        )
        assert observed["flipped"] == 1, observed
    db_session.commit()
    actual = projection(db_session, memory)
    assert (actual.status, actual.invalidation_reason) == ("active", None)
    current_identity = [(row.id, row.text, row.index_version) for row in chunks(db_session, actual)]
    assert all(identity in current_identity for identity in old_identity)


@pytest.mark.parametrize("reason", [None, "unknown_legacy", "source_invalidated"])
def test_unknown_or_source_tombstone_stays_closed(db_session, world, reason):
    user, _ = world
    memory = save_memory(db_session, user, "orchidgrove prohibited restore")
    document = projection(db_session, memory)
    document.status = "invalidated"
    document.invalidation_reason = reason
    db_session.commit()
    before = snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, memory)
    assert error.value.status_code == 409
    db_session.commit()
    with SessionLocal() as observer:
        assert snapshot(observer) == before


def test_stage_late_conflict_rolls_back_prior_flips_on_caller_commit(db_session, world):
    user, _ = world
    memories = [save_memory(db_session, user, f"orchidgrove batch source {i}") for i in range(3)]
    document = projection(db_session, memories[-1])
    db_session.add(
        RAGChunk(
            document_id=document.id,
            chunk_index=0,
            text="conflicting preexisting v1 text",
            token_estimate=10,
            source_revision=document.revision,
            index_version="fts5-trigram-v1",
            status="active",
            created_at=utcnow(),
        )
    )
    db_session.commit()
    before = snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        rag_maintenance.stage_index_version(
            db_session, target_version="fts5-trigram-v1", worker_id="review"
        )
    assert error.value.status_code == 409
    db_session.commit()  # Deliberately commit the outer transaction after rejection.
    with SessionLocal() as observer:
        assert snapshot(observer) == before


@pytest.mark.parametrize("entry", ["batch", "stage"])
def test_final_sql_policy_fence_rolls_back_all_prior_writes(db_session, world, monkeypatch, entry):
    user, _ = world
    set_rag(db_session, entry == "stage")
    memories = [save_memory(db_session, user, f"orchidgrove fence source {i}") for i in range(3)]
    last_id = memories[-1].id
    set_rag(db_session, True)
    db_session.commit()
    before = snapshot(db_session)
    original = memory_rag._materialize_chunks
    touched = []

    def materialize(db, document, *args, **kwargs):
        result = original(db, document, *args, **kwargs)
        touched.append(document.id)
        if document.source_id == str(last_id):
            db.execute(text(
                "UPDATE rag_index_maintenance_state SET policy_version = 'future-policy-v9'"
            ))
        return result

    monkeypatch.setattr(memory_rag, "_materialize_chunks", materialize)
    with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
        if entry == "batch":
            rag_maintenance.run_maintenance_batch(db_session, worker_id="review")
        else:
            rag_maintenance.stage_index_version(
                db_session, target_version="fts5-trigram-v1", worker_id="review"
            )
    assert len(touched) == 3
    db_session.commit()
    with SessionLocal() as observer:
        assert snapshot(observer) == before


def test_backoff_does_not_starve_low_id_under_arrivals(db_session, world, monkeypatch):
    user, _ = world
    set_rag(db_session, False)
    low = save_memory(db_session, user, "orchidgrove delayed low id")
    save_memory(db_session, user, "orchidgrove original upper id")
    set_rag(db_session, True)
    db_session.commit()
    original = memory_rag.ensure_memory_index
    attempts = []
    clock = [utcnow()]
    monkeypatch.setattr(rag_maintenance, "utcnow", lambda: clock[0])

    def ensure(db, memory, **kwargs):
        if memory.id == low.id:
            attempts.append(memory.id)
            if len(attempts) == 1:
                raise RuntimeError("one synthetic transient error")
        return original(db, memory, **kwargs)

    monkeypatch.setattr(memory_rag, "ensure_memory_index", ensure)
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="review", batch_size=1)
    db_session.commit()
    assert first["failed"] == 1
    retry_at = db_session.get(RAGIndexMaintenanceFailure, low.id).next_retry_at
    for i in range(3):
        set_rag(db_session, False)
        save_memory(db_session, user, f"orchidgrove arriving {i}")
        set_rag(db_session, True)
        db_session.commit()
        rag_maintenance.run_maintenance_batch(db_session, worker_id="review", batch_size=1)
        db_session.commit()
    assert len(attempts) == 1  # Full-round revisit still obeyed the retry time.
    clock[0] = retry_at + timedelta(seconds=1)
    for _ in range(10):
        rag_maintenance.run_maintenance_batch(db_session, worker_id="review", batch_size=1)
        db_session.commit()
        if projection(db_session, low) is not None:
            break
    assert len(attempts) == 2
    assert projection(db_session, low) is not None
    assert db_session.get(RAGIndexMaintenanceFailure, low.id, populate_existing=True) is None


def test_saved_and_exact_references_survive_switch_repair_and_reader_loss(db_session, world):
    user, space = world
    root = save_memory(db_session, user, "orchidgrove original root " + "long " * 400)
    document = projection(db_session, root)
    old = chunks(db_session, document)[0]
    ref = memory_sources.ExactChunkRef(
        document_id=document.id,
        chunk_id=old.id,
        source_type=document.source_type,
        source_id=document.source_id,
        source_revision=document.revision,
        index_version=old.index_version,
        chunk_index=old.chunk_index,
        content_hash=memory_sources.quote_hash(old.text),
    )
    saved = save_memory(
        db_session, user, "orchidgrove saved dependent", quote=old.text,
        source={"kind": "rag_chunk", "document_id": document.id, "chunk_id": old.id,
                "revision": document.revision, "index_version": old.index_version,
                "space_id": space.id},
    )
    db_session.commit()
    old_identity = (old.id, old.text, old.index_version, old.chunk_index)
    assert rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v1", worker_id="review"
    )["flipped"] == 2
    db_session.commit()
    memory_rag.repair_fts(db_session)
    db_session.commit()
    resolved = memory_sources.read_exact_chunk(
        db_session, ref, actor=user, account=user.account, space_id=space.id
    )
    assert resolved is not None
    assert (resolved.chunk.id, resolved.chunk.text, resolved.chunk.index_version,
            resolved.chunk.chunk_index) == old_identity
    assert memory_sources.read_exact_chunk(
        db_session, ref, actor=user, account=user.account, space_id=space.id,
        require_active_index=True,
    ) is None
    assert memory_sources.memory_access(db_session, saved, actor=user, account=user.account).readable
    member = db_session.scalar(select(SpaceMember).where(
        SpaceMember.user_id == user.id, SpaceMember.space_id == space.id
    ))
    member.status = "removed"
    db_session.commit()
    assert memory_sources.memory_materializable(db_session, root)
    assert memory_rag.repair_fts(db_session) > 0
    db_session.commit()
    assert memory_sources.read_exact_chunk(
        db_session, ref, actor=user, account=user.account, space_id=space.id
    ) is None
    assert projection(db_session, root).status == "active"
    member.status = "active"
    db_session.commit()
    assert memory_sources.read_exact_chunk(
        db_session, ref, actor=user, account=user.account, space_id=space.id
    ) is not None
    assert memory_sources.memory_access(db_session, saved, actor=user, account=user.account).readable


def test_legacy_partial_original_set_cannot_authorize_a_complete_target(db_session, world):
    user, _ = world
    memory = save_memory(db_session, user, "orchidgrove " + "x" * 2200)
    document = projection(db_session, memory)
    original = chunks(db_session, document)
    assert len(original) > 1
    db_session.delete(original[-1])
    document.content_sha256 = None  # Simulate a pre-digest row.
    for i, value in enumerate(memory_rag.INDEX_CHUNKERS["fts5-trigram-v1"](memory.content)):
        db_session.add(RAGChunk(
            document_id=document.id, chunk_index=i, text=value,
            token_estimate=10, source_revision=document.revision,
            index_version="fts5-trigram-v1", status="active", created_at=utcnow(),
        ))
    db_session.commit()
    before = snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        rag_maintenance.stage_index_version(
            db_session, target_version="fts5-trigram-v1", worker_id="review"
        )
    assert error.value.status_code == 409
    db_session.commit()
    with SessionLocal() as observer:
        assert snapshot(observer) == before
