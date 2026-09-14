"""Additive evidence migration preserves legacy rows and refuses unsafe rollback."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_context_migration_legacy_preservation_and_preflight(tmp_path):
    path = Path(__file__).parents[1] / "migrations/versions/0046_context_execution_contract.py"
    spec = importlib.util.spec_from_file_location("context_contract_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'context.sqlite'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE context_builds (id INTEGER PRIMARY KEY, blocks_json JSON)"))
        conn.execute(
            text("INSERT INTO context_builds VALUES (1, :legacy)"),
            {"legacy": '[{"text":"legacy witness"}]'},
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            assert conn.execute(
                text("SELECT blocks_json, policy_json, invalidated_at FROM context_builds")
            ).one() == ('[{"text":"legacy witness"}]', None, None)
            # Empty/legacy-only databases can roundtrip without inferred evidence.
            module.downgrade()
            module.upgrade()
            conn.execute(
                text("UPDATE context_builds SET policy_json = :policy"),
                {"policy": '{"estimator_version":"new"}'},
            )
            before = conn.execute(text("SELECT * FROM context_builds")).all()
            with pytest.raises(RuntimeError, match="roll forward"):
                module.downgrade()
            assert conn.execute(text("SELECT * FROM context_builds")).all() == before
    engine.dispose()
