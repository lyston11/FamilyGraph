"""Owner approval for joining a space, plus free-text relation labels between members.

Joining a space now goes through the space's own owner (`space_admin`): an
invitation needs the owner's approval before the invitee accepts, and a join
request or an invite-code redemption becomes active only once the owner approves.
A separate `space_member_approvals` table carries that state, so `space_members`
keeps its plain membership shape: a member row created before this migration has no
approval row and therefore keeps its old behaviour, and no existing pending row
suddenly requires an approval it was never asked for.

The second part is a new `member_relation_labels` table: the free-text relation a
joiner states about the other person ("兄弟", "朋友", "闺蜜", ...). It is a label,
not a kinship fact — it never enters source_facts/relations/social_relations and
never affects relationship paths, reachability, generations or topology edges.
One row per normalized pair (user_a_id < user_b_id), editable by either endpoint.

Revision ID: 0053_member_approval_and_labels
Revises: 0052_seed_lineage_membership_boundary
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_member_approval_and_labels"
down_revision: str | None = "0052_seed_lineage_membership_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 只新建表，不改既有表：ALTER space_members 会让所有按旧 schema 造数的迁移测试
    # 直接失败（ORM 模型始终反映 HEAD），而审批状态本就属于另一层语义。
    op.create_table(
        "space_member_approvals",
        sa.Column(
            "member_id",
            sa.Integer(),
            sa.ForeignKey("space_members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("owner_approved_at", sa.DateTime(), nullable=True),
        sa.Column(
            "approved_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("origin IN ('invite','join_request','code')", name="ck_sma_origin"),
    )
    op.create_index("ix_space_member_approvals_space", "space_member_approvals", ["space_id"])
    op.create_table(
        "member_relation_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_a_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_b_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "user_a_id", "user_b_id", name="uq_mrl_pair"),
        sa.CheckConstraint("user_a_id != user_b_id", name="ck_mrl_no_self"),
        sa.CheckConstraint("length(label) BETWEEN 1 AND 64", name="ck_mrl_label_length"),
    )
    op.create_index("ix_member_relation_labels_space", "member_relation_labels", ["space_id"])


def downgrade() -> None:
    # 拒绝合同先于任何 DDL：SQLite 的 DROP TABLE 不保证事务回滚，先删表再由祖先
    # 拒绝会留下半降级 schema（与 0049/0050/0051/0052 同一约定）。
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE space_members SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    import sys as _sys

    print(
        f"DBG script={context.script is not None} dest={destination} opts={list(context.opts)}",
        file=_sys.stderr,
    )
    if context.script is not None and destination is not None:
        # 走位必须先从**父 revision** 开始（不是本迁移自己）：深层相对目标（如 -4）
        # 在祖先上是歧义的，Alembic 会在那里抛 "Ambiguous walk"；若先删本迁移的表
        # 再由祖先报错，就留下半降级 schema（SQLite DDL 不保证事务回滚）。
        # `iterate_revisions` 是惰性生成器：必须消费才真正走位，否则守卫形同虚设。
        assert down_revision is not None
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        # 每个即将执行的祖先在**它自己**的 preflight 里都会从它的父 revision 走位；
        # 其中任一走位歧义（如深层相对目标 -4 在 0051 上）都会在它开始执行时抛错——
        # 而那已经晚于本迁移的 DDL。这里逐个复现同一走位，把该错误提前到 DDL 之前。
        for revision in planned:
            # 生成器惰性：必须显式消费才会真正走位
            list(context.script.iterate_revisions(revision, destination, select_for_downgrade=True))
        # 祖先的拒绝合同同样必须先于本迁移的 DDL 履行。
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
    if connection.scalar(
        sa.text("SELECT 1 FROM space_member_approvals LIMIT 1")
    ) or connection.scalar(sa.text("SELECT 1 FROM member_relation_labels LIMIT 1")):
        raise RuntimeError(
            "owner-approval or relation-label evidence exists; retain data and roll forward"
        )
    op.drop_index("ix_member_relation_labels_space", table_name="member_relation_labels")
    op.drop_table("member_relation_labels")
    op.drop_index("ix_space_member_approvals_space", table_name="space_member_approvals")
    op.drop_table("space_member_approvals")
