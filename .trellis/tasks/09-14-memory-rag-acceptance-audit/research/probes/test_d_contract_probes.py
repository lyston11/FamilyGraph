"""Read-only D review probes; every database is synthetic and Alembic-built."""

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, local

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text

import app
from app import config
from app.db import SessionLocal, engine
from app.models.memory import Memory
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument, RAGIndexMaintenanceState
from app.services import maintenance, memory_rag, memory_sources, rag_maintenance
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


BACKEND = Path(os.environ['FG_AUDIT_BACKEND']).resolve()


def observe(label, **data):
    print('OBSERVATION ' + json.dumps({'case': label, **data}, ensure_ascii=False, default=str))


def features(db, *, rag):
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, memory_enabled=True, rag_enabled=rag, updated_at=utcnow())
        db.add(row)
    else:
        row.memory_enabled = True
        row.rag_enabled = rag
    db.flush()
    return row


def confirm(db, user, summary, *, source=None, quote=None):
    candidate = memory_rag.propose_candidate(
        db, author_account_id=user.account.id,
        source=source or {'kind': 'manual'},
        source_quote=quote if quote is not None else summary,
        summary=summary, suggested_scope='private', purpose='synthetic D review',
    )
    return memory_rag.confirm_candidate(
        db, candidate_id=candidate.id, confirmer=user,
        confirmer_account=user.account, scope='private',
    )


def root(db, *, enabled=True, summary='orchidgrove synthetic original'):
    assert Path(app.__file__).resolve().parent == BACKEND / 'app'
    user, space = create_agent_fixture(db, name='d-review-owner')
    features(db, rag=enabled)
    memory = confirm(db, user, summary)
    db.commit()
    return user, space, memory


def doc_for(db, memory_id):
    return db.scalar(select(RAGDocument).where(
        RAGDocument.source_type == 'memory', RAGDocument.source_id == str(memory_id)
    ))


def chunks_for(db, document_id, version=None):
    stmt = select(RAGChunk).where(RAGChunk.document_id == document_id)
    if version is not None:
        stmt = stmt.where(RAGChunk.index_version == version)
    return db.scalars(stmt.order_by(RAGChunk.chunk_index)).all()


def test_two_real_sessions_cannot_create_duplicate_documents(db_session):
    _user, _space, memory = root(db_session, enabled=False)
    features(db_session, rag=True)
    db_session.commit()
    barrier = Barrier(2)
    worker = local()

    def after_select(_connection, _cursor, statement, _parameters, _context, _many):
        if (getattr(worker, 'enabled', False)
            and statement.startswith('SELECT rag_documents.')
            and 'rag_documents.source_id = ' in statement):
            worker.enabled = False
            barrier.wait(timeout=5)

    def index_one():
        worker.enabled = True
        with SessionLocal() as db:
            own_memory = db.get(Memory, memory.id)
            document = memory_rag.index_memory(db, own_memory)
            db.commit()
            return document.id

    event.listen(engine, 'after_cursor_execute', after_select)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(index_one), pool.submit(index_one)
            ids = [a.result(timeout=10), b.result(timeout=10)]
    finally:
        event.remove(engine, 'after_cursor_execute', after_select)
    documents = db_session.scalars(select(RAGDocument).where(
        RAGDocument.source_id == str(memory.id)
    )).all()
    observe('concurrent_document_identity', returned_ids=ids, stored_count=len(documents))
    assert len(documents) == 1
    assert ids[0] == ids[1]


def test_same_revision_conflicting_text_preserves_old_chunk(db_session):
    _user, _space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    chunk = chunks_for(db_session, document.id)[0]
    before = (chunk.id, chunk.text, chunk.source_revision, chunk.index_version)
    memory.content = 'orchidgrove conflicting replacement without a new revision'
    error = None
    try:
        memory_rag.index_memory(db_session, memory)
    except HTTPException as exc:
        error = exc.status_code
    db_session.flush()
    after = (chunk.id, chunk.text, chunk.source_revision, chunk.index_version)
    observe('same_revision_content_conflict', before=before, after=after, error=error)
    assert after == before
    assert error == 409


def test_stale_session_cannot_overwrite_new_lease_cursor(db_session):
    user, _space, _memory = root(db_session, enabled=False)
    for index in range(3):
        confirm(db_session, user, f'synthetic unindexed {index}')
    features(db_session, rag=True)
    rag_maintenance._ensure_state(db_session, utcnow())
    db_session.commit()
    with SessionLocal() as old, SessionLocal() as current:
        stale = old.get(RAGIndexMaintenanceState, 1)
        assert stale.cursor_memory_id == 0
        first = rag_maintenance.run_maintenance_batch(current, worker_id='current', batch_size=3)
        current.commit()
        baseline = current.get(RAGIndexMaintenanceState, 1)
        before = (baseline.cursor_memory_id, baseline.attempt, baseline.lease_owner)
        rejected = False
        try:
            rag_maintenance.run_maintenance_batch(old, worker_id='stale', batch_size=1)
            old.commit()
        except rag_maintenance.MaintenanceLeaseLost:
            old.rollback()
            rejected = True
        current.expire_all()
        latest = current.get(RAGIndexMaintenanceState, 1)
        after = (latest.cursor_memory_id, latest.attempt, latest.lease_owner)
        observe('stale_real_session', first=first, before=before, after=after, rejected=rejected)
        assert rejected
        assert after == before


def test_real_tick_rolls_back_batch_when_final_fence_rejects(db_session, monkeypatch):
    _user, _space, memory = root(db_session, enabled=False)
    features(db_session, rag=True)
    db_session.commit()
    monkeypatch.setattr(config, 'AGENT_RUNTIME_ENABLED', False)
    monkeypatch.setattr(config, 'STEWARD_ENABLED', False)

    def reject(*_args, **_kwargs):
        raise rag_maintenance.MaintenanceLeaseLost('synthetic final fence rejection')

    monkeypatch.setattr(rag_maintenance, '_renew_lease', reject)
    counts = maintenance.run_maintenance_tick()
    with SessionLocal() as reader:
        persisted = doc_for(reader, memory.id)
        state = reader.get(RAGIndexMaintenanceState, 1)
        observe('tick_final_fence_atomicity', counters=counts,
                document_id=persisted.id if persisted else None,
                cursor=state.cursor_memory_id if state else None)
        assert persisted is None


def test_stage_respects_effective_off_and_another_lease(db_session):
    _user, _space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    old_version = document.index_version
    state = rag_maintenance._ensure_state(db_session, utcnow())
    rag_maintenance._acquire_lease(db_session, state, 'legitimate', utcnow())
    features(db_session, rag=False)
    db_session.commit()
    rejected = False
    result = None
    try:
        result = rag_maintenance.stage_index_version(
            db_session, target_version='fts5-trigram-v3-review', worker_id='intruder'
        )
        db_session.commit()
    except (HTTPException, rag_maintenance.MaintenanceLeaseLost):
        db_session.rollback()
        rejected = True
    db_session.refresh(document)
    observe('stage_disabled_foreign_lease', result=result, rejected=rejected,
            version=document.index_version, owner=state.lease_owner)
    assert document.index_version == old_version
    assert rejected


def test_stage_switch_is_searchable_and_not_undone_by_next_batch(db_session):
    user, space, memory = root(db_session)
    def search():
        return memory_rag.search_rag(db_session, actor=user, account=user.account,
                                    space_id=space.id, query='orchidgrove')
    before_hits = len(search())
    target = 'fts5-trigram-v3-review'
    result = rag_maintenance.stage_index_version(db_session, target_version=target, worker_id='v3')
    db_session.commit()
    document = doc_for(db_session, memory.id)
    after_hits = len(search())
    actual_versions = {c.index_version for c in chunks_for(db_session, document.id)}
    result2 = rag_maintenance.run_maintenance_batch(db_session, worker_id='v2')
    db_session.commit()
    db_session.refresh(document)
    observe('version_switch_lifecycle', before_hits=before_hits, after_hits=after_hits,
            stage=result, chunk_versions=sorted(actual_versions), next_batch=result2,
            after_next_batch_version=document.index_version)
    assert after_hits == before_hits == 1
    assert document.index_version == target


def test_current_algorithm_version_can_be_upgrade_target(db_session, monkeypatch):
    _user, _space, memory = root(db_session)
    monkeypatch.setattr(memory_rag, 'RAG_INDEX_VERSION', 'fts5-trigram-v3-review')
    error = None
    try:
        rag_maintenance.stage_index_version(db_session, target_version=memory_rag.RAG_INDEX_VERSION,
                                            worker_id='new-algorithm')
    except HTTPException as exc:
        error = exc.status_code
    observe('upgrade_after_algorithm_bump', error=error,
            document_version=doc_for(db_session, memory.id).index_version)
    assert error is None


def test_stage_rejects_conflicting_preexisting_target_chunks(db_session):
    _user, _space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    old_version = document.index_version
    target = 'fts5-trigram-v3-review'
    db_session.add(RAGChunk(document_id=document.id, chunk_index=0,
        source_revision=document.revision, text='conflicting staged synthetic text',
        token_estimate=12, index_version=target, status='active', created_at=utcnow()))
    db_session.flush()
    error = None
    try:
        rag_maintenance.stage_index_version(db_session, target_version=target, worker_id='stage')
    except HTTPException as exc:
        error = exc.status_code
    db_session.flush()
    observe('preexisting_target_conflict', error=error, active=document.index_version,
            target_text=[c.text for c in chunks_for(db_session, document.id, target)])
    assert document.index_version == old_version
    assert error == 409


def test_maintenance_restores_missing_fts_after_source_verification(db_session):
    user, space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    old_chunk = chunks_for(db_session, document.id)[0]
    memory.source_verification = 'unverified'
    db_session.execute(text('DELETE FROM rag_chunks_fts WHERE rowid = :id'), {'id': old_chunk.id})
    db_session.commit()
    first = rag_maintenance.run_maintenance_batch(db_session, worker_id='repair')
    db_session.commit()
    memory.source_verification = 'verified'
    db_session.commit()
    second = rag_maintenance.run_maintenance_batch(db_session, worker_id='repair')
    db_session.commit()
    hits = memory_rag.search_rag(db_session, actor=user, account=user.account,
                                space_id=space.id, query='orchidgrove')
    observe('verification_fts_restore', first=first, second=second, hit_count=len(hits))
    assert len(hits) == 1


def test_maintenance_restores_incomplete_active_chunk_set(db_session):
    _user, _space, memory = root(db_session, summary='长文本 synthetic orchidgrove。' * 75)
    document = doc_for(db_session, memory.id)
    chunks = chunks_for(db_session, document.id)
    expected = len(chunks)
    assert expected > 1
    db_session.delete(chunks[-1])
    db_session.commit()
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id='repair')
    db_session.commit()
    actual = len(chunks_for(db_session, document.id))
    observe('incomplete_chunk_set', expected=expected, actual=actual, batch=result)
    assert actual == expected


def test_rag_only_actual_loop_can_backfill_after_platform_enable(db_session, monkeypatch):
    _user, _space, memory = root(db_session, enabled=False)
    monkeypatch.setattr(config, 'AGENT_RUNTIME_ENABLED', False)
    monkeypatch.setattr(config, 'STEWARD_ENABLED', False)
    monkeypatch.setattr(config, 'RAG_ENABLED', True)
    monkeypatch.setattr(config, 'MAINTENANCE_INTERVAL_SECONDS', 0.5)
    monkeypatch.setattr(maintenance, '_task', None)
    monkeypatch.setattr(maintenance, '_holders', 0)
    actual_tick = maintenance.run_maintenance_tick

    async def exercise():
        loop = asyncio.get_running_loop()
        observed = asyncio.Queue()
        def tick():
            result = actual_tick()
            loop.call_soon_threadsafe(observed.put_nowait, result)
            return result
        monkeypatch.setattr(maintenance, 'run_maintenance_tick', tick)
        task = maintenance.start_maintenance_loop()
        assert task is not None
        try:
            first = await asyncio.wait_for(observed.get(), timeout=5)
            with SessionLocal() as writer:
                features(writer, rag=True)
                writer.commit()
            second = await asyncio.wait_for(observed.get(), timeout=5)
            observe('rag_only_live_loop', disabled_tick=first, enabled_tick=second)
            assert first['rag_index_materialized'] == 0
            assert second['rag_index_materialized'] == 1
        finally:
            await maintenance.stop_maintenance_loop()

    asyncio.run(exercise())
    assert doc_for(db_session, memory.id) is not None


def test_unknown_tombstone_stays_invalid(db_session):
    _user, _space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    document.status = 'invalidated'
    document.invalidation_reason = None
    db_session.commit()
    assert memory_sources.memory_materializable(db_session, memory)
    for ensure in (memory_rag.index_memory, memory_rag.ensure_memory_index):
        with pytest.raises(HTTPException):
            ensure(db_session, memory)
    result = rag_maintenance.run_maintenance_batch(db_session, worker_id='tombstone')
    db_session.commit()
    db_session.refresh(document)
    observe('unknown_tombstone', status=document.status, reason=document.invalidation_reason,
            batch=result)
    assert document.status == 'invalidated'


def alembic_run(*args, data_dir=None):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(BACKEND)
    if data_dir is not None:
        env['DATA_DIR'] = str(data_dir)
    assert 'familygraph-tests-' in env['DATA_DIR'] or 'd-review-migration' in env['DATA_DIR']
    return subprocess.run([str(BACKEND / '.venv/bin/alembic'), *args], cwd=BACKEND,
                          env=env, text=True, capture_output=True, timeout=30)


def test_migration_blocks_duplicate_and_revision_conflict(tmp_path):
    data = tmp_path / 'd-review-migration'
    for previous in ('0044_rag_citation_contract', '0044_steward_terminology'):
        result = alembic_run('upgrade', previous, data_dir=data)
        assert result.returncode == 0, result.stderr
    database = data / 'db/app.db'
    with sqlite3.connect(database) as db:
        insert = '''INSERT INTO rag_documents
            (source_type,source_id,scope,sensitivity,confirmation_status,revision,
             source_revision,index_version,status,created_at,updated_at)
            VALUES ('authorized_document',?,'public','normal','authorized',?,?,
                    'fts5-trigram-v2','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'''
        db.execute(insert, ('duplicate-source', 1, 1))
        db.execute(insert, ('duplicate-source', 1, 1))
        db.execute(insert, ('mirror-conflict', 2, 9))
        db.commit()
    result = alembic_run('upgrade', 'head', data_dir=data)
    with sqlite3.connect(database) as db:
        rows = db.execute('SELECT source_id,revision,source_revision FROM rag_documents ORDER BY id').fetchall()
        indexes = db.execute("PRAGMA index_list('rag_documents')").fetchall()
    observe('migration_conflict_preflight', exit_code=result.returncode, rows=rows, indexes=indexes)
    assert result.returncode != 0
    assert len(rows) == 3


def test_downgrade_preserves_saved_rag_dependency(db_session):
    user, space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    chunk = chunks_for(db_session, document.id)[0]
    saved = confirm(db_session, user, 'saved synthetic dependency', source={
        'kind': 'rag_chunk', 'document_id': document.id, 'chunk_id': chunk.id,
        'revision': document.revision, 'index_version': chunk.index_version,
        'space_id': space.id,
    }, quote=chunk.text)
    saved_id, chunk_id, user_id, space_id = saved.id, chunk.id, user.id, space.id
    rag_maintenance.stage_index_version(db_session, target_version='fts5-trigram-v3-review',
                                        worker_id='switch')
    db_session.commit()
    access = memory_sources.memory_access(db_session, saved, actor=user,
                                          account=user.account, space_id=space.id)
    assert access.readable, 'old-version source is correctly readable before downgrade'
    db_session.close()
    try:
        result = alembic_run('downgrade', '0044_rag_citation_contract')
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(config.DB_PATH) as db:
            remaining = db.execute('SELECT id FROM rag_chunks WHERE id=?', (chunk_id,)).fetchone()
            persisted = db.execute('SELECT source_span_json FROM memories WHERE id=?', (saved_id,)).fetchone()
    finally:
        upgrade = alembic_run('upgrade', 'head')
        assert upgrade.returncode == 0, upgrade.stderr
    from app.models.user import User
    db_session.expire_all()
    user = db_session.get(User, user_id)
    saved = db_session.get(Memory, saved_id)
    after = memory_sources.memory_access(db_session, saved, actor=user,
                                         account=user.account, space_id=space_id)
    observe('downgrade_saved_dependency', old_chunk_id=chunk_id, old_chunk_still_present=remaining is not None,
            saved_reference_still_present=persisted is not None, after_access=after.status)
    assert remaining is not None
    assert after.readable


def test_full_round_does_not_starve_low_ids_under_new_arrivals(db_session):
    user, _space, memory = root(db_session, enabled=False)
    memory.source_verification = 'unverified'
    confirm(db_session, user, 'initial upper bound synthetic memory')
    features(db_session, rag=True)
    db_session.commit()
    rag_maintenance.run_maintenance_batch(db_session, worker_id='round', batch_size=1)
    db_session.commit()
    memory.source_verification = 'verified'
    db_session.commit()
    rounds = []
    for index in range(5):
        confirm(db_session, user, f'new arrival synthetic memory {index}')
        db_session.commit()
        result = rag_maintenance.run_maintenance_batch(db_session, worker_id='round', batch_size=1)
        db_session.commit()
        rounds.append(result['round'])
    materialized = doc_for(db_session, memory.id)
    observe('full_round_low_id_starvation', round_history=rounds,
            low_id_materialized=materialized is not None)
    assert materialized is not None


def test_batch_rechecks_effective_flag_at_final_checkpoint(db_session, monkeypatch):
    _user, _space, memory = root(db_session, enabled=False)
    features(db_session, rag=True)
    db_session.commit()
    actual_ensure = memory_rag.ensure_memory_index
    def disable_after_ensure(db, row):
        document = actual_ensure(db, row)
        features(db, rag=False)
        return document
    monkeypatch.setattr(memory_rag, 'ensure_memory_index', disable_after_ensure)
    rejected = False
    result = None
    try:
        result = rag_maintenance.run_maintenance_batch(db_session, worker_id='switch-check')
        db_session.commit()
    except HTTPException:
        db_session.rollback()
        rejected = True
    observe('final_effective_flag_checkpoint', result=result, rejected=rejected,
            rag_enabled=rag_maintenance.platform_features.is_rag_enabled(db_session),
            materialized=doc_for(db_session, memory.id) is not None)
    assert rejected or doc_for(db_session, memory.id) is None


def test_old_policy_cannot_write_future_policy_state(db_session):
    _user, _space, memory = root(db_session, enabled=False)
    features(db_session, rag=True)
    state = rag_maintenance._ensure_state(db_session, utcnow())
    state.policy_version = 'future-policy-v99'
    state.round = 99
    db_session.commit()
    rejected = False
    try:
        result = rag_maintenance.run_maintenance_batch(db_session, worker_id='old-policy')
        db_session.commit()
    except rag_maintenance.MaintenanceLeaseLost:
        db_session.rollback()
        rejected = True
        result = None
    db_session.refresh(state)
    observe('policy_fence', rejected=rejected, state_policy=state.policy_version,
            code_policy=rag_maintenance.MAINTENANCE_POLICY_VERSION, result=result,
            materialized=doc_for(db_session, memory.id) is not None)
    assert rejected


def test_fts_repair_uses_source_legality(db_session):
    user, space, memory = root(db_session)
    memory.source_verification = 'unverified'
    db_session.commit()
    rebuilt = memory_rag.repair_fts(db_session)
    db_session.commit()
    fts_count = db_session.scalar(text('SELECT count(*) FROM rag_chunks_fts'))
    hits = memory_rag.search_rag(db_session, actor=user, account=user.account,
                                space_id=space.id, query='orchidgrove')
    observe('fts_repair_unverified_source', rebuilt=rebuilt, fts_rows=fts_count,
            public_search_hits=len(hits), source_verification=memory.source_verification)
    assert fts_count == 0
    assert not hits


def test_reader_loss_unverified_and_root_invalidation_stay_distinct(db_session):
    from conftest import create_space_member
    owner, space = create_agent_fixture(db_session, name='shared-owner')
    reader, _ = create_agent_fixture(db_session, name='shared-reader')
    create_space_member(db_session, space.id, reader.id)
    features(db_session, rag=True)
    document = memory_rag.ingest_authorized_document(
        db_session, source_type='authorized_document', source_id='synthetic-shared-source',
        text_value='shared synthetic orchidgrove source', author_account_id=None,
        scope='household', space_id=space.id,
    )
    chunk = chunks_for(db_session, document.id)[0]
    saved = confirm(db_session, reader, 'saved synthetic shared copy', source={
        'kind': 'rag_chunk', 'document_id': document.id, 'chunk_id': chunk.id,
        'revision': document.revision, 'index_version': chunk.index_version,
        'space_id': space.id,
    }, quote=chunk.text)
    db_session.commit()
    def access():
        return memory_sources.memory_access(db_session, saved, actor=reader,
                                            account=reader.account, space_id=space.id)
    assert access().readable
    create_space_member(db_session, space.id, reader.id, status='removed')
    db_session.commit()
    assert memory_sources.memory_materializable(db_session, saved)
    assert not access().readable
    rag_maintenance.run_maintenance_batch(db_session, worker_id='source-distinction')
    db_session.commit()
    db_session.refresh(document)
    assert document.status == 'active'
    assert memory_sources.document_readable(db_session, document, actor=owner,
                                            account=owner.account, space_id=space.id)
    create_space_member(db_session, space.id, reader.id)
    db_session.commit()
    assert access().readable
    saved.source_verification = 'unverified'
    db_session.commit()
    assert memory_sources.source_lifecycle(db_session, saved) == 'unverified'
    assert access().status == 'unverified'
    saved.source_verification = 'verified'
    memory_rag.invalidate_source(db_session, source_type=document.source_type,
                                 source_id=document.source_id)
    db_session.commit()
    assert memory_sources.source_lifecycle(db_session, saved) == 'invalid'
    assert not access().readable
    assert saved.raw_quote == 'shared synthetic orchidgrove source'
    observe('reader_unverified_invalidation_distinction', reader_loss_tombstone=False,
            permanent_invalidation=document.status, preserved_saved_quote=True)


def test_stage_rechecks_source_after_concurrent_revocation(db_session, monkeypatch):
    user, _space, memory = root(db_session)
    document = doc_for(db_session, memory.id)
    old_version = document.index_version
    actual_materializable = memory_sources.memory_materializable
    revoked = False
    def revoke_after_validation(db, row):
        nonlocal revoked
        valid = actual_materializable(db, row)
        if valid and row.id == memory.id and not revoked:
            revoked = True
            with SessionLocal() as writer:
                memory_rag.revoke_memory(writer, memory_id=row.id,
                                         account_id=user.account.id)
                writer.commit()
        return valid
    monkeypatch.setattr(memory_sources, 'memory_materializable', revoke_after_validation)
    result = rag_maintenance.stage_index_version(db_session, target_version='fts5-trigram-v3-review',
                                                worker_id='racing-stage')
    db_session.commit()
    db_session.refresh(document)
    new_active_chunks = [c.id for c in chunks_for(db_session, document.id)
                         if c.index_version == 'fts5-trigram-v3-review' and c.status == 'active']
    observe('stage_concurrent_source_revocation', committed_revocation=revoked,
            result=result, final_status=document.status, final_version=document.index_version,
            new_active_chunks=new_active_chunks)
    assert document.status == 'invalidated'
    assert document.index_version == old_version
    assert not new_active_chunks
