"""m1b 关系 FSM / 环检测 / 合并请求 / kinship / 图查询测试。"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models.relation import Relation
from app.services import relation_fsm
from app.services.kinship import display_relation


@pytest.fixture()
def two_users(db_session):
    a = create_user_with_pin(db_session, "张甲", "111111")
    b = create_user_with_pin(db_session, "张乙", "222222")
    db_session.commit()
    return a, b


def _login_header(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


# ---- Relation FSM ----


def test_fsm_full_branches(db_session, two_users):
    a, b = two_users
    edge = relation_fsm.create_relation(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder", label="爸爸"
    )
    assert edge.status == "pending"
    relation_fsm.transition(edge, "accept", b.id, db_session)  # 仅被请求方
    assert edge.status == "active"
    relation_fsm.transition(edge, "revoke", a.id, db_session)  # 任一方可断连
    assert edge.status == "revoked"


def test_fsm_illegal_transitions(db_session, two_users):
    a, b = two_users
    edge = relation_fsm.create_relation(
        db_session, from_user=a.id, to_user=b.id, dir_class="peer", label=None
    )
    with pytest.raises(HTTPException) as ei:
        relation_fsm.transition(edge, "revoke", a.id, db_session)
    assert ei.value.status_code == 409
    relation_fsm.transition(edge, "reject", b.id, db_session)
    with pytest.raises(HTTPException) as ei2:
        relation_fsm.transition(edge, "cancel", a.id, db_session)
    assert ei2.value.status_code == 409


def test_fsm_actor_permissions(db_session, two_users):
    a, b = two_users
    third = create_user_with_pin(db_session, "路人", "333333")
    db_session.commit()
    edge = relation_fsm.create_relation(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder", label=None
    )
    with pytest.raises(HTTPException) as ei3:
        relation_fsm.transition(edge, "accept", third.id, db_session)
    assert ei3.value.status_code == 403
    with pytest.raises(HTTPException) as ei4:
        relation_fsm.transition(edge, "reject", a.id, db_session)
    assert ei4.value.status_code == 403


# ---- 环检测 ----


def test_elder_cycle_direct_and_chain(db_session, client: TestClient, two_users):
    a, b = two_users
    create_user_with_pin(db_session, "张丙", "444444")
    ha = _login_header(client, "张甲", "111111")
    hc = _login_header(client, "张丙", "444444")

    # A：B 是我的长辈 → OK
    r1 = client.post(
        "/api/connection-requests",
        json={"target_id": b.id, "dir_class": "elder"},
        headers=ha,
    )
    assert r1.status_code == 201, r1.text
    hb = _login_header(client, "张乙", "222222")
    assert (
        client.post(f"/api/connection-requests/{r1.json()['id']}/accept", headers=hb).status_code
        == 200
    )

    # C：B 是我的长辈 → OK（链 A←B←C）
    r2 = client.post(
        "/api/connection-requests",
        json={"target_id": b.id, "dir_class": "elder"},
        headers=hc,
    )
    assert r2.status_code == 201
    assert (
        client.post(f"/api/connection-requests/{r2.json()['id']}/accept", headers=hb).status_code
        == 200
    )

    # C：A 是我的长辈？A 是 B 的晚辈，B 是 C 的长辈 ⇒ A 是 C 的同辈或更小，不必然成环——
    # 但 C elder→A 表示 A 是 C 的长辈，链上 A→B→C 已存在（A 的长辈是 B，B 的长辈是 C），
    # 再加 C 的长辈是 A 即成环 A←B←C←A。
    r3 = client.post(
        "/api/connection-requests",
        json={"target_id": a.id, "dir_class": "elder"},
        headers=hc,
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["code"] == "RELATION_CYCLE_FORBIDDEN"

    # 回滚无残留：C 名下与 A 相关的边只有已 accept 的两条，无 pending 残留
    leftovers = db_session.query(Relation).filter(Relation.status == "pending").all()
    assert leftovers == []


def test_spouse_no_cycle_check(db_session, client: TestClient, two_users):
    """spouse 边不参与层级计算：双向互建 spouse 均允许（不同对用户语义下各自成边被唯一约束拦住，
    此处验证 spouse 写入不做环检测即可通过）。"""
    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")
    r = client.post(
        "/api/connection-requests",
        json={"target_id": b.id, "dir_class": "spouse", "label": "老伴"},
        headers=ha,
    )
    assert r.status_code == 201
    assert (
        client.post(f"/api/connection-requests/{r.json()['id']}/accept", headers=hb).status_code
        == 200
    )


# ---- partial unique index（真实 DB 冲突）----


def test_duplicate_pair_blocked_both_directions(db_session, client: TestClient, two_users):
    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")
    r1 = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "peer"}, headers=ha
    )
    assert r1.status_code == 201

    # 同向重复 pending
    r2 = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "peer"}, headers=ha
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "RELATION_DUPLICATE_PAIR"

    # 反向也撞同一约束（B 对 A 发起同样被拒）
    r3 = client.post(
        "/api/connection-requests", json={"target_id": a.id, "dir_class": "peer"}, headers=hb
    )
    assert r3.status_code == 409


def test_revoke_then_new_edge_ok(db_session, client: TestClient, two_users):
    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")
    r1 = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "peer"}, headers=ha
    )
    edge_id = r1.json()["id"]
    client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)

    # 断连（D8 任一方）
    rv = client.post(f"/api/relations/{edge_id}/revoke", headers=hb)
    assert rv.status_code == 200
    assert rv.json()["status"] == "revoked"

    # 重连 = 新边成功
    r2 = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "peer"}, headers=ha
    )
    assert r2.status_code == 201
    assert r2.json()["id"] != edge_id


def test_accept_idempotent_and_actor_rules(db_session, client: TestClient, two_users):
    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")
    r1 = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "elder"}, headers=ha
    )
    edge_id = r1.json()["id"]
    # 发起方不能 accept 自己的请求（动作权限 403）
    ra = client.post(f"/api/connection-requests/{edge_id}/accept", headers=ha)
    assert ra.status_code in (403, 409)
    first = client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)
    assert first.status_code == 200
    second = client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONNECTION_ALREADY_RESOLVED"


# ---- AD-4 合并语义：relation + space_member 同事务 ----


def test_connection_request_with_space_merged_semantics(db_session, client, two_users):
    from app.models.space import SpaceMember

    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")

    # A 建空间（owner 即 active 成员）
    s = client.post("/api/spaces", json={"name": "甲家"}, headers=ha)
    assert s.status_code == 201, s.text
    space_id = s.json()["id"]

    # 发起带空间意图的合并请求：relation pending + space_members pending 同事务
    r1 = client.post(
        "/api/connection-requests",
        json={
            "target_id": b.id,
            "dir_class": "elder",
            "space_membership": {"space_id": space_id},
        },
        headers=ha,
    )
    assert r1.status_code == 201, r1.text
    edge_id = r1.json()["id"]
    assert r1.json()["pending_space_id"] == space_id

    pm = (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.user_id == b.id)
        .one()
    )
    assert pm.status == "pending"

    # B 接受：关系与空间成员同时 active（跨表原子）
    acc = client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)
    assert acc.status_code == 200
    db_session.expire_all()
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.user_id == b.id)
        .one()
        .status
        == "active"
    )

    # 幂等：重复 accept → 409 ALREADY_RESOLVED
    again = client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONNECTION_ALREADY_RESOLVED"


def test_connection_reject_withdraws_pending_space_member(db_session, client, two_users):
    from app.models.space import SpaceMember

    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    hb = _login_header(client, "张乙", "222222")
    space_id = client.post("/api/spaces", json={"name": "甲家"}, headers=ha).json()["id"]
    r = client.post(
        "/api/connection-requests",
        json={
            "target_id": b.id,
            "dir_class": "peer",
            "space_membership": {"space_id": space_id},
        },
        headers=ha,
    )
    edge_id = r.json()["id"]
    rej = client.post(f"/api/connection-requests/{edge_id}/reject", headers=hb)
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.user_id == b.id)
        .one()
        .status
        == "withdrawn"
    )


def test_connection_space_intent_requires_active_membership(db_session, client, two_users):
    """发起人对目标空间不是 active 成员 → 404 防枚举。"""
    a, b = two_users
    ha = _login_header(client, "张甲", "111111")
    r = client.post(
        "/api/connection-requests",
        json={
            "target_id": b.id,
            "dir_class": "peer",
            "space_membership": {"space_id": 99999},
        },
        headers=ha,
    )
    assert r.status_code == 404


# ---- kinship 反译 ----


def test_kinship_display_translation(db_session, two_users):
    a, b = two_users
    cases = [("elder", "younger"), ("younger", "elder"), ("peer", "peer"), ("spouse", "spouse")]
    for cls, expected_reverse in cases:
        edge = Relation(
            from_user=a.id,
            to_user=b.id,
            dir_class=cls,
            label="称谓原文",
            created_by=a.id,
            status="active",
            created_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
            updated_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
        )
        d_from, label_from, fc_from = display_relation(edge, a.id)
        assert (d_from, label_from, fc_from) == (cls, "称谓原文", False)
        d_to, label_to, fc_to = display_relation(edge, b.id)
        assert d_to == expected_reverse
        assert label_to == "称谓原文"  # D3：label 恒创建者视角原文
        assert fc_to is True


# ---- 图查询 fixture ----


def test_graph_family_vs_clan_scope(db_session, client: TestClient):
    a = create_user_with_pin(db_session, "甲", "101010")
    b = create_user_with_pin(db_session, "乙", "202020")
    c = create_user_with_pin(db_session, "丙", "303030")
    d = create_user_with_pin(db_session, "丁", "404040")  # 独立家族
    db_session.commit()

    def _mk(from_u, to_u, cls, status="active"):
        edge = Relation(
            from_user=from_u.id,
            to_user=to_u.id,
            dir_class=cls,
            created_by=from_u.id,
            status=status,
            created_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
            updated_at=__import__("app.utils.timeutil", fromlist=["utcnow"]).utcnow(),
        )
        db_session.add(edge)
        return edge

    _mk(a, b, "elder")  # 甲的长辈是乙
    _mk(b, c, "peer")  # 乙的同辈是丙
    db_session.commit()

    h = _login_header(client, "甲", "101010")
    fam = client.get("/api/graph/me?scope=family&depth=1", headers=h).json()
    fam_ids = {n["id"] for n in fam["nodes"]}
    assert a.id in fam_ids and b.id in fam_ids and c.id not in fam_ids and d.id not in fam_ids
    assert all(e["status"] == "active" for e in fam["edges"])
    # viewer 视角：A 是 from_user → 结构类原样 elder、label_from_creator=False（D3）
    e_ab = next(e for e in fam["edges"] if {e["from_user"], e["to_user"]} == {a.id, b.id})
    assert e_ab["view"]["dir_class"] == "elder"
    assert e_ab["view"]["label_from_creator"] is False

    clan = client.get("/api/graph/me?scope=clan", headers=h).json()
    clan_ids = {n["id"] for n in clan["nodes"]}
    assert clan_ids == {a.id, b.id, c.id}  # 含 C 不含 D

    hd = _login_header(client, "丁", "404040")
    lone = client.get("/api/graph/me?scope=clan", headers=hd).json()
    assert {n["id"] for n in lone["nodes"]} == {d.id}
    assert lone["edges"] == []
