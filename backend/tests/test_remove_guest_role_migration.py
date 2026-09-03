"""Migration coverage for retiring the guest space role."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "versions" / "0026_remove_guest_role.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0026", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection, *, role_check: str) -> None:
    connection.execute(text("PRAGMA foreign_keys=ON"))
    connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    connection.execute(
        text(
            "CREATE TABLE family_spaces (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL "
            "REFERENCES users(id) ON DELETE RESTRICT)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE space_members ("
            "id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL "
            "REFERENCES family_spaces(id) ON DELETE CASCADE,"
            "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            f"role VARCHAR(16) NOT NULL CHECK({role_check}),"
            "status VARCHAR(16) NOT NULL CHECK(status IN "
            "('pending','active','rejected','withdrawn','removed')),"
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
            "CONSTRAINT uq_space_member_pair UNIQUE(space_id,user_id))"
        )
    )
    connection.execute(text("CREATE INDEX ix_space_members_space ON space_members(space_id)"))
    connection.execute(text("CREATE INDEX ix_space_members_user ON space_members(user_id)"))
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_space_active_admin ON space_members(space_id) "
            "WHERE role='space_admin' AND status='active'"
        )
    )
    connection.execute(text("INSERT INTO users(id) VALUES (1), (2), (3)"))
    connection.execute(text("INSERT INTO family_spaces(id,owner_id) VALUES (1,1)"))


def _run(module: ModuleType, connection, direction: str) -> None:
    context = MigrationContext.configure(connection)
    operations = Operations(context)
    module.op = operations
    getattr(module, direction)()


def _assert_space_member_structure(connection) -> None:
    indexes = {item["name"] for item in inspect(connection).get_indexes("space_members")}
    assert indexes == {
        "ix_space_members_space",
        "ix_space_members_user",
        "uq_space_active_admin",
    }
    unique_constraints = {
        item["name"]: tuple(item["column_names"])
        for item in inspect(connection).get_unique_constraints("space_members")
    }
    assert unique_constraints == {"uq_space_member_pair": ("space_id", "user_id")}
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_list(space_members)").mappings()
    assert {(fk["table"], fk["on_delete"]) for fk in foreign_keys} == {
        ("family_spaces", "CASCADE"),
        ("users", "CASCADE"),
        ("users", "SET NULL"),
    }


def test_upgrade_rejects_guest_rows_without_mutating_schema(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'guest.db'}")
    module = _load_migration()
    with engine.begin() as connection:
        _create_schema(connection, role_check="role IN ('space_admin','member','guest')")
        connection.execute(
            text(
                "INSERT INTO space_members VALUES "
                "(1,1,2,1,'guest','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="contains 1 guest row"):
            _run(module, connection, "upgrade")
        assert "guest" in connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='space_members'")
        )


def test_upgrade_rejects_any_unsupported_role_without_mutating_schema(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unsupported-role.db'}")
    module = _load_migration()
    with engine.begin() as connection:
        _create_schema(connection, role_check="role IS NOT NULL")
        connection.execute(
            text(
                "INSERT INTO space_members VALUES "
                "(1,1,2,1,'legacy','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="unsupported.*legacy"):
            _run(module, connection, "upgrade")
        assert connection.scalar(text("SELECT role FROM space_members WHERE id=1")) == "legacy"
        assert "role IS NOT NULL" in connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='space_members'")
        )


def test_upgrade_and_downgrade_role_constraint_preserve_structure(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'roles.db'}")
    module = _load_migration()
    with engine.begin() as connection:
        _create_schema(connection, role_check="role IN ('space_admin','member','guest')")
        connection.execute(
            text(
                "INSERT INTO space_members VALUES "
                "(1,1,1,1,'space_admin','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
                "(2,1,2,1,'member','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        _run(module, connection, "upgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1

        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO space_members VALUES "
                "(3,1,3,1,'guest','removed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        _assert_space_member_structure(connection)

        _run(module, connection, "downgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        _assert_space_member_structure(connection)
        connection.exec_driver_sql(
            "INSERT INTO space_members VALUES "
            "(3,1,3,1,'guest','removed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        assert connection.scalar(text("SELECT role FROM space_members WHERE id=3")) == "guest"


def test_rebuild_preserves_connection_foreign_key_setting(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'foreign-keys.db'}")
    module = _load_migration()
    with engine.connect() as connection:
        _create_schema(connection, role_check="role IN ('space_admin','member','guest')")
        connection.execute(
            text(
                "INSERT INTO space_members VALUES "
                "(1,1,2,1,'member','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        assert connection.scalar(text("PRAGMA foreign_keys")) == 0

        _run(module, connection, "upgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == 0

        _run(module, connection, "downgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == 0
