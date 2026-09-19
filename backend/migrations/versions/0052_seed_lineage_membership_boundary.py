"""Remove the seed-granted lineage membership that bypassed lineage approval.

The demo manifest used to declare 朱元璋 as an active 李氏家族 (lineage) member
even though he only joined the 李家 household. That row was never a user
decision: it was inserted by the seed, and it is exactly what let a household
member read another family's lineage tree without any lineage approval.

The manifest no longer declares it (see app/dev_seed.py), but seed convergence is
insert-only and can never remove a row, so the correction is done here once. The
condition is deliberately narrow — one exact (lineage space name, member name)
pair from the demo dataset, active status, and the membership created by that
space's owner rather than by the member themselves — so a real user's own join
request or an admin-approved invitation is never touched. Other spaces, other
users and every other row are left alone.

This is a data correction only: no schema change, and downgrade deliberately does
not restore the wrong membership.

Revision ID: 0052_seed_lineage_membership_boundary
Revises: 0051_run_event_timing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_seed_lineage_membership_boundary"
down_revision: str | None = "0051_run_event_timing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (lineage 空间名, 成员名)：旧演示清单授予的越权家族成员资格。
_WRONG_SEED_LINEAGE_MEMBERSHIPS: tuple[tuple[str, str], ...] = (("李氏家族", "朱元璋"),)


def _drop_wrong_seed_lineage_memberships(connection: sa.Connection) -> int:
    """删除指定的越权种子成员行，返回删除行数（供测试断言）。"""
    removed = 0
    for space_name, user_name in _WRONG_SEED_LINEAGE_MEMBERSHIPS:
        result = connection.execute(
            sa.text(
                "DELETE FROM space_members WHERE id IN ("
                "  SELECT m.id FROM space_members AS m"
                "  JOIN family_spaces AS f ON f.id = m.space_id"
                "  JOIN users AS u ON u.id = m.user_id"
                "  WHERE m.status = 'active'"
                "    AND m.added_by = f.owner_id"
                "    AND f.kind = 'lineage'"
                "    AND f.name = :space_name"
                "    AND u.name = :user_name"
                ")"
            ),
            {"space_name": space_name, "user_name": user_name},
        )
        removed += result.rowcount or 0
    return removed


def _refuse_if_timing_evidence(connection: sa.Connection) -> None:
    """Mirror 0051's own refusal contract for pre-version-move preflight.

    0051 is a historical migration; its guard is inline in its ``downgrade`` and
    cannot be imported without rewriting an applied revision, so the identical
    condition is restated here (same convention as 0050 mirroring 0049).
    The two must stay in sync.
    """
    # 拆成两个独立标量查询：把 EXISTS 当成 agent_run_events 的 WHERE 谓词时，
    # 该表为空会让整个查询零行，即使 agent_runs 已有首次租赁证据（实测漏判）。
    if connection.scalar(
        sa.text("SELECT 1 FROM agent_run_events WHERE timing_json IS NOT NULL LIMIT 1")
    ) or connection.scalar(
        sa.text("SELECT 1 FROM agent_runs WHERE first_leased_at IS NOT NULL LIMIT 1")
    ):
        raise RuntimeError("sidecar timing evidence exists; retain data and roll forward")


def upgrade() -> None:
    _drop_wrong_seed_lineage_memberships(op.get_bind())


def downgrade() -> None:
    # 父级拒绝合同必须先于本迁移的任何动作（与 0049/0050/0051 同一约定）：本迁移
    # 只有一条 DELETE，但跨过 0051 的深层降级仍必须由父级先拒绝，否则会出现
    # 「版本未动、数据已改」的中间态。走位从**父 revision** 开始，复现父迁移
    # 自己的 preflight（相对目标在父 revision 上才会如实歧义报错）。
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE space_members SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None:
        assert down_revision is not None
        # 只要目的地不是本迁移的父 revision，父级 0051 随后就会执行，其拒绝合同
        # （源计时证据）必须先在版本移动前履行。
        if destination != down_revision:
            _refuse_if_timing_evidence(connection)
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        if "0049_steward_candidate_evidence" in planned:
            evidence_guard = context.script.get_revision("0050_term_alias_spouse_fix")
            assert evidence_guard is not None
            evidence_guard.module._refuse_if_candidate_evidence(connection)
        if "0048_steward_terminology_publication" in planned:
            merge_guard = context.script.get_revision("0048_steward_terminology_publication")
            assert merge_guard is not None
            merge_guard.module._preflight_parent_downgrade(planned=planned)
    # 数据修正刻意不逆向：不把已删除的越权成员资格恢复为旧种子值。
