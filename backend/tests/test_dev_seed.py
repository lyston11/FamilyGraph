"""dev 种子模块合同（09-05 dev-seed-demo-data + family-profile-nav-disclosure R3/R6 + 增量收敛）。

- DEV_SEED_DEMO_DATA 未开启（默认 "0"）→ maybe_seed_demo_data 零写入；
- env=1 + 空库 → 全量播种：14 用户（含出生日期）/4 空间（household×2 + lineage×2，
  两个 household 显式配对所属 lineage）/23 成员行/17 关系/17 confirmed SourceFact
  （全局一份）/基础五类披露全局开放，PIN 123456 校验通过；进程内重复调用
  （_SEED_DONE）幂等跳过；
- env=1 + 非空库 → insert-only 增量补缺：清单缺失行自动补齐（用户按姓名 /
  空间按名 / 成员行按 (space,user) / 关系边按两人任一方向 pending|active 匹配），
  既有行（含清单外用户）零改动，返回 True；清单已完全收敛 → 返回 False 且零写入；
- 种子不创建/修改 system_admins（admin bootstrap 专属职责，09-04 合同）；
- --reset：先备份到 backups/pre-reset-*.db 再删除 db/-wal/-shm。
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import create_user_with_pin

from app import config, dev_seed
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.models.user import User
from app.models.v2_foundation import DisclosurePreference
from app.services import admin_bootstrap
from app.utils import security

_DEMO_NAMES = {
    "王德海",
    "周秀英",
    "王建军",
    "王小雨",
    "王远山",
    "王小虎",
    "赵桂兰",
    "王秀兰",
    "张伟",
    "刘婷婷",
    "王朵朵",
    "李国强",
    "孙桂芳",
    "李念",
}


@pytest.fixture(autouse=True)
def _reset_seed_done():
    """进程级防重入单例按测试隔离（同 conftest 对 _BOOTSTRAP_DONE 的处理）。"""
    dev_seed._SEED_DONE = False
    yield
    dev_seed._SEED_DONE = False


def test_disabled_by_default_writes_nothing(db_session, monkeypatch) -> None:
    """env 未开启（默认 "0"）→ 返回 False 且零写入。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "0")
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(User).count() == 0
    assert db_session.query(FamilySpace).count() == 0
    assert db_session.query(Relation).count() == 0


def test_seed_creates_demo_family_on_empty_db(db_session, monkeypatch) -> None:
    """env=1 + 空库 → 完整演示数据集；进程内重复调用幂等跳过。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    users = db_session.query(User).all()
    assert len(users) == 14
    by_name = {user.name: user for user in users}
    assert set(by_name) == _DEMO_NAMES
    for user in users:
        # claimed + identity_confirmed + PIN 123456（hash_pin 存储，非明文）
        assert user.profile_status == "identity_confirmed"
        assert user.account.status == "claimed"
        assert user.account.pin_must_change is False
        assert user.account.pin_hash != "123456"
        assert security.verify_pin("123456", user.account.pin_hash)
        # R3：结构化出生日期（solar）；王小虎 2018 = 未成年人
        assert user.birth is not None and user.birth.get("cal_type") == "solar"
        assert user.birth.get("date")
    assert by_name["王远山"].birth["date"] == "1940-05-12"
    assert by_name["王小虎"].birth["date"] == "2018-09-14"
    assert by_name["王朵朵"].birth["date"] == "2021-05-09"
    assert by_name["孙桂芳"].birth["date"] == "1968-04-17"
    assert by_name["李念"].birth["date"] == "2012-09-23"

    spaces = db_session.query(FamilySpace).all()
    assert len(spaces) == 4
    by_space_name = {space.name: space for space in spaces}
    household = by_space_name["王德海家"]
    lineage = by_space_name["王氏家族"]
    second_space = by_space_name["李国强家"]
    second_lineage = by_space_name["李氏家族"]
    assert household.kind == "household" and lineage.kind == "lineage"
    assert second_space.kind == "household" and second_lineage.kind == "lineage"
    assert household.owner_id == by_name["王德海"].id
    assert lineage.owner_id == by_name["王德海"].id
    assert second_space.owner_id == by_name["李国强"].id
    assert second_lineage.owner_id == by_name["李国强"].id
    assert {space.owner_id for space in spaces} == {by_name["王德海"].id, by_name["李国强"].id}
    # 家族配对：本次新建 household 显式挂入所属 lineage（「当前家族空间」切换数据基础）
    assert household.lineage_space_id == lineage.id
    assert second_space.lineage_space_id == second_lineage.id

    # 王德海家 6 人 / 王氏家族 11 人（旁系亲属只进家族空间）/ 李国强家 2 人 /
    # 李氏家族 4 人（李国强+王德海 跨家族 + 李家两人）
    expected_rosters = (
        (household, 6, by_name["王德海"].id),
        (lineage, 11, by_name["王德海"].id),
        (second_space, 2, by_name["李国强"].id),
        (second_lineage, 4, by_name["李国强"].id),
    )
    for space, size, admin_id in expected_rosters:
        members = db_session.query(SpaceMember).filter_by(space_id=space.id).all()
        assert len(members) == size
        assert all(member.status == "active" for member in members)
        admin_rows = [member for member in members if member.role == "space_admin"]
        assert len(admin_rows) == 1
        assert admin_rows[0].user_id == admin_id
        assert sum(1 for member in members if member.role == "member") == size - 1
    # 王德海以 member 身份进入李国强家（跨空间成员资格）
    second_members = db_session.query(SpaceMember).filter_by(space_id=second_space.id).all()
    assert {member.user_id for member in second_members} == {
        by_name["李国强"].id,
        by_name["王德海"].id,
    }
    # 李氏家族名册：李国强 + 王德海 + 孙桂芳 + 李念
    second_lineage_members = (
        db_session.query(SpaceMember).filter_by(space_id=second_lineage.id).all()
    )
    assert {member.user_id for member in second_lineage_members} == {
        by_name["李国强"].id,
        by_name["王德海"].id,
        by_name["孙桂芳"].id,
        by_name["李念"].id,
    }
    assert db_session.query(SpaceMember).count() == 23

    relations = db_session.query(Relation).all()
    assert len(relations) == 17
    assert {relation.status for relation in relations} == {"active"}
    spouse_edges = [r for r in relations if r.dir_class == "spouse"]
    elder_edges = [r for r in relations if r.dir_class == "elder"]
    assert len(spouse_edges) == 5 and len(elder_edges) == 12
    # v1 边方向语义：to_user 是 from_user 的 dir_class（ elder/younger 指向长辈）
    by_id = {user.id: user.name for user in users}
    assert {(by_id[r.from_user], by_id[r.to_user]) for r in spouse_edges} == {
        ("王德海", "周秀英"),
        ("王远山", "赵桂兰"),
        ("王秀兰", "张伟"),
        ("王建军", "刘婷婷"),
        ("李国强", "孙桂芳"),
    }
    assert {(by_id[r.from_user], by_id[r.to_user]) for r in elder_edges} == {
        ("王德海", "王远山"),
        ("王建军", "王德海"),
        ("王小雨", "王德海"),
        ("王小虎", "王建军"),
        ("王德海", "赵桂兰"),
        ("王秀兰", "王远山"),
        ("王秀兰", "赵桂兰"),
        ("王小虎", "刘婷婷"),
        ("王朵朵", "王建军"),
        ("王朵朵", "刘婷婷"),
        ("李念", "李国强"),
        ("李念", "孙桂芳"),
    }

    facts = db_session.query(SourceFact).all()
    assert len(facts) == 17  # 全局事实（space_id=NULL），四空间共享投影
    assert {fact.state for fact in facts} == {"confirmed"}
    assert {fact.fact_type for fact in facts} == {"spouse", "biological_parent"}
    assert {fact.space_id for fact in facts} == {None}
    pairs = {
        (fact.fact_type, by_id[fact.subject_user_id], by_id[fact.object_user_id]) for fact in facts
    }
    assert pairs == {
        ("spouse", "王德海", "周秀英"),
        ("spouse", "王远山", "赵桂兰"),
        ("spouse", "王秀兰", "张伟"),
        ("spouse", "王建军", "刘婷婷"),
        ("spouse", "李国强", "孙桂芳"),
        ("biological_parent", "王远山", "王德海"),
        ("biological_parent", "赵桂兰", "王德海"),
        ("biological_parent", "王远山", "王秀兰"),
        ("biological_parent", "赵桂兰", "王秀兰"),
        ("biological_parent", "王德海", "王建军"),
        ("biological_parent", "王德海", "王小雨"),
        ("biological_parent", "王建军", "王小虎"),
        ("biological_parent", "刘婷婷", "王小虎"),
        ("biological_parent", "王建军", "王朵朵"),
        ("biological_parent", "刘婷婷", "王朵朵"),
        ("biological_parent", "李国强", "李念"),
        ("biological_parent", "孙桂芳", "李念"),
    }

    # R6：基础五类披露全局开放（成员互见）；高敏感五类保持关闭（Q4=b 仅为可开）
    prefs = db_session.query(DisclosurePreference).all()
    assert len(prefs) == 14 * 5  # 14 用户 × 基础五类
    assert all(pref.scope == "global" and pref.allowed for pref in prefs)
    assert {pref.category for pref in prefs} == {
        "avatar",
        "photos",
        "dates",
        "bio",
        "attachments",
    }

    # 幂等（路径一）：_SEED_DONE 单例跳过
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(User).count() == 14
    # 幂等（路径二）：清掉单例后再跑 → 清单已完全收敛，零写入返回 False
    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(User).count() == 14
    assert db_session.query(SourceFact).count() == 17


def test_non_empty_db_backfills_manifest_and_preserves_existing_user(
    db_session, monkeypatch
) -> None:
    """env=1 + 非空库 → 增量补缺：清单行全部补齐，清单外既有用户零变动。

    insert-only 红线：既有用户的 id/PIN/档案/披露不动，也不被拉进任何空间、
    关系或事实。
    """
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    existing = create_user_with_pin(db_session, "既有用户", "999888")

    assert dev_seed.maybe_seed_demo_data(db_session) is True

    # 清单外既有用户：同 id、PIN 仍 999888、无 birth、不入任何空间/关系/事实/披露
    untouched = db_session.get(User, existing.id)
    assert untouched is not None
    assert untouched.name == "既有用户"
    assert untouched.birth is None
    assert untouched.account.status == "claimed"
    assert security.verify_pin("999888", untouched.account.pin_hash)
    assert db_session.query(SpaceMember).filter_by(user_id=existing.id).count() == 0
    assert (
        db_session.query(Relation)
        .filter((Relation.from_user == existing.id) | (Relation.to_user == existing.id))
        .count()
        == 0
    )
    assert (
        db_session.query(SourceFact)
        .filter(
            (SourceFact.subject_user_id == existing.id) | (SourceFact.object_user_id == existing.id)
        )
        .count()
        == 0
    )
    assert db_session.query(DisclosurePreference).filter_by(profile_id=existing.id).count() == 0

    # 固定清单全部补齐：14 演示用户 / 4 空间 / 23 成员行 / 17 关系 / 17 fact
    demo_users = db_session.query(User).filter(User.name != "既有用户").all()
    assert {user.name for user in demo_users} == _DEMO_NAMES
    assert len(demo_users) == 14
    assert db_session.query(FamilySpace).count() == 4
    assert db_session.query(SpaceMember).count() == 23
    assert db_session.query(Relation).count() == 17
    assert db_session.query(SourceFact).count() == 17


def test_manifest_extension_backfills_missing_rows_only(db_session, monkeypatch) -> None:
    """往固定清单加成员/生辰/边 → 复位单例再跑：缺失行自动补齐，既有行零改动。

    模拟「编辑 dev_seed.py 常量 → 重启 api」的真实工作流（monkeypatch 模块常量）。
    """
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    before = {
        user.name: (user.id, user.gender, user.birth, user.profile_status, user.privacy_mode)
        for user in db_session.query(User).all()
    }
    assert len(before) == 14

    # 清单扩展：lineage 名册追加王二丫（2024 年生）+ 一条亲子结构边
    monkeypatch.setattr(
        dev_seed,
        "_SEED_LINEAGE_EXTRA_MEMBERS",
        (*dev_seed._SEED_LINEAGE_EXTRA_MEMBERS, ("王二丫", "f")),
    )
    monkeypatch.setattr(dev_seed, "_SEED_BIRTHS", {**dev_seed._SEED_BIRTHS, "王二丫": (2024, 1, 1)})
    monkeypatch.setattr(
        dev_seed, "_SEED_EDGES", (*dev_seed._SEED_EDGES, ("王二丫", "王建军", "elder"))
    )

    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    # 只增：15 用户 / 24 成员行 / 18 关系 / 18 fact
    assert db_session.query(User).count() == 15
    assert db_session.query(SpaceMember).count() == 24
    assert db_session.query(Relation).count() == 18
    assert db_session.query(SourceFact).count() == 18
    # 既有 14 用户行 id 与关键字段零变动
    for user in db_session.query(User).all():
        if user.name == "王二丫":
            continue
        assert (user.id, user.gender, user.birth, user.profile_status, user.privacy_mode) == (
            before[user.name]
        )
    # 新成员落在王氏家族（旁系只进家族空间），王德海家仍 6 人
    erke = db_session.query(User).filter_by(name="王二丫").one()
    assert erke.birth == {"cal_type": "solar", "date": "2024-01-01", "is_leap_month": False}
    lineage = db_session.query(FamilySpace).filter_by(name="王氏家族").one()
    assert (
        db_session.query(SpaceMember).filter_by(space_id=lineage.id, user_id=erke.id).count() == 1
    )
    household = db_session.query(FamilySpace).filter_by(name="王德海家").one()
    assert db_session.query(SpaceMember).filter_by(space_id=household.id).count() == 6
    # 新边 active + 随新建一起落的 confirmed 全局 fact（王建军 是 王二丫 的父亲）
    edge = db_session.query(Relation).filter_by(from_user=erke.id, dir_class="elder").one()
    assert edge.to_user == before["王建军"][0]
    assert edge.status == "active"
    fact = (
        db_session.query(SourceFact)
        .filter_by(subject_user_id=before["王建军"][0], object_user_id=erke.id)
        .one()
    )
    assert fact.fact_type == "biological_parent"
    assert fact.state == "confirmed"
    assert fact.space_id is None
    # 披露偏好只补新成员的 5 行（15×5），既有用户披露不动
    assert db_session.query(DisclosurePreference).count() == 15 * 5


def test_converged_manifest_returns_false_and_writes_nothing(db_session, monkeypatch) -> None:
    """env=1 + 清单已完全收敛 → 返回 False，各表计数零变动。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is False

    assert db_session.query(User).count() == 14
    assert db_session.query(FamilySpace).count() == 4
    assert db_session.query(SpaceMember).count() == 23
    assert db_session.query(Relation).count() == 17
    assert db_session.query(SourceFact).count() == 17
    assert db_session.query(DisclosurePreference).count() == 14 * 5


def test_seed_never_touches_system_admins(db_session, monkeypatch) -> None:
    """红线：种子不创建/修改 system_admins；bootstrap 先行（先管理员后演示数据）。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    # conftest 夹具已把 _BOOTSTRAP_DONE 复位为 False：先走真实 bootstrap
    admin_bootstrap.run_startup_preflight(db_session)
    assert db_session.query(SystemAdmin).count() == 1
    assert admin_bootstrap._BOOTSTRAP_DONE is True

    assert dev_seed.maybe_seed_demo_data(db_session) is True

    admins = db_session.query(SystemAdmin).all()
    assert len(admins) == 1
    assert admins[0].username == "admin"
    assert db_session.query(SystemAdminAccount).count() == 1


def test_reset_backs_up_then_removes_db_files(db_session, monkeypatch, tmp_path) -> None:
    """--reset：备份当前 db 到 backups/pre-reset-*.db，再删除 db/-wal/-shm。"""
    fake_db = tmp_path / "db" / "app.db"
    fake_backups = tmp_path / "backups"
    fake_db.parent.mkdir(parents=True)
    source = sqlite3.connect(str(fake_db))
    try:
        source.execute("CREATE TABLE demo (x INTEGER)")
        source.commit()
    finally:
        source.close()
    # 零长度 -wal/-shm（SQLite「无内容」合法形态），断言三件套一并删除
    (fake_db.parent / "app.db-wal").write_bytes(b"")
    (fake_db.parent / "app.db-shm").write_bytes(b"")
    monkeypatch.setattr(config, "DB_PATH", fake_db)
    monkeypatch.setattr(config, "BACKUPS_DIR", fake_backups)

    backup_path = dev_seed.reset_database()

    assert backup_path is not None
    assert not fake_db.exists()
    assert not (fake_db.parent / "app.db-wal").exists()
    assert not (fake_db.parent / "app.db-shm").exists()
    assert backup_path.parent == fake_backups
    assert backup_path.name.startswith("pre-reset-")
    check = sqlite3.connect(str(backup_path))
    try:
        tables = {
            row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        check.close()
    assert "demo" in tables


def test_reset_without_existing_db_is_noop_backup(db_session, monkeypatch, tmp_path) -> None:
    """全新数据卷（无 db 文件）→ 无备份文件产出，删除路径不抛错。"""
    fake_db = tmp_path / "db" / "app.db"
    fake_backups = tmp_path / "backups"
    monkeypatch.setattr(config, "DB_PATH", fake_db)
    monkeypatch.setattr(config, "BACKUPS_DIR", fake_backups)

    assert dev_seed.reset_database() is None
    assert not fake_db.exists()
    assert list(fake_backups.glob("pre-reset-*.db")) == []
