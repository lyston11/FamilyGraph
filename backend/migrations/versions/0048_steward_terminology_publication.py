"""Join staged publication and Memory/RAG; fence terminology display inputs.

Existing revision IDs and source data are preserved. Only display-relevant
projection changes invalidate views: attempt bookkeeping and baseline-only rows
must not turn each integrity scan into another recomputation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_steward_terminology_publication"
down_revision: tuple[str, str] = (
    "0047_rag_lifecycle_integrity",
    "0045_steward_staged_publication",
)
branch_labels = None
depends_on = None

# (table, display inputs, space column, row predicate). Identifiers and SQL
# predicates are static migration constants, never application input.
_SOURCES: tuple[tuple[str, Sequence[str], str | None, str | None], ...] = (
    (
        "term_usages",
        ("id", "term_entry_id", "account_id", "space_id", "created_at"),
        "space_id",
        None,
    ),
    (
        "steward_term_suppressions",
        ("viewer_account_id", "space_id", "target_user_id", "suppression_key"),
        "space_id",
        None,
    ),
    (
        "steward_term_projections",
        (
            "space_id",
            "viewer_account_id",
            "root_user_id",
            "target_user_id",
            "concept_code",
            "semantic_hash",
            "baseline_term",
            "baseline_source",
            "term",
            "origin",
            "status",
            "revision",
            "suppression_key",
            "rule_version",
        ),
        "space_id",
        "{prefix}.term IS NOT NULL",
    ),
    (
        "agent_space_provider_settings",
        ("space_id", "agent_kind", "enabled", "assist_terminology"),
        "space_id",
        "{prefix}.agent_kind = 'steward'",
    ),
    (
        "platform_feature_configs",
        ("steward_assist_terminology",),
        None,
        None,
    ),
)


def _scope_query(prefix: str, column: str | None) -> str:
    if column is None:
        return "SELECT 0 AS scope_id"
    value = f"{prefix}.{column}"
    return (
        f"SELECT COALESCE({value}, 0) AS scope_id UNION "
        f"SELECT CASE WHEN lineage_space_a_id = {value} "
        "THEN lineage_space_b_id ELSE lineage_space_a_id END AS scope_id "
        "FROM personal_family_bridges WHERE status = 'active' AND "
        f"(lineage_space_a_id = {value} OR lineage_space_b_id = {value})"
    )


def _invalidate_existing_views() -> None:
    # Includes databases upgraded from either parent of this merge. Revision
    # tombstones remain monotonic across downgrade/re-upgrade as well.
    op.execute(
        sa.text(
            "INSERT INTO steward_input_revisions "
            "(scope_id, structural, presentation, inferred) VALUES (0, 0, 1, 0) "
            "ON CONFLICT(scope_id) DO UPDATE SET presentation = presentation + 1"
        )
    )


def _restore_inferred_setting_triggers() -> None:
    # If 0045 was installed first, 0044_steward_terminology's SQLite batch
    # rebuild of this table drops its existing 0045 triggers. Restore that
    # parent contract after both branches are present, without rewriting either
    # historical migration. Downgrade keeps these 0045-owned triggers intact.
    table = "agent_space_provider_settings"
    for event, prefixes in (
        ("INSERT", ("NEW",)),
        ("DELETE", ("OLD",)),
        ("UPDATE", ("OLD", "NEW")),
    ):
        when = ""
        if event == "UPDATE":
            when = " WHEN " + " OR ".join(
                f"OLD.{column} IS NOT NEW.{column}"
                for column in ("space_id", "agent_kind", "inferred_tree")
            )
        scopes = " UNION ".join(_scope_query(p, "space_id") for p in prefixes)
        op.execute(
            sa.text(
                f"CREATE TRIGGER IF NOT EXISTS sri_{table}_inferred_{event.lower()} "
                f"AFTER {event} ON {table}{when} BEGIN "
                "INSERT INTO steward_input_revisions "
                "(scope_id, structural, presentation, inferred) "
                f"SELECT scope_id, 0, 0, 1 FROM ({scopes}) WHERE true "
                "ON CONFLICT(scope_id) DO UPDATE SET inferred = inferred + 1; END"
            )
        )


def upgrade() -> None:
    # Global maintenance takes the oldest eligible pending intent. The due
    # index orders by available_at before id and otherwise forces a full
    # pending-queue sort (including correlated prerequisite checks).
    op.create_index("ix_sdi_status_id", "steward_delivery_intents", ["status", "id"])
    _restore_inferred_setting_triggers()
    for table, columns, scope_column, predicate in _SOURCES:
        for event, prefixes in (
            ("INSERT", ("NEW",)),
            ("DELETE", ("OLD",)),
            ("UPDATE", ("OLD", "NEW")),
        ):
            conditions: list[str] = []
            if event == "UPDATE":
                conditions.append(
                    "(" + " OR ".join(f"OLD.{col} IS NOT NEW.{col}" for col in columns) + ")"
                )
            if predicate is not None:
                conditions.append(
                    "(" + " OR ".join(predicate.format(prefix=p) for p in prefixes) + ")"
                )
            when = " WHEN " + " AND ".join(conditions) if conditions else ""
            scopes = " UNION ".join(_scope_query(p, scope_column) for p in prefixes)
            op.execute(
                sa.text(
                    f"CREATE TRIGGER sri_{table}_presentation_{event.lower()} "
                    f"AFTER {event} ON {table}{when} BEGIN "
                    "INSERT INTO steward_input_revisions "
                    "(scope_id, structural, presentation, inferred) "
                    f"SELECT scope_id, 0, 1, 0 FROM ({scopes}) WHERE true "
                    "ON CONFLICT(scope_id) DO UPDATE SET presentation = presentation + 1; END"
                )
            )
    _invalidate_existing_views()


def downgrade() -> None:
    # This merge only installs input triggers and an index. It does not delete projections,
    # generations, delivery responsibilities, facts, or user feedback.
    _preflight_parent_downgrade()
    _invalidate_existing_views()
    for table, _columns, _scope, _predicate in _SOURCES:
        for event in ("insert", "delete", "update"):
            op.execute(sa.text(f"DROP TRIGGER sri_{table}_presentation_{event}"))
    op.drop_index("ix_sdi_status_id", table_name="steward_delivery_intents")


def _preflight_parent_downgrade(planned: set[str] | None = None) -> None:
    """Honor parent refusal contracts before changing this merge's schema.

    SQLite migration DDL is not assumed transactional. A command descending
    through either parent must refuse unsafe evidence loss before this merge
    drops any input triggers. Unmerging to the two parents remains lossless.
    """
    context = op.get_context()
    if planned is None:
        destination = context.opts.get("destination_rev")
        if context.script is None or destination is None:
            return
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                revision, destination, select_for_downgrade=True
            )
        }
    connection = op.get_bind()
    # Establish the writer before examining evidence, just as the parent RAG
    # guard does. No source row changes and no trigger fires for this statement.
    connection.execute(sa.text("UPDATE steward_input_revisions SET scope_id=scope_id WHERE 0"))
    if "0047_rag_lifecycle_integrity" in planned and connection.scalar(
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
    if "0046_context_execution_contract" in planned and connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM context_builds "
            "WHERE policy_json IS NOT NULL OR invalidated_at IS NOT NULL)"
        )
    ):
        raise RuntimeError("context execution evidence exists; retain data and roll forward")
    if "0045_steward_staged_publication" in planned and connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM steward_generations "
            "WHERE manifest_sealed=1 AND status IN ('running','failed')) "
            "OR EXISTS (SELECT 1 FROM steward_delivery_intents "
            "WHERE status IN ('pending','failed')) "
            "OR EXISTS (SELECT 1 FROM steward_view_demands WHERE fulfilled_revision < revision)"
        )
    ):
        raise RuntimeError(
            "steward downgrade would discard unfinished generations or delivery intents"
        )
    if "0045_rag_index_lifecycle" in planned:
        historical = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id "
                "WHERE c.index_version != d.index_version"
            )
        )
        collisions = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM (SELECT document_id, chunk_index FROM rag_chunks "
                "GROUP BY document_id, chunk_index HAVING COUNT(*) > 1)"
            )
        )
        reasons = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM rag_documents WHERE invalidation_reason IS NOT NULL "
                "AND invalidation_reason != 'source_invalidated'"
            )
        )
        if historical or collisions or reasons:
            dependencies = connection.scalar(
                sa.text("SELECT COUNT(*) FROM memories WHERE source_kind='rag_chunk'")
            )
            raise RuntimeError(
                "Cannot losslessly downgrade RAG lifecycle: "
                f"historical_chunks={historical}, key_collisions={collisions}, "
                f"distinct_reasons={reasons}, saved_dependencies={dependencies}; "
                "retain chunks and roll forward"
            )
    if "0042_memory_source_contract" in planned and connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM memories) OR EXISTS (SELECT 1 FROM memory_candidates)"
        )
    ):
        raise RuntimeError(
            "Memory source downgrade would discard provenance/history; "
            "keep the data and forward-fix, or make an explicit data decision"
        )
