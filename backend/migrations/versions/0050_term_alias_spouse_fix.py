"""term_alias_spouse_fix：修复四条配偶旁系内置词方向（09-16-steward-terminology-auto-apply）。

变更总览（只改存量种子行文本，不改表结构）：
- 内置词典中 `Sm-Bm/Sm-Bf/Sf-Bm/Sf-Bf` 四条的 term 与编码定义相反
  （`Sm`=丈夫、`Sf`=妻子，见 services/relationship_resolver.py 的 `_SYM_LETTER`
  与既有 `Sm-Um`=公公、`Sf-Um`=岳父）。本迁移把已落库的错误行就地改正，
  并 revision+1，使词条版本哈希与已发布 PFV 一致失效重算。
- 只匹配 level=locale、locale='zh-CN'、无 space/owner 归属、且 term 恰为旧错误
  文本的行；personal/space 用户词条、usage、反馈与历史 revision 一律不碰。
- 若正确文本行已存在（运维手工补过），不重复插入、不删除历史行，仅跳过。

Revision ID: 0050_term_alias_spouse_fix
Revises: 0049_steward_candidate_evidence
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_term_alias_spouse_fix"
down_revision: str | None = "0049_steward_candidate_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (concept_code, 旧错误文本, 正确文本)；与 models/term_registry.BUILTIN_ZH_CN_TERMS 同步。
_SPOUSE_FIXES: tuple[tuple[str, str, str], ...] = (
    ("Sm-Bm", "妻子的兄弟", "丈夫的兄弟"),
    ("Sm-Bf", "妻子的姐妹", "丈夫的姐妹"),
    ("Sf-Bm", "丈夫的兄弟", "妻子的兄弟"),
    ("Sf-Bf", "丈夫的姐妹", "妻子的姐妹"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for code, wrong, right in _SPOUSE_FIXES:
        conn.execute(
            sa.text(
                "UPDATE term_entries SET term = :right, revision = revision + 1 "
                "WHERE level = 'locale' AND locale = 'zh-CN' AND concept_code = :code "
                "AND term = :wrong AND space_id IS NULL AND owner_account_id IS NULL "
                "AND status = 'active'"
            ),
            {"code": code, "wrong": wrong, "right": right},
        )


def _refuse_if_candidate_evidence(connection: sa.Connection) -> None:
    """Mirror 0049's own refusal contract for pre-version-move preflight.

    0049 is a historical migration; its guard cannot be imported without
    rewriting an applied revision, so the identical condition is restated
    here. The two must stay in sync.
    """
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM steward_candidate_evidence_versions) OR EXISTS "
            "(SELECT 1 FROM steward_llm_candidates WHERE attribution_status != 'legacy')"
        )
    ):
        raise RuntimeError(
            "Cannot discard candidate evidence or adopted attribution; "
            "retain data and roll forward"
        )


def downgrade() -> None:
    # 本迁移的 upgrade 只修正词义（数据修复），刻意不逆向改写回错误方向。
    # 但作为新的 head，必须在 alembic 移动 version_num 之前先履行所有父级拒绝合同，
    # 否则深层降级会先把版本落到 0049 再于 0049 拒绝，留下"已降级"的中间版本。
    # 这与 0049 → 0048 preflight 的模式一致（见 0049 downgrade 注释）。
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE term_entries SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is None or destination is None:
        return
    planned = {
        item.revision
        for item in context.script.iterate_revisions(
            revision, destination, select_for_downgrade=True
        )
    }
    parent = context.script.get_revision("0048_steward_terminology_publication")
    if "0048_steward_terminology_publication" in planned:
        assert parent is not None
        parent.module._preflight_parent_downgrade(planned=planned)
    if "0049_steward_candidate_evidence" in planned:
        _refuse_if_candidate_evidence(connection)
    return None
