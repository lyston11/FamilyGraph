"""收紧通用 Agent Runtime 为 assistant-only。

Steward 使用独立的 ``StewardJob``/maintenance 执行链。迁移保留所有通用
runtime 数据；如果历史库仍有 steward 或其他 kind，必须先诊断并中止，不能
把不可达的运行记录静默删除或改写。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_agent_runtime_assistant_only"
down_revision: str | None = "0023_system_admin_decision_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_TRIGGER_SQL = """
CREATE TRIGGER trg_agent_sessions_scope_immutable
BEFORE UPDATE ON agent_sessions
WHEN OLD.account_id <> NEW.account_id
  OR OLD.space_id <> NEW.space_id
  OR OLD.agent_kind <> NEW.agent_kind
BEGIN
    SELECT RAISE(ABORT, 'agent_sessions scope is immutable');
END;
"""

_RUNTIME_KIND_COLUMNS = (
    ("agent_sessions", "agent_kind"),
    ("agent_runs", "kind"),
    ("agent_jobs", "kind"),
)


def _kind_expression(column: str, allowed: tuple[str, ...]) -> str:
    values = ", ".join(f"'{value}'" for value in allowed)
    if allowed == ("assistant",):
        return f"{column} = 'assistant'"
    return f"{column} IN ({values})"


def _validate_existing_kinds(conn: sa.Connection, allowed: tuple[str, ...]) -> None:
    """Abort before DDL when the old runtime contains an unrepresentable kind."""
    allowed_sql = ", ".join(f"'{value}'" for value in allowed)
    conflicts: list[str] = []
    for table, column in _RUNTIME_KIND_COLUMNS:
        rows = conn.execute(
            sa.text(
                f"SELECT id, {column} AS kind FROM {table} "
                f"WHERE {column} IS NULL OR {column} NOT IN ({allowed_sql}) "
                "ORDER BY id LIMIT 21"
            )
        ).mappings()
        for row in rows:
            conflicts.append(f"{table}[id={row['id']}, kind={row['kind']!r}]")
    if conflicts:
        raise RuntimeError(
            "agent runtime kind migration refused: expected "
            f"{', '.join(allowed)}; conflicting rows: {', '.join(conflicts)}"
        )


def _rebuild_runtime_tables(
    conn: sa.Connection, *, allowed: tuple[str, ...], restore_steward_index: bool
) -> None:
    """Rebuild SQLite CHECK constraints while retaining rows and FK shape."""
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_agent_jobs_space_active"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_agent_sessions_scope_immutable"))
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        session_kind = _kind_expression("agent_kind", allowed)
        run_kind = _kind_expression("kind", allowed)
        conn.execute(
            sa.text(
                "CREATE TABLE agent_sessions_new ("
                "id INTEGER NOT NULL,"
                "account_id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                f"agent_kind VARCHAR(16) NOT NULL CONSTRAINT "
                f"ck_agent_sessions_ck_agent_sessions_kind CHECK ({session_kind}),"
                "created_at DATETIME NOT NULL,"
                "term_usage_consent BOOLEAN DEFAULT (0) NOT NULL,"
                "CONSTRAINT pk_agent_sessions PRIMARY KEY (id),"
                "CONSTRAINT fk_agent_sessions_account_id_accounts "
                "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_sessions_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE agent_runs_new ("
                "id INTEGER NOT NULL,"
                "session_id INTEGER NOT NULL,"
                "message_id INTEGER,"
                "job_id INTEGER,"
                f"kind VARCHAR(16) NOT NULL CONSTRAINT "
                f"ck_agent_runs_ck_agent_runs_kind CHECK ({run_kind}),"
                "status VARCHAR(16) DEFAULT 'queued' NOT NULL CONSTRAINT "
                "ck_agent_runs_ck_agent_runs_status CHECK "
                "(status IN ('queued','leased','running','succeeded','failed',"
                "'cancelled','expired')),"
                "attempt INTEGER DEFAULT '0' NOT NULL,"
                "max_attempts INTEGER DEFAULT '3' NOT NULL,"
                "lease_expires_at DATETIME,"
                "heartbeat_at DATETIME,"
                "cancel_requested BOOLEAN DEFAULT (0) NOT NULL,"
                "error_code VARCHAR(64),"
                "error_json JSON,"
                "policy_version VARCHAR(32) NOT NULL,"
                "tool_allowlist_json JSON NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "updated_at DATETIME NOT NULL,"
                "settled_at DATETIME,"
                "runtime_snapshot_json JSON,"
                "CONSTRAINT pk_agent_runs PRIMARY KEY (id),"
                "CONSTRAINT uq_agent_runs_job_id UNIQUE (job_id),"
                "CONSTRAINT fk_agent_runs_session_id_agent_sessions "
                "FOREIGN KEY(session_id) REFERENCES agent_sessions (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_runs_message_id_agent_messages "
                "FOREIGN KEY(message_id) REFERENCES agent_messages (id) ON DELETE SET NULL,"
                "CONSTRAINT fk_agent_runs_job_id_agent_jobs "
                "FOREIGN KEY(job_id) REFERENCES agent_jobs (id) ON DELETE SET NULL"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE agent_jobs_new ("
                "id INTEGER NOT NULL,"
                "space_id INTEGER,"
                "account_id INTEGER,"
                f"kind VARCHAR(16) NOT NULL CONSTRAINT "
                f"ck_agent_jobs_ck_agent_jobs_kind CHECK ({run_kind}),"
                "status VARCHAR(16) DEFAULT 'queued' NOT NULL CONSTRAINT "
                "ck_agent_jobs_ck_agent_jobs_status CHECK "
                "(status IN ('queued','leased','running','succeeded','failed',"
                "'cancelled','expired')),"
                "attempt INTEGER DEFAULT '0' NOT NULL,"
                "max_attempts INTEGER DEFAULT '3' NOT NULL,"
                "lease_expires_at DATETIME,"
                "heartbeat_at DATETIME,"
                "cancel_requested BOOLEAN DEFAULT 0 NOT NULL,"
                "leased_by VARCHAR(120),"
                "error_json JSON,"
                "policy_version VARCHAR(32) NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "updated_at DATETIME NOT NULL,"
                "run_id INTEGER NOT NULL,"
                "CONSTRAINT pk_agent_jobs PRIMARY KEY (id),"
                "UNIQUE (run_id),"
                "CONSTRAINT uq_agent_jobs_run_id UNIQUE (run_id),"
                "CONSTRAINT fk_agent_jobs_account_id_accounts "
                "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_jobs_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_jobs_run_id_agent_runs "
                "FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE"
                ")"
            )
        )

        conn.execute(
            sa.text(
                "INSERT INTO agent_sessions_new "
                "(id, account_id, space_id, agent_kind, created_at, term_usage_consent) "
                "SELECT id, account_id, space_id, agent_kind, created_at, term_usage_consent "
                "FROM agent_sessions"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs_new "
                "(id, session_id, message_id, job_id, kind, status, attempt, max_attempts, "
                "lease_expires_at, heartbeat_at, cancel_requested, error_code, error_json, "
                "policy_version, tool_allowlist_json, created_at, updated_at, settled_at, "
                "runtime_snapshot_json) "
                "SELECT id, session_id, message_id, job_id, kind, status, attempt, max_attempts, "
                "lease_expires_at, heartbeat_at, cancel_requested, error_code, error_json, "
                "policy_version, tool_allowlist_json, created_at, updated_at, settled_at, "
                "runtime_snapshot_json FROM agent_runs"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_jobs_new "
                "(id, space_id, account_id, kind, status, attempt, max_attempts, "
                "lease_expires_at, heartbeat_at, cancel_requested, leased_by, error_json, "
                "policy_version, created_at, updated_at, run_id) "
                "SELECT id, space_id, account_id, kind, status, attempt, max_attempts, "
                "lease_expires_at, heartbeat_at, cancel_requested, leased_by, error_json, "
                "policy_version, created_at, updated_at, run_id FROM agent_jobs"
            )
        )

        # Foreign keys are disabled while the mutually-referencing old tables are replaced.
        for table in ("agent_jobs", "agent_runs", "agent_sessions"):
            conn.execute(sa.text(f"DROP TABLE {table}"))
        for table in ("agent_sessions", "agent_runs", "agent_jobs"):
            conn.execute(sa.text(f"ALTER TABLE {table}_new RENAME TO {table}"))

        conn.execute(sa.text("CREATE INDEX ix_agent_runs_session_id ON agent_runs (session_id)"))
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id) "
                "WHERE status IN ('queued','leased','running')"
            )
        )
        conn.execute(sa.text("CREATE INDEX ix_agent_jobs_lease_scan ON agent_jobs (kind, status)"))
        if restore_steward_index:
            conn.execute(
                sa.text(
                    "CREATE UNIQUE INDEX uq_agent_jobs_space_active ON agent_jobs (space_id) "
                    "WHERE kind = 'steward' AND status IN ('queued','leased','running')"
                )
            )
        conn.execute(sa.text(_SCOPE_TRIGGER_SQL))
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


def upgrade() -> None:
    conn = op.get_bind()
    _validate_existing_kinds(conn, ("assistant",))
    _rebuild_runtime_tables(conn, allowed=("assistant",), restore_steward_index=False)


def downgrade() -> None:
    conn = op.get_bind()
    _validate_existing_kinds(conn, ("assistant", "steward"))
    _rebuild_runtime_tables(conn, allowed=("assistant", "steward"), restore_steward_index=True)
