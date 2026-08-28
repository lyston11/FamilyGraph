"""v2 授权矩阵 IDOR 测试（AC-F4，spec/architecture.md §0.1 四级合同逐行断言）。

fixture 人物：
    甲：household「甲家」owner；lineage「宗族」active 成员
    乙：甲家 active member（非 guest）
    丙：甲家 active guest
    丁：甲家 pending 成员
    戊：宗族 active 成员（与甲同 lineage，无共同 household）
    己：与乙有 active elder 边但无任何共同空间（直系跨 household）
    小明：甲家 active member，未成年（birth ≈ 10 年前）
    庚：无关用户
    运营者：platform_operator（无任何家庭关系）

断言主体：以各角色 JWT 直打 GET /api/users/{id}，逐行核对四级层级投影。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

MASKED = {"__masked__": True}


def _minor_birth() -> dict:
    from app.utils.timeutil import utcnow

    return {
        "cal_type": "solar",
        "date": (utcnow().date() - timedelta(days=3650)).isoformat(),
    }


@pytest.fixture()
def matrix(db_session):
    from app.models.relation import Relation
    from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
    from app.utils.timeutil import utcnow

    def _user(name, pin, **kw):
        return create_user_with_pin(db_session, name, pin, claim_status="claimed", **kw)

    jia = _user("甲", "111111")
    yi = _user(
        "乙",
        "222222",
        gender="m",
        birth={"cal_type": "solar", "date": "1950-01-01"},
        bio="乙的简介",
    )
    bing = _user("丙", "333333")
    ding = _user("丁", "444444")
    wu = _user("戊", "555555", birth={"cal_type": "solar", "date": "1948-03-03"})
    ji = _user("己", "666666")
    xiaoming = _user("小明", "777777", birth=_minor_birth())
    geng = _user("庚", "888888")
    operator = _user("运营者", "999999", is_admin=True)

    now = utcnow()

    def edge(f, t, cls):
        db_session.add(
            Relation(
                from_user=f.id,
                to_user=t.id,
                dir_class=cls,
                created_by=f.id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    def member(space, user, role="member", status="active"):
        db_session.add(
            SpaceMember(
                space_id=space.id,
                user_id=user.id,
                added_by=user.id,
                role=role,
                status=status,
                created_at=now,
                updated_at=now,
            )
        )

    h1 = FamilySpace(name="甲家", owner_id=jia.id, kind="household", created_at=now)
    db_session.add(h1)
    db_session.flush()
    member(h1, jia, role="owner")
    member(h1, yi)
    member(h1, bing, role="guest")
    member(h1, xiaoming)
    member(h1, ding, status="pending")

    l1 = FamilySpace(name="宗族", owner_id=wu.id, kind="lineage", created_at=now)
    db_session.add(l1)
    db_session.flush()
    member(l1, wu)
    member(l1, jia)

    # 直系跨 household：己 --elder--> 乙，无共同空间
    edge(ji, yi, "elder")

    # provisional 人物以最小 ref 出现在宗族
    provisional = create_user_with_pin(db_session, "先祖", "121212", profile_status="provisional")
    db_session.add(
        SpaceProfileRef(
            space_id=l1.id,
            user_id=provisional.id,
            added_by=jia.id,
            status="active",
            created_at=now,
        )
    )

    db_session.commit()
    return {
        "甲": jia,
        "乙": yi,
        "丙": bing,
        "丁": ding,
        "戊": wu,
        "己": ji,
        "小明": xiaoming,
        "庚": geng,
        "运营者": operator,
        "先祖": provisional,
        "H1": h1,
        "L1": l1,
    }


def _h(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_household_active_member_gets_household_detail(db_session, client, matrix):
    ha = _h(client, "甲", "111111")
    r = client.get(f"/api/users/{matrix['乙'].id}", headers=ha)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "乙"
    assert isinstance(body["birth"], dict) and "__masked__" not in body["birth"]
    assert body["bio"] == "乙的简介"


def test_lineage_member_gets_lineage_summary_only(db_session, client, matrix):
    """同 lineage 无共同 household → 基线 + 披露扩展，敏感字段遮蔽。"""
    hw = _h(client, "戊", "555555")
    r = client.get(f"/api/users/{matrix['甲'].id}", headers=hw)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "甲"
    assert body["birth"] == MASKED
    assert body["bio"] == MASKED


def test_direct_edge_cross_household_no_longer_full(db_session, client, matrix):
    """v2 核心回归：直系结构边跨 household 不再自动 full（≤ lineage_summary）。"""
    hj = _h(client, "己", "666666")
    r = client.get(f"/api/users/{matrix['乙'].id}", headers=hj)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "乙"
    assert body["birth"] == MASKED
    assert body["bio"] == MASKED


def test_guest_does_not_get_household_detail(db_session, client, matrix):
    hb = _h(client, "丙", "333333")
    r = client.get(f"/api/users/{matrix['乙'].id}", headers=hb)
    assert r.status_code == 200
    assert r.json()["birth"] == MASKED

    # 反向：成员看 guest 同样不构成 household_detail
    hy = _h(client, "乙", "222222")
    r2 = client.get(f"/api/users/{matrix['丙'].id}", headers=hy)
    assert r2.status_code == 200
    assert r2.json()["gender"] == MASKED


def test_pending_membership_minimal_visibility_both_ways(db_session, client, matrix):
    hd = _h(client, "丁", "444444")
    r = client.get(f"/api/users/{matrix['甲'].id}", headers=hd)
    assert r.status_code == 200
    assert r.json()["birth"] == MASKED
    assert r.json()["gender"] == MASKED

    hj = _h(client, "甲", "111111")
    r2 = client.get(f"/api/users/{matrix['丁'].id}", headers=hj)
    assert r2.status_code == 200
    assert r2.json()["birth"] == MASKED


def test_custodian_does_not_get_household_detail_for_provisional(db_session, client, matrix):
    """F3：代管创建者不得仅凭 created_by 绕过 provisional 最小节点规则。"""
    jia = matrix["甲"]
    child = create_user_with_pin(
        db_session,
        "待认领子",
        "000000",
        claim_status="managed",
        profile_status="provisional",
        created_by=jia.id,
        gender="f",
        birth={"cal_type": "solar", "date": "2015-05-05"},
        bio="私人描述",
    )
    ha = _h(client, "甲", "111111")
    r = client.get(f"/api/users/{child.id}", headers=ha)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "待认领子"
    # provisional 档案最小节点规则：即使是代管人也无 household_detail 内容字段
    assert body["gender"] == MASKED
    assert body["birth"] == MASKED
    assert body["bio"] == MASKED


def test_provisional_ref_minimal_node_not_space_member(db_session, client, matrix):
    """AC-F2：provisional 只以最小节点出现；非 SpaceMember；无推荐资格。"""
    from app.services.identity_fsm import recommendation_eligible

    ancestor = matrix["先祖"]

    # 不是 SpaceMember
    from app.models.space import SpaceMember

    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == ancestor.id).count() == 0

    # 推荐资格恒 False
    assert recommendation_eligible(ancestor) is False

    # 可见性：仅基线（连性别都遮蔽）
    ha = _h(client, "甲", "111111")
    r = client.get(f"/api/users/{ancestor.id}", headers=ha)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "先祖"
    assert body["gender"] == MASKED
    assert body["birth"] == MASKED


def test_minor_overlay_masks_sensitive_fields_even_in_household(db_session, client, matrix):
    ha = _h(client, "甲", "111111")
    r = client.get(f"/api/users/{matrix['小明'].id}", headers=ha)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "小明"
    # household_detail 本可见精确生日，但未成年人 overlay 收紧
    assert body["birth"] == MASKED
    assert body["bio"] == MASKED


def test_platform_operator_reads_family_profile_404(db_session, client, matrix):
    ho = _h(client, "运营者", "999999")
    r = client.get(f"/api/users/{matrix['乙'].id}", headers=ho)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"

    # 列表同样不含他人档案
    ids = {m["id"] for m in client.get("/api/users", headers=ho).json()}
    assert ids == {matrix["运营者"].id}


def test_unrelated_user_invisible_404_and_graph_excluded(db_session, client, matrix):
    hg = _h(client, "庚", "888888")
    r = client.get(f"/api/users/{matrix['乙'].id}", headers=hg)
    assert r.status_code == 404

    g = client.get("/api/graph/me?scope=clan", headers=hg).json()
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {matrix["庚"].id}


def test_disclosure_expands_fields_for_lineage_viewer(db_session, client, matrix):
    """显式披露只扩展字段投影，不授予可见性；默认全不公开。"""
    from conftest import auth_header as ah

    hwu = _h(client, "戊", "555555")
    # 戊未开放披露：甲看戊为 MASKED
    hja = _h(client, "甲", "111111")
    r1 = client.get(f"/api/users/{matrix['戊'].id}", headers=hja)
    assert r1.json()["bio"] == MASKED

    # 戊本人开放 dates/bio 全局披露 → 甲（lineage viewer）可读
    put = client.put(
        f"/api/users/{matrix['戊'].id}/disclosure",
        json={"avatar": False, "photos": False, "dates": True, "bio": True, "attachments": False},
        headers=hwu,
    )
    assert put.status_code == 200, put.text
    r2 = client.get(f"/api/users/{matrix['戊'].id}", headers=hja)
    body2 = r2.json()
    assert isinstance(body2["birth"], dict) and "__masked__" not in body2["birth"]
    assert body2["bio"] is None or body2["bio"] != MASKED  # bio 为空但未遮蔽

    # 关闭恢复遮罩
    client.put(
        f"/api/users/{matrix['戊'].id}/disclosure",
        json={"avatar": False, "photos": False, "dates": False, "bio": False, "attachments": False},
        headers=hwu,
    )
    r3 = client.get(f"/api/users/{matrix['戊'].id}", headers=hja)
    assert r3.json()["birth"] == MASKED
    del ah


def test_graph_nodes_carry_v2_level_strings(db_session, client, matrix):
    """图节点携带 v2 层级字符串；household 节点字段真实，lineage 节点裁剪。"""
    from app.models.relation import Relation
    from app.models.space import FamilySpace, SpaceMember
    from app.utils.timeutil import utcnow

    a = create_user_with_pin(db_session, "图甲", "313131", claim_status="claimed")
    b = create_user_with_pin(db_session, "图乙", "414141", claim_status="claimed", gender="m")
    c = create_user_with_pin(db_session, "图丙", "515151", claim_status="claimed")
    now = utcnow()
    space = FamilySpace(name="图家", owner_id=a.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    for u, role in ((a, "owner"), (b, "member")):
        db_session.add(
            SpaceMember(
                space_id=space.id,
                user_id=u.id,
                added_by=a.id,
                role=role,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    # b --elder--> a：同 household 且直系；c 仅 peer 边（v2 不再授予可见性）
    db_session.add(
        Relation(
            from_user=b.id,
            to_user=a.id,
            dir_class="elder",
            created_by=b.id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        Relation(
            from_user=b.id,
            to_user=c.id,
            dir_class="peer",
            created_by=b.id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    ha = _h(client, "图甲", "313131")
    g = client.get("/api/graph/me?scope=clan", headers=ha).json()
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes[a.id]["visibility"] == "self_private"
    assert nodes[b.id]["visibility"] == "household_detail"
    assert nodes[b.id]["gender"] == "m"  # household 节点性别真实
    assert c.id not in nodes  # peer-only 对端不再可见
