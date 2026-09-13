"""0042 legacy classification and SQLite FK-safe rebuild against real migrations."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from conftest import (
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
    seed_space_with_owner,
)

BACKEND = Path(__file__).parents[1]
OLD_HEAD = "0041_term_pack_expansion"
NEW_HEAD = "0044_rag_citation_contract"


def _migrate(data_dir, *args):
    result = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-m", "alembic", *args],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        text=True,
        capture_output=True,
    )
    return result


def _engine(data_dir, fk_enabled=True):
    engine = create_engine(f"sqlite:///{data_dir / 'db/app.db'}")

    @event.listens_for(engine, "connect")
    def set_fk(connection, _record):
        connection.execute(f"PRAGMA foreign_keys={'ON' if fk_enabled else 'OFF'}")

    return engine


def _migration():
    spec = importlib.util.spec_from_file_location(
        "memory_source_migration", BACKEND / "migrations/versions/0042_memory_source_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_legacy(engine, *, duplicates=False):
    with Session(engine) as db:
        actor, space = create_agent_fixture(db, name="migration-source-owner")
        other_space = seed_space_with_owner(db, actor.id, name="migration-other-space")
        session = create_agent_session(db, account_id=actor.account.id, space_id=space.id)
        original = create_agent_message(db, session, content={"text": "Original migration quote."})
        assistant = create_agent_message(
            db, session, role="assistant", content={"text": "Assistant migration quote."}
        )
        # A valid user source, unknown doc label, Assistant source, forged quote,
        # and an old cross-space Memory must be distinguished without losing data.
        sources = [
            (original.id, None, "Original migration quote.", "private", None),
            (
                None,
                "arbitrary historical document label",
                "Legacy document quote.",
                "private",
                None,
            ),
            (assistant.id, None, "Assistant migration quote.", "private", None),
            (original.id, None, "Forged migration quote.", "private", None),
            (original.id, None, "Original migration quote.", "household", other_space.id),
        ]
        for row_id, (message_id, document_ref, quote, scope, space_id) in enumerate(sources, 1):
            values = {
                "id": row_id,
                "account_id": actor.account.id,
                "message_id": message_id,
                "document_ref": document_ref,
                "quote": quote,
                "created_at": actor.created_at.isoformat(sep=" "),
                "scope": scope,
                "space_id": space_id,
            }
            db.execute(
                text("""
                INSERT INTO memory_candidates
                (id,author_account_id,source_message_id,source_document_ref,source_span_json,
                 source_quote,summary,suggested_scope,purpose,sensitivity,extractor_version,
                 status,confirmed_by_account_id,confirmed_at,decided_at,memory_id,created_at,updated_at)
                VALUES (:id,:account_id,:message_id,:document_ref,'{"historic":"preserve"}',
                    :quote,:quote,'private','old purpose','normal','old-v1','confirmed',
                    :account_id,:created_at,:created_at,:id,:created_at,:created_at)
            """),
                values,
            )
            db.execute(
                text("""
                INSERT INTO memories
                (id,author_account_id,source_candidate_id,source_message_id,source_document_ref,
                 source_span_json,raw_quote,content,scope,space_id,sensitivity,purpose,
                 confirmed_by_account_id,confirmed_at,created_at,updated_at)
                VALUES (:id,:account_id,:id,:message_id,:document_ref,'{}',:quote,:quote,
                    :scope,:space_id,'normal','old purpose',:account_id,
                    :created_at,:created_at,:created_at)
            """),
                values,
            )
        if duplicates:
            db.execute(
                text("""
                INSERT INTO memories
                (id,author_account_id,source_candidate_id,source_message_id,source_document_ref,
                 source_span_json,raw_quote,content,scope,space_id,sensitivity,purpose,
                 confirmed_by_account_id,confirmed_at,created_at,updated_at)
                SELECT 6,author_account_id,source_candidate_id,source_message_id,
                    source_document_ref,
                    source_span_json,raw_quote,content,scope,space_id,sensitivity,purpose,
                    confirmed_by_account_id,confirmed_at,created_at,updated_at
                FROM memories WHERE id=1
            """)
            )
        db.commit()
        return session.id


@pytest.mark.parametrize("fk_enabled", [False, True])
def test_upgrade_preserves_references_and_quarantines_ambiguous_legacy(tmp_path, fk_enabled):
    result = _migrate(tmp_path, "upgrade", OLD_HEAD)
    assert result.returncode == 0, result.stderr
    engine = _engine(tmp_path, fk_enabled)
    session_id = _seed_legacy(engine)
    with engine.begin() as connection:
        original = connection.execute(
            text(
                "SELECT id,source_candidate_id,raw_quote,content,scope,space_id,confirmed_at "
                "FROM memories ORDER BY id"
            )
        ).all()
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()
        after = connection.execute(
            text(
                "SELECT id,source_candidate_id,raw_quote,content,scope,space_id,confirmed_at "
                "FROM memories ORDER BY id"
            )
        ).all()
        assert after == original
        states = connection.execute(
            text("SELECT id,source_kind,source_verification FROM memories ORDER BY id")
        ).all()
        assert states == [
            (1, "agent_message", "verified"),
            (2, "legacy", "unverified"),
            (3, "legacy", "unverified"),
            (4, "legacy", "unverified"),
            (5, "legacy", "unverified"),
        ]
        assert connection.execute(
            text("SELECT id,memory_id,status FROM memory_candidates ORDER BY id")
        ).all() == [(row_id, row_id, "confirmed") for row_id in range(1, 6)]
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == int(fk_enabled)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert "uq_memories_source_candidate" in {
            index["name"] for index in inspect(connection).get_indexes("memories")
        }
        assert "uq_memory_candidates_request" in {
            index["name"] for index in inspect(connection).get_indexes("memory_candidates")
        }
    # Verify the real FK action, including quarantined Assistant candidates. No
    # removed-FK/source CHECK can block deleting the original session.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(text("DELETE FROM agent_sessions WHERE id=:id"), {"id": session_id})
        connection.commit()
        assert connection.scalar(text("SELECT count(*) FROM memories")) == 5
        assert connection.scalar(text("SELECT count(*) FROM memory_candidates")) == 5
        assert connection.scalar(text("SELECT source_message_id FROM memories WHERE id=1")) is None
        assert (
            connection.scalar(text("SELECT source_kind FROM memories WHERE id=1"))
            == "agent_message"
        )
        assert connection.scalar(text("SELECT source_id FROM memories WHERE id=1")) is not None
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_duplicate_old_confirmations_abort_without_deleting_or_rewriting_rows(tmp_path):
    result = _migrate(tmp_path, "upgrade", OLD_HEAD)
    assert result.returncode == 0, result.stderr
    engine = _engine(tmp_path)
    _seed_legacy(engine, duplicates=True)
    with engine.begin() as connection:
        before = connection.execute(text("SELECT * FROM memories ORDER BY id")).all()
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="confirmation duplicates"):
                _migration().upgrade()
        assert connection.execute(text("SELECT * FROM memories ORDER BY id")).all() == before
        assert "source_kind" not in {
            column["name"] for column in inspect(connection).get_columns("memories")
        }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_real_alembic_chain_and_nonempty_downgrade_guard(tmp_path):
    result = _migrate(tmp_path, "upgrade", OLD_HEAD)
    assert result.returncode == 0, result.stderr
    engine = _engine(tmp_path)
    _seed_legacy(engine)
    result = _migrate(tmp_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW_HEAD
        original = connection.execute(
            text(
                "SELECT id,raw_quote,source_candidate_id,source_verification "
                "FROM memories ORDER BY id"
            )
        ).all()
    result = _migrate(tmp_path, "downgrade", OLD_HEAD)
    assert result.returncode != 0 and "discard provenance/history" in result.stderr
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT id,raw_quote,source_candidate_id,source_verification "
                    "FROM memories ORDER BY id"
                )
            ).all()
            == original
        )
        # The guarded 0042 downgrade aborted after 0044/0043 were reverted:
        # provenance columns and rows are intact and the version stops at the
        # guarded 0042 revision.
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0042_memory_source_contract"
        )
    engine.dispose()


def test_empty_schema_can_roundtrip_new_migration(tmp_path):
    for args in (("upgrade", "head"), ("downgrade", OLD_HEAD), ("upgrade", "head")):
        result = _migrate(tmp_path, *args)
        assert result.returncode == 0, result.stderr
