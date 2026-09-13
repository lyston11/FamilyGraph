"""term_pack_expansion：内置称谓词典补全（09-13-steward-term-autofix）。

变更总览：
- 只补种子行，不改表结构：BUILTIN_TERM_SEEDS 清单扩充（zh-CN 新增 ≤4 跳常用
  亲属码 + 性别未知中性泛化；system 新增书面泛化兜底）。升级前已存在的
  (level, locale, concept_code, term) 行不重复插入（幂等）；personal/space
  用户数据永不触碰。
- 与 services/terms.seed_builtin_packs 共用同一清单来源
  （models/term_registry.BUILTIN_TERM_SEEDS）；测试夹具清表后重灌走同函数。

说明：新增行均为新 (level, locale, concept_code) 组合，downgrade 按同一清单
精确删除，不会误删既有行。

Revision ID: 0041_term_pack_expansion
Revises: 0040_agent_session_title
Create Date: 2026-09-13
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from app.models.term_registry import BUILTIN_TERM_SEEDS

revision: str = "0041_term_pack_expansion"
down_revision: str | None = "0040_agent_session_title"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_TIME = datetime(2026, 9, 13)

# 0012 已写入的旧清单（迁移前 DB 的期望全集）；本次只插入其差集。
# 直接以 zh-CN 旧清单长度切片不可靠（清单可能被后续演进），改为显式按
# (level, locale, code) 在库内查重后插入缺失行。
_PREEXISTING_ZH_CN_CODES = frozenset(
    {
        "Um",
        "Uf",
        "Um-Um",
        "Um-Uf",
        "Uf-Um",
        "Uf-Uf",
        "Um-Uf-Bm",
        "Uf-Bm",
        "Uf-Bf",
        "Um-Bm",
        "Um-Bf",
        "Uam",
        "Uaf",
        "Usm",
        "Usf",
        "Ug",
        "Ugm",
        "Ugf",
        "Dm",
        "Df",
        "D",
        "Bm",
        "Bf",
        "B",
        "Sm",
        "Sf",
        "S",
        "Pm",
        "Pf",
        "P",
        "Sm-Um",
        "Sm-Uf",
        "Sf-Um",
        "Sf-Uf",
        "Dm-Sf",
        "Df-Sm",
    }
)
_PREEXISTING_SYSTEM_CODES = frozenset(
    {
        "SELF",
        "U",
        "Um",
        "Uf",
        "D",
        "Dm",
        "Df",
        "B",
        "Bm",
        "Bf",
        "S",
        "Sm",
        "Sf",
        "P",
        "Pm",
        "Pf",
    }
)


def _new_seed_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for level, locale, code, term in BUILTIN_TERM_SEEDS:
        if level == "locale" and locale == "zh-CN":
            if code in _PREEXISTING_ZH_CN_CODES:
                continue
        elif level == "system":
            if code in _PREEXISTING_SYSTEM_CODES:
                continue
        else:
            # wu 等其他包：本迁移不涉及
            continue
        rows.append(
            {
                "concept_code": code,
                "level": level,
                "space_id": None,
                "owner_account_id": None,
                "locale": locale,
                "term": term,
                "status": "active",
                "revision": 1,
                "created_at": _SEED_TIME,
                "updated_at": _SEED_TIME,
            }
        )
    return rows


_entries_table = sa.table(
    "term_entries",
    sa.column("concept_code", sa.String),
    sa.column("level", sa.String),
    sa.column("space_id", sa.Integer),
    sa.column("owner_account_id", sa.Integer),
    sa.column("locale", sa.String),
    sa.column("term", sa.String),
    sa.column("status", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = _new_seed_rows()
    if not rows:
        return
    # 库内查重（幂等）：已存在的 (level, locale, concept_code) 不再插入，
    # 不比较 term——同码旧词条（若运维手工补过）优先保留，避免覆盖语义。
    existing = conn.execute(
        sa.text("SELECT level, locale, concept_code FROM term_entries"),
    ).fetchall()
    existing_keys = {(row[0], row[1], row[2]) for row in existing}
    missing = [
        row
        for row in rows
        if (row["level"], row["locale"], row["concept_code"]) not in existing_keys
    ]
    if missing:
        op.bulk_insert(_entries_table, missing)


def downgrade() -> None:
    # 仅删除本迁移引入的 (level, locale, concept_code) 组合；手工写入的
    # 同码异词行（若有）同样在列——种子层不存在用户数据。
    new_keys = {(row["level"], row["locale"], row["concept_code"]) for row in _new_seed_rows()}
    if not new_keys:
        return
    conn = op.get_bind()
    for level, locale, code in sorted(new_keys):
        conn.execute(
            sa.text(
                "DELETE FROM term_entries WHERE level = :level AND locale = :locale "
                "AND concept_code = :code"
            ),
            {"level": level, "locale": locale, "code": code},
        )
