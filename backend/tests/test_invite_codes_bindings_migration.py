"""0030/0031/0032 邀请码与绑定迁移回归（09-05 P2-1 + P2-2）。

与 test_remove_guest_role_migration.py（0026）同风格；区别是本文件用真实 Alembic
链（conftest 同款 subprocess + DATA_DIR 覆盖）跑 upgrade/downgrade 往返，CHECK
负向探针与正向断言都在真实迁移库上执行，另按 database-guidelines「SQLite 迁移
连接状态边界」用进程内方式覆盖 0032 重建在 foreign_keys=ON/OFF 两种连接状态下
的行为。ORM 层 CHECK 兜底另有 test_invite_codes_service.py，本文件全部走裸 SQL，
可发现迁移 SQL 与模型漂移。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).parents[1]
ALEMBIC_BIN = BACKEND_DIR / ".venv" / "bin" / "alembic"
MIGRATION_0032_PATH = (
    BACKEND_DIR / "migrations" / "versions" / "0032_invite_codes_creator_set_null.py"
)


def _alembic(*args: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    """真实迁移链执行（DATABASE_URL 由 DATA_DIR 派生，单一配置来源不破坏）。"""
    return subprocess.run(
        [str(ALEMBIC_BIN), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "DATA_DIR": str(data_dir)},
        cwd=BACKEND_DIR,
    )


def _migrate(*args: str, data_dir: Path) -> None:
    result = _alembic(*args, data_dir=data_dir)
    assert result.returncode == 0, result.stderr


def _engine(data_dir: Path):
    engine = create_engine(f"sqlite:///{data_dir / 'db' / 'app.db'}")

    # 项目启动合同：foreign_keys=ON（数据库规范第 1 条）；探针必须带 FK 语义执行
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _seed_actors(connection) -> tuple[int, int, int]:
    """最小合法父行：两个 user + 一个 household 空间（owner=user 1）。"""
    connection.execute(text("INSERT INTO users (id, name, created_at) VALUES (1, '甲', 0)"))
    connection.execute(text("INSERT INTO users (id, name, created_at) VALUES (2, '乙', 0)"))
    connection.execute(
        text(
            "INSERT INTO family_spaces (id, name, owner_id, kind, created_at) "
            "VALUES (1, '空间', 1, 'household', 0)"
        )
    )
    connection.commit()
    return 1, 2, 1


def _insert_code(connection, *, code: str, kind: str, space_id: int | None, max_uses: int | None):
    return connection.execute(
        text(
            "INSERT INTO invite_codes (code, kind, creator_id, space_id, max_uses, "
            "used_count, expires_at, created_at) "
            "VALUES (:code, :kind, 2, :space_id, :max_uses, 0, 0, 0)"
        ),
        {"code": code, "kind": kind, "space_id": space_id, "max_uses": max_uses},
    )


def _table_names(connection) -> set[str]:
    rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    return {row[0] for row in rows}


def _fk_actions(connection, table: str) -> set[tuple[str, str]]:
    return {
        (row["table"], row["on_delete"])
        for row in connection.exec_driver_sql(f"PRAGMA foreign_key_list({table})").mappings()
    }


def _index_names(connection, table: str) -> set[str]:
    return {item["name"] for item in inspect(connection).get_indexes(table)}


# ---- 1. 0029 → 0030 → 0031 upgrade/downgrade 往返 ----


def test_round_trip_0029_to_0031_and_back_to_head(tmp_path: Path) -> None:
    _migrate("upgrade", "0029_admin_access_audit", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        tables = _table_names(connection)
        assert "invite_codes" not in tables
        assert "account_bindings" not in tables

    _migrate("upgrade", "0030_add_invite_codes", data_dir=tmp_path)
    with engine.connect() as connection:
        assert "invite_codes" in _table_names(connection)
        assert "account_bindings" not in _table_names(connection)
        ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='invite_codes'")
        ).scalar_one()
        for constraint in (
            "ck_invite_code_kind",
            "ck_invite_code_space_pair",
            "ck_invite_code_max_uses",
            "ck_invite_code_used_count",
            "CONSTRAINT uq_invite_codes_code UNIQUE",
        ):
            assert constraint in ddl, f"0030 缺少 {constraint}"
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "RESTRICT"),
            ("family_spaces", "CASCADE"),
        }
        assert _index_names(connection, "invite_codes") == {
            "ix_invite_codes_creator",
            "ix_invite_codes_space",
        }

    _migrate("upgrade", "0031_add_account_bindings", data_dir=tmp_path)
    with engine.connect() as connection:
        ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' " "AND name='account_bindings'")
        ).scalar_one()
        assert "ck_account_binding_status" in ddl
        assert _fk_actions(connection, "account_bindings") == {
            ("users", "CASCADE"),
            ("users", "SET NULL"),
        }
        assert _index_names(connection, "account_bindings") == {
            "ix_account_bindings_target",
            "ix_account_bindings_initiator",
            "ix_account_bindings_person",
        }
        _seed_actors(connection)

    _migrate("downgrade", "0029_admin_access_audit", data_dir=tmp_path)
    with engine.connect() as connection:
        tables = _table_names(connection)
        assert "invite_codes" not in tables
        assert "account_bindings" not in tables
        # down 只删自己的新表，不动既有主体行
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 2

    # 再 up：down 不留残骸，up 可从 0029 直接回到 head
    _migrate("upgrade", "head", data_dir=tmp_path)
    with engine.connect() as connection:
        assert {"invite_codes", "account_bindings"} <= _table_names(connection)
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 2


# ---- 2. CHECK 负向探针（真实迁移库，裸 SQL，断言指定 CHECK 拒绝）----


@pytest.mark.parametrize(
    ("kind", "space_id", "max_uses", "expected_check"),
    [
        # stranger 码带 space_id（stranger ⇔ space_id IS NULL）
        ("stranger", 1, None, "ck_invite_code_space_pair"),
        # household/lineage 码无 space_id
        ("household", None, 1, "ck_invite_code_space_pair"),
        ("lineage", None, 1, "ck_invite_code_space_pair"),
        # household 码 max_uses=2 或 NULL（一次性码必须显式排除 NULL）
        ("household", 1, 2, "ck_invite_code_max_uses"),
        ("household", 1, None, "ck_invite_code_max_uses"),
        # 未知 kind
        ("family", 1, 1, "ck_invite_code_kind"),
    ],
)
def test_invite_code_check_constraints_reject_invalid_rows(
    tmp_path: Path, kind: str, space_id: int | None, max_uses: int | None, expected_check: str
) -> None:
    _migrate("upgrade", "head", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)
        with pytest.raises(IntegrityError, match=expected_check):
            _insert_code(
                connection, code="PROBE001", kind=kind, space_id=space_id, max_uses=max_uses
            )
        connection.rollback()


def test_account_binding_check_constraint_rejects_unknown_status(tmp_path: Path) -> None:
    _migrate("upgrade", "head", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)
        with pytest.raises(IntegrityError, match="ck_account_binding_status"):
            connection.execute(
                text(
                    "INSERT INTO account_bindings (initiator_id, target_id, person_id, "
                    "status, created_at) VALUES (1, 2, NULL, 'approved', 0)"
                )
            )
        connection.rollback()


# ---- 3. 合法行 ACCEPTED 正向断言（真实迁移库）----


def test_valid_rows_accepted_on_migrated_schema(tmp_path: Path) -> None:
    _migrate("upgrade", "head", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)

        # household/lineage 码：space_id 非空、max_uses=1
        _insert_code(connection, code="HOME0001", kind="household", space_id=1, max_uses=1)
        _insert_code(connection, code="LINE0001", kind="lineage", space_id=1, max_uses=1)
        # stranger 码：space_id 必须 NULL，max_uses 可 NULL（不限次）或 >= 1
        _insert_code(connection, code="STRG0001", kind="stranger", space_id=None, max_uses=None)
        _insert_code(connection, code="STRG0002", kind="stranger", space_id=None, max_uses=5)
        # 合法 binding（撞名建档 pending 形态）
        connection.execute(
            text(
                "INSERT INTO account_bindings (initiator_id, target_id, person_id, "
                "status, created_at) VALUES (1, 2, NULL, 'pending', 0)"
            )
        )
        connection.commit()

        rows = connection.execute(
            text("SELECT code, kind, space_id, max_uses FROM invite_codes ORDER BY code")
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("HOME0001", "household", 1, 1),
            ("LINE0001", "lineage", 1, 1),
            ("STRG0001", "stranger", None, None),
            ("STRG0002", "stranger", None, 5),
        ]
        assert connection.execute(text("SELECT COUNT(*) FROM account_bindings")).scalar_one() == 1

        # 正向形态下 FK 删除动作契约（head = 0032 之后）：creator SET NULL、space CASCADE
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "SET NULL"),
            ("family_spaces", "CASCADE"),
        }
        connection.rollback()


# ---- 4. 0032 重建迁移：行保留、FK 放宽、downgrade fail-closed ----


def _load_migration_0032() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0032", MIGRATION_0032_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_in_process(module: ModuleType, connection, direction: str) -> None:
    context = MigrationContext.configure(connection)
    operations = Operations(context)
    module.op = operations
    getattr(module, direction)()


def test_0032_upgrade_preserves_rows_and_relaxes_creator_fk(tmp_path: Path) -> None:
    _migrate("upgrade", "0031_add_account_bindings", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)
        _insert_code(connection, code="KEEP0001", kind="household", space_id=1, max_uses=1)
        _insert_code(connection, code="KEEP0002", kind="stranger", space_id=None, max_uses=5)
        connection.execute(text("UPDATE invite_codes SET used_count = 2 WHERE code = 'KEEP0002'"))
        connection.commit()

    _migrate("upgrade", "0032_invite_codes_creator_set_null", data_dir=tmp_path)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT code, kind, creator_id, used_count FROM invite_codes ORDER BY code")
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("KEEP0001", "household", 2, 0),
            ("KEEP0002", "stranger", 2, 2),
        ]
        notnull = {
            row["name"]: row["notnull"]
            for row in connection.exec_driver_sql("PRAGMA table_info(invite_codes)").mappings()
        }
        assert notnull["creator_id"] == 0  # 放宽为 nullable
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "SET NULL"),
            ("family_spaces", "CASCADE"),
        }
        assert _index_names(connection, "invite_codes") == {
            "ix_invite_codes_creator",
            "ix_invite_codes_space",
        }

        # 创建者删除：码行保留（使用计数/撤销历史不被级联抹掉），指针置空
        connection.execute(text("DELETE FROM users WHERE id = 2"))
        connection.commit()
        rows = connection.execute(
            text("SELECT code, creator_id FROM invite_codes ORDER BY code")
        ).fetchall()
        assert [tuple(row) for row in rows] == [("KEEP0001", None), ("KEEP0002", None)]


def test_0032_downgrade_fail_closed_on_orphan_creator_rows(tmp_path: Path) -> None:
    _migrate("upgrade", "head", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)
        _insert_code(connection, code="ORPH0001", kind="household", space_id=1, max_uses=1)
        connection.commit()
        connection.execute(text("DELETE FROM users WHERE id = 2"))
        connection.commit()

    result = _alembic("downgrade", "0031_add_account_bindings", data_dir=tmp_path)
    assert result.returncode != 0
    assert "explicit data decision" in result.stderr
    # fail-closed：版本停在 head，schema 未被回退
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version.startswith("0032")
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "SET NULL"),
            ("family_spaces", "CASCADE"),
        }


def test_0032_round_trip_restores_restrict_when_no_orphans(tmp_path: Path) -> None:
    _migrate("upgrade", "head", data_dir=tmp_path)
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        _seed_actors(connection)
        _insert_code(connection, code="CYCL0001", kind="household", space_id=1, max_uses=1)
        connection.commit()

    _migrate("downgrade", "0031_add_account_bindings", data_dir=tmp_path)
    with engine.connect() as connection:
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "RESTRICT"),
            ("family_spaces", "CASCADE"),
        }
        notnull = {
            row["name"]: row["notnull"]
            for row in connection.exec_driver_sql("PRAGMA table_info(invite_codes)").mappings()
        }
        assert notnull["creator_id"] == 1
        assert (
            connection.scalar(text("SELECT used_count FROM invite_codes WHERE code = 'CYCL0001'"))
            == 0
        )  # 数据逐列拷贝无丢失

    _migrate("upgrade", "head", data_dir=tmp_path)
    with engine.connect() as connection:
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "SET NULL"),
            ("family_spaces", "CASCADE"),
        }


@pytest.mark.parametrize("foreign_keys_on", [True, False], ids=["fk-on", "fk-off"])
def test_0032_rebuild_preserves_connection_foreign_key_setting(
    tmp_path: Path, foreign_keys_on: bool
) -> None:
    """database-guidelines：迁移不得改变调用方连接的 foreign_keys 状态。"""
    _migrate("upgrade", "0031_add_account_bindings", data_dir=tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'db' / 'app.db'}")
    module = _load_migration_0032()
    expected = 1 if foreign_keys_on else 0
    with engine.connect() as connection:
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if foreign_keys_on else 'OFF'}")
        assert connection.scalar(text("PRAGMA foreign_keys")) == expected

        _run_in_process(module, connection, "upgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == expected
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "SET NULL"),
            ("family_spaces", "CASCADE"),
        }

        _run_in_process(module, connection, "downgrade")
        assert connection.scalar(text("PRAGMA foreign_keys")) == expected
        assert _fk_actions(connection, "invite_codes") == {
            ("users", "RESTRICT"),
            ("family_spaces", "CASCADE"),
        }
