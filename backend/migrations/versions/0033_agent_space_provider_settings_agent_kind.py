"""agent_space_provider_settings 增加 agent_kind 维度 + 平台默认单行表（09-06 治理迁移）。

Provider 治理迁移到系统管理员后台（09-06-agent-provider-admin-migration）后，
空间级模型选择按双 Agent 维度拆分：assistant 与 steward 各自独立选择
（design §1.1）。原 space_id 列级 unique 无法在 SQLite 上 ALTER 成复合唯一
(space_id, agent_kind)，按 0032/0026 同款整表重建：建新表 → INSERT SELECT
拷贝（存量行回填 agent_kind='assistant'）→ drop 旧表 → rename。不在迁移内
切换 PRAGMA foreign_keys，FK 删除动作（CASCADE/CASCADE）原样保留；唯一
放宽是 provider_id/model 改为可空（design §3.2 显式停用行允许无选择）。

同迁移内建 agent_platform_defaults 单行表（design §1.2，照 web_platform_configs
懒建先例）：管理员维护每 agent_kind 的平台默认 provider+model；owner 未显式
选择时由 services/agent_provider.py 回退到此，云同意仍归空间。

downgrade fail-closed：存在 agent_kind='steward' 行时 raise RuntimeError 中止
（单列唯一版无法表达双 Agent 维度，静默丢弃会改变空间运行时行为）；无 steward
行时反向重建回 0009 原形态并删除 agent_platform_defaults。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_agent_space_provider_settings_agent_kind"
down_revision: str | None = "0032_invite_codes_creator_set_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SETTING_COLUMNS = "id, space_id, provider_id, model, cloud_allowed, local_required, enabled"


def _create_settings_new(*, with_agent_kind: bool) -> None:
    """按目标形态建 agent_space_provider_settings_new（agent_kind 随方向增减）。"""
    columns = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]
    if with_agent_kind:
        columns.append(
            sa.Column(
                "agent_kind",
                sa.String(32),
                server_default="assistant",
                nullable=False,
            )
        )
    columns.extend(
        [
            # provider_id/model 可空：enabled=False 的显式停用行允许无选择
            # （owner 明确停用继承时不强制选型，解析走 setting_disabled；design §3.2）
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("agent_providers.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("model", sa.String(120), nullable=True),
            sa.Column("cloud_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("local_required", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        ]
    )
    constraints = []
    if with_agent_kind:
        constraints.extend(
            [
                sa.CheckConstraint(
                    "agent_kind IN ('assistant','steward')", name="ck_asps_agent_kind"
                ),
                sa.UniqueConstraint("space_id", "agent_kind", name="uq_asps_space_agent"),
            ]
        )
    else:
        # downgrade 回 0009 原形态：space_id 列级唯一。
        columns[1].unique = True
    op.create_table(
        "agent_space_provider_settings_new",
        *columns,
        *constraints,
    )


def _finalize_settings() -> None:
    """删旧表 → 改名（该表无命名索引，唯一约束走 SQLite 自动索引）。"""
    op.drop_table("agent_space_provider_settings")
    op.rename_table("agent_space_provider_settings_new", "agent_space_provider_settings")


def _create_platform_defaults() -> None:
    op.create_table(
        "agent_platform_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assistant_provider_id",
            sa.Integer(),
            sa.ForeignKey("agent_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assistant_model", sa.String(120), nullable=True),
        sa.Column(
            "steward_provider_id",
            sa.Integer(),
            sa.ForeignKey("agent_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("steward_model", sa.String(120), nullable=True),
        sa.Column(
            "updated_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("system_admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_agent_platform_default_singleton"),
    )


def upgrade() -> None:
    _create_settings_new(with_agent_kind=True)
    op.execute(
        f"INSERT INTO agent_space_provider_settings_new "
        f"({_SETTING_COLUMNS}, agent_kind) "
        f"SELECT {_SETTING_COLUMNS}, 'assistant' FROM agent_space_provider_settings"
    )
    _finalize_settings()
    _create_platform_defaults()


def downgrade() -> None:
    bind = op.get_bind()
    steward_count = bind.scalar(
        sa.text("SELECT COUNT(*) FROM agent_space_provider_settings WHERE agent_kind = 'steward'")
    )
    if steward_count:
        raise RuntimeError(
            "agent_space_provider_settings downgrade requires an explicit data decision: "
            f"{steward_count} steward row(s) exist; the pre-0033 single-column unique "
            "cannot express per-agent-kind settings and dropping them would silently "
            "change space runtime behavior"
        )
    op.drop_table("agent_platform_defaults")
    _create_settings_new(with_agent_kind=False)
    op.execute(
        f"INSERT INTO agent_space_provider_settings_new ({_SETTING_COLUMNS}) "
        f"SELECT {_SETTING_COLUMNS} FROM agent_space_provider_settings"
    )
    _finalize_settings()
