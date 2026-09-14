"""Retain candidate identity/history and add internal support evidence versions."""

import sqlalchemy as sa
from alembic import op

revision: str = "0049_steward_candidate_evidence"
down_revision: str = "0048_steward_terminology_publication"
branch_labels = None
depends_on = None

_TABLE = "steward_candidate_evidence_versions"


def upgrade() -> None:
    # In-place ALTER is essential: rebuilding this parent would CASCADE or
    # SET NULL existing suggestion/inferred references with foreign_keys=ON.
    op.execute(
        sa.text(
            "ALTER TABLE steward_llm_candidates ADD COLUMN attribution_status "
            "VARCHAR(16) NOT NULL DEFAULT 'legacy' "
            "CONSTRAINT ck_slc_attribution_status "
            "CHECK (attribution_status IN ('legacy','unsupported','versioned'))"
        )
    )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("steward_llm_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("validation_contract_version", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("support_facts_json", sa.JSON(), nullable=False),
        sa.Column(
            "source_job_id", sa.Integer(), sa.ForeignKey("steward_jobs.id", ondelete="SET NULL")
        ),
        sa.Column(
            "source_batch_id",
            sa.Integer(),
            sa.ForeignKey("steward_assist_batches.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source_model_call_id",
            sa.Integer(),
            sa.ForeignKey("steward_model_calls.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "projection_job_id",
            sa.Integer(),
            sa.ForeignKey("steward_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("projection_checked_at", sa.DateTime()),
        sa.Column("invalidation_reason", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("candidate_id", "evidence_digest", name="uq_scev_candidate_digest"),
        sa.CheckConstraint(
            "status IN ('pending','projected','invalidated')", name="ck_scev_status"
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND projection_job_id IS NULL "
            "AND projection_checked_at IS NULL AND invalidation_reason IS NULL) OR "
            "(status = 'projected' AND projection_checked_at IS NOT NULL "
            "AND invalidation_reason IS NULL) OR "
            "(status = 'invalidated' AND projection_checked_at IS NOT NULL "
            "AND invalidation_reason IS NOT NULL)",
            name="ck_scev_projection_result",
        ),
    )
    op.create_index("ix_scev_space_pending", _TABLE, ["space_id", "status", "id"])
    # A source FK may become NULL, but neither its replacement nor the original
    # certificate/terminal attestation may be rewritten. Domain deletion is
    # still allowed through the candidate/space CASCADE foreign keys.
    immutable = (
        "id",
        "candidate_id",
        "space_id",
        "validation_contract_version",
        "evidence_digest",
        "support_facts_json",
        "created_at",
    )
    conditions = [f"OLD.{column} IS NOT NEW.{column}" for column in immutable]
    for column in ("source_job_id", "source_batch_id", "source_model_call_id"):
        conditions.append(f"(OLD.{column} IS NOT NEW.{column} AND NEW.{column} IS NOT NULL)")
    conditions.append(
        "(OLD.status != 'pending' AND (OLD.status IS NOT NEW.status "
        "OR OLD.projection_checked_at IS NOT NEW.projection_checked_at "
        "OR OLD.invalidation_reason IS NOT NEW.invalidation_reason "
        "OR (OLD.projection_job_id IS NOT NEW.projection_job_id "
        "AND NEW.projection_job_id IS NOT NULL)))"
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_scev_immutable BEFORE UPDATE ON {_TABLE} WHEN "
            + " OR ".join(conditions)
            + " BEGIN SELECT RAISE(ABORT, 'candidate evidence is immutable'); END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_slc_internal_sticky BEFORE UPDATE OF attribution_status "
            "ON steward_llm_candidates WHEN OLD.attribution_status = 'versioned' "
            "AND NEW.attribution_status != 'versioned' "
            "BEGIN SELECT RAISE(ABORT, 'candidate internal mode is sticky'); END"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    # SQLite DDL is not assumed transactional. Establish a writer and run ALL
    # applicable refusal contracts before removing any of this head's schema.
    connection.execute(sa.text("UPDATE steward_llm_candidates SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None:
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                revision, destination, select_for_downgrade=True
            )
        }
        parent = context.script.get_revision(down_revision)
        assert parent is not None
        parent.module._preflight_parent_downgrade(planned=planned)
    if connection.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {_TABLE}) OR EXISTS "
            "(SELECT 1 FROM steward_llm_candidates WHERE attribution_status != 'legacy')"
        )
    ):
        raise RuntimeError(
            "Cannot discard candidate evidence or adopted attribution; "
            "retain data and roll forward"
        )
    op.execute(sa.text("DROP TRIGGER trg_slc_internal_sticky"))
    op.execute(sa.text("DROP TRIGGER trg_scev_immutable"))
    op.drop_table(_TABLE)
    op.drop_column("steward_llm_candidates", "attribution_status")
