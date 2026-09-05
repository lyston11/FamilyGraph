"""dev 种子模块合同（09-05 dev-seed-demo-data + family-profile-nav-disclosure R3/R6）。

- DEV_SEED_DEMO_DATA 未开启（默认 "0"）→ maybe_seed_demo_data 零写入；
- env=1 + 空库 → 6 用户（含出生日期）/2 空间（household+lineage）/12 成员行/
  5 关系/10 confirmed SourceFact（双空间各投影）/基础五类披露全局开放，
  PIN 123456 校验通过；重复调用（_SEED_DONE 或非空库）幂等跳过；
- env=1 + 非空库 → 跳过且既有数据零变动；
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

_DEMO_NAMES = {"王德海", "周秀英", "王建军", "王小雨", "王远山", "王小虎"}


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
    """env=1 + 空库 → 完整演示数据集；重复调用幂等跳过。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    users = db_session.query(User).all()
    assert len(users) == 6
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

    spaces = db_session.query(FamilySpace).all()
    assert len(spaces) == 2
    by_kind = {space.kind: space for space in spaces}
    household = by_kind["household"]
    lineage = by_kind["lineage"]
    assert household.name == "王德海家"
    assert lineage.name == "王氏家族"
    assert {space.owner_id for space in spaces} == {by_name["王德海"].id}

    # 每个空间 6 名成员（王德海 space_admin，其余 member）
    for space in (household, lineage):
        members = db_session.query(SpaceMember).filter_by(space_id=space.id).all()
        assert len(members) == 6
        assert all(member.status == "active" for member in members)
        admin_rows = [member for member in members if member.role == "space_admin"]
        assert len(admin_rows) == 1
        assert admin_rows[0].user_id == by_name["王德海"].id
        assert sum(1 for member in members if member.role == "member") == 5
    assert db_session.query(SpaceMember).count() == 12

    relations = db_session.query(Relation).all()
    assert len(relations) == 5
    assert {relation.status for relation in relations} == {"active"}
    spouse_edges = [r for r in relations if r.dir_class == "spouse"]
    elder_edges = [r for r in relations if r.dir_class == "elder"]
    assert len(spouse_edges) == 1 and len(elder_edges) == 4
    # v1 边方向语义：to_user 是 from_user 的 dir_class（ elder/younger 指向长辈）
    spouse = spouse_edges[0]
    by_id = {user.id: user.name for user in users}
    assert (by_id[spouse.from_user], by_id[spouse.to_user]) == ("王德海", "周秀英")
    assert {(by_id[r.from_user], by_id[r.to_user]) for r in elder_edges} == {
        ("王德海", "王远山"),
        ("王建军", "王德海"),
        ("王小雨", "王德海"),
        ("王小虎", "王建军"),
    }

    facts = db_session.query(SourceFact).all()
    assert len(facts) == 5  # 全局事实（space_id=NULL），双空间共享投影
    assert {fact.state for fact in facts} == {"confirmed"}
    assert {fact.fact_type for fact in facts} == {"spouse", "biological_parent"}
    assert {fact.space_id for fact in facts} == {None}
    pairs = {
        (fact.fact_type, by_id[fact.subject_user_id], by_id[fact.object_user_id]) for fact in facts
    }
    assert pairs == {
        ("spouse", "王德海", "周秀英"),
        ("biological_parent", "王远山", "王德海"),
        ("biological_parent", "王德海", "王建军"),
        ("biological_parent", "王德海", "王小雨"),
        ("biological_parent", "王建军", "王小虎"),
    }

    # R6：基础五类披露全局开放（成员互见）；高敏感五类保持关闭（Q4=b 仅为可开）
    prefs = db_session.query(DisclosurePreference).all()
    assert len(prefs) == 6 * 5  # 6 用户 × 基础五类
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
    assert db_session.query(User).count() == 6
    # 幂等（路径二）：清掉单例后仍被「库非空」门控跳过，无重复行
    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(User).count() == 6
    assert db_session.query(SourceFact).count() == 5


def test_non_empty_db_skips_and_touches_nothing(db_session, monkeypatch) -> None:
    """env=1 + 非空库（预置一个用户）→ 跳过且该用户数据零变动。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    existing = create_user_with_pin(db_session, "既有用户", "999888")

    assert dev_seed.maybe_seed_demo_data(db_session) is False

    assert db_session.query(User).count() == 1
    untouched = db_session.query(User).one()
    assert untouched.id == existing.id
    assert untouched.name == "既有用户"
    assert untouched.account.status == "claimed"
    assert security.verify_pin("999888", untouched.account.pin_hash)
    assert db_session.query(FamilySpace).count() == 0
    assert db_session.query(SpaceMember).count() == 0
    assert db_session.query(Relation).count() == 0
    assert db_session.query(SourceFact).count() == 0


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
