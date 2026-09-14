"""Both integration parents retain source data and pending delivery on roundtrip."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.relationship_facts import SourceFact
from app.models.steward import StewardDeliveryIntent, StewardGeneration
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_user_with_pin

BACKEND = Path(__file__).parents[1]
HEAD = "0048_steward_terminology_publication"
PARENTS = ("0047_rag_lifecycle_integrity", "0045_steward_staged_publication")
RUNNER = """
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, 'connect')
def configure(connection, record):
    connection.execute('PRAGMA foreign_keys=' + sys.argv[3])
    assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == int(sys.argv[3])
getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
"""


def _migrate(data_dir, direction, target, *, foreign_keys=False):
    result = subprocess.run(
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
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr


def _snapshot(engine, tables):
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        return {
            table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in tables
        }


@pytest.mark.parametrize("first_parent", PARENTS)
@pytest.mark.parametrize("foreign_keys", [False, True])
def test_merge_preserves_both_parents_and_pending_delivery(tmp_path, first_parent, foreign_keys):
    _migrate(tmp_path, "upgrade", first_parent)
    engine = create_engine(f"sqlite:///{tmp_path / 'db/app.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with Session(engine, expire_on_commit=False) as db:
            root, space = create_agent_fixture(db, name="merge-parent")
            target = create_user_with_pin(db, "merge-child", "123456")
            db.add(
                SourceFact(
                    fact_type="biological_parent",
                    subject_user_id=root.id,
                    object_user_id=target.id,
                    space_id=space.id,
                    state="confirmed",
                    provenance="manual_entry",
                    revision=3,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            db.commit()
            space_id = space.id
        source_tables = ("users", "accounts", "family_spaces", "source_facts", "term_entries")
        sources = _snapshot(engine, source_tables)
        other_parent = next(parent for parent in PARENTS if parent != first_parent)
        _migrate(tmp_path, "upgrade", other_parent)
        assert _snapshot(engine, source_tables) == sources
        _migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
        assert _snapshot(engine, source_tables) == sources

        with Session(engine, expire_on_commit=False) as db:
            generation = StewardGeneration(
                space_id=space_id,
                status="running",
                execution_cursor=7,
                manifest_sealed=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(generation)
            db.flush()
            db.add(
                StewardDeliveryIntent(
                    generation_id=generation.id,
                    space_id=space_id,
                    intent_key="terminology:pending",
                    kind="terminology",
                    payload_json={"target_user_id": target.id},
                    status="pending",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            db.commit()
        retained_tables = (*source_tables, "steward_generations", "steward_delivery_intents")
        before = _snapshot(engine, retained_tables)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all() == [HEAD]
            revision = connection.scalar(
                text("SELECT presentation FROM steward_input_revisions WHERE scope_id=0")
            )
            assert [
                row[2] for row in connection.exec_driver_sql("PRAGMA index_info(ix_sdi_status_id)")
            ] == ["status", "id"]
        _migrate(tmp_path, "downgrade", PARENTS[0], foreign_keys=foreign_keys)
        assert _snapshot(engine, retained_tables) == before
        with engine.connect() as connection:
            assert set(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            ) == set(PARENTS)
            assert not connection.exec_driver_sql("PRAGMA index_info(ix_sdi_status_id)").all()
        _migrate(tmp_path, "upgrade", "head", foreign_keys=foreign_keys)
        assert _snapshot(engine, retained_tables) == before
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all() == [HEAD]
            assert (
                connection.scalar(
                    text("SELECT presentation FROM steward_input_revisions WHERE scope_id=0")
                )
                == revision + 2
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM sqlite_master "
                        "WHERE type='trigger' AND name LIKE 'sri_%'"
                    )
                )
                == 60
            )
            assert [
                row[2] for row in connection.exec_driver_sql("PRAGMA index_info(ix_sdi_status_id)")
            ] == ["status", "id"]
        with Session(engine) as db:
            before_inferred = (
                db.scalar(
                    text("SELECT inferred FROM steward_input_revisions WHERE scope_id=:space"),
                    {"space": space_id},
                )
                or 0
            )
            db.add(AgentSpaceProviderSetting(space_id=space_id, agent_kind="steward"))
            db.commit()
            assert (
                db.scalar(
                    text("SELECT inferred FROM steward_input_revisions WHERE scope_id=:space"),
                    {"space": space_id},
                )
                == before_inferred + 1
            )
    finally:
        engine.dispose()
