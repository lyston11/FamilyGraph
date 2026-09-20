"""0054 演示种子 household 名册修正：只改指定的那一行，不做 schema 变更。

背景（09-20 走查）：演示清单曾把 朱元璋 放进「李家」household，但他在该空间内
没有任何结构事实（他与 朱佛女 是 sibling，而 sibling 不产生 Relation），因此在
家庭卡上渲染成孤立节点——两个人同处一室却看不出任何联系。清单已改为 朱佛女
（李贞之妻），但种子收敛是 insert-only（绝不删行），故由本迁移一次性修正。

契约：只匹配指定的 (household 空间名, 成员名) 且 active、由该空间 owner 添加的
行；不碰用户自己的申请、不碰其他空间与用户；无 schema 变更；补入幂等；降级不逆向
恢复数据，并让父级 0053 的拒绝合同先于版本移动。
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
PARENT = "0053_member_approval_and_labels"
# 本文件只验证 0054 的纯数据修正：目标固定为 0054 自己，而不是会随新迁移前进的
# `head`（后续迁移可能再改 schema，与本迁移「schema 完全不变」的断言无关）。
SUBJECT = "0054_seed_household_roster_fix"

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


def _seed_legacy_household_roster(engine) -> None:
    """复现旧演示清单的 household 名册，以及必须保留的对照行。"""
    with engine.begin() as connection:
        for name in ("朱元璋", "李贞", "朱佛女", "马皇后"):
            connection.execute(
                text(
                    "INSERT INTO users (name, created_at, gender, privacy_mode, profile_status) "
                    "VALUES (:name, CURRENT_TIMESTAMP, 'm', 'handover', 'identity_confirmed')"
                ),
                {"name": name},
            )
        # 1 李家（household，owner=李贞=2）；2 马府（household，owner=马皇后=4，对照）
        connection.execute(
            text(
                "INSERT INTO family_spaces (name, owner_id, kind, created_at) "
                "VALUES ('李家', 2, 'household', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO family_spaces (name, owner_id, kind, created_at) "
                "VALUES ('马府', 4, 'household', CURRENT_TIMESTAMP)"
            )
        )
        # 旧种子行：朱元璋 被李家 owner 拉进 household（不是他本人申请）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 1, 2, 'member', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：该空间 owner 自己的成员行（必须保留）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 2, 2, 'space_admin', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：另一个空间里同样由 owner 添加的成员行（必须保留）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (2, 1, 4, 'member', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：朱佛女本人 pending 申请加入李家（用户自己的申请，必须保留）
        connection.execute(
            text(
                "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                "created_at, updated_at) "
                "VALUES (1, 3, 3, 'member', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 对照：李家 已有一条 PFV 投影（迁移应标 stale，不应删除）
        connection.execute(
            text(
                "INSERT INTO personal_family_views "
                "(space_id, viewer_account_id, root_user_id, status, created_at, updated_at) "
                "VALUES (1, 1, 2, 'current', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )


def _membership_rows(engine) -> list[tuple[int, int, str, str]]:
    with engine.connect() as connection:
        return [
            (row[0], row[1], row[2], row[3])
            for row in connection.execute(
                text("SELECT space_id, user_id, status, role FROM space_members ORDER BY id")
            )
        ]


def test_upgrade_swaps_only_the_designated_household_member(tmp_path):
    """只改指定的 household 成员行：移除 朱元璋、补入 朱佛女，其余行原样保留。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_legacy_household_roster(engine)
        before = _membership_rows(engine)
        assert (1, 1, "active", "member") in before  # 旧种子行
        assert (1, 3, "pending", "member") in before  # 朱佛女自己的申请

        _migrate(tmp_path, "upgrade", SUBJECT)

        after = _membership_rows(engine)
        assert (1, 1, "active", "member") not in after, "朱元璋的 household 成员行应被移除"
        # 用户自己的 pending 申请必须原样保留（不是 owner 添加的行），且**不得**被
        # 改成 active——唯一约束建在 (space_id, user_id) 不分状态，若迁移把它改活就
        # 等于绕过房主审批边界，故此处跳过补入。
        assert (1, 3, "pending", "member") in after
        assert (1, 3, "active", "member") not in after, "不得绕过审批把 pending 改活"
        # 李家 owner 行与另一空间里同由 owner 添加的行必须保留
        assert (1, 2, "active", "space_admin") in after
        assert (2, 1, "active", "member") in after
        # 只动了指定的一行：净变化 = 移除 1（补入因 pending 冲突被跳过）
        assert len(after) == len(before) - 1
    finally:
        engine.dispose()


def test_upgrade_adds_the_replacement_when_no_row_exists(tmp_path):
    """替换成员没有任何既有行时，迁移补入 active 成员行。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        with engine.begin() as connection:
            for name in ("朱元璋", "李贞", "朱佛女"):
                connection.execute(
                    text(
                        "INSERT INTO users (name, created_at, gender, privacy_mode, "
                        "profile_status) VALUES (:name, CURRENT_TIMESTAMP, 'm', 'handover', "
                        "'identity_confirmed')"
                    ),
                    {"name": name},
                )
            connection.execute(
                text(
                    "INSERT INTO family_spaces (name, owner_id, kind, created_at) "
                    "VALUES ('李家', 2, 'household', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                    "created_at, updated_at) "
                    "VALUES (1, 1, 2, 'member', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO space_members (space_id, user_id, added_by, role, status, "
                    "created_at, updated_at) "
                    "VALUES (1, 2, 2, 'space_admin', 'active', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        _migrate(tmp_path, "upgrade", SUBJECT)

        rows = _membership_rows(engine)
        assert (1, 1, "active", "member") not in rows
        assert (1, 3, "active", "member") in rows, "朱佛女应被补入为 active 成员"
        assert (1, 2, "active", "space_admin") in rows
    finally:
        engine.dispose()


def test_upgrade_is_idempotent_and_leaves_schema_alone(tmp_path):
    """重复 upgrade 不重复插入；本迁移不产生任何 DDL。

    夹具里 朱佛女 已有 pending 行，故这里同时覆盖「撞唯一约束时不插入」的路径——
    重复执行必须仍不插入、不报错。
    """
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_legacy_household_roster(engine)
        first = _migrate(tmp_path, "upgrade", SUBJECT)
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in first.stdout
        once = _membership_rows(engine)

        second = _migrate(tmp_path, "upgrade", SUBJECT)
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in second.stdout
        assert _membership_rows(engine) == once
    finally:
        engine.dispose()


def test_upgrade_stales_affected_views_without_deleting_them(tmp_path):
    """迁移把受影响空间的投影标 stale（不删行、不写计算内容）。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_legacy_household_roster(engine)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM personal_family_views WHERE space_id = 1")
                )
                == "current"
            )

        _migrate(tmp_path, "upgrade", SUBJECT)

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM personal_family_views WHERE space_id = 1")
                )
                == "stale"
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM personal_family_views")) == 1
            ), "只标 stale，不删行"
    finally:
        engine.dispose()


def test_downgrade_refuses_before_any_ddl_when_ancestor_would_refuse(tmp_path):
    """降级跨过结构迁移时，父级拒绝合同必须先于任何 DDL 执行。

    本迁移自身无 schema（downgrade 只是空操作，数据修正刻意不逆向），所以版本会
    正常退到父 revision；要守住的契约是「**没有任何 DDL 在父级拒绝之前发生**」——
    否则会留下半降级 schema（SQLite 的 DROP TABLE 不保证事务回滚）。
    """
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        with engine.begin() as connection:
            # 制造 0053 的拒绝证据（owner-approval 行），迫使父级 downgrade 拒绝
            connection.execute(
                text(
                    "INSERT INTO space_member_approvals (member_id, space_id, origin, "
                    "created_at) VALUES (1, 1, 'invite', CURRENT_TIMESTAMP)"
                )
            )

        # 目的地越过 0053（结构迁移）才会执行它的拒绝合同。
        result = _migrate(
            tmp_path, "downgrade", "0052_seed_lineage_membership_boundary", expect_success=False
        )

        assert result.returncode != 0, "父级拒绝合同应中止降级"
        assert "retain data and roll forward" in result.stderr
        assert "ACTUAL_ALEMBIC_DDL_COUNT=0" in result.stdout, "拒绝必须先于任何 DDL"
        # 两张 0053 建的表仍在（正是「拒绝先于 DDL」的结果）
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM space_member_approvals")) == 1
            assert connection.scalar(text("SELECT count(*) FROM member_relation_labels")) == 0
    finally:
        engine.dispose()


def test_deep_relative_downgrade_refuses_before_moving_the_version(tmp_path):
    """深层相对目标是歧义的：走位复现必须把该错误提前到任何动作之前。

    `-4` 在祖先 revision 上是歧义的（Alembic 抛 "Ambiguous walk"）。该错误必须
    在版本移动与本迁移的数据修正**之前**抛出，否则会留下"版本未动、数据已改"或
    半降级 schema 的中间态。
    """
    _migrate(tmp_path, "upgrade", HEAD)
    engine = _engine(tmp_path)
    try:
        before = _head(engine)
        assert before == HEAD

        result = _migrate(tmp_path, "downgrade", "-4", expect_success=False)

        assert result.returncode != 0
        assert "Ambiguous walk" in result.stderr
        assert _head(engine) == before, "歧义走位必须中止，版本不得移动"
        with engine.connect() as connection:
            # 祖先的 DDL 与本迁移的数据修正都不得发生
            assert connection.scalar(text("SELECT count(*) FROM space_members")) == 0
            assert connection.scalar(text("SELECT count(*) FROM space_member_approvals")) == 0
    finally:
        engine.dispose()


def test_downgrade_does_not_restore_the_removed_membership(tmp_path):
    """数据修正刻意不逆向：降级不恢复被移除的成员资格。"""
    _migrate(tmp_path, "upgrade", PARENT)
    engine = _engine(tmp_path)
    try:
        _seed_legacy_household_roster(engine)
        _migrate(tmp_path, "upgrade", SUBJECT)
        assert (1, 1, "active", "member") not in _membership_rows(engine)

        _migrate(tmp_path, "downgrade", PARENT)

        rows = _membership_rows(engine)
        assert (1, 1, "active", "member") not in rows, "降级不得恢复旧种子成员资格"
        assert (1, 3, "pending", "member") in rows, "降级不得改动用户的申请行"
    finally:
        engine.dispose()
