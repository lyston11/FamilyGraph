"""m2c 加入申请、断连即时降级与幂等（architecture §4 [AD-4]）。

09-20 起 join-by-user 是家族空间限定的受限准入：申请人须与目标同属该 active
家族空间，目标空间只能是对方在该家族空间下的家庭空间（不再回退 owner 解析）；
household pending 行由该空间管理员批准——申请人本人不得自批。家庭空间成员资格
不等于家族空间成员资格。
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


def _lineage_space(session, *users):
    """共同家族空间：join 准入要求双方同属该 active lineage（09-20 家族限定）。"""
    from app.models.space import FamilySpace
    from app.utils.timeutil import utcnow

    space = FamilySpace(
        name=f"准入lineage-{users[0].id}", kind="lineage", owner_id=users[0].id, created_at=utcnow()
    )
    session.add(space)
    session.flush()
    for user in users:
        create_space_member(session, space.id, user.id)
    return space


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
    # 可见性靠 v1 直系边（既有合同）；准入资格靠「与目标同属一个 active lineage」。
    _elder_relation(db_session, elder_id=b.id, younger_id=a.id)
    _lineage_space(db_session, a, b)
    db_session.commit()
    ha = _login(client, "阿甲", "111111")
    hb = _login(client, "阿乙", "222222")

    # 乙建空间（owner 即 active space_admin），并配对到共同家族空间
    lineage = _lineage_space(db_session, a, b)
    created = client.post(
        "/api/spaces",
        json={"name": "乙家", "lineage_space_id": lineage.id},
        headers=hb,
    )
    assert created.status_code == 201, created.text
    space_id = created.json()["id"]
    db_session.commit()

    # 甲与乙同属该家族空间 → 可申请（限定在该家族空间范围内）
    payload = {"lineage_space_id": lineage.id, "target_user_id": b.id, "relation_label": "堂兄弟"}
    r1 = client.post("/api/spaces/join-by-user", json=payload, headers=ha)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "pending"
    assert r1.json()["space_id"] == space_id

    # 幂等：重复 join 返回既有 pending 行（不新增）
    r2 = client.post("/api/spaces/join-by-user", json=payload, headers=ha)
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.user_id == a.id)
        .count()
        == 1
    )

    # 09-20：申请人不得自批自己的加入申请
    self_accept = client.post(f"/api/space-memberships/{r1.json()['id']}/approve", headers=ha)
    assert self_accept.status_code == 403, self_accept.text
    db_session.expire_all()
    assert db_session.get(SpaceMember, r1.json()["id"]).status == "pending"

    # 09-20 审批链：房主（乙）批准 → 直接 active，甲获得 household 可见性
    acc = client.post(f"/api/space-memberships/{r1.json()['id']}/approve", headers=hb)
    assert acc.status_code == 200, acc.text
    assert acc.json()["status"] == "active"

    detail = client.get(f"/api/users/{b.id}", headers=ha).json()
    assert isinstance(detail["birth"], dict) and "__masked__" not in detail["birth"]


def test_join_requires_shared_lineage(db_session, client: TestClient) -> None:
    """可见性不足以申请：不在同一个 active lineage → 403，且不落 pending 行。"""
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
    # 仅凭代管创建者链接获得可见性（旧门禁下就足够，现在必须被 lineage 门禁拦住）
    owner.created_by = outsider.id
    db_session.commit()

    # 申请人在该家族空间里，但目标不在 → 同族门禁拒绝（不落 pending）
    lineage = _lineage_space(db_session, outsider)
    db_session.commit()
    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": lineage.id,
            "target_user_id": owner.id,
            "relation_label": "堂兄弟",
        },
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
    # 与 owner 同属一个 lineage（门禁可通过）；对 plain 可见（v1 直系边）但无任何亲属关系。
    create_space_member(db_session, space.id, joiner.id, status="removed")
    _lineage_space(db_session, owner, joiner)
    _elder_relation(db_session, elder_id=plain.id, younger_id=joiner.id)
    db_session.commit()

    # 09-20：空间解析不再看 owner，只看「对方在该家族空间下是否有家庭空间」。
    # plain 在该家族空间下没有家庭空间 → 409（旧代码的 owner 回退已移除）。
    lineage = _lineage_space(db_session, owner, joiner)
    db_session.commit()
    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": lineage.id,
            "target_user_id": plain.id,
            "relation_label": "堂兄弟",
        },
        headers=_login(client, "解析申请者", "777777"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_RELATION"


def test_join_invisible_target_404(db_session, client: TestClient):
    """跨家族不可见目标 → 404（invisible ≠ 拒绝）。"""
    create_user_with_pin(db_session, "隐士", "777777", claim_status="claimed")
    create_user_with_pin(db_session, "求加", "888888", claim_status="claimed")
    db_session.commit()
    hs = _login(client, "求加", "888888")
    target_id = db_session.execute(
        __import__("sqlalchemy").text("SELECT id FROM users WHERE name='隐士'")
    ).scalar()
    r = client.post(
        "/api/spaces/join-by-user",
        json={"lineage_space_id": 1, "target_user_id": target_id, "relation_label": "堂兄弟"},
        headers=hs,
    )
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
