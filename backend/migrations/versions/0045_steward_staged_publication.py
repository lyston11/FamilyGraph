"""Versioned Steward results, durable input revisions, demand and delivery.

Downgrade refuses to discard unfinished generations or undelivered effects.
Input revision tombstones have no FK: deleting/recreating a scope must not cause
an ABA revision match with an old worker. Only source columns advance versions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_steward_staged_publication"
down_revision: str | None = "0044_steward_generations"
branch_labels = None
depends_on = None

_GEN_COLUMNS = (
    sa.Column("input_versions_json", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("lease_owner", sa.String(120), nullable=True),
    sa.Column("lease_attempt", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("valid_until", sa.DateTime(), nullable=True),
    sa.Column("manifest_sealed", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("required_views", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("ready_views", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("failed_views", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("intents_prepared", sa.Boolean(), nullable=False, server_default="0"),
)
_VIEW_COLUMNS = (
    sa.Column("structural_hash", sa.String(64), nullable=True),
    sa.Column("presentation_hash", sa.String(64), nullable=True),
    sa.Column("topology_revision", sa.String(64), nullable=True),
    sa.Column("skeleton_json", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("demand_revision", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("result_view_id", sa.Integer(), nullable=True),
)
_BUDGET_COLUMNS = (
    sa.Column("retry_after", sa.DateTime(), nullable=True),
    sa.Column("manual_retry_at", sa.DateTime(), nullable=True),
    sa.Column("manual_grants", sa.Integer(), nullable=False, server_default="0"),
)

# (table, relevant columns, layer, scope columns). A global source uses no
# scope column. Local changes also invalidate the opposite side of active
# one-hop bridges; a viewer cannot traverse a second bridge.
_SOURCES: tuple[tuple[str, Sequence[str], str, Sequence[str]], ...] = (
    ("relations", ("id", "from_user", "to_user", "dir_class", "status"), "structural", ()),
    (
        "source_facts",
        ("id", "space_id", "subject_user_id", "object_user_id", "fact_type", "state", "revision"),
        "structural",
        ("space_id",),
    ),
    ("space_members", ("space_id", "user_id", "status", "role"), "structural", ("space_id",)),
    ("space_profile_refs", ("space_id", "user_id", "status"), "structural", ("space_id",)),
    (
        "family_spaces",
        ("id", "kind", "owner_id", "lineage_space_id", "name"),
        "structural",
        ("id",),
    ),
    (
        "users",
        (
            "id",
            "gender",
            "privacy_mode",
            "created_by",
            "created_at",
            "deleted_at",
            "profile_status",
        ),
        "structural",
        (),
    ),
    ("users", ("name", "birth", "death", "bio", "avatar_path"), "presentation", ()),
    ("accounts", ("id", "user_id", "status", "token_version", "pin_must_change"), "structural", ()),
    ("platform_role_assignments", ("account_id", "role"), "structural", ()),
    (
        "disclosure_preferences",
        ("profile_id", "category", "scope", "space_id", "allowed"),
        "structural",
        (),
    ),
    (
        "personal_family_bridges",
        (
            "id",
            "lineage_space_a_id",
            "lineage_space_b_id",
            "anchor_a_user_id",
            "anchor_b_user_id",
            "status",
            "revision",
            "expires_at",
            "scope_json",
            "consent_a_account_id",
            "consent_b_account_id",
            "consent_a_at",
            "consent_b_at",
        ),
        "structural",
        ("lineage_space_a_id", "lineage_space_b_id"),
    ),
    (
        "term_entries",
        (
            "id",
            "concept_code",
            "level",
            "space_id",
            "owner_account_id",
            "locale",
            "term",
            "status",
            "revision",
        ),
        "presentation",
        ("space_id",),
    ),
    (
        "agent_space_provider_settings",
        ("space_id", "agent_kind", "inferred_tree"),
        "inferred",
        ("space_id",),
    ),
    (
        "steward_inferred_edges",
        (
            "id",
            "space_id",
            "subject_user_id",
            "object_user_id",
            "relation_kind",
            "status",
            "revision",
            "source_candidate_id",
            "evidence_hash",
            "evidence_json",
            "created_at",
        ),
        "inferred",
        ("space_id",),
    ),
    (
        "steward_llm_candidates",
        ("id", "space_id", "status", "payload_json"),
        "inferred",
        ("space_id",),
    ),
)


def _scope_query(prefix: str, scope_columns: Sequence[str]) -> str:
    if not scope_columns:
        return "SELECT 0 AS scope_id"
    branches: list[str] = []
    for column in scope_columns:
        value = f"{prefix}.{column}"
        branches.extend(
            (
                f"SELECT COALESCE({value}, 0) AS scope_id",
                "SELECT CASE WHEN lineage_space_a_id = "
                + value
                + " THEN lineage_space_b_id ELSE lineage_space_a_id END AS scope_id "
                + "FROM personal_family_bridges WHERE status = 'active' AND "
                + f"(lineage_space_a_id = {value} OR lineage_space_b_id = {value})",
            )
        )
    return " UNION ".join(branches)


def _install_triggers() -> None:
    for table, columns, layer, scope_columns in _SOURCES:
        for event, prefixes in (
            ("INSERT", ("NEW",)),
            ("DELETE", ("OLD",)),
            ("UPDATE", ("OLD", "NEW")),
        ):
            query = " UNION ".join(_scope_query(prefix, scope_columns) for prefix in prefixes)
            when = ""
            if event == "UPDATE":
                when = " WHEN " + " OR ".join(
                    f"OLD.{column} IS NOT NEW.{column}" for column in columns
                )
            # All identifiers come from the static migration inventory above.
            op.execute(
                sa.text(
                    f"CREATE TRIGGER sri_{table}_{layer}_{event.lower()} "
                    f"AFTER {event} ON {table}{when} BEGIN "
                    "INSERT INTO steward_input_revisions"
                    "(scope_id, structural, presentation, inferred) "
                    f"SELECT scope_id, {int(layer == 'structural')}, "
                    f"{int(layer == 'presentation')}, {int(layer == 'inferred')} "
                    f"FROM ({query}) WHERE true ON CONFLICT(scope_id) "
                    f"DO UPDATE SET {layer} = {layer} + 1; END"
                )
            )


def upgrade() -> None:
    op.create_table(
        "steward_input_revisions",
        sa.Column("scope_id", sa.Integer(), primary_key=True),
        sa.Column("structural", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("presentation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inferred", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(sa.text("INSERT INTO steward_input_revisions(scope_id) VALUES (0)"))
    for table, columns in (
        ("steward_generations", _GEN_COLUMNS),
        ("steward_generation_views", _VIEW_COLUMNS),
        ("steward_retry_budgets", _BUDGET_COLUMNS),
    ):
        for column in columns:
            op.add_column(table, column)
    op.create_index(
        "ix_sgv_generation_status", "steward_generation_views", ["generation_id", "status"]
    )
    op.create_index("ix_sgv_result_source", "steward_generation_views", ["result_view_id"])
    op.create_index("ix_sg_space_id", "steward_generations", ["space_id", "id"])
    op.create_index(
        "ix_steward_suggestions_candidate", "steward_suggestions", ["source_candidate_id"]
    )
    op.create_table(
        "steward_view_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "view_id",
            sa.Integer(),
            sa.ForeignKey("steward_generation_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("distance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolution_json", sa.JSON(), nullable=True),
        sa.Column("edge_json", sa.JSON(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("view_id", "target_user_id", name="uq_svt_view_target"),
        sa.CheckConstraint(
            "status IN ('pending','ready','unavailable','failed')", name="ck_svt_status"
        ),
    )
    op.create_index("ix_svt_view_status", "steward_view_targets", ["view_id", "status"])
    op.create_table(
        "steward_publications",
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "generation_id",
            sa.Integer(),
            sa.ForeignKey("steward_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sp_generation", "steward_publications", ["generation_id"])
    op.create_table(
        "steward_view_demands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "viewer_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fulfilled_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "focus_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("retry_requested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("space_id", "viewer_account_id", name="uq_svd_space_viewer"),
    )
    op.create_index(
        "ix_svd_unfulfilled", "steward_view_demands", ["space_id", "fulfilled_revision", "revision"]
    )
    op.create_table(
        "steward_delivery_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "generation_id",
            sa.Integer(),
            sa.ForeignKey("steward_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("intent_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("effect_fingerprint", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("generation_id", "intent_key", name="uq_sdi_generation_key"),
        sa.CheckConstraint(
            "status IN ('pending','done','failed','superseded')", name="ck_sdi_status"
        ),
    )
    op.create_index("ix_sdi_due", "steward_delivery_intents", ["status", "available_at", "id"])
    op.create_index("ix_sdi_generation", "steward_delivery_intents", ["generation_id", "status"])
    op.create_index(
        "ix_sdi_effect", "steward_delivery_intents", ["space_id", "effect_fingerprint", "id"]
    )
    op.create_table(
        "steward_finding_deliveries",
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("signature", sa.String(200), primary_key=True),
        sa.Column("occurrence_generation_id", sa.Integer(), primary_key=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "steward_inferred_overlays",
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "viewer_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("input_versions_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    _install_triggers()
    # Legacy caches lack a complete input fence. Keep data for rollback/audit,
    # but never serve it as a valid publication in the new protocol.
    op.execute(
        sa.text("UPDATE personal_family_views SET status = 'stale' WHERE status = 'current'")
    )


def downgrade() -> None:
    pending = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM steward_generations "
            "WHERE manifest_sealed = 1 AND status IN ('running','failed')) "
            "OR EXISTS (SELECT 1 FROM steward_delivery_intents "
            "WHERE status IN ('pending','failed')) "
            "OR EXISTS (SELECT 1 FROM steward_view_demands "
            "WHERE fulfilled_revision < revision)"
        )
    )
    if pending:
        raise RuntimeError(
            "steward downgrade would discard unfinished generations or delivery intents"
        )
    for table, _columns, layer, _scope in _SOURCES:
        for event in ("insert", "delete", "update"):
            op.execute(sa.text(f"DROP TRIGGER sri_{table}_{layer}_{event}"))
    for table in (
        "steward_inferred_overlays",
        "steward_finding_deliveries",
        "steward_delivery_intents",
        "steward_view_demands",
        "steward_publications",
        "steward_view_targets",
    ):
        op.drop_table(table)
    op.drop_index("ix_sgv_generation_status", table_name="steward_generation_views")
    op.drop_index("ix_sgv_result_source", table_name="steward_generation_views")
    op.drop_index("ix_sg_space_id", table_name="steward_generations")
    op.drop_index("ix_steward_suggestions_candidate", table_name="steward_suggestions")
    for table, columns in (
        ("steward_retry_budgets", _BUDGET_COLUMNS),
        ("steward_generation_views", _VIEW_COLUMNS),
        ("steward_generations", _GEN_COLUMNS),
    ):
        for column in reversed(columns):
            op.drop_column(table, column.name)
    op.drop_table("steward_input_revisions")
