"""v2_foundation：身份/空间/角色/披露/owner 保护与合同表（spec/architecture.md §0）。

变更总览（概念不得合并回单一 admin 标志，PRD F-1..F-6）：
- platform_role_assignments 新表；users.is_admin=1 行迁移为 platform_operator 角色后删列。
- users 增 profile_status（provisional|identity_confirmed，存量数据均为有意建档 →
  server_default 'identity_confirmed'）与 profile_confirmed_at；
  删除 claim_status（迁至 accounts.status）、clan_disclosure_json（迁至 disclosure_preferences）。
- accounts 增 status（managed|claimed，自 users.claim_status 回填）与 claimed_at。
- family_spaces：增 kind（household|lineage）；owner FK 由 CASCADE 改为 RESTRICT ——
  选择保留 owner_id 列而非成员派生：所有权可查询、可保护，删除 owner 由数据库拒绝，
  移交流程显式处理。SQLite 经整表重建实现（子表 FK 按名引用，rename 后依然有效）。
- space_members.role 扩展为 owner|space_admin|member|guest（整表重建 CHECK）。
- 新表：space_profile_refs、owner_invitations、ownership_transfers、claim_disputes、
  data_right_requests、domain_events、profile_fact_reviews。

说明：
- Alembic SQLite 迁移按非事务 DDL 处理；本迁移仅保证空库/开发库一次性升级成功。
- downgrade 仅做结构还原（开发空库用途），不做完整数据逆映射
  （is_admin/disclosure 明细不可逆恢复），符合任务"无生产数据迁移兼容"前提。

Revision ID: 0008_v2_foundation
Revises: 0007_m3a_attachments
Create Date: 2026-08-26

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_v2_foundation"
down_revision: str | None = "0007_m3a_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISCLOSURE_FALSE_DEFAULT = (
    '{"avatar": false, "photos": false, "dates": false, "bio": false, "attachments": false}'
)


def upgrade() -> None:
    # ---- 1. 平台角色：先建表并迁移 is_admin 数据 ----
    op.create_table(
        "platform_role_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "role",
            sa.String(32),
            sa.CheckConstraint("role IN ('platform_operator')", name="ck_pra_role"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        "INSERT INTO platform_role_assignments (account_id, role, created_by, created_at)"
        " SELECT accounts.id, 'platform_operator', NULL, users.created_at"
        " FROM users JOIN accounts ON accounts.user_id = users.id"
        " WHERE users.is_admin = 1"
    )

    # ---- 2. 披露偏好：迁移 clan_disclosure_json 中为 true 的类别为全局偏好 ----
    op.create_table(
        "disclosure_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.String(20),
            sa.CheckConstraint(
                "category IN ('avatar','photos','dates','bio','attachments',"
                "'health','address','school','contact','private_notes')",
                name="ck_dp_category",
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.String(10),
            sa.CheckConstraint("scope IN ('global', 'space')", name="ck_dp_scope"),
            server_default="global",
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(scope = 'global' AND space_id IS NULL)"
            " OR (scope = 'space' AND space_id IS NOT NULL)",
            name="ck_dp_scope_pair",
        ),
    )
    # SQLite UNIQUE 不把 NULL 视为相等 → 表达式唯一索引保证全局行至多一条
    op.create_index(
        "uq_disclosure_pref_scope",
        "disclosure_preferences",
        ["profile_id", "category", sa.text("COALESCE(space_id, -1)")],
        unique=True,
    )
    conn = op.get_bind()
    from app.utils.timeutil import utcnow

    for row in conn.execute(sa.text("SELECT id, clan_disclosure_json FROM users")).all():
        raw = row[1]
        try:
            flags = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            continue
        for category, allowed in flags.items():
            if allowed:
                conn.execute(
                    sa.text(
                        "INSERT INTO disclosure_preferences"
                        " (profile_id, category, scope, space_id, allowed, updated_at)"
                        " VALUES (:pid, :cat, 'global', NULL, 1, :ts)"
                    ),
                    {"pid": row[0], "cat": category, "ts": utcnow()},
                )

    # ---- 3. accounts 生命周期列 + 自 users.claim_status 回填 ----
    op.execute(
        "ALTER TABLE accounts ADD COLUMN status VARCHAR(16)"
        " NOT NULL DEFAULT 'managed'"
        " CONSTRAINT ck_accounts_status CHECK (status IN ('managed', 'claimed'))"
    )
    op.execute("ALTER TABLE accounts ADD COLUMN claimed_at DATETIME NULL")
    op.execute(
        "UPDATE accounts SET status ="
        " COALESCE((SELECT claim_status FROM users WHERE users.id = accounts.user_id),"
        " 'managed')"
    )

    # ---- 4. users：确档状态列 + 删除被取代的三列（原生 ADD/DROP COLUMN，
    #      不重建表，保留既有 CHECK 与索引）----
    op.execute(
        "ALTER TABLE users ADD COLUMN profile_status VARCHAR(20)"
        " NOT NULL DEFAULT 'identity_confirmed'"
        " CONSTRAINT ck_users_profile_status CHECK"
        " (profile_status IN ('provisional', 'identity_confirmed'))"
    )
    op.execute("ALTER TABLE users ADD COLUMN profile_confirmed_at DATETIME NULL")
    op.execute("ALTER TABLE users DROP COLUMN is_admin")
    op.execute("ALTER TABLE users DROP COLUMN claim_status")
    op.execute("ALTER TABLE users DROP COLUMN clan_disclosure_json")

    # ---- 5. family_spaces 整表重建：增 kind + owner FK 改 RESTRICT ----
    op.execute(
        "CREATE TABLE family_spaces_new ("
        " id INTEGER PRIMARY KEY,"
        " name VARCHAR(64) NOT NULL,"
        " owner_id INTEGER NOT NULL REFERENCES users (id) ON DELETE RESTRICT,"
        " kind VARCHAR(16) NOT NULL DEFAULT 'household'"
        " CONSTRAINT ck_family_spaces_kind CHECK (kind IN ('household', 'lineage')), "
        " created_at DATETIME NOT NULL)"
    )
    op.execute(
        "INSERT INTO family_spaces_new (id, name, owner_id, kind, created_at)"
        " SELECT id, name, owner_id, 'household', created_at FROM family_spaces"
    )
    op.execute("DROP TABLE family_spaces")
    op.execute("ALTER TABLE family_spaces_new RENAME TO family_spaces")

    # ---- 6. space_members 整表重建：role CHECK 扩展至 v2 四角色 ----
    op.execute(
        "CREATE TABLE space_members_new ("
        " id INTEGER PRIMARY KEY,"
        " space_id INTEGER NOT NULL REFERENCES family_spaces (id) ON DELETE CASCADE,"
        " user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,"
        " added_by INTEGER REFERENCES users (id) ON DELETE SET NULL,"
        " role VARCHAR(16) NOT NULL DEFAULT 'member'"
        " CONSTRAINT ck_sm_role CHECK (role IN ('owner','space_admin','member','guest')), "
        " status VARCHAR(16) NOT NULL DEFAULT 'pending'"
        " CONSTRAINT ck_sm_status CHECK"
        " (status IN ('pending','active','rejected','withdrawn','removed')), "
        " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
        " CONSTRAINT uq_space_member_pair UNIQUE (space_id, user_id))"
    )
    op.execute(
        "INSERT INTO space_members_new (id, space_id, user_id, added_by, role, status,"
        " created_at, updated_at)"
        " SELECT id, space_id, user_id, added_by, role, status, created_at, updated_at"
        " FROM space_members"
    )
    op.execute("DROP TABLE space_members")
    op.execute("ALTER TABLE space_members_new RENAME TO space_members")
    op.create_index("ix_space_members_space", "space_members", ["space_id"])
    op.create_index("ix_space_members_user", "space_members", ["user_id"])

    # ---- 7. 其余 v2 合同表 ----
    op.create_table(
        "space_profile_refs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint("status IN ('active', 'removed')", name="ck_spr_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_profile_ref_pair"),
    )
    op.create_index("ix_space_profile_refs_space", "space_profile_refs", ["space_id"])
    op.create_index("ix_space_profile_refs_user", "space_profile_refs", ["user_id"])

    op.create_table(
        "owner_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "ownership_transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_user",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_user",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('pending','accepted','cancelled','expired')", name="ck_ot_status"
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_ownership_transfer_active",
        "ownership_transfers",
        ["space_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "claim_disputes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raised_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint(
                "status IN ('open','resolved_claim','resolved_reject','withdrawn')",
                name="ck_cd_status",
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "data_right_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requestor_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_profile_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.String(16),
            sa.CheckConstraint("type IN ('export','correct','delete')", name="ck_drr_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('pending','processing','completed','rejected','expired')",
                name="ck_drr_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("scope", sa.String(64), server_default="self", nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("result_path", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "domain_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_domain_events_aggregate", "domain_events", ["aggregate_type", "aggregate_id"]
    )

    op.create_table(
        "profile_fact_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_ref_json", sa.JSON(), nullable=False),
        sa.Column(
            "proposed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('proposed','confirmed','disputed')", name="ck_pfr_status"
            ),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    # 结构还原（开发空库用途）：新表逆序删除，再还原被重构的表
    op.drop_table("profile_fact_reviews")
    op.drop_index("ix_domain_events_aggregate", table_name="domain_events")
    op.drop_table("domain_events")
    op.drop_table("data_right_requests")
    op.drop_table("claim_disputes")
    op.drop_index("uq_ownership_transfer_active", table_name="ownership_transfers")
    op.drop_table("ownership_transfers")
    op.drop_table("owner_invitations")
    op.drop_index("ix_space_profile_refs_user", table_name="space_profile_refs")
    op.drop_index("ix_space_profile_refs_space", table_name="space_profile_refs")
    op.drop_table("space_profile_refs")

    # space_members 还原 v1 两角色形状
    op.execute(
        "CREATE TABLE space_members_old ("
        " id INTEGER PRIMARY KEY,"
        " space_id INTEGER NOT NULL REFERENCES family_spaces (id) ON DELETE CASCADE,"
        " user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,"
        " added_by INTEGER REFERENCES users (id) ON DELETE SET NULL,"
        " role VARCHAR(16) NOT NULL DEFAULT 'member'"
        " CONSTRAINT ck_sm_role CHECK (role IN ('owner','member')), "
        " status VARCHAR(16) NOT NULL DEFAULT 'pending'"
        " CONSTRAINT ck_sm_status CHECK"
        " (status IN ('pending','active','rejected','withdrawn','removed')), "
        " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
        " CONSTRAINT uq_space_member_pair UNIQUE (space_id, user_id))"
    )
    op.execute(
        "INSERT INTO space_members_old (id, space_id, user_id, added_by, role, status,"
        " created_at, updated_at) SELECT id, space_id, user_id, added_by, role, status,"
        " created_at, updated_at FROM space_members"
    )
    op.execute("DROP TABLE space_members")
    op.execute("ALTER TABLE space_members_old RENAME TO space_members")
    op.create_index("ix_space_members_space", "space_members", ["space_id"])
    op.create_index("ix_space_members_user", "space_members", ["user_id"])

    # family_spaces 还原 CASCADE 形状并去掉 kind
    op.execute(
        "CREATE TABLE family_spaces_old ("
        " id INTEGER PRIMARY KEY,"
        " name VARCHAR(64) NOT NULL,"
        " owner_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,"
        " created_at DATETIME NOT NULL)"
    )
    op.execute(
        "INSERT INTO family_spaces_old (id, name, owner_id, created_at)"
        " SELECT id, name, owner_id, created_at FROM family_spaces"
    )
    op.execute("DROP TABLE family_spaces")
    op.execute("ALTER TABLE family_spaces_old RENAME TO family_spaces")

    # users 还原：先自 accounts.status 回填 claim_status，再互删列
    op.execute(
        "ALTER TABLE users ADD COLUMN clan_disclosure_json JSON"
        f" NOT NULL DEFAULT '{_DISCLOSURE_FALSE_DEFAULT}'"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN claim_status VARCHAR(16)"
        " NOT NULL DEFAULT 'managed'"
        " CONSTRAINT ck_users_claim_status CHECK (claim_status IN ('managed', 'claimed'))"
    )
    op.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
    op.execute(
        "UPDATE users SET claim_status ="
        " COALESCE((SELECT status FROM accounts WHERE accounts.user_id = users.id), 'managed')"
    )
    op.execute("ALTER TABLE users DROP COLUMN profile_confirmed_at")
    op.execute("ALTER TABLE users DROP COLUMN profile_status")

    op.execute("ALTER TABLE accounts DROP COLUMN claimed_at")
    op.execute("ALTER TABLE accounts DROP COLUMN status")

    op.drop_index("uq_disclosure_pref_scope", table_name="disclosure_preferences")
    op.drop_table("disclosure_preferences")
    op.drop_table("platform_role_assignments")
