"""Real Alembic connections, FK ON/OFF, and actual persisted saved references."""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from test_rag_lifecycle_acceptance import chunks_for, confirm, document_for, features

from app.models.memory import Memory
from app.models.user import User
from app.services import memory_rag, memory_sources, rag_maintenance
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture

BACKEND = Path(__file__).parents[1]
OLD_HEAD = "0046_context_execution_contract"
NEW_HEAD = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()

# env.py creates its own engine. A listener on sqlalchemy.Engine configures
# that actual connection; setting PRAGMA on an application connection is not
# evidence. before_cursor_execute additionally checks the connection Alembic
# really uses to query rag_documents, and counts DDL before a refusal.
RUNNER = r"""
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from alembic import command
from alembic.config import Config

requested = int(sys.argv[3])
observed = set()
ddl = []
@event.listens_for(Engine, 'connect')
def configure(connection, record):
    connection.execute('PRAGMA foreign_keys=' + str(requested))
    assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == requested
@event.listens_for(Engine, 'before_cursor_execute')
def witness(conn, cursor, statement, parameters, context, executemany):
    if 'rag_documents' in statement.lower():
        cursor.execute('PRAGMA foreign_keys')
        actual = cursor.fetchone()[0]
        assert actual == requested
        observed.add(actual)
    if statement.lstrip().upper().startswith(('CREATE ', 'ALTER ', 'DROP ')):
        ddl.append(statement.split()[0].upper())
try:
    getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
finally:
    print('ACTUAL_ALEMBIC_RAG_FK=' + ','.join(map(str, sorted(observed))))
    print('ACTUAL_ALEMBIC_DDL_COUNT=' + str(len(ddl)))
"""


def migrate(data_dir, direction, target, *, foreign_keys):
    return subprocess.run(
        [
            str(BACKEND / ".venv/bin/python"),
            "-c",
            RUNNER,
            direction,
            target,
            str(int(foreign_keys)),
        ],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        text=True,
        capture_output=True,
    )


def migration_engine(data_dir):
    engine = create_engine(f"sqlite:///{data_dir / 'db/app.db'}")

    @event.listens_for(engine, "connect")
    def set_fk(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def snapshot(engine, *, schema=False):
    with engine.connect() as conn:
        result = {
            table: conn.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
            for table in ("memories", "memory_candidates", "rag_chunks")
        }
        # The new nullable digest is intentionally not inferred during upgrade.
        result["documents"] = conn.execute(
            text(
                "SELECT id,source_type,source_id,revision,source_revision,"
                "author_account_id,owner_user_id,"
                "space_id,scope,sensitivity,confirmation_status,visibility_snapshot,visibility_snapshot_key,"
                "index_version,status,invalidated_at,invalidation_reason,created_at,updated_at "
                "FROM rag_documents ORDER BY id"
            )
        ).all()
        result["fts"] = conn.execute(
            text("SELECT rowid,chunk_id,text FROM rag_chunks_fts ORDER BY rowid")
        ).all()
        if schema:
            result["schema"] = conn.execute(
                text(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ).all()
            result["version"] = conn.execute(text("SELECT * FROM alembic_version ORDER BY 1")).all()
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        return result


def seed_real_saved_dependency(engine, monkeypatch):
    with Session(engine, expire_on_commit=False) as db:
        user, space = create_agent_fixture(db, name="migration-lifecycle")
        features(db, rag=True)
        with monkeypatch.context() as patch:
            patch.setattr(memory_rag, "RAG_INDEX_VERSION", "fts5-trigram-v1")
            memory = confirm(db, user, "orchidgrove migration " + "A" * 1500)
            document = document_for(db, memory.id)
            original = chunks_for(db, document)[0]
            saved = confirm(
                db,
                user,
                "persisted saved reference",
                quote=original.text,
                source={
                    "kind": "rag_chunk",
                    "document_id": document.id,
                    "chunk_id": original.id,
                    "revision": document.revision,
                    "index_version": original.index_version,
                    "space_id": space.id,
                },
            )
        rag_maintenance.stage_index_version(
            db, target_version="fts5-trigram-v2", worker_id="migration"
        )
        assert memory_sources.memory_access(db, saved, actor=user, account=user.account).readable
        revoked = confirm(db, user, "synthetic tombstone")
        memory_rag.revoke_memory(db, memory_id=revoked.id, account_id=user.account.id)
        unknown = confirm(db, user, "synthetic unknown tombstone")
        unknown_document = document_for(db, unknown.id)
        unknown_document.status = "invalidated"
        unknown_document.invalidation_reason = None
        legacy = confirm(db, user, "synthetic unverified")
        legacy.source_verification = "unverified"
        expired = confirm(db, user, "synthetic expired")
        expired.retention_until = utcnow() - timedelta(days=1)
        db.flush()
        # Prepare the actual legacy state: no historical full-input digest.
        # 0047's safe downgrade then removes only new nullable metadata/indexes;
        # no schema or source/chunk rows are manually dropped to fake 0046.
        db.execute(text("UPDATE rag_documents SET content_sha256 = NULL"))
        db.commit()
        return {
            "memory_id": memory.id,
            "saved_id": saved.id,
            "user_id": user.id,
            "chunk_id": original.id,
            "chunk_text": original.text,
            "chunk_version": original.index_version,
            "root_id": document.id,
        }


def legacy_database(tmp_path, monkeypatch, *, foreign_keys):
    result = migrate(tmp_path, "upgrade", "head", foreign_keys=False)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    ids = seed_real_saved_dependency(engine, monkeypatch)
    result = migrate(tmp_path, "downgrade", OLD_HEAD, foreign_keys=foreign_keys)
    assert result.returncode == 0, result.stderr
    assert f"ACTUAL_ALEMBIC_RAG_FK={int(foreign_keys)}" in result.stdout
    return engine, ids


def assert_saved_access(engine, ids):
    with Session(engine) as db:
        saved = db.get(Memory, ids["saved_id"])
        user = db.get(User, ids["user_id"])
        assert saved.source_span_json["chunk_id"] == ids["chunk_id"]
        assert saved.source_span_json["index_version"] == ids["chunk_version"]
        assert saved.raw_quote == ids["chunk_text"]
        assert memory_sources.memory_access(db, saved, actor=user, account=user.account).readable
        original = db.execute(
            text("SELECT text,index_version FROM rag_chunks WHERE id=:id"), {"id": ids["chunk_id"]}
        ).one()
        assert original == (ids["chunk_text"], ids["chunk_version"])


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_upgrade_keeps_all_versions_fts_and_real_saved_dependencies(
    tmp_path, monkeypatch, foreign_keys
):
    engine, ids = legacy_database(tmp_path, monkeypatch, foreign_keys=foreign_keys)
    try:
        before = snapshot(engine)
        result = migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
        assert result.returncode == 0, result.stderr
        assert f"ACTUAL_ALEMBIC_RAG_FK={int(foreign_keys)}" in result.stdout
        assert '"saved_rag_dependencies": 1' in result.stderr
        assert '"unknown_tombstones": 1' in result.stderr
        assert snapshot(engine) == before
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == NEW_HEAD
            assert (
                conn.scalar(
                    text("SELECT COUNT(*) FROM rag_documents WHERE content_sha256 IS NOT NULL")
                )
                == 0
            )
            indexes = conn.exec_driver_sql("PRAGMA index_list('rag_documents')").all()
            assert any(row[1] == "ix_rag_documents_source" and row[2] == 1 for row in indexes)
        assert_saved_access(engine, ids)
    finally:
        engine.dispose()


@pytest.mark.parametrize("foreign_keys", [False, True])
@pytest.mark.parametrize("conflict", ["duplicate", "mirror", "both"])
def test_preflight_reports_and_refuses_before_first_ddl_without_modifying_evidence(
    tmp_path,
    monkeypatch,
    foreign_keys,
    conflict,
):
    engine, ids = legacy_database(tmp_path, monkeypatch, foreign_keys=foreign_keys)
    try:
        with engine.begin() as conn:
            if conflict in ("duplicate", "both"):
                columns = [
                    row[1]
                    for row in conn.exec_driver_sql("PRAGMA table_info('rag_documents')")
                    if row[1] != "id"
                ]
                names = ",".join(columns)
                conn.execute(
                    text(
                        f"INSERT INTO rag_documents ({names}) "
                        f"SELECT {names} FROM rag_documents WHERE id=:id"
                    ),
                    {"id": ids["root_id"]},
                )
            if conflict in ("mirror", "both"):
                conn.execute(
                    text("UPDATE rag_documents SET source_revision=9 WHERE id=:id"),
                    {"id": ids["root_id"]},
                )
        before = snapshot(engine, schema=True)
        result = migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
        assert result.returncode != 0 and "RAG canonical identity conflict" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert f"ACTUAL_ALEMBIC_RAG_FK={int(foreign_keys)}" in result.stdout
        assert '"saved_rag_dependencies": 1' in result.stderr
        assert ids["chunk_text"] not in result.stderr
        assert "persisted saved reference" not in result.stderr
        assert snapshot(engine, schema=True) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_direct_0045_downgrade_refuses_before_dropping_tables_or_old_references(
    tmp_path,
    monkeypatch,
    foreign_keys,
):
    engine, ids = legacy_database(tmp_path, monkeypatch, foreign_keys=foreign_keys)
    try:
        result = migrate(
            tmp_path, "downgrade", "0045_rag_index_lifecycle", foreign_keys=foreign_keys
        )
        assert result.returncode == 0, result.stderr
        before = snapshot(engine, schema=True)
        result = migrate(
            tmp_path, "downgrade", "0044_rag_citation_contract", foreign_keys=foreign_keys
        )
        assert result.returncode != 0 and "retain chunks and roll forward" in result.stderr
        assert "saved_dependencies=1" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert f"ACTUAL_ALEMBIC_RAG_FK={int(foreign_keys)}" in result.stdout
        assert snapshot(engine, schema=True) == before
        result = migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
        assert result.returncode == 0, result.stderr
        assert_saved_access(engine, ids)
    finally:
        engine.dispose()


def test_new_digest_evidence_cannot_be_discarded_by_downgrade(tmp_path, monkeypatch):
    result = migrate(tmp_path, "upgrade", "head", foreign_keys=False)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    try:
        ids = seed_real_saved_dependency(engine, monkeypatch)
        with Session(engine) as db:
            # A complete real legacy projection can establish input evidence.
            memory = db.get(Memory, ids["memory_id"])
            memory_rag.ensure_memory_index(db, memory)
            db.commit()
        before = snapshot(engine, schema=True)
        result = migrate(tmp_path, "downgrade", OLD_HEAD, foreign_keys=True)
        assert result.returncode != 0 and "retain data and roll forward" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert snapshot(engine, schema=True) == before
        assert_saved_access(engine, ids)
    finally:
        engine.dispose()


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_integrated_head_preflights_deep_rag_refusal_before_any_ddl(
    tmp_path, monkeypatch, foreign_keys
):
    result = migrate(tmp_path, "upgrade", "head", foreign_keys=False)
    assert result.returncode == 0, result.stderr
    engine = migration_engine(tmp_path)
    try:
        ids = seed_real_saved_dependency(engine, monkeypatch)
        before = snapshot(engine, schema=True)
        result = migrate(
            tmp_path, "downgrade", "0044_rag_citation_contract", foreign_keys=foreign_keys
        )
        assert result.returncode != 0 and "retain chunks and roll forward" in result.stderr
        assert "saved_dependencies=1" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert snapshot(engine, schema=True) == before
        assert_saved_access(engine, ids)
    finally:
        engine.dispose()
