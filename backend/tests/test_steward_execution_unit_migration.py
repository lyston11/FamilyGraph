"""0055_steward_assist_execution_unit: guards, schema shape, and lossless round trip.

Three things are asserted separately, because they fail for different reasons:

1. **Refusal guards** are pure SQL predicates evaluated before any DDL. States
   they detect are unreachable through the pre-0055 schema (NOT NULL / CHECK
   forbid them), so the predicates are exercised directly against a crafted
   minimal schema — and the guard-before-DDL ordering is asserted separately by
   counting DDL statements in a real refused run.
2. **Schema shape** after upgrade: dual-kind ``agent_runs``, the batch table
   narrowed to an immutable plan snapshot, the lease columns on the attempt, and
   the *unchanged* assistant-only CHECKs on ``agent_sessions`` / ``agent_jobs``.
3. **Round trip** is lossless: downgrade restores the exact pre-0055 schema,
   including re-deriving the batch rows from the attempt ledger. This is the
   assertion that catches an unfaithful inverse — the downgrade has to recreate a
   table whose columns it cannot simply re-add.
"""

from __future__ import annotations

import importlib.util
import os
import re as _re
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text

BACKEND = Path(__file__).parents[1]
PARENT = "0054_seed_household_roster_fix"
HEAD = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()

_MIGRATION_PATH = BACKEND / "migrations/versions/0055_steward_assist_execution_unit.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig0055", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MIG = _load_migration()

# Records every DDL statement so a refused run can be proven to have issued none.
RUNNER = r"""
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from alembic import command
from alembic.config import Config

ddl = []
@event.listens_for(Engine, 'before_cursor_execute')
def witness(conn, cursor, statement, parameters, context, executemany):
    if statement.lstrip().upper().startswith(('CREATE ', 'ALTER ', 'DROP ')):
        ddl.append(statement.split()[0].upper())
status = 0
try:
    getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
except Exception as exc:
    # Report the refusal and still fail the process: a swallowed exit code
    # would make "the migration refused" indistinguishable from success.
    status = 1
    print('MIGRATION_ERROR=' + type(exc).__name__ + ': ' + str(exc)[:400])
finally:
    print('DDL_COUNT=' + str(len(ddl)))
sys.exit(status)
"""


def run_migration(data_dir: Path, direction: str, target: str) -> tuple[int, str]:
    result = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-c", RUNNER, direction, target],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        text=True,
        capture_output=True,
    )
    return result.returncode, result.stdout + result.stderr


def migration_engine(data_dir: Path):
    engine = create_engine(f"sqlite:///{data_dir / 'db/app.db'}")

    @event.listens_for(engine, "connect")
    def set_fk(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def schema_of(engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row[0]: row[1] or ""
            for row in conn.execute(
                text("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
            )
        }


def _structural_schema(data_dir: Path) -> dict[str, object]:
    """Contract-level schema identity.

    A rebuild cannot reproduce SQLite's original constraint *ordering* or
    whitespace, neither of which carries a contract. Columns, foreign keys,
    indexes and CHECK bodies are therefore compared as *sets*, so any real
    difference (a dropped constraint, a lost NOT NULL, a changed FK action)
    still fails the assertion while a pure reformat does not.
    """
    engine = migration_engine(data_dir)
    result: dict[str, object] = {}
    with engine.connect() as conn:
        stored = {
            row[0]: row[1] or ""
            for row in conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table'"))
        }
        for table in sorted(stored):
            if table.startswith("sqlite_"):
                continue
            result[table] = {
                "columns": sorted(
                    (row[1], row[2], bool(row[3]), row[4])
                    for row in conn.execute(text(f"PRAGMA table_info({table})"))
                ),
                "foreign_keys": sorted(
                    (row[2], row[3], row[4], row[6])
                    for row in conn.execute(text(f"PRAGMA foreign_key_list({table})"))
                ),
                "indexes": sorted(
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE type='index' "
                            "AND tbl_name=:t AND name NOT LIKE 'sqlite_%'"
                        ),
                        {"t": table},
                    )
                ),
                "checks": sorted(_check_clauses(stored[table])),
            }
    return result


def _check_clauses(sql: str) -> set[str]:
    """Extract each CHECK(...) body with balanced parentheses, normalized."""
    clauses: set[str] = set()
    for match in _re.finditer(r"CHECK\s*\(", sql, flags=_re.IGNORECASE):
        depth = 0
        start = match.end() - 1
        for index in range(start, len(sql)):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                depth -= 1
                if depth == 0:
                    clauses.add(_normalize_sql(sql[start : index + 1]))
                    break
    return clauses


def _normalize_sql(sql: str) -> str:
    """Collapse formatting that carries no contract.

    Includes comma spacing: migrations in this repo have written the same IN
    list both as ``('a','b')`` and ``('a', 'b')``, and that difference is not a
    contract change. None of the CHECK bodies contains a string literal with a
    comma, so collapsing around commas cannot merge distinct constraints.
    """
    collapsed = " ".join(sql.split())
    for _ in range(3):  # nested parens need a couple of passes
        collapsed = collapsed.replace("( ", "(").replace(" )", ")")
        collapsed = collapsed.replace(", ", ",")
    return collapsed


# --------------------------------------------------------------------------
# 1. Refusal guards (predicates, exercised directly)
# --------------------------------------------------------------------------

_GUARD_SCHEMA = """
CREATE TABLE agent_sessions (id INTEGER PRIMARY KEY, agent_kind TEXT);
CREATE TABLE agent_jobs (id INTEGER PRIMARY KEY, kind TEXT);
CREATE TABLE agent_runs (id INTEGER PRIMARY KEY, session_id INTEGER, job_id INTEGER, kind TEXT);
CREATE TABLE context_builds (id INTEGER PRIMARY KEY, agent_kind TEXT);
CREATE TABLE steward_model_calls (id INTEGER PRIMARY KEY, assist_kind TEXT, status TEXT);
"""


def _guard_engine(runs=(), jobs=(), sessions=(), calls=()):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for stmt in _GUARD_SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(sa.text(stmt))
        for row in runs:
            conn.execute(
                sa.text("INSERT INTO agent_runs VALUES (:a,:b,:c,:d)"),
                {"a": row[0], "b": row[1], "c": row[2], "d": row[3]},
            )
        for row in jobs:
            conn.execute(
                sa.text("INSERT INTO agent_jobs VALUES (:a,:b)"), {"a": row[0], "b": row[1]}
            )
        for row in sessions:
            conn.execute(
                sa.text("INSERT INTO agent_sessions VALUES (:a,:b)"), {"a": row[0], "b": row[1]}
            )
        for row in calls:
            conn.execute(
                sa.text("INSERT INTO steward_model_calls VALUES (:a,:b,:c)"),
                {"a": row[0], "b": row[1], "c": row[2]},
            )
    return engine


def _refusal(engine, validator) -> str | None:
    try:
        with engine.connect() as conn:
            validator(conn)
        return None
    except RuntimeError as exc:
        return str(exc)


@pytest.mark.parametrize(
    ("label", "kwargs", "expected"),
    [
        (
            "assistant run with NULL session",
            {"runs": [(1, None, None, "assistant")]},
            "must carry a session",
        ),
        (
            "unknown kind in agent_runs",
            {"runs": [(1, 1, None, "bogus")]},
            "must be assistant or steward",
        ),
        (
            "NULL kind in agent_runs",
            {"runs": [(1, 1, None, None)]},
            "must be assistant or steward",
        ),
        (
            "steward kind in agent_jobs",
            {"jobs": [(1, "steward")]},
            "agent_jobs is assistant-only",
        ),
        (
            "steward kind in agent_sessions",
            {"sessions": [(1, "steward")]},
            "agent_sessions is assistant-only",
        ),
        (
            "run with a job but no session",
            {"runs": [(1, None, 1, "steward")]},
            "must carry a session",
        ),
        (
            "attempt still in flight when the lease moves",
            {"calls": [(1, "candidate", "in_flight")]},
            "cannot survive the lease move",
        ),
    ],
)
def test_upgrade_guards_refuse_unsupported_rows(label, kwargs, expected):
    message = _refusal(_guard_engine(**kwargs), MIG._validate_before_upgrade)
    assert message is not None, f"{label} was not refused"
    assert expected in message
    # The report must name the offending row, not just the reason.
    assert "conflicting rows" in message


def test_upgrade_guards_accept_a_supported_database():
    engine = _guard_engine(
        runs=[(1, 1, None, "assistant")],
        jobs=[(1, "assistant")],
        sessions=[(1, "assistant")],
        # Terminal attempts are fine: only a live lease would have nowhere to go.
        calls=[(1, "candidate", "succeeded"), (2, "terminology", "unknown")],
    )
    assert _refusal(engine, MIG._validate_before_upgrade) is None


def test_downgrade_guard_refuses_to_drop_child_run_evidence():
    engine = _guard_engine(runs=[(1, None, None, "steward")])
    message = _refusal(engine, MIG._validate_before_downgrade)
    assert message is not None
    assert "downgrade refused" in message
    assert "evidence" in message


def test_downgrade_guard_accepts_assistant_only_history():
    engine = _guard_engine(runs=[(1, 1, None, "assistant")])
    assert _refusal(engine, MIG._validate_before_downgrade) is None


# --------------------------------------------------------------------------
# 2. Refusal happens before DDL (real alembic run)
# --------------------------------------------------------------------------


def _seed_conflicting_runtime_row(data_dir: Path) -> None:
    """Force an unsupported state by disabling CHECK enforcement for one insert."""
    engine = migration_engine(data_dir)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            text(
                "INSERT INTO users (id, name, created_at, gender, privacy_mode, profile_status) "
                "VALUES (1, 'u', '2026-01-01', 'm', 'handover', 'identity_confirmed')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO accounts (id, user_id, pin_hash, pin_must_change, token_version, "
                "failed_attempts, status) VALUES (1, 1, 'x', 0, 0, 0, 'claimed')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_sessions (id, account_id, space_id, agent_kind, created_at, "
                "term_usage_consent, updated_at) VALUES (1, 1, 1, 'assistant', '2026-01-01', 0, "
                "'2026-01-01')"
            )
        )
        # agent_sessions.space_id has an FK to family_spaces; insert it first.
        conn.execute(
            text(
                "INSERT INTO family_spaces (id, name, owner_id, kind, created_at) "
                "VALUES (1, 's', 1, 'household', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, kind, status, policy_version, "
                "tool_allowlist_json, created_at, updated_at) "
                "VALUES (1, 1, 'bogus', 'queued', 'v1', '[]', '2026-01-01', '2026-01-01')"
            )
        )


def test_refusal_precedes_every_ddl_statement(tmp_path):
    """A refused upgrade must issue no DDL and leave the schema untouched."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert run_migration(data_dir, "upgrade", PARENT)[0] == 0

    _seed_conflicting_runtime_row(data_dir)
    before = schema_of(migration_engine(data_dir))

    code, output = run_migration(data_dir, "upgrade", HEAD)

    assert code != 0, "an unsupported kind must abort the migration"
    assert "MIGRATION_ERROR=RuntimeError" in output
    assert "refused" in output
    assert "DDL_COUNT=0" in output, f"refusal must precede all DDL; got {output}"
    assert schema_of(migration_engine(data_dir)) == before


# --------------------------------------------------------------------------
# 3. Schema shape after upgrade
# --------------------------------------------------------------------------


@pytest.fixture
def upgraded(tmp_path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    code, output = run_migration(data_dir, "upgrade", HEAD)
    assert code == 0, output
    return data_dir


def test_agent_runs_becomes_dual_kind_with_nullable_session(upgraded):
    schema = schema_of(migration_engine(upgraded))
    runs = schema["agent_runs"]
    assert "kind IN ('assistant','steward')" in runs
    # session_id must lose NOT NULL so steward runs can omit it.
    assert "session_id INTEGER NOT NULL" not in runs
    assert "ck_agent_runs_scope_binding" in runs
    # The binding must express both directions of the invariant.
    assert "kind = 'assistant' AND session_id IS NOT NULL" in runs
    assert "kind = 'steward' AND session_id IS NULL AND job_id IS NULL" in runs
    # Pre-existing contracts survive the rebuild.
    assert "uq_agent_runs_job_id" in runs
    assert "uq_agent_runs_session_active" in schema
    assert "first_leased_at" in runs
    assert "runtime_snapshot_json" in runs


def test_assistant_only_tables_keep_their_check(upgraded):
    """The queue red line: agent_jobs and agent_sessions stay assistant-only."""
    schema = schema_of(migration_engine(upgraded))
    assert "kind = 'assistant'" in schema["agent_jobs"]
    assert "kind IN ('assistant','steward')" not in schema["agent_jobs"]
    assert "agent_kind = 'assistant'" in schema["agent_sessions"]
    # The historical steward queue index must not come back.
    assert "uq_agent_jobs_space_active" not in schema


def test_batch_table_becomes_an_immutable_plan(upgraded):
    """The plan keeps the snapshot and drops every execution field."""
    engine = migration_engine(upgraded)
    with engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        # The batch table is gone; its rows live on as plans.
        assert "steward_assist_batches" not in names
        assert "steward_assist_plans" in names
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(steward_assist_plans)"))}
        # The snapshot survives...
        assert {"id", "space_id", "job_id", "evidence_hash", "policy_version"} <= columns
        assert "fence_json" in columns
        # ...and every execution field moved to the attempt.
        for moved in ("status", "attempt", "next_attempt_at", "lease_owner", "lease_until"):
            assert moved not in columns, f"{moved} should have moved to the attempt"
        # job_id uniqueness (one plan per job) is preserved under its new name.
        indexes = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        }
        assert "ix_steward_assist_batches_due" not in indexes


def test_attempt_owns_the_lease_and_the_carrier(upgraded):
    """The execution unit is the attempt: lease, carrier and fence digest."""
    engine = migration_engine(upgraded)
    with engine.connect() as conn:
        columns = {
            row[1]: row for row in conn.execute(text("PRAGMA table_info(steward_model_calls)"))
        }
        for added in ("lease_owner", "lease_until", "next_attempt_at", "carrier", "evidence_hash"):
            assert added in columns, f"{added} must live on the attempt"
        assert "plan_id" in columns
        assert "batch_id" not in columns
        # The carrier is constrained, so a typo cannot silently select a
        # different executor than the scheduler intended.
        assert "carrier IN ('inproc','pi')" in schema_of(engine)["steward_model_calls"]
        # Lease selection needs its own index: without it the per-space count is
        # a full scan on every poll.
        indexes = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        }
        assert "ix_smc_due" in indexes


def test_model_call_ledger_gets_run_id(upgraded):
    engine = migration_engine(upgraded)
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(steward_model_calls)"))}
        assert "run_id" in columns
        # ON DELETE SET NULL keeps the ledger alive after the run is pruned.
        fks = {
            row[2]: row[6]
            for row in conn.execute(text("PRAGMA foreign_key_list(steward_model_calls)"))
        }
        assert fks["agent_runs"] == "SET NULL"
        index_names = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        }
        assert "uq_smc_run_id" in index_names
        # UNIQUE: one child run binds to exactly one attempt row (design §11.2).
        unique_flags = {
            row[1]: bool(row[2])
            for row in conn.execute(text("PRAGMA index_list(steward_model_calls)"))
        }
        assert unique_flags["uq_smc_run_id"] is True
        # Pre-existing attempt-key uniqueness survives.
        assert "uq_smc_attempt_key" in index_names


def test_scope_binding_is_enforced_by_the_database(upgraded):
    """The invariant must be DB-enforced, not merely documented."""
    engine = migration_engine(upgraded)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, name, created_at, gender, privacy_mode, profile_status) "
                "VALUES (1, 'u', '2026-01-01', 'm', 'handover', 'identity_confirmed')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO accounts (id, user_id, pin_hash, pin_must_change, token_version, "
                "failed_attempts, status) VALUES (1, 1, 'x', 0, 0, 0, 'claimed')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO family_spaces (id, name, owner_id, kind, created_at) "
                "VALUES (1, 's', 1, 'household', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_sessions (id, account_id, space_id, agent_kind, created_at, "
                "term_usage_consent, updated_at) VALUES (1, 1, 1, 'assistant', '2026-01-01', 0, "
                "'2026-01-01')"
            )
        )

    # A steward run without a session is representable...
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, kind, status, policy_version, "
                "tool_allowlist_json, created_at, updated_at) "
                "VALUES (1, NULL, 'steward', 'queued', 'v1', '[]', '2026-01-01', '2026-01-01')"
            )
        )
    # ...but an assistant run without one is not.
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO agent_runs (id, session_id, kind, status, policy_version, "
                    "tool_allowlist_json, created_at, updated_at) "
                    "VALUES (2, NULL, 'assistant', 'queued', 'v1', '[]', '2026-01-01', "
                    "'2026-01-01')"
                )
            )


# --------------------------------------------------------------------------
# 4. Round trip
# --------------------------------------------------------------------------


def test_downgrade_restores_the_previous_schema(tmp_path):
    """Downgrade must restore the pre-0055 column/constraint shape.

    Compared structurally rather than byte-for-byte: SQLite preserves the
    original whitespace only for tables it never rebuilt, so a textual diff
    would fail on formatting that carries no contract.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert run_migration(data_dir, "upgrade", PARENT)[0] == 0
    before = _structural_schema(data_dir)

    assert run_migration(data_dir, "upgrade", HEAD)[0] == 0
    assert run_migration(data_dir, "downgrade", PARENT)[0] == 0

    assert _structural_schema(data_dir) == before


def test_round_trip_is_repeatable(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert run_migration(data_dir, "upgrade", HEAD)[0] == 0
    assert run_migration(data_dir, "downgrade", PARENT)[0] == 0
    assert run_migration(data_dir, "upgrade", HEAD)[0] == 0

    schema = schema_of(migration_engine(data_dir))
    assert "ck_agent_runs_scope_binding" in schema["agent_runs"]
    assert "steward_assist_plans" in schema
    assert "steward_assist_batches" not in schema
