"""Correct the demo seed's 李家 household roster: drop 朱元璋, add 朱佛女.

The demo manifest used to put 朱元璋 in the 李家 household even though he has no
structural fact in that space at all — his only tie to the family is that his
elder sister 朱佛女 married 李贞, and sibling facts do not produce a `Relation`.
So he rendered as an isolated node next to 李贞 with no visible connection: two
people in one household with nothing between them.

The manifest now lists 朱佛女 (李贞's actual spouse) instead, which both keeps the
documented "2-person household = 本家 admin + 帝室 member" shape and gives the card
a real relation to show. Seed convergence is insert-only and can never remove a
row, so the removal is done here once.

The delete condition is deliberately narrow — the exact (household space name,
member name) pair from the demo dataset, active status, and the membership added
by that space's owner rather than by the member themselves — so a real user's own
join request or an admin-approved invitation is never touched. Other spaces, other
users and every other row are left alone. The insert is guarded the same way, so
re-running the upgrade cannot duplicate the row.

This is a data correction only: no schema change, and downgrade deliberately does
not restore the removed membership.

Revision ID: 0054_seed_household_roster_fix
Revises: 0053_member_approval_and_labels
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_seed_household_roster_fix"
down_revision: str | None = "0053_member_approval_and_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (household 空间名, 应移除的成员名, 应补入的成员名)
_SEED_HOUSEHOLD_ROSTER_FIXES: tuple[tuple[str, str, str], ...] = (("李家", "朱元璋", "朱佛女"),)

# 旧清单里 household 的帝室 member 一律由空间 owner 添加（见 app/dev_seed.py 的
# _seed_space_member）。真实用户自己的加入申请走另一条 added_by 路径。
_MEMBER_ADDED_BY_OWNER = (
    "  SELECT m.id FROM space_members AS m"
    "  JOIN family_spaces AS f ON f.id = m.space_id"
    "  JOIN users AS u ON u.id = m.user_id"
    "  WHERE m.status = 'active'"
    "    AND m.added_by = f.owner_id"
    "    AND f.kind = 'household'"
    "    AND f.name = :space_name"
    "    AND u.name = :user_name"
)


def _drop_wrong_seed_household_memberships(connection: sa.Connection) -> int:
    """删除指定的种子成员行，返回删除行数（供测试断言）。"""
    removed = 0
    for space_name, user_name, _replacement in _SEED_HOUSEHOLD_ROSTER_FIXES:
        result = connection.execute(
            sa.text(f"DELETE FROM space_members WHERE id IN ({_MEMBER_ADDED_BY_OWNER})"),
            {"space_name": space_name, "user_name": user_name},
        )
        removed += result.rowcount or 0
    return removed


def _add_seed_household_memberships(connection: sa.Connection) -> int:
    """幂等补入替换成员，返回插入行数（供测试断言）。

    用 INSERT ... SELECT ... WHERE NOT EXISTS 表达，重复 upgrade 不会重复插入；
    `added_by` 与旧清单同口径取空间 owner，role 取 member（household 每空间恰好
    一个 active admin，由 partial unique index 保证）。

    NOT EXISTS 检查**任意状态**的行，而非只看 active：`uq_space_member_pair` 唯一
    约束在建在 (space_id, user_id) 上、不分状态，只看 active 会撞约束。更重要的是
    语义：若该用户已有一行 pending（他本人提交的申请），本迁移**不得**把它直接改成
    active——那会绕过 0053 建立的「房主审批」边界（V5/V6）。此时跳过补入，让申请走
    正常审批流程。
    """
    added = 0
    for space_name, _removed, replacement in _SEED_HOUSEHOLD_ROSTER_FIXES:
        result = connection.execute(
            sa.text(
                "INSERT INTO space_members"
                " (space_id, user_id, added_by, role, status, created_at, updated_at)"
                " SELECT f.id, u.id, f.owner_id, 'member', 'active',"
                "        :now, :now"
                " FROM family_spaces AS f"
                " JOIN users AS u ON u.name = :user_name"
                " WHERE f.kind = 'household'"
                "   AND f.name = :space_name"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM space_members AS m"
                "     WHERE m.space_id = f.id AND m.user_id = u.id"
                "   )"
            ),
            {
                "space_name": space_name,
                "user_name": replacement,
                "now": _utcnow_iso(),
            },
        )
        added += result.rowcount or 0
    return added


def _utcnow_iso() -> str:
    """与 app.utils.timeutil.utcnow() 同格式的 UTC ISO8601 文本。"""
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _stale_affected_space_views(connection: sa.Connection) -> int:
    """把受影响空间的全部 PFV 投影标 stale，返回标记行数。

    直接删成员行不会触发 ``space.membership.changed`` 领域事件，物化投影会保留
    含已移除成员的陈旧快照（实测：删行后未重建的 PFV 节点集合不变）。读取授权
    本身不依赖投影（按 active membership 现算），但为了让迁移后立刻一致，这里
    复用 ``invalidate_space_views`` 的同一语义：只把 ``status`` 标为 ``stale``，
    不删行、不写计算内容。
    """
    marked = 0
    for space_name, _removed, _replacement in _SEED_HOUSEHOLD_ROSTER_FIXES:
        result = connection.execute(
            sa.text(
                "UPDATE personal_family_views"
                " SET status = 'stale', invalidated_at = :now, updated_at = :now"
                " WHERE status != 'never_computed'"
                "   AND space_id IN (SELECT id FROM family_spaces WHERE name = :space_name)"
            ),
            {"space_name": space_name, "now": _utcnow_iso()},
        )
        marked += result.rowcount or 0
    return marked


def upgrade() -> None:
    connection = op.get_bind()
    _drop_wrong_seed_household_memberships(connection)
    _add_seed_household_memberships(connection)
    _stale_affected_space_views(connection)


def downgrade() -> None:
    # 父级拒绝合同必须先于本迁移的任何动作（与 0049/0050/0051/0052/0053 同一约定）。
    # 本迁移只有 DELETE/INSERT/UPDATE，但跨过 0053（结构迁移，含 DROP TABLE）的深层
    # 降级仍必须由父级先拒绝，否则会出现「版本未动、数据已改」或半降级 schema 的
    # 中间态。走位从**父 revision** 开始，复现父迁移自己的 preflight（相对目标在父
    # revision 上才会如实歧义报错）。
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE space_members SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None:
        assert down_revision is not None
        # `iterate_revisions` 是惰性生成器：必须消费才真正走位，否则守卫形同虚设。
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        # 每个即将执行的祖先在它自己的 preflight 里都会从它的父 revision 走位；其中
        # 任一走位歧义（如深层相对目标 -4 在 0051 上）都会在它开始执行时才抛错——那
        # 已晚于本迁移的动作。这里逐个复现同一走位，把该错误提前。
        for revision in planned:
            list(context.script.iterate_revisions(revision, destination, select_for_downgrade=True))
        # 祖先的拒绝合同同样必须先于本迁移的动作履行。
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
    # 数据修正刻意不逆向：不把已删除的成员资格恢复为旧种子值，也不回退补入的成员。
