"""Additional independent checks for the narrow index_superseded repair."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from test_d_independent import chunks, projection, save_memory, snapshot, world
from app.db import SessionLocal
from app.services import memory_rag, memory_sources, rag_maintenance


def dependent(db, user, space, document, chunk):
    return save_memory(
        db, user, "orchidgrove retained dependent", quote=chunk.text,
        source={"kind": "rag_chunk", "document_id": document.id, "chunk_id": chunk.id,
                "revision": document.revision, "index_version": chunk.index_version,
                "space_id": space.id},
    )


def test_stage_still_skips_known_superseded_without_touching_sources(db_session, world):
    user, _ = world
    memory = save_memory(db_session, user, "orchidgrove explicit stage boundary")
    document = projection(db_session, memory)
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    db_session.commit()
    before = snapshot(db_session)
    result = rag_maintenance.stage_index_version(
        db_session, target_version="fts5-trigram-v1", worker_id="review"
    )
    assert result == {"scanned": 1, "staged": 0, "flipped": 0, "skipped": 1}
    db_session.commit()
    after = snapshot(db_session)
    for key in ("rag_documents", "rag_chunks", "memories", "memory_candidates", "fts"):
        assert after[key] == before[key]


def test_batch_recovery_restores_existing_saved_dependency_without_reconfirmation(db_session, world):
    user, space = world
    root = save_memory(db_session, user, "orchidgrove intact root with dependent")
    document = projection(db_session, root)
    chunk = chunks(db_session, document)[0]
    saved = dependent(db_session, user, space, document, chunk)
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    db_session.execute(text("DELETE FROM rag_chunks_fts"))
    db_session.commit()
    before_sources = snapshot(db_session)
    assert not memory_sources.memory_access(
        db_session, saved, actor=user, account=user.account
    ).readable
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id="review")
    assert result["failed"] == result["skipped_invalid"] == 0
    assert result["materialized"] == 2
    db_session.commit()
    after = snapshot(db_session)
    assert after["memories"] == before_sources["memories"]
    assert after["memory_candidates"] == before_sources["memory_candidates"]
    assert memory_sources.memory_access(
        db_session, saved, actor=user, account=user.account
    ).readable
    assert chunks(db_session, projection(db_session, root))[0].id == chunk.id


def test_known_superseded_cannot_restore_when_transitive_root_is_revoked(db_session, world):
    user, space = world
    root = save_memory(db_session, user, "orchidgrove root to revoke")
    root_document = projection(db_session, root)
    old = chunks(db_session, root_document)[0]
    saved = dependent(db_session, user, space, root_document, old)
    saved_document = projection(db_session, saved)
    saved_document.status = "invalidated"
    saved_document.invalidation_reason = "index_superseded"
    memory_rag.revoke_memory(db_session, memory_id=root.id, account_id=user.account.id)
    db_session.commit()
    before = snapshot(db_session)
    assert saved.status == "active" and saved.source_verification == "verified"
    assert not memory_sources.memory_materializable(db_session, saved)
    with pytest.raises(HTTPException) as error:
        memory_rag.ensure_memory_index(db_session, saved)
    assert error.value.status_code == 409
    db_session.commit()
    with SessionLocal() as observer:
        assert snapshot(observer) == before
