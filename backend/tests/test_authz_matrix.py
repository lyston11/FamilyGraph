"""m2a 授权矩阵 IDOR 测试（三家庭 fixture，逐行断言 architecture.md §6）。

fixture：
    A(甲) --elder--> B(乙)   直系结构边（B 是 A 的长辈）
    B --peer--> C(丙)        同族非直系
    D(丁)                    独立家族
断言主体：以 A 的 JWT 直打 API。
"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

MASKED = {"__masked__": True}


@pytest.fixture()
def three_families(db_session):
    a = create_user_with_pin(db_session, "甲", "111111", claim_status="claimed")
    b = create_user_with_pin(
        db_session,
        "乙",
        "222222",
        claim_status="claimed",
        gender="m",
        birth={"cal_type": "solar", "date": "1950-01-01"},
        bio="乙的简介",
    )
    c = create_user_with_pin(
        db_session,
        "丙",
        "333333",
        claim_status="claimed",
        gender="f",
        birth={"cal_type": "solar", "date": "1960-02-02"},
        bio="丙的简介",
    )
    d = create_user_with_pin(db_session, "丁", "444444", claim_status="claimed")
    from app.models.relation import Relation
    from app.utils.timeutil import utcnow

    def edge(f, t, cls):
        now = utcnow()
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

    edge(a, b, "elder")
    edge(b, c, "peer")
    db_session.commit()
    return {"A": a, "B": b, "C": c, "D": d}


def _h(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_direct_structural_edge_full_both_ways(db_session, client: TestClient, three_families):
    A, B = three_families["A"], three_families["B"]
    ha = _h(client, "甲", "111111")
    hb = _h(client, "乙", "222222")

    ra = client.get(f"/api/users/{B.id}", headers=ha)
    assert ra.status_code == 200
    body = ra.json()
    assert body["name"] == "乙"
    assert isinstance(body["birth"], dict) and "__masked__" not in body["birth"]

    rb = client.get(f"/api/users/{A.id}", headers=hb)
    assert rb.status_code == 200


def test_clan_reachable_summary_with_disclosure_toggle(
    db_session, client: TestClient, three_families
):
    _a, C = three_families["A"], three_families["C"]
    ha = _h(client, "甲", "111111")

    # 未开放披露：基线摘要，敏感字段 MASKED
    r1 = client.get(f"/api/users/{C.id}", headers=ha)
    assert r1.status_code == 200
    body = r1.json()
    assert body["name"] == "丙"
    assert body["birth"] == MASKED
    assert body["bio"] == MASKED

    # C 的代管创建者不存在 → 用 admin？AD-9 开关修改权 = D5 编辑权主体。
    # fixture 中 C 由 create_user_with_pin 直接造（created_by=None、claimed）→ 本人改
    hc = _h(client, "丙", "333333")
    put = client.put(
        f"/api/users/{C.id}/disclosure",
        json={"avatar": False, "photos": False, "dates": True, "bio": True, "attachments": False},
        headers=hc,
    )
    assert put.status_code == 200, put.text

    r2 = client.get(f"/api/users/{C.id}", headers=ha)
    body2 = r2.json()
    assert isinstance(body2["birth"], dict) and "__masked__" not in body2["birth"]
    assert body2["bio"] == "丙的简介"

    # 关闭恢复遮罩
    client.put(
        f"/api/users/{C.id}/disclosure",
        json={"avatar": False, "photos": False, "dates": False, "bio": False, "attachments": False},
        headers=hc,
    )
    r3 = client.get(f"/api/users/{C.id}", headers=ha)
    assert r3.json()["birth"] == MASKED


def test_independent_family_invisible_everywhere(db_session, client: TestClient, three_families):
    _a, D = three_families["A"], three_families["D"]
    ha = _h(client, "甲", "111111")

    # 档案 404（invisible ≠ 遮罩）
    r = client.get(f"/api/users/{D.id}", headers=ha)
    assert r.status_code == 404

    # 图接口不出现 D 节点
    g = client.get("/api/graph/me?scope=clan", headers=ha).json()
    ids = {n["id"] for n in g["nodes"]}
    assert D.id not in ids
    assert all(D.id not in (e["from_user"], e["to_user"]) for e in g["edges"])

    # D 自己看自己正常
    hd = _h(client, "丁", "444444")
    rd = client.get(f"/api/users/{D.id}", headers=hd)
    assert rd.status_code == 200 and rd.json()["name"] == "丁"


def test_graph_clan_summary_node_trimmed_and_full_node_intact(
    db_session, client: TestClient, three_families
):
    _a, B, C = (
        three_families["A"],
        three_families["B"],
        three_families["C"],
    )
    ha = _h(client, "甲", "111111")
    g = client.get("/api/graph/me?scope=clan", headers=ha).json()
    nodes = {n["id"]: n for n in g["nodes"]}
    # 直系对端 full：性别可见
    assert nodes[B.id]["gender"] != "unknown"
    # peer 对端 summary：性别裁剪为 unknown 占位
    assert nodes[C.id]["gender"] == "unknown"  # summary 裁剪为占位
    assert nodes[B.id]["gender"] == "m"  # full 保持真实


def test_pending_pair_mutual_summary_not_transitive(db_session, client: TestClient, three_families):
    """pending 请求两端点互见摘要；但不把对方家族带入可达范围。"""

    A, B, C = three_families["A"], three_families["B"], three_families["C"]
    stranger = create_user_with_pin(db_session, "陌生", "555555", claim_status="claimed")
    db_session.commit()

    ha = _h(client, "甲", "111111")
    hs = _h(client, "陌生", "555555")

    # 甲向陌生人发 pending 合并请求
    r = client.post(
        "/api/connection-requests",
        json={"target_id": stranger.id, "dir_class": "peer"},
        headers=ha,
    )
    assert r.status_code == 201
    # pending 期间互见摘要
    r_a = client.get(f"/api/users/{stranger.id}", headers=ha)
    assert r_a.status_code == 200
    assert r_a.json()["birth"] == MASKED
    r_s = client.get(f"/api/users/{A.id}", headers=hs)
    assert r_s.status_code == 200
    assert r_s.json()["birth"] == MASKED

    # 不传递：陌生人的其他家族成员仍不可见（此处无额外成员，验证边不进图）
    g = client.get("/api/graph/me?scope=family", headers=ha).json()
    assert all(e["status"] == "active" for e in g["edges"])
    void = (B.id, C.id)
    del void
