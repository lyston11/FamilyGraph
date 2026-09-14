"""D acceptance: real sessions, production tick, immutable data and bounded rounds.

All rows and clocks are synthetic. The v3 chunker is test-only; production
registers only the actual v1 and v2 algorithms.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import config
from app.db import SessionLocal
from app.models.memory import Memory
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument, RAGIndexMaintenanceState
from app.models.steward import StewardJob
from app.services import maintenance, memory_rag, memory_sources, rag_maintenance, steward
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture

V3 = "fts5-trigram-v3-test"


def features(db, *, rag):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, memory_enabled=True, rag_enabled=rag, updated_at=utcnow())
        db.add(row)
    else:
        row.rag_enabled = rag
    db.flush()


def confirm(db, user, body="orchidgrove synthetic memory", *, source=None, quote=None):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=user.account.id,
        source=source or {"kind": "manual"},
        source_quote=quote if quote is not None else body,
        summary=body,
        suggested_scope="private",
        purpose="synthetic lifecycle acceptance",
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=user,
        confirmer_account=user.account,
        scope="private",
    )


def document_for(db, memory_id):
    return db.scalar(
        select(RAGDocument)
        .where(RAGDocument.source_type == "memory", RAGDocument.source_id == str(memory_id))
        .execution_options(populate_existing=True)
    )


def chunks_for(db, document):
    return list(
        db.scalars(
            select(RAGChunk)
            .where(
                RAGChunk.document_id == document.id,
                RAGChunk.index_version == document.index_version,
            )
            .order_by(RAGChunk.chunk_index)
            .execution_options(populate_existing=True)
        )
    )


def projection_snapshot(db):
    tables = (
        "rag_documents",
        "rag_chunks",
        "rag_index_maintenance_state",
        "rag_index_maintenance_failures",
    )
    snapshot = {
        table: db.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all() for table in tables
    }
    snapshot["fts"] = db.execute(
        text("SELECT rowid, chunk_id, text FROM rag_chunks_fts ORDER BY rowid")
    ).all()
    return snapshot


def search(db, user, space, query="orchidgrove"):
    return memory_rag.search_rag(
        db, actor=user, account=user.account, space_id=space.id, query=query, for_model=False
    )


@pytest.fixture()
def world(db_session):
    user, space = create_agent_fixture(db_session, name="lifecycle-acceptance")
    features(db_session, rag=True)
    return user, space


@pytest.fixture()
def synthetic_v3(monkeypatch):
    monkeypatch.setitem(
        memory_rag.INDEX_CHUNKERS, V3, lambda value: memory_rag._chunk_text(value, max_chars=400)
    )
    return V3


@pytest.mark.parametrize("entry", ["memory", "authorized"])
def test_two_real_sessions_create_one_canonical_projection(db_session, world, entry):
    user, _ = world
    features(db_session, rag=False)
    memory = confirm(db_session, user)
    features(db_session, rag=True)
    db_session.commit()
    memory_id, account_id = memory.id, user.account.id
    barrier = Barrier(2)

    def create():
        with SessionLocal() as db:
            cached = db.get(Memory, memory_id)
            assert document_for(db, memory_id) is None
            barrier.wait(timeout=5)
            if entry == "memory":
                document = memory_rag.index_memory(db, cached)
            else:
                document = memory_rag.ingest_authorized_document(
                    db,
                    source_type="authorized_document",
                    source_id="canonical-race",
                    author_account_id=account_id,
                    scope="private",
                    space_id=None,
                    text_value="orchidgrove authorized synthetic source",
                )
            ids = document.id, tuple(row.id for row in chunks_for(db, document))
            db.commit()
            return ids

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _ in range(2)]
        ids = [future.result(timeout=10) for future in futures]
    assert ids[0] == ids[1]
    assert db_session.scalar(text("SELECT COUNT(*) FROM rag_documents")) == 1
    assert db_session.scalar(text("SELECT COUNT(*) FROM rag_chunks")) == 1
    # Enforce identity below the service as well, including the mirror rule.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO rag_documents (source_type,source_id,revision,source_revision,scope,"
                "sensitivity,confirmation_status,index_version,status,created_at,updated_at) "
                "SELECT source_type,source_id,revision,source_revision,scope,sensitivity,"
                "confirmation_status,index_version,status,created_at,updated_at FROM rag_documents"
            )
        )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("UPDATE rag_documents SET source_revision = revision + 1"))


@pytest.mark.parametrize(
    "different",
    [
        {"text_value": "other content"},
        {"sensitivity": "sensitive"},
        {"visibility_snapshot_key": "different-authorization"},
    ],
)
def test_authorized_replay_rejects_text_or_metadata_conflicts(db_session, world, different):
    user, _ = world
    kwargs = dict(
        source_type="authorized_document",
        source_id="same-source",
        revision=3,
        author_account_id=user.account.id,
        scope="private",
        space_id=None,
        text_value="orchidgrove unchanged content",
    )
    first = memory_rag.ingest_authorized_document(db_session, **kwargs)
    assert memory_rag.ingest_authorized_document(db_session, **kwargs).id == first.id
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ingest_authorized_document(db_session, **{**kwargs, **different})
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before


def test_missing_chunk_plus_same_revision_edit_cannot_rewrite_identity(db_session, world):
    user, _ = world
    body = "A" * 800 + "B" * 600
    memory = confirm(db_session, user, body, quote="independent raw source quote")
    document = document_for(db_session, memory.id)
    original_digest = document.content_sha256
    assert original_digest == memory_rag.query_hash(body)
    assert original_digest != memory.source_span_json["quote_sha256"]
    chunks = chunks_for(db_session, document)
    assert len(chunks) == 2
    db_session.delete(chunks[1])
    memory.content = "A" * 800 + "C" * 600  # surviving first chunk still matches
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, memory)
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before
    assert document_for(db_session, memory.id).content_sha256 == original_digest


def test_partial_legacy_projection_cannot_sign_current_content(db_session, world):
    user, _ = world
    memory = confirm(db_session, user, "A" * 800 + "B" * 600)
    document = document_for(db_session, memory.id)
    document.content_sha256 = None  # exact state of a migrated legacy projection
    db_session.delete(chunks_for(db_session, document)[-1])
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException):
        memory_rag.ensure_memory_index(db_session, memory)
    db_session.commit()
    assert projection_snapshot(db_session) == before
    assert document_for(db_session, memory.id).content_sha256 is None


@pytest.mark.parametrize("damage", ["text", "source_revision", "status", "extra"])
def test_ensure_never_rewrites_existing_chunk_fields(db_session, world, damage):
    user, _ = world
    memory = confirm(db_session, user)
    document = document_for(db_session, memory.id)
    chunk = chunks_for(db_session, document)[0]
    if damage == "extra":
        db_session.add(
            RAGChunk(
                document_id=document.id,
                chunk_index=99,
                source_revision=1,
                text="extra",
                token_estimate=2,
                index_version=document.index_version,
                status="active",
                created_at=utcnow(),
            )
        )
    else:
        setattr(
            chunk,
            damage,
            {"text": "conflicting text", "source_revision": 2, "status": "invalidated"}[damage],
        )
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException):
        memory_rag.index_memory(db_session, memory)
    db_session.commit()
    assert projection_snapshot(db_session) == before


def test_missing_chunks_and_fts_are_restored_only_with_complete_input_evidence(db_session, world):
    user, space = world
    memory = confirm(db_session, user, "orchidgrove " + "A" * 1500)
    document = document_for(db_session, memory.id)
    chunks = chunks_for(db_session, document)
    survivor = (chunks[0].id, chunks[0].text, chunks[0].index_version)
    db_session.delete(chunks[-1])
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="restorer")
    assert result["materialized"] == 1 and result["failed"] == 0
    restored = chunks_for(db_session, document)
    assert [row.text for row in restored] == memory_rag._chunk_text(memory.content)
    assert (restored[0].id, restored[0].text, restored[0].index_version) == survivor
    assert search(db_session, user, space)


def test_verified_again_recovers_missing_fts_on_next_round(db_session, world):
    user, space = world
    memory = confirm(db_session, user)
    document = document_for(db_session, memory.id)
    original_ids = [row.id for row in chunks_for(db_session, document)]
    memory.source_verification = "unverified"
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="restorer")
    assert first["skipped_invalid"] == 1
    db_session.commit()
    memory.source_verification = "verified"
    db_session.commit()
    second = rag_maintenance.run_maintenance_batch(db_session, worker_id="restorer")
    assert second["materialized"] == 1
    assert [row.id for row in chunks_for(db_session, document)] == original_ids
    assert search(db_session, user, space)


def test_stale_identity_map_cannot_take_lease_or_rewind_cursor(db_session, world):
    user, _ = world
    features(db_session, rag=False)
    for index in range(4):
        confirm(db_session, user, f"orchidgrove {index}")
    features(db_session, rag=True)
    rag_maintenance._ensure_state(db_session, utcnow())
    db_session.commit()
    with SessionLocal() as stale:
        cached = stale.get(RAGIndexMaintenanceState, 1)
        assert cached.cursor_memory_id == 0
        with SessionLocal() as current:
            result = rag_maintenance.run_maintenance_batch(
                current, worker_id="current", batch_size=2
            )
            assert result["materialized"] == 2
            current.commit()
        assert cached.cursor_memory_id == 0  # actual stale ORM state, not attempt + 5
        with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
            rag_maintenance._acquire_lease(stale, cached, "stale", utcnow())
        stale.rollback()
        with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
            rag_maintenance.run_maintenance_batch(stale, worker_id="stale", batch_size=1)
        stale.commit()
    current = db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True)
    assert (current.cursor_memory_id, current.upper_memory_id, current.lease_owner) == (
        2,
        4,
        "current",
    )
    assert db_session.scalar(text("SELECT COUNT(*) FROM rag_documents")) == 2


def test_expired_worker_cannot_renew_after_real_session_takes_lease(db_session, world, monkeypatch):
    user, _ = world
    confirm(db_session, user)
    state = rag_maintenance._ensure_state(db_session, utcnow())
    lease = rag_maintenance._acquire_lease(db_session, state, "old", utcnow())
    db_session.commit()
    future = lease.lease_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(rag_maintenance, "utcnow", lambda: future)
    with SessionLocal() as other:
        rag_maintenance.run_maintenance_batch(other, worker_id="replacement")
        other.commit()
    assert state.attempt != db_session.scalar(
        text("SELECT attempt FROM rag_index_maintenance_state")
    )
    with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
        rag_maintenance._renew_lease(db_session, lease, expected_attempt=lease.attempt, now=future)
    db_session.rollback()
    actual = db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True)
    assert actual.lease_owner == "replacement" and actual.attempt == lease.attempt + 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("round", 7),
        ("stage_round", 9),
        ("cursor_memory_id", 88),
        ("upper_memory_id", 99),
        ("cursor_document_id", 10),
        ("upper_document_id", 20),
        ("policy_version", "future-policy-v99"),
        ("target_index_version", "future-index-v99"),
        ("lease_owner", "replacement"),
    ],
)
def test_lease_cas_binds_all_persistent_expectations(db_session, world, field, value):
    state = rag_maintenance._ensure_state(db_session, utcnow())
    lease = rag_maintenance._acquire_lease(db_session, state, "old", utcnow())
    db_session.commit()
    with SessionLocal() as other:
        current = other.get(RAGIndexMaintenanceState, 1)
        setattr(current, field, value)
        other.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
        rag_maintenance._renew_lease(
            db_session, lease, expected_attempt=lease.attempt, now=utcnow()
        )
    db_session.commit()
    assert projection_snapshot(db_session) == before


@pytest.mark.parametrize("future", ["policy", "target"])
def test_old_worker_cannot_modify_future_policy_or_unknown_target(db_session, world, future):
    user, _ = world
    features(db_session, rag=False)
    confirm(db_session, user)
    features(db_session, rag=True)
    state = rag_maintenance._ensure_state(db_session, utcnow())
    if future == "policy":
        state.policy_version = "future-policy-v99"
    else:
        state.target_index_version = "future-index-v99"
    state.round = 99
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(rag_maintenance.MaintenanceLeaseLost):
        rag_maintenance.run_maintenance_batch(db_session, worker_id="old")
    db_session.commit()
    assert projection_snapshot(db_session) == before


@pytest.mark.parametrize("entry", ["index", "batch", "stage"])
def test_source_revoked_after_initial_read_before_writer_is_not_materialized(
    db_session,
    world,
    monkeypatch,
    synthetic_v3,
    entry,
):
    user, _ = world
    features(db_session, rag=entry == "stage")
    memory = confirm(db_session, user)
    features(db_session, rag=True)
    db_session.commit()
    memory_id, account_id = memory.id, user.account.id
    original_writer = memory_rag._acquire_index_writer
    revoked = False

    def revoke_before_lock(db):
        nonlocal revoked
        if not revoked:
            revoked = True
            with SessionLocal() as other:
                memory_rag.revoke_memory(other, memory_id=memory_id, account_id=account_id)
                other.commit()
        original_writer(db)

    # The original loaded Memory remains active until the real writer refresh.
    assert memory.status == "active"
    monkeypatch.setattr(memory_rag, "_acquire_index_writer", revoke_before_lock)
    if entry == "index":
        with pytest.raises(HTTPException):
            memory_rag.index_memory(db_session, memory)
    elif entry == "batch":
        assert (
            rag_maintenance.run_maintenance_batch(db_session, worker_id="scanner")[
                "skipped_invalid"
            ]
            == 1
        )
    else:
        assert (
            rag_maintenance.stage_index_version(db_session, target_version=V3, worker_id="scanner")[
                "flipped"
            ]
            == 0
        )
    db_session.commit()
    assert db_session.get(Memory, memory_id, populate_existing=True).status == "revoked"
    if entry == "stage":
        document = document_for(db_session, memory_id)
        assert (
            document.status == "invalidated"
            and document.index_version == memory_rag.RAG_INDEX_VERSION
        )
        assert db_session.scalar(select(RAGChunk.id).where(RAGChunk.index_version == V3)) is None
    else:
        assert document_for(db_session, memory_id) is None


@pytest.mark.parametrize("rejection", ["final_lease", "final_flag", "final_source", "item_lease"])
def test_real_tick_rolls_back_entire_rag_batch_and_preserves_core(
    db_session,
    world,
    monkeypatch,
    rejection,
):
    user, space = world
    features(db_session, rag=False)
    memories = [confirm(db_session, user, f"orchidgrove tick {i}") for i in range(3)]
    memory_ids = [row.id for row in memories]
    features(db_session, rag=True)
    rag_maintenance._ensure_state(db_session, utcnow())
    db_session.commit()
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=3
    )
    db_session.commit()
    before = projection_snapshot(db_session)
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    original_ensure = memory_rag.ensure_memory_index
    original_renew = rag_maintenance._renew_lease

    def ensure(db, memory, **kwargs):
        if memory.id == memory_ids[1]:
            if rejection == "item_lease":
                raise rag_maintenance.MaintenanceLeaseLost("synthetic lease rejection")
            raise RuntimeError("synthetic ordinary bad row")
        return original_ensure(db, memory, **kwargs)

    def final_fence(db, lease, *, expected_attempt, now):
        if rejection == "final_lease":
            raise rag_maintenance.MaintenanceLeaseLost("synthetic final rejection")
        renewed = original_renew(db, lease, expected_attempt=expected_attempt, now=now)
        if rejection == "final_flag":
            features(db, rag=False)
        elif rejection == "final_source":
            memory_rag.revoke_memory(db, memory_id=memory_ids[0], account_id=user.account.id)
        return renewed

    monkeypatch.setattr(memory_rag, "ensure_memory_index", ensure)
    monkeypatch.setattr(rag_maintenance, "_renew_lease", final_fence)
    result = maintenance.run_maintenance_tick()
    assert result["steward_executed"] >= 1
    assert all(
        result[key] == 0
        for key in ("rag_index_scanned", "rag_index_materialized", "rag_index_failed")
    )
    assert projection_snapshot(db_session) == before
    assert db_session.get(StewardJob, job.id, populate_existing=True).status == "succeeded"
    assert all(
        db_session.get(Memory, key, populate_existing=True).status == "active" for key in memory_ids
    )


def test_fixed_memory_watermark_revisits_low_ids_with_continuous_arrivals(db_session, world):
    user, _ = world
    features(db_session, rag=False)
    low = confirm(db_session, user, "orchidgrove low id")
    high = confirm(db_session, user, "orchidgrove high id")
    low.source_verification = "unverified"
    features(db_session, rag=True)
    db_session.commit()
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id="scanner", batch_size=1)
    db_session.commit()
    state = db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True)
    assert first["skipped_invalid"] == 1 and state.upper_memory_id == high.id
    low.source_verification = "verified"
    for index in range(3):
        features(db_session, rag=False)
        confirm(db_session, user, f"orchidgrove arrival {index}")
        features(db_session, rag=True)
        db_session.commit()
        rag_maintenance.run_maintenance_batch(db_session, worker_id="scanner", batch_size=1)
        db_session.commit()
    assert db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True).round >= 1
    assert document_for(db_session, low.id) is not None


def test_rag_only_loop_observes_platform_late_enable(db_session, world, monkeypatch):
    user, _ = world
    features(db_session, rag=False)
    memory = confirm(db_session, user)
    db_session.commit()
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    monkeypatch.setattr(config, "MAINTENANCE_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr(maintenance, "_task", None)
    monkeypatch.setattr(maintenance, "_holders", 0)
    original = maintenance.run_maintenance_tick

    async def exercise():
        loop = asyncio.get_running_loop()
        observed = asyncio.Queue()

        def tick():
            result = original()
            loop.call_soon_threadsafe(observed.put_nowait, result)
            return result

        monkeypatch.setattr(maintenance, "run_maintenance_tick", tick)
        assert maintenance.start_maintenance_loop() is not None
        try:
            first = await asyncio.wait_for(observed.get(), timeout=5)
            with SessionLocal() as writer:
                features(writer, rag=True)
                writer.commit()
            second = await asyncio.wait_for(observed.get(), timeout=5)
            assert first["rag_index_materialized"] == 0
            assert second["rag_index_materialized"] == 1
        finally:
            await maintenance.stop_maintenance_loop()

    asyncio.run(exercise())
    assert document_for(db_session, memory.id) is not None


def test_stage_is_searchable_retains_saved_dependency_and_next_batch_does_not_downgrade(
    db_session,
    world,
    synthetic_v3,
):
    user, space = world
    memory = confirm(db_session, user, "orchidgrove " + "A" * 1500)
    document = document_for(db_session, memory.id)
    old = chunks_for(db_session, document)[0]
    old_identity = (old.id, old.text, old.index_version, old.source_revision)
    saved = confirm(
        db_session,
        user,
        "saved dependency summary",
        quote=old.text,
        source={
            "kind": "rag_chunk",
            "document_id": document.id,
            "chunk_id": old.id,
            "revision": document.revision,
            "index_version": old.index_version,
            "space_id": space.id,
        },
    )
    db_session.commit()
    result = rag_maintenance.stage_index_version(
        db_session, target_version=V3, worker_id="switcher"
    )
    db_session.commit()
    assert result["flipped"] == 2
    hits = search(db_session, user, space)
    assert hits and all(hit.index_version == V3 for hit in hits)
    assert len(chunks_for(db_session, document_for(db_session, memory.id))) > 2
    old = db_session.get(RAGChunk, old_identity[0], populate_existing=True)
    assert (old.id, old.text, old.index_version, old.source_revision) == old_identity
    assert memory_sources.memory_access(
        db_session, saved, actor=user, account=user.account
    ).readable
    assert memory_rag.ensure_memory_index(db_session, memory).index_version == V3
    rag_maintenance.run_maintenance_batch(db_session, worker_id="switcher")
    db_session.commit()
    assert document_for(db_session, memory.id).index_version == V3
    assert memory_sources.memory_access(
        db_session, saved, actor=user, account=user.account
    ).readable


def test_real_v1_to_current_v2_target_and_explicit_rollback(db_session, world, monkeypatch):
    user, space = world
    body = "orchidgrove " + "A" * 1800
    monkeypatch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v1")
    memory = confirm(db_session, user, body)
    document = document_for(db_session, memory.id)
    old_ids = [row.id for row in chunks_for(db_session, document)]
    assert len(old_ids) == 2
    monkeypatch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v2")
    result = rag_maintenance.stage_index_version(
        db_session, target_version=memory_rag.RAG_INDEX_VERSION, worker_id="switcher"
    )
    assert result["flipped"] == 1
    assert [row.text for row in chunks_for(db_session, document)] == memory_rag._chunk_text(body)
    assert search(db_session, user, space)[0].index_version == "fts5-trigram-v2"
    assert memory_rag.ensure_memory_index(db_session, memory).index_version == "fts5-trigram-v2"
    result = rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v1", worker_id="switcher"
    )
    assert result["flipped"] == 1
    assert [row.id for row in chunks_for(db_session, document)] == old_ids
    assert search(db_session, user, space)[0].index_version == "fts5-trigram-v1"


@pytest.mark.parametrize("damage", ["text", "revision", "status", "extra"])
def test_stage_validates_all_existing_target_chunks_before_switch(
    db_session,
    world,
    synthetic_v3,
    damage,
):
    user, _ = world
    memory = confirm(db_session, user, "orchidgrove " + "A" * 1500)
    document = document_for(db_session, memory.id)
    target_text = memory_rag.INDEX_CHUNKERS[V3](memory.content)[0]
    db_session.add(
        RAGChunk(
            document_id=document.id,
            chunk_index=99 if damage == "extra" else 0,
            source_revision=2 if damage == "revision" else 1,
            text="conflicting content" if damage == "text" else target_text,
            token_estimate=4,
            index_version=V3,
            status="invalidated" if damage == "status" else "active",
            created_at=utcnow(),
        )
    )
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        rag_maintenance.stage_index_version(db_session, target_version=V3, worker_id="switcher")
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before
    assert document_for(db_session, memory.id).index_version == memory_rag.RAG_INDEX_VERSION


def test_stage_resumes_valid_partial_target_and_preserves_its_ids(db_session, world, synthetic_v3):
    user, _ = world
    memory = confirm(db_session, user, "orchidgrove " + "A" * 1500)
    document = document_for(db_session, memory.id)
    expected = memory_rag.INDEX_CHUNKERS[V3](memory.content)
    chunk = RAGChunk(
        document_id=document.id,
        chunk_index=0,
        source_revision=1,
        text=expected[0],
        token_estimate=4,
        index_version=V3,
        status="active",
        created_at=utcnow(),
    )
    db_session.add(chunk)
    db_session.commit()
    old_id = chunk.id
    assert (
        rag_maintenance.stage_index_version(db_session, target_version=V3, worker_id="switcher")[
            "flipped"
        ]
        == 1
    )
    actual = chunks_for(db_session, document)
    assert [row.text for row in actual] == expected and actual[0].id == old_id


def test_stage_keeps_non_memory_old_active_projection_searchable(db_session, world, monkeypatch):
    user, space = world
    monkeypatch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v1")
    document = memory_rag.ingest_authorized_document(
        db_session,
        source_type="authorized_document",
        source_id="retained-raw-input-unavailable",
        author_account_id=user.account.id,
        scope="private",
        space_id=None,
        text_value="orchidgrove " + "A" * 1500,
    )
    before = [(row.id, row.text, row.index_version) for row in chunks_for(db_session, document)]
    monkeypatch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v2")
    result = rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v2", worker_id="switcher"
    )
    assert result["flipped"] == 0 and result["skipped"] == 1
    assert [
        (row.id, row.text, row.index_version) for row in chunks_for(db_session, document)
    ] == before
    assert search(db_session, user, space)[0].index_version == "fts5-trigram-v1"


@pytest.mark.parametrize("gate", ["off", "other_owner", "unknown_algorithm"])
def test_stage_requires_effective_flag_owner_and_known_target(
    db_session, world, synthetic_v3, gate
):
    user, _ = world
    confirm(db_session, user)
    state = rag_maintenance._ensure_state(db_session, utcnow())
    rag_maintenance._acquire_lease(db_session, state, "legitimate", utcnow())
    if gate == "off":
        features(db_session, rag=False)
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises((HTTPException, rag_maintenance.MaintenanceLeaseLost)):
        rag_maintenance.stage_index_version(
            db_session,
            target_version="not-an-algorithm" if gate == "unknown_algorithm" else V3,
            worker_id="intruder",
        )
    db_session.commit()
    assert projection_snapshot(db_session) == before


def test_stage_has_bounded_fixed_document_watermark(db_session, world, synthetic_v3):
    user, _ = world
    originals = [confirm(db_session, user, f"orchidgrove original {i}") for i in range(3)]
    db_session.commit()
    first = rag_maintenance.stage_index_version(
        db_session, target_version=V3, worker_id="switcher", batch_size=1
    )
    assert first["scanned"] == first["flipped"] == 1
    db_session.commit()
    state = db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True)
    upper, stage_round = state.upper_document_id, state.stage_round
    assert upper == 3
    for index in range(2):
        confirm(db_session, user, f"orchidgrove stage arrival {index}")
        db_session.commit()
        result = rag_maintenance.stage_index_version(
            db_session, target_version=V3, worker_id="switcher", batch_size=1
        )
        assert result["scanned"] == 1
        db_session.commit()
    state = db_session.get(RAGIndexMaintenanceState, 1, populate_existing=True)
    assert state.stage_round == stage_round + 1 and state.upper_document_id is None
    assert all(document_for(db_session, row.id).index_version == V3 for row in originals)


def test_fts_repair_filters_verification_retention_and_root_lifecycle_without_reconfirmation(
    db_session,
    world,
):
    user, space = world
    valid = confirm(db_session, user, "orchidgrove valid")
    unverified = confirm(db_session, user, "orchidgrove unverified")
    expired = confirm(db_session, user, "orchidgrove expired")
    root = memory_rag.ingest_authorized_document(
        db_session,
        source_type="authorized_document",
        source_id="root-authorization",
        author_account_id=user.account.id,
        scope="private",
        space_id=None,
        text_value="orchidgrove root",
    )
    root_chunk = chunks_for(db_session, root)[0]
    saved = confirm(
        db_session,
        user,
        "orchidgrove dependent",
        quote=root_chunk.text,
        source={
            "kind": "rag_chunk",
            "document_id": root.id,
            "chunk_id": root_chunk.id,
            "revision": root.revision,
            "index_version": root.index_version,
            "space_id": space.id,
        },
    )
    unverified.source_verification = "unverified"
    expired.retention_until = utcnow() - timedelta(days=1)
    memory_rag.invalidate_source(db_session, source_type=root.source_type, source_id=root.source_id)
    db_session.commit()
    before_sources = db_session.execute(text("SELECT * FROM memories ORDER BY id")).all()
    before_docs = db_session.execute(text("SELECT * FROM rag_documents ORDER BY id")).all()
    rebuilt = memory_rag.repair_fts(db_session)
    assert rebuilt == len(chunks_for(db_session, document_for(db_session, valid.id)))
    ids = set(db_session.scalars(text("SELECT chunk_id FROM rag_chunks_fts")))
    assert ids == {row.id for row in chunks_for(db_session, document_for(db_session, valid.id))}
    assert db_session.execute(text("SELECT * FROM memories ORDER BY id")).all() == before_sources
    assert db_session.execute(text("SELECT * FROM rag_documents ORDER BY id")).all() == before_docs
    assert saved.raw_quote == root_chunk.text


@pytest.mark.parametrize("entry", ["ensure", "batch"])
@pytest.mark.parametrize("evidence", ["trusted_complete", "legacy_complete", "trusted_partial"])
def test_index_superseded_restores_only_proven_memory_projection(
    db_session,
    world,
    monkeypatch,
    entry,
    evidence,
):
    user, space = world
    with monkeypatch.context() as patch:
        patch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v1")
        memory = confirm(
            db_session,
            user,
            "orchidgrove " + "A" * 1900,
            quote="independent confirmed source quote",
        )
    document = document_for(db_session, memory.id)
    original = chunks_for(db_session, document)
    retained = [(row.id, row.text, row.index_version, row.chunk_index) for row in original]
    reference = memory_sources.ExactChunkRef(
        document_id=document.id,
        chunk_id=original[0].id,
        source_type="memory",
        source_id=str(memory.id),
        source_revision=memory.revision,
        index_version=document.index_version,
        chunk_index=original[0].chunk_index,
        content_hash=memory_sources.quote_hash(original[0].text),
    )
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = document.updated_at = utcnow() - timedelta(days=1)
    if evidence == "legacy_complete":
        document.content_sha256 = None
    elif evidence == "trusted_partial":
        db_session.delete(original[-1])
        retained.pop()
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    sources = db_session.execute(text("SELECT * FROM memories ORDER BY id")).all()
    candidates = db_session.execute(text("SELECT * FROM memory_candidates ORDER BY id")).all()
    assert memory_sources.memory_materializable(db_session, memory)
    if entry == "ensure":
        assert memory_rag.ensure_memory_index(db_session, memory).id == document.id
    else:
        result = rag_maintenance.run_maintenance_batch(db_session, worker_id="restore-superseded")
        assert result["materialized"] == 1 and result["failed"] == 0
    db_session.commit()
    document = document_for(db_session, memory.id)
    assert (document.status, document.invalidation_reason, document.invalidated_at) == (
        "active",
        None,
        None,
    )
    assert document.index_version == "fts5-trigram-v1"
    assert document.content_sha256 == memory_rag.query_hash(memory.content)
    actual = chunks_for(db_session, document)
    assert [row.text for row in actual] == memory_rag.INDEX_CHUNKERS["fts5-trigram-v1"](
        memory.content
    )
    assert all(
        identity in [(row.id, row.text, row.index_version, row.chunk_index) for row in actual]
        for identity in retained
    )
    assert (
        memory_sources.read_exact_chunk(
            db_session, reference, actor=user, account=user.account, space_id=space.id
        )
        is not None
    )
    assert search(db_session, user, space)[0].index_version == "fts5-trigram-v1"
    assert db_session.execute(text("SELECT * FROM memories ORDER BY id")).all() == sources
    assert (
        db_session.execute(text("SELECT * FROM memory_candidates ORDER BY id")).all() == candidates
    )
    stable = projection_snapshot(db_session)
    memory_rag.ensure_memory_index(db_session, memory)
    db_session.commit()
    assert projection_snapshot(db_session) == stable


@pytest.mark.parametrize(
    "status,reason",
    [
        ("invalidated", None),
        ("invalidated", "unknown"),
        ("invalidated", "source_invalidated"),
        ("deleted", "index_superseded"),
        ("revoked", "index_superseded"),
        ("active", "index_superseded"),
    ],
)
def test_index_superseded_exception_does_not_reopen_other_document_states(
    db_session,
    world,
    status,
    reason,
):
    user, _ = world
    memory = confirm(db_session, user)
    document = document_for(db_session, memory.id)
    document.status, document.invalidation_reason = status, reason
    document.invalidated_at = utcnow()
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, memory)
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before


@pytest.mark.parametrize(
    "damage",
    [
        "revoked",
        "deleted",
        "expired",
        "unverified",
        "changed_content",
        "legacy_partial",
        "chunk_text",
        "chunk_status",
        "metadata",
    ],
)
def test_index_superseded_with_invalid_source_or_unproven_content_stays_closed(
    db_session,
    world,
    damage,
):
    user, _ = world
    memory = confirm(db_session, user, "A" * 800 + "B" * 600)
    document = document_for(db_session, memory.id)
    rows = chunks_for(db_session, document)
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = utcnow()
    if damage in ("revoked", "deleted"):
        memory.status = damage
    elif damage == "expired":
        memory.retention_until = utcnow() - timedelta(seconds=1)
    elif damage == "unverified":
        memory.source_verification = "unverified"
    elif damage == "changed_content":
        db_session.delete(rows[-1])
        memory.content = "A" * 800 + "C" * 600
    elif damage == "legacy_partial":
        document.content_sha256 = None
        db_session.delete(rows[-1])
    elif damage == "chunk_text":
        rows[0].text = "conflicting text"
    elif damage == "chunk_status":
        rows[0].status = "invalidated"
    else:
        document.sensitivity = "sensitive"
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, memory)
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before


def test_index_superseded_recovery_and_fts_rollback_after_final_gate_rejection(
    db_session,
    world,
    monkeypatch,
):
    user, _ = world
    memory = confirm(db_session, user, "orchidgrove " + "A" * 1500)
    document = document_for(db_session, memory.id)
    db_session.delete(chunks_for(db_session, document)[-1])
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = utcnow()
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    before = projection_snapshot(db_session)
    original = memory_rag._require_fresh_rag_enabled
    calls = 0

    def close_at_final_gate(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert (
                db.scalar(select(RAGDocument.status).where(RAGDocument.id == document.id))
                == "active"
            )
            features(db, rag=False)
        original(db)

    monkeypatch.setattr(memory_rag, "_require_fresh_rag_enabled", close_at_final_gate)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, memory)
    assert calls == 2 and error.value.status_code == 503
    db_session.commit()
    assert projection_snapshot(db_session) == before
    assert db_session.get(PlatformFeatureConfig, 1, populate_existing=True).rag_enabled


def test_index_superseded_rechecks_source_revoked_before_writer(
    db_session,
    world,
    monkeypatch,
):
    user, _ = world
    memory = confirm(db_session, user)
    document = document_for(db_session, memory.id)
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = utcnow()
    db_session.commit()
    before = projection_snapshot(db_session)
    original = memory_rag._acquire_index_writer

    def revoke_before_writer(db):
        with SessionLocal() as other:
            memory_rag.revoke_memory(other, memory_id=memory.id, account_id=user.account.id)
            other.commit()
        original(db)

    monkeypatch.setattr(memory_rag, "_acquire_index_writer", revoke_before_writer)
    with pytest.raises(HTTPException):
        memory_rag.ensure_memory_index(db_session, memory)
    db_session.commit()
    assert projection_snapshot(db_session) == before
    assert db_session.get(Memory, memory.id, populate_existing=True).status == "revoked"


def test_authorized_ingest_does_not_inherit_memory_index_superseded_recovery(db_session, world):
    user, _ = world
    kwargs = dict(
        source_type="authorized_document",
        source_id="closed-authorized-source",
        text_value="orchidgrove authorized source",
        author_account_id=user.account.id,
        scope="private",
        space_id=None,
    )
    document = memory_rag.ingest_authorized_document(db_session, **kwargs)
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = utcnow()
    db_session.commit()
    before = projection_snapshot(db_session)
    with pytest.raises(HTTPException) as error:
        memory_rag.ingest_authorized_document(db_session, **kwargs)
    assert error.value.status_code == 409
    db_session.commit()
    assert projection_snapshot(db_session) == before
