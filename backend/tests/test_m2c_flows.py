"""m2c 加入申请、断连即时降级与幂等（architecture §4 [AD-4]）。

09-19 起 join-by-user 是受限准入：申请人须与该空间的 active 成员存在 confirmed
亲属路径（SourceFact 图，不是 v1 relations 表），且 pending 只能由该空间的
space_admin 批准——申请人本人不得自批。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.space import SpaceMember
from app.services import source_facts as sf
from conftest import auth_header, create_space_member, create_user_with_pin, login

MASKED = {"__masked__": True}


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _confirm(
    session, fact_type: str, subject_id: int, object_id: int, space_id: int | None = None
) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


def _elder_relation(session, *, elder_id: int, younger_id: int) -> None:
    """v1 结构边：v2 可见性仍由它（而非 SourceFact）提供「可见」。"""
    from app.models.relation import Relation
    from app.utils.timeutil import utcnow

    session.add(
        Relation(
            from_user=elder_id,
            to_user=younger_id,
            dir_class="elder",
            created_by=elder_id,
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )


def test_join_by_user_full_flow_and_idempotency(db_session, client: TestClient):
    a = create_user_with_pin(db_session, "阿甲", "111111", claim_status="claimed")
    b = create_user_with_pin(
        db_session,
        "阿乙",
        "222222",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1975-05-05"},
    )
    db_session.flush()
    # 可见性靠 v1 直系边（既有合同），亲属门禁靠 confirmed SourceFact（结构真源）。
    _elder_relation(db_session, elder_id=b.id, younger_id=a.id)
    _confirm(db_session, "biological_parent", b.id, a.id)
    db_session.commit()
    ha = _login(client, "阿甲", "111111")
    hb = _login(client, "阿乙", "222222")

    # 乙建空间（owner 即 active space_admin）
    created = client.post("/api/spaces", json={"name": "乙家"}, headers=hb)
    assert created.status_code == 201, created.text
    space_id = created.json()["id"]

    # 甲与乙有 confirmed 亲属路径 → 可申请
    r1 = client.post("/api/spaces/join-by-user", json={"target_user_id": b.id}, headers=ha)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "pending"
    assert r1.json()["space_id"] == space_id

    # 幂等：重复 join 返回既有 pending 行（不新增）
    r2 = client.post("/api/spaces/join-by-user", json={"target_user_id": b.id}, headers=ha)
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == a.id).count() == 1

    # 09-19：申请人不得自批自己的加入申请
    self_accept = client.post(f"/api/space-memberships/{r1.json()['id']}/accept", headers=ha)
    assert self_accept.status_code == 403, self_accept.text
    db_session.expire_all()
    assert db_session.get(SpaceMember, r1.json()["id"]).status == "pending"

    # 该空间 space_admin 批准 → active，甲获得完整可见性
    acc = client.post(f"/api/space-memberships/{r1.json()['id']}/accept", headers=hb)
    assert acc.status_code == 200, acc.text
    assert acc.json()["status"] == "active"

    detail = client.get(f"/api/users/{b.id}", headers=ha).json()
    assert isinstance(detail["birth"], dict) and "__masked__" not in detail["birth"]


def test_join_requires_confirmed_kinship_with_space_member(db_session, client: TestClient) -> None:
    """可见性不足以申请：无 confirmed 亲属路径 → 403，且不落 pending 行。"""
    from app.models.space import FamilySpace
    from app.utils.timeutil import utcnow

    owner = create_user_with_pin(db_session, "准入owner", "333333", claim_status="claimed")
    space = FamilySpace(
        name="准入-无亲属", kind="household", owner_id=owner.id, created_at=utcnow()
    )
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="space_admin")
    outsider = create_user_with_pin(db_session, "准入外人", "444444", claim_status="claimed")
    # 仅凭代管创建者链接获得可见性（旧门禁下就足够，现在必须被亲属门禁拦住）
    owner.created_by = outsider.id
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={"target_user_id": owner.id},
        headers=_login(client, "准入外人", "444444"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_RELATION"
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == outsider.id)
        .count()
        == 0
    )


def test_join_target_space_resolved_from_manager_not_any_membership(
    db_session, client: TestClient
) -> None:
    """目标空间只按 owner/space_admin 解析：普通成员身份不得回退成「任意成员资格」。"""
    from app.models.space import FamilySpace
    from app.utils.timeutil import utcnow

    owner = create_user_with_pin(db_session, "解析owner", "555555", claim_status="claimed")
    space = FamilySpace(name="解析-空间", kind="household", owner_id=owner.id, created_at=utcnow())
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="space_admin")
    plain = create_user_with_pin(db_session, "解析普通成员", "666666", claim_status="claimed")
    create_space_member(db_session, space.id, plain.id)
    joiner = create_user_with_pin(db_session, "解析申请者", "777777", claim_status="claimed")
    db_session.flush()
    # 与 owner 有亲属路径（门禁可通过）；对 plain 可见（v1 直系边）但无任何亲属关系。
    create_space_member(db_session, space.id, joiner.id, status="removed")
    _confirm(db_session, "biological_parent", owner.id, joiner.id)
    _elder_relation(db_session, elder_id=plain.id, younger_id=joiner.id)
    db_session.commit()

    # 以普通成员身份作为「其空间」的解析输入：plain 不是任何空间的管理者，
    # 故必须 409，而不是落到 owner 的 space（旧代码的 active_ids[0] 回退）。
    resp = client.post(
        "/api/spaces/join-by-user",
        json={"target_user_id": plain.id},
        headers=_login(client, "解析申请者", "777777"),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_TARGET_SPACE"


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
    # v2：直系跨空间 = lineage_summary，敏感字段遮蔽但可见（通知/摘要需要）
    assert ok.json()["birth"] == MASKED

    # 父 revoke 断连
    hp = _login(client, "父", "121212")
    rv = client.post(f"/api/relations/{edge.id}/revoke", headers=hp)
    assert rv.status_code == 200

    # 即时降级：无其他连接/空间 → 直接不可见
    after = client.get(f"/api/users/{a.id}", headers=hc)
    assert after.status_code == 404
