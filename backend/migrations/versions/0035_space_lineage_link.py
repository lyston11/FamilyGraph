"""家族空间显式配对（family_spaces.lineage_space_id）。

family_spaces 加 lineage_space_id 自引用列（仅 household 行有值）：household
→ 所属 lineage 空间。此前壳层「当前家族空间」选择器只能靠 owner 相等启发式
推断配对（owner 不同 / 多候选即失败）；显式列让「家族空间」成为唯一切换维度
（选择家族后，当前页面落到同一家族的家庭卡或家族树）。

- 纯加列 + 自引用 FK（RESTRICT，与 owner 同哲学：删除 lineage 前必须先显式
  解除配对，禁止 FK 级联静默断链）；
- 走原生 ALTER TABLE ADD/DROP COLUMN，不重建表：0008 曾以裸 SQL 重建本表，
  kind 列携带内联 CHECK，alembic batch 反射会拼出坏 DDL（0008 自身的先例
  就是「原生 ADD/DROP，不重建表，保留既有 CHECK 与索引」）；
- 存量收敛：owner 恰好拥有一个 lineage 时，把其未配对 household 一次性回填
  （幂等，只填 NULL；owner 名下多 lineage 的歧义情形不猜，留给显式
  PUT /spaces/{id}/lineage-link 端点）。

downgrade：drop 列（纯 schema 回退，无不可逆数据风险）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_space_lineage_link"
down_revision: str | None = "0034_steward_model_assist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 存量回填（SQLite 相关子查询）：household 的 owner 恰好拥有一个 lineage 才回填；
# 只填 lineage_space_id IS NULL 的行，可重复执行；歧义（多 lineage）/无匹配不动。
OWNER_PAIRING_BACKFILL_SQL = """
UPDATE family_spaces
SET lineage_space_id = (
    SELECT L.id
    FROM family_spaces AS L
    WHERE L.kind = 'lineage'
      AND L.owner_id = family_spaces.owner_id
      AND NOT EXISTS (
          SELECT 1 FROM family_spaces AS L2
          WHERE L2.kind = 'lineage'
            AND L2.owner_id = family_spaces.owner_id
            AND L2.id <> L.id
      )
    ORDER BY L.id
    LIMIT 1
)
WHERE kind = 'household'
  AND lineage_space_id IS NULL
"""


def upgrade() -> None:
    # 原生 ADD COLUMN（SQLite 支持内联 REFERENCES）。不走 op.add_column：
    # alembic 1.14 对带 FK 的列会再发 ALTER ADD CONSTRAINT（SQLite 不支持）；
    # 也不走 batch：0008 裸 SQL 留下的 kind 内联 CHECK 使反射重建必坏（见 docstring）。
    op.execute(
        "ALTER TABLE family_spaces ADD COLUMN lineage_space_id INTEGER"
        " REFERENCES family_spaces (id) ON DELETE RESTRICT"
    )
    op.execute(OWNER_PAIRING_BACKFILL_SQL)


def downgrade() -> None:
    op.execute("ALTER TABLE family_spaces DROP COLUMN lineage_space_id")
