"""Alembic 迁移环境：数据库 URL 统一来自 app.config，禁止在 ini 中硬编码。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from app import config as app_config
from app.models import Base  # noqa: E402

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

alembic_config.set_main_option("sqlalchemy.url", app_config.DATABASE_URL)

# ORM metadata：m0b 起挂载，后续 autogenerate 以此为基准
target_metadata = Base.metadata

#: `alembic_version.version_num` 的宽度。
#:
#: Alembic 硬编码 `String(32)`（`alembic/ddl/impl.py` 的 `version_table_impl`），
#: 而本仓有 **10 个** revision id 超过 32 字符（最长 45：
#: `0033_agent_space_provider_settings_agent_kind`）。
#:
#: 后果（2026-10-24 在线上库的克隆上实测）：在 **PostgreSQL** 上，
#: 那些迁移执行完 DDL 后写版本号时抛
#: `StringDataRightTruncation: value too long for type character varying(32)`，
#: 于是 `upgrade` 整体失败、**迁移无法被标记为已应用**。SQLite 不检查长度，
#: 所以本地套件从未暴露它——这正是「历史 Alembic 链在 PG 上跑不通」的主因之一
#: （另一个是 SQLite-only DDL）。
#:
#: 修法：Alembic 建版本表时用 `checkfirst=True`（`runtime/migration.py` 实测确认），
#: 因此**预先**把表建成更宽的列，Alembic 就会沿用它。
VERSION_NUM_LENGTH = 128


def _ensure_version_table_width(connection: Connection) -> None:
    """把 `alembic_version.version_num` 加宽到 `VERSION_NUM_LENGTH`。

    幂等且非破坏性：表/列已存在时按需 `ALTER`；列已够宽时不做任何事。
    只处理 PostgreSQL——SQLite 的 `VARCHAR` 不限制长度，且不支持该形式的 `ALTER`。
    """
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        # 表还不存在：以正确宽度建出来，Alembic 的 checkfirst 会沿用它。
        connection.execute(
            text(
                f"CREATE TABLE alembic_version "
                f"(version_num VARCHAR({VERSION_NUM_LENGTH}) NOT NULL, "
                f"CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        return
    length: int | None = None
    for column in inspector.get_columns("alembic_version"):
        if column["name"] == "version_num":
            length = getattr(column["type"], "length", None)
            break
    if length is not None and length >= VERSION_NUM_LENGTH:
        return
    connection.execute(
        text(
            f"ALTER TABLE alembic_version "
            f"ALTER COLUMN version_num TYPE VARCHAR({VERSION_NUM_LENGTH})"
        )
    )


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=app_config.DATABASE_URL,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连数据库执行迁移。"""
    # SQLite 需要父目录存在才能建库文件（幂等）
    app_config.ensure_data_dirs()

    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # 版本表必须在 Alembic 读取/写入版本号**之前**加宽，否则长 revision id
        # 会在写版本号时抛 StringDataRightTruncation，使迁移无法标记为已应用。
        _ensure_version_table_width(connection)
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
