"""dev 种子模块合同（09-05 dev-seed-demo-data + 09-13 明皇室数据集 + 增量收敛）。

- DEV_SEED_DEMO_DATA 未开启（默认 "0"）→ maybe_seed_demo_data 零写入；
- env=1 + 空库 → 全量播种：51 用户（含出生日期）/20 空间（household×10 +
  lineage×10，十个 household 显式配对所属 lineage）/92 成员行/74 关系/74
  confirmed SourceFact（全局一份）/基础五类披露全局开放，PIN 123456 校验通过；
  进程内重复调用（_SEED_DONE）幂等跳过；
- env=1 + 非空库 → insert-only 增量补缺：清单缺失行自动补齐（用户按姓名 /
  空间按名 / 成员行按 (space,user) / 关系边按两人任一方向 pending|active 匹配），
  既有行（含清单外用户）零改动，返回 True；清单已完全收敛 → 返回 False 且零写入；
- 种子不创建/修改 system_admins（admin bootstrap 专属职责，09-04 合同）；
- --reset：先备份到 backups/pre-reset-*.db 再删除 db/-wal/-shm。
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select

from app import config, dev_seed
from app.models.personal_family_view import PersonalFamilyView
from app.models.relation import Relation
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.models.user import User
from app.models.v2_foundation import DisclosurePreference
from app.services import admin_bootstrap
from app.utils import security
from conftest import create_user_with_pin

_DEMO_NAMES = {
    # 明皇室 household + 朱氏皇族 lineage（六世帝系 + 旁系 + 姻亲）
    "朱元璋",
    "马皇后",
    "朱标",
    "朱棣",
    "朱世珍",
    "朱允炆",
    "陈氏",
    "朱兴隆",
    "朱文正",
    "朱樉",
    "朱棡",
    "朱橚",
    "宁国公主",
    "安庆公主",
    "常氏",
    "吕氏",
    "徐皇后",
    "梅殷",
    "欧阳伦",
    "朱允熥",
    "朱高炽",
    "朱高煦",
    "朱高燧",
    "张皇后",
    "朱瞻基",
    "朱瞻墡",
    "孙皇后",
    "朱祁镇",
    "朱祁钰",
    "钱皇后",
    # 马氏家族（马皇后本家）
    "马公",
    "郑氏",
    # 徐氏家族（徐皇后本家）
    "徐达",
    "谢氏",
    "徐辉祖",
    "徐增寿",
    # 常氏家族（常遇春本家）
    "常遇春",
    "蓝氏",
    "常茂",
    "常升",
    # 吕氏家族（吕本本家）
    "吕本",
    # 梅氏家族（梅思祖本家）
    "梅思祖",
    # 张氏家族（张麒本家）
    "张麒",
    "张昶",
    # 孙氏家族（孙忠本家）
    "孙忠",
    "孙继宗",
    # 钱氏家族（钱贵本家）
    "钱贵",
    # 李氏家族（曹国长公主朱佛女⇄李贞本家）
    "李贞",
    "朱佛女",
    "李文忠",
    "李景隆",
}

# v1 边方向语义：to_user 是 from_user 的 dir_class（elder 指向长辈）
_SEED_SPOUSE_PAIRS = {
    ("朱元璋", "马皇后"),
    ("朱标", "常氏"),
    ("朱标", "吕氏"),
    ("朱棣", "徐皇后"),
    ("梅殷", "宁国公主"),
    ("欧阳伦", "安庆公主"),
    ("朱高炽", "张皇后"),
    ("朱瞻基", "孙皇后"),
    ("朱祁镇", "钱皇后"),
    ("马公", "郑氏"),
    ("徐达", "谢氏"),
    ("常遇春", "蓝氏"),
    ("李贞", "朱佛女"),
}
_SEED_ELDER_PAIRS = {
    ("朱兴隆", "朱世珍"),
    ("朱兴隆", "陈氏"),
    ("朱元璋", "朱世珍"),
    ("朱元璋", "陈氏"),
    ("朱文正", "朱兴隆"),
    ("朱标", "朱元璋"),
    ("朱标", "马皇后"),
    ("朱樉", "朱元璋"),
    ("朱樉", "马皇后"),
    ("朱棡", "朱元璋"),
    ("朱棡", "马皇后"),
    ("朱棣", "朱元璋"),
    ("朱棣", "马皇后"),
    ("朱橚", "朱元璋"),
    ("朱橚", "马皇后"),
    ("宁国公主", "朱元璋"),
    ("宁国公主", "马皇后"),
    ("安庆公主", "朱元璋"),
    ("安庆公主", "马皇后"),
    ("朱允炆", "朱标"),
    ("朱允炆", "吕氏"),
    ("朱允熥", "朱标"),
    ("朱允熥", "常氏"),
    ("朱高炽", "朱棣"),
    ("朱高炽", "徐皇后"),
    ("朱高煦", "朱棣"),
    ("朱高煦", "徐皇后"),
    ("朱高燧", "朱棣"),
    ("朱高燧", "徐皇后"),
    ("朱瞻基", "朱高炽"),
    ("朱瞻基", "张皇后"),
    ("朱瞻墡", "朱高炽"),
    ("朱瞻墡", "张皇后"),
    ("朱祁镇", "朱瞻基"),
    ("朱祁镇", "孙皇后"),
    ("朱祁钰", "朱瞻基"),
    ("马皇后", "马公"),
    ("马皇后", "郑氏"),
    ("徐皇后", "徐达"),
    ("徐皇后", "谢氏"),
    ("徐辉祖", "徐达"),
    ("徐辉祖", "谢氏"),
    ("徐增寿", "徐达"),
    ("徐增寿", "谢氏"),
    ("常氏", "常遇春"),
    ("常氏", "蓝氏"),
    ("常茂", "常遇春"),
    ("常茂", "蓝氏"),
    ("常升", "常遇春"),
    ("常升", "蓝氏"),
    ("吕氏", "吕本"),
    ("张皇后", "张麒"),
    ("张昶", "张麒"),
    ("孙皇后", "孙忠"),
    ("孙继宗", "孙忠"),
    ("钱皇后", "钱贵"),
    ("朱佛女", "朱世珍"),
    ("朱佛女", "陈氏"),
    ("李文忠", "李贞"),
    ("李文忠", "朱佛女"),
    ("李景隆", "李文忠"),
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
    assert len(users) == 51
    by_name = {user.name: user for user in users}
    assert set(by_name) == _DEMO_NAMES
    for user in users:
        # claimed + identity_confirmed + PIN 123456（hash_pin 存储，非明文）
        assert user.profile_status == "identity_confirmed"
        assert user.account.status == "claimed"
        assert user.account.pin_must_change is False
        assert user.account.pin_hash != "123456"
        assert security.verify_pin("123456", user.account.pin_hash)
        # 结构化出生日期（solar）；明皇室数据集全员历史人物、无未成年人
        assert user.birth is not None and user.birth.get("cal_type") == "solar"
        assert user.birth.get("date")
    assert by_name["朱元璋"].birth["date"] == "1328-10-21"
    assert by_name["马皇后"].birth["date"] == "1332-08-19"
    assert by_name["朱允炆"].birth["date"] == "1377-12-05"
    assert by_name["朱祁镇"].birth["date"] == "1427-11-29"
    assert by_name["徐达"].birth["date"] == "1332-11-11"
    # 朱元璋为 1 号演示用户（名册首见序，smoke 附件用例依赖此约定）
    assert by_name["朱元璋"].id == min(user.id for user in users)

    spaces = db_session.query(FamilySpace).all()
    assert len(spaces) == 20
    by_space_name = {space.name: space for space in spaces}
    household = by_space_name["明皇室"]
    lineage = by_space_name["朱氏皇族"]
    ma_household = by_space_name["马府"]
    ma_lineage = by_space_name["马氏家族"]
    xu_household = by_space_name["徐达家"]
    xu_lineage = by_space_name["徐氏家族"]
    chang_household = by_space_name["常府"]
    chang_lineage = by_space_name["常氏家族"]
    lv_household = by_space_name["吕府"]
    lv_lineage = by_space_name["吕氏家族"]
    mei_household = by_space_name["梅府"]
    mei_lineage = by_space_name["梅氏家族"]
    zhang_household = by_space_name["张家"]
    zhang_lineage = by_space_name["张氏家族"]
    sun_household = by_space_name["孙家"]
    sun_lineage = by_space_name["孙氏家族"]
    qian_household = by_space_name["钱家"]
    qian_lineage = by_space_name["钱氏家族"]
    li_household = by_space_name["李家"]
    li_lineage = by_space_name["李氏家族"]
    assert household.kind == "household" and lineage.kind == "lineage"
    assert ma_household.kind == "household" and ma_lineage.kind == "lineage"
    assert xu_household.kind == "household" and xu_lineage.kind == "lineage"
    assert chang_household.kind == "household" and chang_lineage.kind == "lineage"
    assert lv_household.kind == "household" and lv_lineage.kind == "lineage"
    assert mei_household.kind == "household" and mei_lineage.kind == "lineage"
    assert zhang_household.kind == "household" and zhang_lineage.kind == "lineage"
    assert sun_household.kind == "household" and sun_lineage.kind == "lineage"
    assert qian_household.kind == "household" and qian_lineage.kind == "lineage"
    assert li_household.kind == "household" and li_lineage.kind == "lineage"
    assert household.owner_id == by_name["朱元璋"].id
    assert lineage.owner_id == by_name["朱元璋"].id
    assert ma_household.owner_id == by_name["马皇后"].id
    assert ma_lineage.owner_id == by_name["马皇后"].id
    assert xu_household.owner_id == by_name["徐达"].id
    assert xu_lineage.owner_id == by_name["徐达"].id
    assert chang_household.owner_id == by_name["常遇春"].id
    assert chang_lineage.owner_id == by_name["常遇春"].id
    assert lv_household.owner_id == by_name["吕本"].id
    assert lv_lineage.owner_id == by_name["吕本"].id
    assert mei_household.owner_id == by_name["梅思祖"].id
    assert mei_lineage.owner_id == by_name["梅思祖"].id
    assert zhang_household.owner_id == by_name["张麒"].id
    assert zhang_lineage.owner_id == by_name["张麒"].id
    assert sun_household.owner_id == by_name["孙忠"].id
    assert sun_lineage.owner_id == by_name["孙忠"].id
    assert qian_household.owner_id == by_name["钱贵"].id
    assert qian_lineage.owner_id == by_name["钱贵"].id
    assert li_household.owner_id == by_name["李贞"].id
    assert li_lineage.owner_id == by_name["李贞"].id
    assert {space.owner_id for space in spaces} == {
        by_name["朱元璋"].id,
        by_name["马皇后"].id,
        by_name["徐达"].id,
        by_name["常遇春"].id,
        by_name["吕本"].id,
        by_name["梅思祖"].id,
        by_name["张麒"].id,
        by_name["孙忠"].id,
        by_name["钱贵"].id,
        by_name["李贞"].id,
    }
    # 家族配对：本次新建 household 显式挂入所属 lineage（「当前家族空间」切换数据基础）
    assert household.lineage_space_id == lineage.id
    assert ma_household.lineage_space_id == ma_lineage.id
    assert xu_household.lineage_space_id == xu_lineage.id
    assert chang_household.lineage_space_id == chang_lineage.id
    assert lv_household.lineage_space_id == lv_lineage.id
    assert mei_household.lineage_space_id == mei_lineage.id
    assert zhang_household.lineage_space_id == zhang_lineage.id
    assert sun_household.lineage_space_id == sun_lineage.id
    assert qian_household.lineage_space_id == qian_lineage.id
    assert li_household.lineage_space_id == li_lineage.id

    # 明皇室 6 人 / 朱氏皇族 30 人（旁系与姻亲只进家族空间）；外戚本家 household
    # 2 人 + lineage 收全族；跨空间成员见下方专项断言
    expected_rosters = (
        (household, 6, by_name["朱元璋"].id),
        (lineage, 30, by_name["朱元璋"].id),
        (ma_household, 2, by_name["马皇后"].id),
        (ma_lineage, 4, by_name["马皇后"].id),
        (xu_household, 2, by_name["徐达"].id),
        (xu_lineage, 6, by_name["徐达"].id),
        (chang_household, 2, by_name["常遇春"].id),
        (chang_lineage, 6, by_name["常遇春"].id),
        (lv_household, 2, by_name["吕本"].id),
        (lv_lineage, 3, by_name["吕本"].id),
        (mei_household, 2, by_name["梅思祖"].id),
        (mei_lineage, 3, by_name["梅思祖"].id),
        (zhang_household, 2, by_name["张麒"].id),
        (zhang_lineage, 4, by_name["张麒"].id),
        (sun_household, 2, by_name["孙忠"].id),
        (sun_lineage, 4, by_name["孙忠"].id),
        (qian_household, 2, by_name["钱贵"].id),
        (qian_lineage, 3, by_name["钱贵"].id),
        (li_household, 2, by_name["李贞"].id),
        (li_lineage, 5, by_name["李贞"].id),
    )
    for space, size, admin_id in expected_rosters:
        members = db_session.query(SpaceMember).filter_by(space_id=space.id).all()
        assert len(members) == size
        assert all(member.status == "active" for member in members)
        admin_rows = [member for member in members if member.role == "space_admin"]
        assert len(admin_rows) == 1
        assert admin_rows[0].user_id == admin_id
        assert sum(1 for member in members if member.role == "member") == size - 1
    assert db_session.query(SpaceMember).count() == 92

    # 跨空间成员资格：帝室成员以 member 身份进入各外戚本家
    def _member_ids(space: FamilySpace) -> set[int]:
        return {
            member.user_id
            for member in db_session.query(SpaceMember).filter_by(space_id=space.id).all()
        }

    assert _member_ids(ma_household) == {by_name["马皇后"].id, by_name["朱元璋"].id}
    assert _member_ids(ma_lineage) == {
        by_name["马皇后"].id,
        by_name["朱元璋"].id,
        by_name["马公"].id,
        by_name["郑氏"].id,
    }
    assert _member_ids(xu_household) == {by_name["徐达"].id, by_name["朱棣"].id}
    assert _member_ids(xu_lineage) == {
        by_name["徐达"].id,
        by_name["朱棣"].id,
        by_name["谢氏"].id,
        by_name["徐辉祖"].id,
        by_name["徐增寿"].id,
        by_name["徐皇后"].id,
    }
    assert _member_ids(chang_household) == {by_name["常遇春"].id, by_name["朱标"].id}
    assert _member_ids(chang_lineage) == {
        by_name["常遇春"].id,
        by_name["朱标"].id,
        by_name["蓝氏"].id,
        by_name["常茂"].id,
        by_name["常升"].id,
        by_name["常氏"].id,
    }
    assert _member_ids(lv_household) == {by_name["吕本"].id, by_name["朱标"].id}
    assert _member_ids(lv_lineage) == {
        by_name["吕本"].id,
        by_name["朱标"].id,
        by_name["吕氏"].id,
    }
    assert _member_ids(mei_household) == {by_name["梅思祖"].id, by_name["宁国公主"].id}
    assert _member_ids(mei_lineage) == {
        by_name["梅思祖"].id,
        by_name["宁国公主"].id,
        by_name["梅殷"].id,
    }
    assert _member_ids(zhang_household) == {by_name["张麒"].id, by_name["朱高炽"].id}
    assert _member_ids(zhang_lineage) == {
        by_name["张麒"].id,
        by_name["朱高炽"].id,
        by_name["张昶"].id,
        by_name["张皇后"].id,
    }
    assert _member_ids(sun_household) == {by_name["孙忠"].id, by_name["朱瞻基"].id}
    assert _member_ids(sun_lineage) == {
        by_name["孙忠"].id,
        by_name["朱瞻基"].id,
        by_name["孙继宗"].id,
        by_name["孙皇后"].id,
    }
    assert _member_ids(qian_household) == {by_name["钱贵"].id, by_name["朱祁镇"].id}
    assert _member_ids(qian_lineage) == {
        by_name["钱贵"].id,
        by_name["朱祁镇"].id,
        by_name["钱皇后"].id,
    }
    assert _member_ids(li_household) == {by_name["李贞"].id, by_name["朱元璋"].id}
    assert _member_ids(li_lineage) == {
        by_name["李贞"].id,
        by_name["朱元璋"].id,
        by_name["朱佛女"].id,
        by_name["李文忠"].id,
        by_name["李景隆"].id,
    }

    relations = db_session.query(Relation).all()
    assert len(relations) == 74
    assert {relation.status for relation in relations} == {"active"}
    spouse_edges = [r for r in relations if r.dir_class == "spouse"]
    elder_edges = [r for r in relations if r.dir_class == "elder"]
    assert len(spouse_edges) == 13 and len(elder_edges) == 61
    by_id = {user.id: user.name for user in users}
    assert {(by_id[r.from_user], by_id[r.to_user]) for r in spouse_edges} == _SEED_SPOUSE_PAIRS
    assert {(by_id[r.from_user], by_id[r.to_user]) for r in elder_edges} == _SEED_ELDER_PAIRS

    facts = db_session.query(SourceFact).all()
    assert len(facts) == 74  # 全局事实（space_id=NULL），十对空间共享投影
    assert {fact.state for fact in facts} == {"confirmed"}
    assert {fact.fact_type for fact in facts} == {"spouse", "biological_parent"}
    assert {fact.space_id for fact in facts} == {None}
    # elder f→t → biological_parent(t, f)；spouse 对称（映射合同见 _map_structural_edge）
    parent_pairs = {
        (by_id[fact.subject_user_id], by_id[fact.object_user_id])
        for fact in facts
        if fact.fact_type == "biological_parent"
    }
    assert parent_pairs == {(parent, child) for child, parent in _SEED_ELDER_PAIRS}
    spouse_facts = {
        (by_id[fact.subject_user_id], by_id[fact.object_user_id])
        for fact in facts
        if fact.fact_type == "spouse"
    }
    assert spouse_facts == _SEED_SPOUSE_PAIRS

    # R6：基础五类披露全局开放（成员互见）；高敏感五类保持关闭（Q4=b 仅为可开）
    prefs = db_session.query(DisclosurePreference).all()
    assert len(prefs) == 51 * 5  # 51 用户 × 基础五类
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
    assert db_session.query(User).count() == 51
    # 幂等（路径二）：清掉单例后再跑 → 清单已完全收敛，零写入返回 False
    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(User).count() == 51
    assert db_session.query(SourceFact).count() == 74


def test_full_seed_initializes_personal_family_view_rows(db_session, monkeypatch) -> None:
    """全量播种后，每个种子成员（有 Account）× 其 active 成员资格空间都有
    queued 的 PFV 视图行（09-13 缺口修复：种子不发注册事件，行必须在种子内
    初始化，否则换数据集后家族树永远 never_computed、重算作业空转）。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    users = {user.name: user for user in db_session.query(User).all()}
    memberships = db_session.scalars(
        select(SpaceMember).where(SpaceMember.status == "active")
    ).all()
    expected = set()
    for member in memberships:
        user = next(u for u in users.values() if u.id == member.user_id)
        assert user.account is not None
        expected.add((user.account.id, member.space_id))

    rows = db_session.scalars(select(PersonalFamilyView)).all()
    assert {(row.viewer_account_id, row.space_id) for row in rows} == expected
    assert all(row.status == "queued" for row in rows)
    # 幂等：收敛后重复调用不再新增视图行
    before = len(rows)
    assert dev_seed.maybe_seed_demo_data(db_session) is False
    assert db_session.query(PersonalFamilyView).count() == before


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

    # 固定清单全部补齐：51 演示用户 / 20 空间 / 92 成员行 / 74 关系 / 74 fact
    demo_users = db_session.query(User).filter(User.name != "既有用户").all()
    assert {user.name for user in demo_users} == _DEMO_NAMES
    assert len(demo_users) == 51
    assert db_session.query(FamilySpace).count() == 20
    assert db_session.query(SpaceMember).count() == 92
    assert db_session.query(Relation).count() == 74
    assert db_session.query(SourceFact).count() == 74


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
    assert len(before) == 51

    # 清单扩展：朱氏皇族名册追加朱文奎（朱允炆之子，1394 年生）+ 一条亲子结构边
    rosters = dict(dev_seed._SEED_SPACE_MEMBERS)
    rosters["朱氏皇族"] = (*rosters["朱氏皇族"], ("朱文奎", "m"))
    monkeypatch.setattr(dev_seed, "_SEED_SPACE_MEMBERS", rosters)
    monkeypatch.setattr(
        dev_seed, "_SEED_BIRTHS", {**dev_seed._SEED_BIRTHS, "朱文奎": (1394, 12, 20)}
    )
    monkeypatch.setattr(
        dev_seed, "_SEED_EDGES", (*dev_seed._SEED_EDGES, ("朱文奎", "朱允炆", "elder"))
    )

    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    # 只增：52 用户 / 93 成员行 / 75 关系 / 75 fact
    assert db_session.query(User).count() == 52
    assert db_session.query(SpaceMember).count() == 93
    assert db_session.query(Relation).count() == 75
    assert db_session.query(SourceFact).count() == 75
    # 既有 51 用户行 id 与关键字段零变动
    for user in db_session.query(User).all():
        if user.name == "朱文奎":
            continue
        assert (user.id, user.gender, user.birth, user.profile_status, user.privacy_mode) == (
            before[user.name]
        )
    # 新成员落在朱氏皇族（旁系只进家族空间），明皇室 household 仍 6 人
    wenkui = db_session.query(User).filter_by(name="朱文奎").one()
    assert wenkui.birth == {"cal_type": "solar", "date": "1394-12-20", "is_leap_month": False}
    lineage = db_session.query(FamilySpace).filter_by(name="朱氏皇族").one()
    assert (
        db_session.query(SpaceMember).filter_by(space_id=lineage.id, user_id=wenkui.id).count() == 1
    )
    household = db_session.query(FamilySpace).filter_by(name="明皇室").one()
    assert db_session.query(SpaceMember).filter_by(space_id=household.id).count() == 6
    # 新边 active + 随新建一起落的 confirmed 全局 fact（朱允炆 是 朱文奎 的父亲）
    edge = db_session.query(Relation).filter_by(from_user=wenkui.id, dir_class="elder").one()
    assert edge.to_user == before["朱允炆"][0]
    assert edge.status == "active"
    fact = (
        db_session.query(SourceFact)
        .filter_by(subject_user_id=before["朱允炆"][0], object_user_id=wenkui.id)
        .one()
    )
    assert fact.fact_type == "biological_parent"
    assert fact.state == "confirmed"
    assert fact.space_id is None
    # 披露偏好只补新成员的 5 行（52×5），既有用户披露不动
    assert db_session.query(DisclosurePreference).count() == 52 * 5


def test_converged_manifest_returns_false_and_writes_nothing(db_session, monkeypatch) -> None:
    """env=1 + 清单已完全收敛 → 返回 False，各表计数零变动。"""
    monkeypatch.setattr(config, "DEV_SEED_DEMO_DATA", "1")
    assert dev_seed.maybe_seed_demo_data(db_session) is True

    dev_seed._SEED_DONE = False
    assert dev_seed.maybe_seed_demo_data(db_session) is False

    assert db_session.query(User).count() == 51
    assert db_session.query(FamilySpace).count() == 20
    assert db_session.query(SpaceMember).count() == 92
    assert db_session.query(Relation).count() == 74
    assert db_session.query(SourceFact).count() == 74
    assert db_session.query(DisclosurePreference).count() == 51 * 5


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
