"""m2c 加入申请、断连即时降级与幂等（architecture §4 [AD-4]）。"""

from __future__ import annotations

from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.models.space import SpaceMember


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def test_join_by_user_full_flow_and_idempotency(db_session, client: TestClient):
    a = create_user_with_pin(db_session, "阿甲", "111111", claim_status="claimed")
    b = create_user_with_pin(
        db_session,
        "阿乙",
        "222222",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1975-05-05"},
    )
    db_session.commit()
    ha = _login(client, "阿甲", "111111")
    hb = _login(client, "阿乙", "222222")

    # 乙建空间（owner 即 active）
    client.post("/api/spaces", json={"name": "乙家"}, headers=hb)

    # 甲（clan 可达性：无关系边时 invisible → 404 防枚举；先建立 peer 边使其可达）
    from app.models.relation import Relation
    from app.utils.timeutil import utcnow

    db_session.add(
        Relation(
            from_user=b.id,
            to_user=a.id,
            dir_class="peer",
            created_by=b.id,
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db_session.commit()

    r1 = client.post("/api/spaces/join-by-user", json={"target_user_id": b.id}, headers=ha)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "pending"

    # 幂等：重复 join 返回既有 pending 行（不新增）
    r2 = client.post("/api/spaces/join-by-user", json={"target_user_id": b.id}, headers=ha)
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == a.id).count() == 1

    # owner 审批接受 → active，甲获得完整可见性
    acc = client.post(f"/api/space-memberships/{r1.json()['id']}/accept", headers=ha)
    assert acc.status_code == 200

    detail = client.get(f"/api/users/{b.id}", headers=ha).json()
    assert isinstance(detail["birth"], dict) and "__masked__" not in detail["birth"]


def test_join_invisible_target_404(db_session, client: TestClient):
    """跨家族不可见目标 → 404（invisible ≠ 拒绝）。"""
    create_user_with_pin(db_session, "隐士", "777777", claim_status="claimed")
    create_user_with_pin(db_session, "求加", "888888", claim_status="claimed")
    db_session.commit()
    hs = _login(client, "求加", "888888")
    target_id = db_session.execute(
        __import__("sqlalchemy").text("SELECT id FROM users WHERE name='隐士'")
    ).scalar()
    r = client.post("/api/spaces/join-by-user", json={"target_user_id": target_id}, headers=hs)
    assert r.status_code == 404


def test_revoke_downgrades_visibility_immediately(db_session, client: TestClient):
    """D8 断连轨：revoke 后下一次读取立即回落 summary/invisible（无缓存残留）。"""
    a = create_user_with_pin(
        db_session,
        "父",
        "121212",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1960-01-01"},
    )
    child = create_user_with_pin(
        db_session,
        "子",
        "343434",
        claim_status="claimed",
        created_by=a.id,
    )
    db_session.commit()

    # 建立直系边（active）
    from app.models.relation import Relation
    from app.utils.timeutil import utcnow

    edge = Relation(
        from_user=child.id,
        to_user=a.id,
        dir_class="elder",
        created_by=child.id,
        status="active",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(edge)
    db_session.commit()

    hc = _login(client, "子", "343434")
    ok = client.get(f"/api/users/{a.id}", headers=hc)
    assert ok.status_code == 200
    assert isinstance(ok.json()["birth"], dict) and "__masked__" not in ok.json()["birth"]

    # 父 revoke 断连
    hp = _login(client, "父", "121212")
    rv = client.post(f"/api/relations/{edge.id}/revoke", headers=hp)
    assert rv.status_code == 200

    # 即时降级：无其他连接/空间 → 直接不可见
    after = client.get(f"/api/users/{a.id}", headers=hc)
    assert after.status_code == 404
