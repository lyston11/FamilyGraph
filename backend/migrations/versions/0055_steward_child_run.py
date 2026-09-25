"""Steward Pi child run：把 agent_runs 从 assistant-only 扩展为双 kind。

Steward 的模型辅助从「进程内裸 httpx 直连 Provider」回到 Pi 运行时，作为
``StewardJob`` 的受限 child run。执行记录复用 ``agent_runs`` /
``agent_run_events``（纯执行记录，不含账号语义），Steward 的 scope 由新表
``steward_runs`` 承载。

三处关键不变量：
- ``agent_sessions`` 保持 assistant-only（Steward 无单一账号，伪造 session 行
  就是数据污染）；``agent_runs.session_id`` 因此改为 nullable。
- ``agent_jobs`` 保持 assistant-only：禁止在队列层重建 ``kind='steward'`` 第二
  队列（09-01 记录的红线）。Steward child run 的 ``job_id`` 恒为 NULL。
- ``agent_runs`` 新增 ``ck_agent_runs_scope_binding``，让「assistant 必须有
  session / steward 必须无 session 且无 job」成为 DB 强制不变量。

安全：refusal guards 在**任何 DDL 或版本移动之前**执行；发现不可表达数据即
中止并报告表/行/值，原库保持可恢复。downgrade 同样先跑 guard，绝不静默删除
已产生的 child run 证据行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_steward_child_run"
down_revision: str | None = "0054_seed_household_roster_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# agent_runs 重建后的完整列序（与重建前逐列一致，另加 scope binding 约束）
_AGENT_RUNS_COLUMNS = (
    "id, session_id, message_id, job_id, kind, status, attempt, max_attempts, "
    "lease_expires_at, heartbeat_at, cancel_requested, error_code, error_json, "
    "policy_version, tool_allowlist_json, created_at, updated_at, settled_at, "
    "runtime_snapshot_json, first_leased_at"
)

_ASSIST_KINDS = ("candidate", "ranking", "explanation", "terminology")
_ASSIST_KIND_CHECK = ", ".join(repr(kind) for kind in _ASSIST_KINDS)


def _refuse(conn: sa.Connection, *, reason: str, query: str, detail: str) -> None:
    """Any hit aborts before DDL, reporting the offending rows."""
    rows = conn.execute(sa.text(query)).mappings().all()
    if rows:
        rendered = ", ".join(
            "[" + ", ".join(f"{key}={value!r}" for key, value in row.items()) + "]" for row in rows
        )
        raise RuntimeError(f"{reason}: {detail}; conflicting rows: {rendered}")


def _validate_before_upgrade(conn: sa.Connection) -> None:
    """Five guards. None of them is satisfiable after the DDL, so all run first."""
    _refuse(
        conn,
        reason="steward child run migration refused",
        query="SELECT id FROM agent_runs WHERE session_id IS NULL ORDER BY id LIMIT 21",
        detail="assistant runs must carry a session; NULL session_id has no "
        "pre-migration representation",
    )
    _refuse(
        conn,
        reason="steward child run migration refused",
        query="SELECT id, kind FROM agent_runs "
        "WHERE kind IS NULL OR kind NOT IN ('assistant','steward') "
        "ORDER BY id LIMIT 21",
        detail="agent_runs.kind must be assistant or steward",
    )
    _refuse(
        conn,
        reason="steward child run migration refused",
        query="SELECT id, kind FROM agent_jobs WHERE kind IS NULL OR kind <> 'assistant' "
        "ORDER BY id LIMIT 21",
        detail="agent_jobs is assistant-only; a steward job in the generic queue would "
        "recreate the second queue that 09-01 removed",
    )
    _refuse(
        conn,
        reason="steward child run migration refused",
        query="SELECT id, agent_kind FROM agent_sessions "
        "WHERE agent_kind IS NULL OR agent_kind <> 'assistant' ORDER BY id LIMIT 21",
        detail="agent_sessions is assistant-only; Steward runs must not carry a session",
    )
    _refuse(
        conn,
        reason="steward child run migration refused",
        query="SELECT id FROM agent_runs WHERE job_id IS NOT NULL AND session_id IS NULL "
        "ORDER BY id LIMIT 21",
        detail="a run without a session cannot own a queue job",
    )


def _validate_before_downgrade(conn: sa.Connection) -> None:
    """Refuse to lose child-run evidence by narrowing kind back to assistant-only."""
    _refuse(
        conn,
        reason="steward child run downgrade refused",
        query="SELECT id, kind FROM agent_runs WHERE kind <> 'assistant' ORDER BY id LIMIT 21",
        detail="narrowing agent_runs.kind would drop steward child run evidence; "
        "archive or purge those rows deliberately, then retry",
    )


def _rebuild_agent_runs(conn: sa.Connection, *, allowed: tuple[str, ...]) -> None:
    """Rebuild agent_runs with the new kind CHECK and nullable session_id.

    Only agent_runs is rebuilt: agent_sessions and agent_jobs keep their
    assistant-only CHECK, and their rows are not touched.
    """
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    # The connection-level PRAGMA is restored in `finally`: the migration must
    # not hand the caller a connection whose foreign_keys state it changed.
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        if allowed == ("assistant",):
            kind_check = "kind = 'assistant'"
            scope_check = None
            session_not_null = " NOT NULL"
        else:
            kind_check = "kind IN ('assistant','steward')"
            # assistant requires a session; steward must have neither session nor job.
            scope_check = (
                "(kind = 'assistant' AND session_id IS NOT NULL) OR "
                "(kind = 'steward' AND session_id IS NULL AND job_id IS NULL)"
            )
            session_not_null = ""

        # The scope-binding constraint is only meaningful in the dual-kind
        # shape; the assistant-only rebuild drops it so a downgrade restores the
        # exact pre-0055 schema rather than a table that merely behaves the same.
        scope_clause = (
            ""
            if scope_check is None
            else ",CONSTRAINT ck_agent_runs_scope_binding CHECK (" + scope_check + ")"
        )

        conn.execute(
            sa.text(
                "CREATE TABLE agent_runs_new ("
                "id INTEGER NOT NULL,"
                f"session_id INTEGER{session_not_null},"
                "message_id INTEGER,"
                "job_id INTEGER,"
                "kind VARCHAR(16) NOT NULL CONSTRAINT "
                f"ck_agent_runs_ck_agent_runs_kind CHECK ({kind_check}),"
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
                "first_leased_at DATETIME,"
                "CONSTRAINT pk_agent_runs PRIMARY KEY (id),"
                "CONSTRAINT uq_agent_runs_job_id UNIQUE (job_id),"
                "CONSTRAINT fk_agent_runs_session_id_agent_sessions "
                "FOREIGN KEY(session_id) REFERENCES agent_sessions (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_runs_message_id_agent_messages "
                "FOREIGN KEY(message_id) REFERENCES agent_messages (id) ON DELETE SET NULL,"
                "CONSTRAINT fk_agent_runs_job_id_agent_jobs "
                "FOREIGN KEY(job_id) REFERENCES agent_jobs (id) ON DELETE SET NULL"
                + scope_clause
                + ")"
            )
        )
        conn.execute(
            sa.text(
                f"INSERT INTO agent_runs_new ({_AGENT_RUNS_COLUMNS}) "
                f"SELECT {_AGENT_RUNS_COLUMNS} FROM agent_runs"
            )
        )
        conn.execute(sa.text("DROP TABLE agent_runs"))
        conn.execute(sa.text("ALTER TABLE agent_runs_new RENAME TO agent_runs"))
        conn.execute(sa.text("CREATE INDEX ix_agent_runs_session_id ON agent_runs (session_id)"))
        # Partial unique index is preserved verbatim: NULL session_id values do
        # not collide in SQLite unique indexes, so steward runs are naturally
        # exempt from "one active run per session".
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id) "
                "WHERE status IN ('queued','leased','running')"
            )
        )
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


def upgrade() -> None:
    conn = op.get_bind()
    _validate_before_upgrade(conn)
    _rebuild_agent_runs(conn, allowed=("assistant", "steward"))

    conn.execute(
        sa.text(
            "CREATE TABLE steward_runs ("
            "id INTEGER NOT NULL,"
            "run_id INTEGER NOT NULL,"
            "steward_job_id INTEGER NOT NULL,"
            "assist_batch_id INTEGER,"
            "assist_kind VARCHAR(16) NOT NULL CONSTRAINT "
            f"ck_steward_runs_assist_kind CHECK (assist_kind IN ({_ASSIST_KIND_CHECK})),"
            "viewer_account_id INTEGER,"
            "fence_json JSON NOT NULL DEFAULT '{}',"
            "created_at DATETIME NOT NULL,"
            "CONSTRAINT pk_steward_runs PRIMARY KEY (id),"
            "CONSTRAINT uq_steward_runs_run_id UNIQUE (run_id),"
            "CONSTRAINT fk_steward_runs_run_id_agent_runs "
            "FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,"
            "CONSTRAINT fk_steward_runs_steward_job_id_steward_jobs "
            "FOREIGN KEY(steward_job_id) REFERENCES steward_jobs (id) ON DELETE CASCADE,"
            "CONSTRAINT fk_steward_runs_assist_batch_id_steward_assist_batches "
            "FOREIGN KEY(assist_batch_id) REFERENCES steward_assist_batches (id) "
            "ON DELETE SET NULL,"
            "CONSTRAINT fk_steward_runs_viewer_account_id_accounts "
            "FOREIGN KEY(viewer_account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
            # viewer is meaningful for terminology only; the other kinds are
            # space-scoped or recipient-grouped and must not forge a viewer.
            "CONSTRAINT ck_steward_runs_viewer CHECK ("
            "(assist_kind = 'terminology') = (viewer_account_id IS NOT NULL))"
            ")"
        )
    )
    conn.execute(sa.text("CREATE INDEX ix_steward_runs_job ON steward_runs (steward_job_id)"))
    conn.execute(sa.text("CREATE INDEX ix_steward_runs_batch ON steward_runs (assist_batch_id)"))
    conn.execute(sa.text("CREATE INDEX ix_steward_runs_viewer ON steward_runs (viewer_account_id)"))

    # Ledger column: ON DELETE SET NULL, not CASCADE — the attempt ledger must
    # outlive the execution record (prompt digest / tokens / status / error code
    # stay traceable after the child run is pruned). Historical rows keep NULL
    # and are read as "in-process era"; never backfilled.
    conn.execute(
        sa.text(
            "ALTER TABLE steward_model_calls ADD COLUMN run_id INTEGER "
            "REFERENCES agent_runs (id) ON DELETE SET NULL"
        )
    )
    conn.execute(sa.text("CREATE INDEX ix_smc_run ON steward_model_calls (run_id)"))


def downgrade() -> None:
    connection = op.get_bind()
    # 拒绝合同必须先于本迁移的任何 DDL：SQLite 的 DROP TABLE 不保证事务回滚，
    # 先降本迁移再由祖先拒绝会留下半降级 schema（与 0049..0054 同一约定）。
    #
    # 本迁移既是结构迁移（重建 agent_runs / 删 steward_runs）又要跨过祖先的
    # 拒绝合同，所以顺序是：① 自己的 kind 守卫（能否表达降级结果）② 走位歧义
    # 与祖先守卫 ③ 才动 DDL。
    _validate_before_downgrade(connection)
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None:
        assert down_revision is not None
        # 走位从**父 revision** 开始：深层相对目标（如 -7）在祖先上歧义，Alembic
        # 会在那里抛 "Ambiguous walk"；若先降本迁移再由祖先报错，就留下半降级
        # schema。`iterate_revisions` 是惰性生成器，必须消费才真正走位。
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        for revision in planned:
            list(context.script.iterate_revisions(revision, destination, select_for_downgrade=True))
        if destination != down_revision:
            timing_guard = context.script.get_revision("0052_seed_lineage_membership_boundary")
            assert timing_guard is not None
            timing_guard.module._refuse_if_timing_evidence(connection)
        if "0049_steward_candidate_evidence" in planned:
            evidence_guard = context.script.get_revision("0050_term_alias_spouse_fix")
            assert evidence_guard is not None
            evidence_guard.module._refuse_if_candidate_evidence(connection)
        if "0048_steward_terminology_publication" in planned:
            merge_guard = context.script.get_revision("0048_steward_terminology_publication")
            assert merge_guard is not None
            merge_guard.module._preflight_parent_downgrade(planned=planned)

    conn = connection
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_smc_run"))
    # SQLite cannot drop a column in place on older engines; rebuild the table
    # to restore the exact pre-0055 shape.
    _drop_model_calls_run_id(conn)
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_steward_runs_viewer"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_steward_runs_batch"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_steward_runs_job"))
    conn.execute(sa.text("DROP TABLE IF EXISTS steward_runs"))
    _rebuild_agent_runs(conn, allowed=("assistant",))


def _drop_model_calls_run_id(conn: sa.Connection) -> None:
    """Rebuild steward_model_calls without run_id, preserving every other column."""
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(
            sa.text(
                "CREATE TABLE steward_model_calls_new ("
                "id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                "job_id INTEGER NOT NULL,"
                "policy_version VARCHAR(32) NOT NULL,"
                "assist_kind VARCHAR(16) NOT NULL,"
                "provider_id INTEGER,"
                "model VARCHAR(120),"
                "prompt_digest VARCHAR(64) NOT NULL,"
                "prompt_chars INTEGER NOT NULL,"
                "completion_chars INTEGER DEFAULT '0' NOT NULL,"
                "prompt_tokens INTEGER,"
                "completion_tokens INTEGER,"
                "total_tokens INTEGER,"
                "status VARCHAR(16) DEFAULT 'succeeded' NOT NULL,"
                "error_code VARCHAR(64),"
                "latency_ms INTEGER,"
                "seq INTEGER DEFAULT '1' NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "batch_id INTEGER,"
                "subject_key VARCHAR(200),"
                "input_hash VARCHAR(64),"
                "attempt_no INTEGER DEFAULT '1' NOT NULL,"
                "reserved_input_tokens INTEGER,"
                "reserved_output_tokens INTEGER,"
                "billed_tokens INTEGER,"
                "response_bytes INTEGER,"
                "output_json JSON,"
                "viewer_account_id INTEGER,"
                "CONSTRAINT pk_steward_model_calls PRIMARY KEY (id),"
                "CONSTRAINT uq_smc_job_kind_seq UNIQUE (job_id, assist_kind, seq),"
                "CONSTRAINT ck_steward_model_calls_ck_smc_status CHECK "
                "(status IN ('succeeded','failed','degraded','skipped','reserved',"
                "'in_flight','unknown')),"
                "CONSTRAINT ck_steward_model_calls_ck_smc_assist_kind CHECK "
                f"(assist_kind IN ({_ASSIST_KIND_CHECK})),"
                "CONSTRAINT fk_steward_model_calls_provider_id_agent_providers "
                "FOREIGN KEY(provider_id) REFERENCES agent_providers (id) ON DELETE SET NULL,"
                "CONSTRAINT fk_steward_model_calls_job_id_steward_jobs "
                "FOREIGN KEY(job_id) REFERENCES steward_jobs (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_steward_model_calls_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_smc_batch FOREIGN KEY(batch_id) "
                "REFERENCES steward_assist_batches (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_steward_model_calls_viewer_account_id_accounts "
                "FOREIGN KEY(viewer_account_id) REFERENCES accounts (id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO steward_model_calls_new ("
                "id, space_id, job_id, policy_version, assist_kind, provider_id, model, "
                "prompt_digest, prompt_chars, completion_chars, prompt_tokens, "
                "completion_tokens, total_tokens, status, error_code, latency_ms, seq, "
                "created_at, batch_id, subject_key, input_hash, attempt_no, "
                "reserved_input_tokens, reserved_output_tokens, billed_tokens, "
                "response_bytes, output_json, viewer_account_id) "
                "SELECT id, space_id, job_id, policy_version, assist_kind, provider_id, model, "
                "prompt_digest, prompt_chars, completion_chars, prompt_tokens, "
                "completion_tokens, total_tokens, status, error_code, latency_ms, seq, "
                "created_at, batch_id, subject_key, input_hash, attempt_no, "
                "reserved_input_tokens, reserved_output_tokens, billed_tokens, "
                "response_bytes, output_json, viewer_account_id "
                "FROM steward_model_calls"
            )
        )
        conn.execute(sa.text("DROP TABLE steward_model_calls"))
        conn.execute(sa.text("ALTER TABLE steward_model_calls_new RENAME TO steward_model_calls"))
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_smc_attempt_key ON steward_model_calls "
                "(job_id, assist_kind, subject_key, input_hash, attempt_no)"
            )
        )
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))
