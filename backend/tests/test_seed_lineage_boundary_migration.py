"""0052 演示种子越权成员资格修正：只删指定的那一行，不做 schema 变更。

背景（09-19 家族空间越权修复）：家庭空间成员资格不等于家族空间成员资格。
演示清单曾把朱元璋声明为李氏家族（lineage）的 active 成员，而用户实际只加入了
李家家庭空间——正是这一行让他无需任何家族空间审批就读到了李氏家族树。清单已
不再声明它，但种子收敛是 insert-only（绝不删行），故由本迁移一次性修正。

契约：只匹配指定的 (lineage 空间名, 成员名) 且 active、由该空间 owner 添加的行；
不碰用户自己的申请、不碰其他空间与用户；无 schema 变更；降级不逆向恢复数据，
并让父级 0051 的拒绝合同先于版本移动。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).parents[1]
HEAD = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
PARENT = "0051_run_event_timing"

RUNNER = r"""
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from alembic import command
from alembic.config import Config

ddl = []
@event.listens_for(Engine, 'before_cursor_execute')
def witness(conn, cursor, statement, parameters, context, executemany):
    if statement.lstrip().upper().startswith(('CREATE ', 'ALTER ', 'DROP ')):
        ddl.append(statement.split()[0].upper())
try:
    getattr(command, sys.argv[1])(Config('alembic.ini'), sys.argv[2])
finally:
    print('ACTUAL_ALEMBIC_DDL_COUNT=' + str(len(ddl)))
"""


def _migrate(data_dir, direction, target, *, expect_success=True):
    result = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-c", RUNNER, direction, target],
        cwd=BACKEND,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


def _engine(data_dir):
    return create_engine(f"sqlite:///{data_dir / 'db/app.db'}")


def _head(engine) -> str:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def _seed_demo_lineage_memberships(engine) -> None:
    """复现旧演示清单授予的越权行，以及必须保留的对照行。"""
    with engine.begin() as connection:
        for name in ("朱元璋", "李贞", "朱佛女"):
            connection.execute(
                text(
                    "INSERT INTO users (name, created_at, gender, privacy_mode, profile_status) "
                    "VALUES (:name, CURRENT_TIMESTAMP, 'm', 'handover', 'identity_confirmed')"
                ),
                {"name": name},
            )
        connection.execute(
            text(
                "INSERT INTO family_spaces (name, owner_id, kind, created_at) "
                "VALUES ('李氏家族', 2, 'lineage', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO family_spaces (name, owner_id, kind, created_at) "
                "VALUES ('朱氏皇族', 1, 'lineage', CURRENT_TIMESTAMP)"
            )
        )
        # 越权行：朱元璋被李氏家族 owner 拉进 lineage（不是他本人申请）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 1, 2, 'member', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：该空间 owner 自己的成员行
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 2, 2, 'space_admin', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：朱元璋在朱氏皇族是 owner（合法家族成员资格，必须保留）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (2, 1, 1, 'space_admin', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：朱佛女本人 pending 申请加入李氏家族（用户自己的申请，必须保留）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 3, 3, 'member', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )


def _membership_pairs(engine) -> list[tuple[int, int, str]]:
    with engine.connect() as connection:
        return [
            (row[0], row[1], row[2])
            for row in connection.execute(
                text("SELECT space_id, user_id, status FROM space_members ORDER BY id")
            )
        ]


def test_upgrade_removes_only_the_wrong_seed_membership(tmp_path):
    """只删除指定的越权 lineage 成员行，保留 owner 行与用户自己的 pending 申请。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_demo_lineage_memberships(engine)
        before_schema = sorted(
            (row[0], row[1], row[2])
            for row in engine.connect().execute(
                text("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
            )
        )

        _migrate(tmp_path, "upgrade", HEAD)

        pairs = _membership_pairs(engine)
        assert (1, 1, "active") not in pairs  # 越权行消失
        assert (1, 2, "active") in pairs  # 空间 owner 保留
        assert (2, 1, "active") in pairs  # 合法家族成员资格保留
        assert (1, 3, "pending") in pairs  # 用户自己的申请保留
        # 纯数据修正：schema 完全不变
        after_schema = sorted(
            (row[0], row[1], row[2])
            for row in engine.connect().execute(
                text("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
            )
        )
        assert after_schema == before_schema
    finally:
        engine.dispose()


def test_upgrade_is_idempotent_and_keeps_other_spaces_untouched(tmp_path):
    """重复执行不报错；其他空间与用户的成员行零改动。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_demo_lineage_memberships(engine)
        _migrate(tmp_path, "upgrade", HEAD)
        first = _membership_pairs(engine)

        # 直接重跑迁移函数：幂等（目标行已不存在）
        _migrate(tmp_path, "downgrade", PARENT)
        _migrate(tmp_path, "upgrade", HEAD)
        assert _membership_pairs(engine) == first
    finally:
        engine.dispose()


def test_downgrade_does_not_restore_the_wrong_membership(tmp_path):
    """降级不把已删除的越权成员资格恢复为旧种子值。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        assert _head(engine) == HEAD
        result = _migrate(tmp_path, "downgrade", PARENT)
        assert result.returncode == 0, result.stderr
        assert _head(engine) == PARENT
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM space_members")) == 0
    finally:
        engine.dispose()


def test_deep_downgrade_honours_parent_refusal_before_version_move(tmp_path):
    """跨过 0051 的降级必须让父级拒绝先于版本移动与任何 DDL。"""
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (name, created_at, gender, privacy_mode, profile_status) "
                    "VALUES ('timer', CURRENT_TIMESTAMP, 'm', 'handover', 'identity_confirmed')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO agent_runs (id, session_id, job_id, kind, status, attempt, "
                    "max_attempts, policy_version, tool_allowlist_json, created_at, updated_at, "
                    "first_leased_at) "
                    "VALUES (1, 1, 1, 'assistant', 'running', 1, 3, 'p', '[]', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        result = _migrate(tmp_path, "downgrade", "-2", expect_success=False)

        assert result.returncode != 0
        assert "retain data and roll forward" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout
        assert _head(engine) == HEAD
        # 版本未动，成员表也未被降级路径改动
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM space_members")) == 0
    finally:
        engine.dispose()
