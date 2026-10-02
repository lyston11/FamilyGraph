"""Steward 模型辅助：执行单元从「批次」重构为「一次模型调用」（09-25 E1）。

## 为什么重构

旧结构把**一次模型调用**的状态拆在两张表：`steward_assist_batches` 持有
`status`/`attempt`/`lease_owner`/`lease_until`/`next_attempt_at`，而
`steward_model_calls` 持有 `status`/`output_json`/`billed_tokens`。三个后果都是结构性的：

1. **并发被压成全库 1**：`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 是全局上限，选批
   SQL 无 `space_id` 过滤。20 个空间同一时刻只有一个批次在跑，空间之间互相阻塞；
   一个批次内最多 6 次模型调用串行发送。
2. **两条执行路径各维护全部状态**：in-process（`execute_batch` 的 tx1/tx2/tx3）与
   Pi child run（`lease_child_run`/`settle_child_run`）各自读写同一组字段，共用判定点
   （fence/reserve/apply）被各调一遍，任何一侧改动都要在另一侧同步。
3. **执行单元选错**：`_apply_batch` 自己按 attempt 循环、逐条 `continue`，证明 attempt
   之间本来就是独立的。唯一的跨 attempt 耦合是 fence，而 fence 的输入
   （`evidence_hash`/`policy_version`/provider 身份/卡片 revision/terminology 语义哈希）
   **全是空间级**的，不是批次级。

## 重构后的结构

- `steward_model_calls` 是**唯一执行单元**：自带 `lease_owner`/`lease_until`/
  `next_attempt_at`/`carrier`，以及它需要的那一片 fence 快照。
- `steward_assist_batches` **删除**：`fence_json` 的内容本来就是 per-kind 切片
  （`ranking_groups`/`explain_ids`/`terminology_groups`/`cards`），拆到各 attempt 上
  不需要额外的分组表；`job_id` 与 `space_id` 已经在 attempt 上。
- 并发按空间：`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`。

## 保留的资产（来自第一轮的 0055_steward_child_run）

`agent_runs` 的 kind 扩展与 `ck_agent_runs_scope_binding`、`context_builds.account_id`
可空 + `ck_context_builds_account_binding`。S1 另建的 `steward_runs` 窄表**不需要**：
`job_id`/`viewer_account_id`/`assist_kind` 都已是 attempt 的列，而
`steward_model_calls.run_id`（UNIQUE）就是 run → attempt 的反查键，再加一张表只是
多一处要保持同步。

## SQLite 手法（每条都实测过，不是推测）

- `ALTER TABLE ... DROP COLUMN` 在**有 trigger 引用该列时拒绝**执行
  （`error in trigger trg: no such column: OLD.x`）。因此顺序必须是：先 drop 引用它的
  trigger，再 drop 列，最后 drop 目标表。
- `ALTER TABLE ... RENAME COLUMN` 会**重写** trigger 体与 FK 子句里的列名（SQLite ≥
  3.25；本项目 3.51）。改名因此不需要重建表——这一点关键：重建表会让 SQLAlchemy 的
  批量反射重新推导约束名，破坏更早迁移的 `drop_constraint`。
- 0049 的 `trg_scev_immutable` 引用了 `source_batch_id`，删该列前必须 drop trigger，
  之后按 0049 的原文重建（去掉 `source_batch_id` 分支）。

安全：refusal guards 在**任何 DDL 或版本移动之前**执行；命中即中止并报告表/行/值。
downgrade 同样先跑 guard，绝不静默删除已产生的 child run 证据行（memory #399）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_steward_assist_execution_unit"
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

# 0049 建的表与 trigger：本迁移要删它的 source_batch_id 列，必须先 drop trigger。
_EVIDENCE_TABLE = "steward_candidate_evidence_versions"
_EVIDENCE_TRIGGER = "trg_scev_immutable"
_EVIDENCE_IMMUTABLE = (
    "id",
    "candidate_id",
    "space_id",
    "validation_contract_version",
    "evidence_digest",
    "support_facts_json",
    "created_at",
)


def _refuse(conn: sa.Connection, *, reason: str, query: str, detail: str) -> None:
    """Any hit aborts before DDL, reporting the offending rows."""
    rows = conn.execute(sa.text(query)).mappings().all()
    if rows:
        rendered = ", ".join(
            "[" + ", ".join(f"{key}={value!r}" for key, value in row.items()) + "]" for row in rows
        )
        raise RuntimeError(f"{reason}: {detail}; conflicting rows: {rendered}")


def _relax_context_builds_account(conn: sa.Connection) -> None:
    """Make context_builds.account_id nullable for space-scoped steward runs.

    The design assumed no context_builds change was needed ("steward run 也是
    agent_runs 行"). That is true for the FK, but ``account_id`` is NOT NULL, and
    a steward child run has no account — only an optional viewer. Without this
    the steward context build cannot be recorded at all.

    Assistant builds keep a non-null account: the invariant is re-expressed as a
    CHECK keyed on the run's kind, so relaxing the column cannot let an
    assistant build lose its account.
    """
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(
            sa.text(
                "CREATE TABLE context_builds_new ("
                "id INTEGER NOT NULL,"
                "run_id INTEGER NOT NULL,"
                "account_id INTEGER,"
                "space_id INTEGER NOT NULL,"
                "agent_kind VARCHAR(16) NOT NULL,"
                "query_hash VARCHAR(64) NOT NULL,"
                "policy_version VARCHAR(64) NOT NULL,"
                "token_budget INTEGER NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "attempt INTEGER,"
                "blocks_json JSON,"
                "policy_json JSON,"
                "invalidated_at DATETIME,"
                "invalidation_reason VARCHAR(64),"
                "CONSTRAINT pk_context_builds PRIMARY KEY (id),"
                "CONSTRAINT ck_context_builds_account_binding CHECK ("
                "(agent_kind = 'assistant' AND account_id IS NOT NULL) OR "
                "(agent_kind = 'steward' AND account_id IS NULL)),"
                "CONSTRAINT fk_context_builds_run_id_agent_runs "
                "FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_context_builds_account_id_accounts "
                "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_context_builds_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO context_builds_new ("
                "id, run_id, account_id, space_id, agent_kind, query_hash, "
                "policy_version, token_budget, created_at, attempt, blocks_json, "
                "policy_json, invalidated_at, invalidation_reason) "
                "SELECT id, run_id, account_id, space_id, agent_kind, query_hash, "
                "policy_version, token_budget, created_at, attempt, blocks_json, "
                "policy_json, invalidated_at, invalidation_reason FROM context_builds"
            )
        )
        conn.execute(sa.text("DROP TABLE context_builds"))
        conn.execute(sa.text("ALTER TABLE context_builds_new RENAME TO context_builds"))
        conn.execute(
            sa.text("CREATE INDEX ix_context_builds_run ON context_builds (run_id, created_at)")
        )
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX ix_context_builds_run_attempt "
                "ON context_builds (run_id, attempt)"
            )
        )
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


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


def _rebuild_context_builds_strict(conn: sa.Connection) -> None:
    """Restore context_builds.account_id NOT NULL (pre-0055 shape)."""
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(
            sa.text(
                "CREATE TABLE context_builds_new ("
                "id INTEGER NOT NULL,"
                "run_id INTEGER NOT NULL,"
                "account_id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                "agent_kind VARCHAR(16) NOT NULL,"
                "query_hash VARCHAR(64) NOT NULL,"
                "policy_version VARCHAR(64) NOT NULL,"
                "token_budget INTEGER NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "attempt INTEGER,"
                "blocks_json JSON,"
                "policy_json JSON,"
                "invalidated_at DATETIME,"
                "invalidation_reason VARCHAR(64),"
                "CONSTRAINT pk_context_builds PRIMARY KEY (id),"
                "CONSTRAINT fk_context_builds_run_id_agent_runs "
                "FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_context_builds_account_id_accounts "
                "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_context_builds_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO context_builds_new ("
                "id, run_id, account_id, space_id, agent_kind, query_hash, "
                "policy_version, token_budget, created_at, attempt, blocks_json, "
                "policy_json, invalidated_at, invalidation_reason) "
                "SELECT id, run_id, account_id, space_id, agent_kind, query_hash, "
                "policy_version, token_budget, created_at, attempt, blocks_json, "
                "policy_json, invalidated_at, invalidation_reason FROM context_builds"
            )
        )
        conn.execute(sa.text("DROP TABLE context_builds"))
        conn.execute(sa.text("ALTER TABLE context_builds_new RENAME TO context_builds"))
        conn.execute(
            sa.text("CREATE INDEX ix_context_builds_run ON context_builds (run_id, created_at)")
        )
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX ix_context_builds_run_attempt "
                "ON context_builds (run_id, attempt)"
            )
        )
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


def _validate_before_upgrade(conn: sa.Connection) -> None:
    """Six guards. None is satisfiable after the DDL, so all run first."""
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id FROM agent_runs WHERE session_id IS NULL ORDER BY id LIMIT 21",
        detail="assistant runs must carry a session; a NULL session_id has no "
        "pre-migration representation",
    )
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id, kind FROM agent_runs "
        "WHERE kind IS NULL OR kind NOT IN ('assistant','steward') ORDER BY id LIMIT 21",
        detail="agent_runs.kind must be assistant or steward",
    )
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id, kind FROM agent_jobs WHERE kind IS NULL OR kind <> 'assistant' "
        "ORDER BY id LIMIT 21",
        detail="agent_jobs is assistant-only; a steward job in the generic queue would "
        "recreate the second queue that 09-01 removed",
    )
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id, agent_kind FROM agent_sessions "
        "WHERE agent_kind IS NULL OR agent_kind <> 'assistant' ORDER BY id LIMIT 21",
        detail="agent_sessions is assistant-only; Steward runs must not carry a session",
    )
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id FROM agent_runs WHERE job_id IS NOT NULL AND session_id IS NULL "
        "ORDER BY id LIMIT 21",
        detail="a run without a session cannot own a queue job",
    )
    # The lease moves from the batch to the attempt. An attempt already in flight
    # has no lease to inherit (its lease lives on the batch being dropped), so it
    # could never be reclaimed; refuse rather than guess a recovery path.
    _refuse(
        conn,
        reason="steward execution-unit migration refused",
        query="SELECT id, assist_kind FROM steward_model_calls "
        "WHERE status = 'in_flight' ORDER BY id LIMIT 21",
        detail="an in-flight attempt cannot survive the lease move; stop the assist "
        "executor and let recovery settle it, then retry",
    )


def _refuse_if_memory_provenance(conn: sa.Connection) -> None:
    """Mirror 0042's own refusal contract for preflight.

    Inline in 0042's downgrade body, so there is no callable to import; the
    identical condition is restated here (same pattern as 0050 mirroring 0049).
    Without it a deep downgrade would run this migration's DDL and *then* be
    refused by 0042, leaving a half-downgraded schema.
    """
    count = int(
        conn.scalar(
            sa.text(
                "SELECT (SELECT count(*) FROM memories) + "
                "(SELECT count(*) FROM memory_candidates)"
            )
        )
        or 0
    )
    if count:
        raise RuntimeError(
            "Memory source downgrade would discard provenance/history; "
            "keep the data and forward-fix, or make an explicit data decision"
        )


def _refuse_if_rag_evidence(conn: sa.Connection) -> None:
    """Mirror 0045's and 0047's own refusal contracts for preflight.

    Both are **inline in their downgrade bodies**, so there is no callable to
    import (and rewriting an applied revision is not allowed). The identical
    conditions are restated here — the same pattern 0050 uses for 0049, and for
    the same reason: this migration runs DDL, so without preflighting them a deep
    downgrade would modify the schema and *then* be refused by 0045/0047, leaving
    a half-downgraded database. The three must stay in sync.
    """
    # 0047: RAG integrity evidence or a non-default maintenance target.
    if conn.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM rag_documents WHERE content_sha256 IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM rag_index_maintenance_state "
            "WHERE target_index_version != 'fts5-trigram-v2' "
            "OR policy_version NOT IN ('rag-index-maint-v1', 'rag-index-maint-v2'))"
        )
    ):
        raise RuntimeError(
            "Cannot discard RAG integrity evidence/target policy; retain data and roll forward"
        )
    # 0045: historical chunks, key collisions, distinct reasons, saved dependencies.
    incompatible = conn.scalar(
        sa.text(
            "SELECT COUNT(*) FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            "WHERE c.index_version != d.index_version"
        )
    )
    collisions = conn.scalar(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT document_id, chunk_index FROM rag_chunks "
            "GROUP BY document_id, chunk_index HAVING COUNT(*) > 1)"
        )
    )
    reasons = conn.scalar(
        sa.text(
            "SELECT COUNT(*) FROM rag_documents WHERE invalidation_reason IS NOT NULL "
            "AND invalidation_reason != 'source_invalidated'"
        )
    )
    if incompatible or collisions or reasons:
        dependencies = conn.scalar(
            sa.text("SELECT COUNT(*) FROM memories WHERE source_kind = 'rag_chunk'")
        )
        raise RuntimeError(
            "Cannot losslessly downgrade RAG lifecycle: "
            f"historical_chunks={incompatible}, key_collisions={collisions}, "
            f"distinct_reasons={reasons}, saved_dependencies={dependencies}; "
            "retain chunks and roll forward"
        )


def _validate_before_downgrade(conn: sa.Connection) -> None:
    """Refuse to lose child-run evidence by narrowing kind back to assistant-only."""
    _refuse(
        conn,
        reason="steward execution-unit downgrade refused",
        query="SELECT id, kind FROM agent_runs WHERE kind <> 'assistant' ORDER BY id LIMIT 21",
        detail="narrowing agent_runs.kind would drop steward child run evidence; "
        "archive or purge those rows deliberately, then retry",
    )
    _refuse(
        conn,
        reason="steward execution-unit downgrade refused",
        query="SELECT id FROM context_builds WHERE agent_kind <> 'assistant' ORDER BY id LIMIT 21",
        detail="narrowing context_builds.account_id would break steward context builds; "
        "archive or purge those rows deliberately, then retry",
    )


def _drop_evidence_trigger(conn: sa.Connection) -> None:
    """Drop the 0049 immutability trigger before dropping the column it reads.

    SQLite refuses ``DROP COLUMN`` when a trigger body references the column
    ("error in trigger trg_scev_immutable: no such column: OLD.source_batch_id"),
    so the trigger comes down first and is rebuilt afterwards.
    """
    conn.execute(sa.text(f"DROP TRIGGER IF EXISTS {_EVIDENCE_TRIGGER}"))


def _create_evidence_trigger(conn: sa.Connection, *, source_column: str) -> None:
    """Rebuild the 0049 immutability trigger naming the third source column.

    Reproduced from 0049 verbatim apart from that one name: upstream it is
    ``source_batch_id``, after this migration it is ``source_plan_id``. The
    original guards three nullable source FKs — a source may become NULL, but a
    replacement may not be written. Passing the name in (rather than a boolean)
    keeps the two call sites honest: a wrong guess here produces a trigger that
    aborts every write.
    """
    conditions = [f"OLD.{column} IS NOT NEW.{column}" for column in _EVIDENCE_IMMUTABLE]
    sources = ["source_job_id", "source_model_call_id", source_column]
    for column in sources:
        conditions.append(f"(OLD.{column} IS NOT NEW.{column} AND NEW.{column} IS NOT NULL)")
    conditions.append(
        "(OLD.status != 'pending' AND (OLD.status IS NOT NEW.status "
        "OR OLD.projection_checked_at IS NOT NEW.projection_checked_at "
        "OR OLD.invalidation_reason IS NOT NEW.invalidation_reason "
        "OR (OLD.projection_job_id IS NOT NEW.projection_job_id "
        "AND NEW.projection_job_id IS NOT NULL)))"
    )
    conn.execute(
        sa.text(
            f"CREATE TRIGGER {_EVIDENCE_TRIGGER} BEFORE UPDATE ON {_EVIDENCE_TABLE} WHEN "
            + " OR ".join(conditions)
            + " BEGIN SELECT RAISE(ABORT, 'candidate evidence is immutable'); END"
        )
    )


def _narrow_plan_table(conn: sa.Connection) -> None:
    """Turn the batch row into an immutable plan snapshot.

    The batch used to be the execution unit, so it carried ``status``/``attempt``/
    ``lease_owner``/``lease_until``/``next_attempt_at``/``error_code``. All of
    those move to the attempt; what remains is the *plan*: which kinds this job
    has work for, and the evidence snapshot to fence against.

    Two SQLite constraints force the shape of this function:

    - ``status`` appears in a **table-level** CHECK, and SQLite cannot drop a
      column that a table-level constraint references. The table is therefore
      rebuilt rather than narrowed in place.
    - Dropping the old table while foreign keys are ON **cascades** and deletes
      every ``steward_model_calls`` row pointing at it — the entire attempt
      ledger. Foreign keys are switched off for the rebuild (the same technique
      the agent_runs rebuild already uses) so the rows survive.

    The rename happens first so that the child FK clause is rewritten to the new
    name before the rebuild, keeping the ledger's reference intact.
    """
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(sa.text("ALTER TABLE steward_assist_batches RENAME TO steward_assist_plans"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_steward_assist_batches_due"))
        conn.execute(
            sa.text(
                "CREATE TABLE steward_assist_plans_new ("
                "id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                "job_id INTEGER NOT NULL,"
                "evidence_hash VARCHAR(64) NOT NULL,"
                "policy_version VARCHAR(32) NOT NULL,"
                "fence_json JSON NOT NULL,"
                "deadline_at DATETIME NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "CONSTRAINT pk_steward_assist_plans PRIMARY KEY (id),"
                "CONSTRAINT uq_sap_job UNIQUE (job_id),"
                "CONSTRAINT fk_sap_space FOREIGN KEY(space_id) REFERENCES family_spaces (id) "
                "ON DELETE CASCADE,"
                "CONSTRAINT fk_sap_job FOREIGN KEY(job_id) REFERENCES steward_jobs (id) "
                "ON DELETE CASCADE"
                ")"
            )
        )
        # The old batch lease was the plan's whole wall-clock bound, so deriving
        # deadline_at from created_at preserves the semantics existing rows were
        # built under instead of inventing a new one.
        #
        # The TTL is a **historical literal**, deliberately not read from config:
        # it describes what the rows being migrated were created under, so reading
        # live config would make this migration produce different results after any
        # future default change (and it did break the moment the key was renamed).
        # SQLite cannot bind a parameter inside a datetime() modifier, hence the
        # interpolated literal.
        ttl = 120  # STEWARD_ASSIST_BATCH_LEASE_SECONDS as it stood when 0055 shipped
        conn.execute(
            sa.text(
                "INSERT INTO steward_assist_plans_new "
                "(id, space_id, job_id, evidence_hash, policy_version, fence_json, "
                "deadline_at, created_at) "
                "SELECT id, space_id, job_id, evidence_hash, policy_version, fence_json, "
                f"datetime(created_at, '+{ttl} seconds'), created_at "
                "FROM steward_assist_plans"
            )
        )
        conn.execute(sa.text("DROP TABLE steward_assist_plans"))
        conn.execute(sa.text("ALTER TABLE steward_assist_plans_new RENAME TO steward_assist_plans"))
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


def _add_applied_at_column(conn: sa.Connection) -> None:
    """Add crash point ④'s marker while the batch status is still readable.

    ``status`` records the *call* result, not the *application* result, so
    without this column a crash between the two loses the product permanently.
    It is added ahead of the attempt's other new columns because
    ``_backfill_closed_applied_at`` reads ``steward_assist_batches.status``,
    which ``_narrow_plan_table`` drops.
    """
    conn.execute(sa.text("ALTER TABLE steward_model_calls ADD COLUMN applied_at DATETIME"))


def _backfill_closed_applied_at(conn: sa.Connection) -> None:
    """Close the write-back phase for every attempt the old executor finished.

    The in-process executor owned the whole apply step: it applied the products
    and only afterwards wrote the batch's terminal status. A terminal batch
    therefore means the write-back is over — applied (``applied``), applied
    alongside a failed sibling (``failed``), or deliberately not applied because
    the fence rejected it (``superseded``).

    Leaving those rows at ``applied_at IS NULL`` makes them indistinguishable
    from crash point ④ ("product persisted, write-back not done"), so the first
    recovery pass after deploy would re-fence and rewrite the whole in-process
    ledger — paid-for history turned into ``skipped`` rows for no reason.

    A batch still ``leased``/``applying`` is genuinely unfinished and keeps NULL
    so recovery completes it.
    """
    conn.execute(
        sa.text(
            "UPDATE steward_model_calls SET applied_at = COALESCE("
            "(SELECT b.updated_at FROM steward_assist_batches b WHERE b.id = batch_id),"
            "created_at) "
            "WHERE batch_id IN ("
            "SELECT id FROM steward_assist_batches "
            "WHERE status IN ('applied','failed','superseded'))"
        )
    )


def _create_execution_unit_columns(conn: sa.Connection) -> None:
    """Move the lease onto the attempt and give it the fields it now owns.

    ``batch_id`` is renamed rather than re-added: the attempt already knew which
    plan it belonged to. A rename preserves the rows and lets SQLite rewrite the
    FK clause in place — no table rebuild, so no constraint-name drift (a rebuild
    here previously broke 0044's downgrade).
    """
    conn.execute(sa.text("ALTER TABLE steward_model_calls RENAME COLUMN batch_id TO plan_id"))
    # Lease: was on the batch, now per attempt. This is what makes concurrency
    # per-space instead of global-1.
    conn.execute(sa.text("ALTER TABLE steward_model_calls ADD COLUMN lease_owner VARCHAR(120)"))
    conn.execute(sa.text("ALTER TABLE steward_model_calls ADD COLUMN lease_until DATETIME"))
    conn.execute(sa.text("ALTER TABLE steward_model_calls ADD COLUMN next_attempt_at DATETIME"))
    # Which executor runs this attempt. Data, not a branch: the single dispatch
    # point reads it instead of the scheduler writing `if kind == ...`.
    conn.execute(
        sa.text(
            "ALTER TABLE steward_model_calls ADD COLUMN carrier VARCHAR(16) "
            "NOT NULL DEFAULT 'inproc' CHECK (carrier IN ('inproc','pi'))"
        )
    )
    # The evidence digest this attempt fences on. The card/terminology slices it
    # also needs stay on the plan, which is immutable — copying them per attempt
    # would duplicate the same bytes for every call in a job.
    conn.execute(sa.text("ALTER TABLE steward_model_calls ADD COLUMN evidence_hash VARCHAR(64)"))
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
    # UNIQUE, not a plain index: one child run maps to exactly one attempt row. A
    # shared run across two attempts would make settlement ambiguous about which
    # ledger row the run's outcome belongs to. NULLs do not collide in SQLite, so
    # the in-process era rows are unaffected.
    conn.execute(sa.text("CREATE UNIQUE INDEX uq_smc_run_id ON steward_model_calls (run_id)"))
    conn.execute(
        sa.text(
            "CREATE INDEX ix_smc_due ON steward_model_calls (space_id, status, next_attempt_at)"
        )
    )


def _rename_batch_reference(conn: sa.Connection) -> None:
    """Repoint the candidate-evidence column at the plan table.

    Deliberately a **rename**, not a drop: 0049 declares that FK in a
    table-level ``CONSTRAINT`` clause, and SQLite refuses ``DROP COLUMN`` for any
    column named in one ("unknown column ... in foreign key definition"). A
    rename is accepted and SQLite rewrites both the FK clause and the immutability
    trigger body, so the reference stays valid and no table rebuild is needed.

    The column is not dead weight: it names which plan produced a candidate's
    evidence, which is provenance the audit path reads.
    """
    _drop_evidence_trigger(conn)
    conn.execute(
        sa.text(f"ALTER TABLE {_EVIDENCE_TABLE} RENAME COLUMN source_batch_id TO source_plan_id")
    )
    _create_evidence_trigger(conn, source_column="source_plan_id")


def _restore_batch_table(conn: sa.Connection) -> None:
    """Turn the plan snapshot back into the 0037 batch table, with its rows.

    Faithful inverse: same columns, same named constraints, same index. The plan
    row is the batch row (it was renamed, never recreated), so the execution
    status is re-derived from the attempts rather than invented — a batch whose
    attempts are all terminal is ``applied``/``failed``, and one with work left is
    ``pending``. ``fence_json`` and ``evidence_hash`` carry over verbatim.
    """
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(sa.text("ALTER TABLE steward_assist_plans RENAME TO steward_assist_batches"))
        conn.execute(
            sa.text(
                "CREATE TABLE steward_assist_batches_new ("
                "id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                "job_id INTEGER NOT NULL,"
                "evidence_hash VARCHAR(64) NOT NULL,"
                "policy_version VARCHAR(32) NOT NULL,"
                "status VARCHAR(16) DEFAULT 'pending' NOT NULL,"
                "attempt INTEGER DEFAULT '0' NOT NULL,"
                "next_attempt_at DATETIME,"
                "lease_owner VARCHAR(120),"
                "lease_until DATETIME,"
                "fence_json JSON NOT NULL,"
                "error_code VARCHAR(64),"
                "created_at DATETIME NOT NULL,"
                "updated_at DATETIME NOT NULL,"
                "CONSTRAINT ck_sab_status CHECK "
                "(status IN ('pending','leased','applying','applied','failed','superseded')),"
                "CONSTRAINT pk_steward_assist_batches PRIMARY KEY (id),"
                "CONSTRAINT uq_sab_job UNIQUE (job_id),"
                "CONSTRAINT fk_sab_space FOREIGN KEY(space_id) REFERENCES family_spaces (id) "
                "ON DELETE CASCADE,"
                "CONSTRAINT fk_sab_job FOREIGN KEY(job_id) REFERENCES steward_jobs (id) "
                "ON DELETE CASCADE"
                ")"
            )
        )
        # status/attempt/next_attempt_at are re-derived from the ledger, not
        # guessed: a plan whose attempts are all terminal becomes a terminal
        # batch, otherwise it is pending and schedulable again.
        conn.execute(
            sa.text(
                "INSERT INTO steward_assist_batches_new "
                "(id, space_id, job_id, evidence_hash, policy_version, status, attempt, "
                "next_attempt_at, lease_owner, lease_until, fence_json, error_code, "
                "created_at, updated_at) "
                "SELECT p.id, p.space_id, p.job_id, p.evidence_hash, p.policy_version, "
                "CASE "
                "  WHEN NOT EXISTS (SELECT 1 FROM steward_model_calls c "
                "                   WHERE c.plan_id = p.id "
                "                   AND c.status IN ('reserved','in_flight')) "
                "  THEN 'applied' "
                "  ELSE 'pending' "
                "END, "
                "COALESCE((SELECT MAX(c.attempt_no) FROM steward_model_calls c "
                "          WHERE c.plan_id = p.id), 0), "
                "p.created_at, NULL, NULL, p.fence_json, NULL, p.created_at, p.created_at "
                "FROM steward_assist_batches p"
            )
        )
        conn.execute(sa.text("DROP TABLE steward_assist_batches"))
        conn.execute(
            sa.text("ALTER TABLE steward_assist_batches_new RENAME TO steward_assist_batches")
        )
        conn.execute(
            sa.text(
                "CREATE INDEX ix_steward_assist_batches_due ON steward_assist_batches "
                "(status, next_attempt_at)"
            )
        )
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))
    _drop_evidence_trigger(conn)
    conn.execute(
        sa.text(f"ALTER TABLE {_EVIDENCE_TABLE} RENAME COLUMN source_plan_id TO source_batch_id")
    )
    _create_evidence_trigger(conn, source_column="source_batch_id")


def upgrade() -> None:
    conn = op.get_bind()
    _validate_before_upgrade(conn)

    # --- agent_runs: dual kind + DB-enforced scope binding (S1 asset) ---
    _rebuild_agent_runs(conn, allowed=("assistant", "steward"))
    _relax_context_builds_account(conn)

    # --- the attempt becomes the execution unit ---
    # The historical-write-back marker is written while the batch still carries
    # its status: `_narrow_plan_table` drops that column, and without the marker
    # every in-process-era row matches crash point ④ and gets rewritten.
    _add_applied_at_column(conn)
    _backfill_closed_applied_at(conn)
    _narrow_plan_table(conn)
    _create_execution_unit_columns(conn)
    _rename_batch_reference(conn)


def downgrade() -> None:
    connection = op.get_bind()
    # 拒绝合同必须先于本迁移的任何 DDL：SQLite 的 DROP TABLE 不保证事务回滚，
    # 先降本迁移再由祖先拒绝会留下半降级 schema（与 0049..0054 同一约定）。
    _validate_before_downgrade(connection)

    context = op.get_context()
    destination = context.opts.get("destination_rev")
    planned: set[str] = set()
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
        # 直接祖先的自身拒绝合同也要在此提前履行（0051/0053 的守卫在本迁移之后
        # 才执行，而本迁移已经动了 DDL）。每个守卫由**其后继**唯一镜像表达，沿用
        # 该约定而不在此重写判定：
        #   - 0049 的候选证据守卫 → 0050 mirror；
        #   - 0051 的源计时守卫 → 0052 mirror。
        # 之前这里两个分支都指向 0052，于是「候选证据存在」的拒绝从未被前置，
        # 本迁移先删列、再由 0050 拒绝，留下半降级 schema（测试用 DDL_COUNT 抓住）。
        if "0049_candidate_evidence_versions" in planned or destination != down_revision:
            candidate_guard = context.script.get_revision("0050_term_alias_spouse_fix")
            assert candidate_guard is not None
            candidate_guard.module._refuse_if_candidate_evidence(connection)
        if "0051_run_event_timing" in planned or destination != down_revision:
            timing_guard = context.script.get_revision("0052_seed_lineage_membership_boundary")
            assert timing_guard is not None
            timing_guard.module._refuse_if_timing_evidence(connection)
        # Ancestor refusals that cannot be imported (they are inline in 0045/0047's
        # downgrade bodies) must still precede this migration's DDL, or a deep
        # downgrade leaves a half-modified schema before the ancestor rejects it.
        # Gated on ``planned``: running them unconditionally would refuse every
        # downgrade, including ones that never touch RAG.
        if {
            "0045_rag_index_lifecycle",
            "0047_rag_lifecycle_integrity",
        } & planned:
            _refuse_if_rag_evidence(connection)
        if "0042_memory_source_contract" in planned:
            _refuse_if_memory_provenance(connection)
        if "0053_member_approval_and_labels" in planned:
            if connection.scalar(
                sa.text("SELECT 1 FROM space_member_approvals LIMIT 1")
            ) or connection.scalar(sa.text("SELECT 1 FROM member_relation_labels LIMIT 1")):
                raise RuntimeError(
                    "owner-approval or relation-label evidence exists; "
                    "retain data and roll forward"
                )

    conn = connection
    # Attempts that only exist in the new shape have no representation in the old
    # one. Converge them before dropping the columns that describe them, so no
    # row is silently lost and no in-flight attempt claims to still be running.
    conn.execute(
        sa.text(
            "UPDATE steward_model_calls SET status = 'unknown', "
            "error_code = COALESCE(error_code, 'carrier_removed') "
            "WHERE status = 'in_flight'"
        )
    )
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_smc_run_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_smc_due"))
    # Both restores read ``run_id`` / ``plan_id``, so they run BEFORE those
    # columns are dropped or renamed back. ``_restore_batch_table`` also recreates
    _restore_batch_table(conn)
    for column in (
        "run_id",
        "applied_at",
        "evidence_hash",
        "carrier",
        "next_attempt_at",
        "lease_until",
        "lease_owner",
    ):
        conn.execute(sa.text(f"ALTER TABLE steward_model_calls DROP COLUMN {column}"))
    conn.execute(sa.text("ALTER TABLE steward_model_calls RENAME COLUMN plan_id TO batch_id"))
    _rebuild_context_builds_strict(conn)
    _rebuild_agent_runs(conn, allowed=("assistant",))
