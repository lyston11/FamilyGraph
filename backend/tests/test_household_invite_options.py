"""个人公示页邀请选择与退出空间的授权边界（09-20）。

覆盖：
- `GET /spaces/household-invite-options` 只返回**我** active 成员的家庭空间，
  目标状态三态（active/pending/none）；目标不可见 → 404；不含家族空间；
- 退出空间复用既有 `DELETE /space-memberships/{id}`：本人可退出、非成员不可代
  他人退出、`space_admin` 需先交接；
- `GET /spaces` 暴露 `my_member_id`，供退出入口定位自己的成员行。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.space import FamilySpace, SpaceMember
from app.services import source_facts as sf
from app.utils.timeutil import utcnow
from conftest import auth_header, create_space_member, create_user_with_pin, login


def _login_header(client: TestClient, name: str, pin: str = "123456") -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _make_space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id, role="space_admin")
    return space


def _visible_to(session, *, actor_id: int, target_id: int) -> None:
    """建立 actor→target 可见性（v1 直系边，既有可见性合同）。"""
    from app.models.relation import Relation

    now = utcnow()
    session.add(
        Relation(
            from_user=target_id,
            to_user=actor_id,
            dir_class="elder",
            created_by=target_id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


# ---- 邀请选择 ----


def test_invite_options_report_three_states_for_my_households(
    client: TestClient, db_session
) -> None:
    """三态：已在空间 / 已有待处理邀请 / 可以邀请。"""
    me = create_user_with_pin(db_session, "选项-me", "123456")
    target = create_user_with_pin(db_session, "选项-target", "123456")
    active_space = _make_space(db_session, me, "选项-已在")
    pending_space = _make_space(db_session, me, "选项-待接受")
    free_space = _make_space(db_session, me, "选项-可邀请")
    create_space_member(db_session, active_space.id, target.id)
    create_space_member(db_session, pending_space.id, target.id, status="pending")
    _visible_to(db_session, actor_id=me.id, target_id=target.id)
    db_session.commit()

    resp = client.get(
        f"/api/spaces/household-invite-options?target_user_id={target.id}",
        headers=_login_header(client, "选项-me"),
    )
    assert resp.status_code == 200, resp.text
    by_id = {item["space_id"]: item for item in resp.json()}
    assert by_id[active_space.id]["target_status"] == "active"
    assert by_id[pending_space.id]["target_status"] == "pending"
    assert by_id[free_space.id]["target_status"] == "none"
    assert by_id[free_space.id]["space_name"] == "选项-可邀请"


def test_invite_options_exclude_lineage_and_other_peoples_spaces(
    client: TestClient, db_session
) -> None:
    """只返回我 active 成员资格的家庭空间：家族空间与他人空间都不出现。"""
    me = create_user_with_pin(db_session, "范围-me", "123456")
    other = create_user_with_pin(db_session, "范围-other", "123456")
    target = create_user_with_pin(db_session, "范围-target", "123456")
    mine = _make_space(db_session, me, "范围-我的家庭")
    my_lineage = _make_space(db_session, me, "范围-我的家族", kind="lineage")
    others = _make_space(db_session, other, "范围-别人的家庭")
    _visible_to(db_session, actor_id=me.id, target_id=target.id)
    db_session.commit()

    resp = client.get(
        f"/api/spaces/household-invite-options?target_user_id={target.id}",
        headers=_login_header(client, "范围-me"),
    )
    assert resp.status_code == 200, resp.text
    ids = {item["space_id"] for item in resp.json()}
    assert mine.id in ids
    assert my_lineage.id not in ids  # 家族空间不是邀请目标
    assert others.id not in ids  # 我不是该空间成员


def test_invite_options_404_for_invisible_target(client: TestClient, db_session) -> None:
    """目标不可见 → 404（与不可见用户同一形状，不做存在性探针）。"""
    me = create_user_with_pin(db_session, "隐身-me", "123456")
    stranger = create_user_with_pin(db_session, "隐身-target", "123456")
    _make_space(db_session, me, "隐身-我的家庭")
    db_session.commit()

    resp = client.get(
        f"/api/spaces/household-invite-options?target_user_id={stranger.id}",
        headers=_login_header(client, "隐身-me"),
    )
    assert resp.status_code == 404, resp.text


def test_invite_options_are_read_only(client: TestClient, db_session) -> None:
    """读取不落库：不创建 pending 行、不写事实。"""
    me = create_user_with_pin(db_session, "只读-me", "123456")
    target = create_user_with_pin(db_session, "只读-target", "123456")
    space = _make_space(db_session, me, "只读-我的家庭")
    _visible_to(db_session, actor_id=me.id, target_id=target.id)
    db_session.commit()

    members_before = db_session.query(SpaceMember).count()
    facts_before = db_session.query(sf.SourceFact).count()
    resp = client.get(
        f"/api/spaces/household-invite-options?target_user_id={target.id}",
        headers=_login_header(client, "只读-me"),
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.query(SpaceMember).count() == members_before
    assert db_session.query(sf.SourceFact).count() == facts_before
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == space.id, SpaceMember.user_id == target.id)
        .count()
        == 0
    )


def test_invite_then_options_show_pending(client: TestClient, db_session) -> None:
    """走既有邀请命令后，同一端点立即反映 pending（服务端为真源）。"""
    me = create_user_with_pin(db_session, "闭环-me", "123456")
    target = create_user_with_pin(db_session, "闭环-target", "123456")
    space = _make_space(db_session, me, "闭环-我的家庭")
    _visible_to(db_session, actor_id=me.id, target_id=target.id)
    db_session.commit()

    invited = client.post(
        f"/api/spaces/{space.id}/members",
        json={"user_id": target.id},
        headers=_login_header(client, "闭环-me"),
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["status"] == "pending"

    resp = client.get(
        f"/api/spaces/household-invite-options?target_user_id={target.id}",
        headers=_login_header(client, "闭环-me"),
    )
    assert resp.status_code == 200, resp.text
    item = next(item for item in resp.json() if item["space_id"] == space.id)
    assert item["target_status"] == "pending"


# ---- 退出空间 ----


def test_my_spaces_expose_my_member_id(client: TestClient, db_session) -> None:
    """GET /spaces 暴露 my_member_id，指向我自己的成员行。"""
    me = create_user_with_pin(db_session, "退出-me", "123456")
    space = _make_space(db_session, me, "退出-我的家庭")
    db_session.commit()

    resp = client.get("/api/spaces", headers=_login_header(client, "退出-me"))
    assert resp.status_code == 200, resp.text
    item = next(item for item in resp.json() if item["id"] == space.id)
    mine = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == me.id)
    )
    assert item["my_member_id"] == mine.id


def test_member_can_leave_own_space(client: TestClient, db_session) -> None:
    """普通成员可退出自己所在的空间 → 终态 removed。"""
    owner = create_user_with_pin(db_session, "退出-owner", "123456")
    member = create_user_with_pin(db_session, "退出-member", "123456")
    space = _make_space(db_session, owner, "退出-空间")
    row = create_space_member(db_session, space.id, member.id)
    db_session.commit()

    resp = client.delete(
        f"/api/space-memberships/{row.id}", headers=_login_header(client, "退出-member")
    )
    assert resp.status_code == 204, resp.text
    db_session.expire_all()
    assert db_session.get(SpaceMember, row.id).status == "removed"
    # 退出后空间列表不再包含该空间
    listed = client.get("/api/spaces", headers=_login_header(client, "退出-member")).json()
    assert all(item["id"] != space.id for item in listed)


def test_member_cannot_remove_someone_else(client: TestClient, db_session) -> None:
    """不得代他人退出：非本人的 active 成员行由第三人删除 → 403。"""
    owner = create_user_with_pin(db_session, "代退-owner", "123456")
    member = create_user_with_pin(db_session, "代退-member", "123456")
    bystander = create_user_with_pin(db_session, "代退-bystander", "123456")
    space = _make_space(db_session, owner, "代退-空间")
    row = create_space_member(db_session, space.id, member.id)
    create_space_member(db_session, space.id, bystander.id)
    db_session.commit()

    resp = client.delete(
        f"/api/space-memberships/{row.id}", headers=_login_header(client, "代退-bystander")
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
    db_session.expire_all()
    assert db_session.get(SpaceMember, row.id).status == "active"


def test_space_admin_must_transfer_before_leaving(client: TestClient, db_session) -> None:
    """space_admin 不得直接退出：服务端要求先完成管理员交接。"""
    admin = create_user_with_pin(db_session, "交接-admin", "123456")
    space = _make_space(db_session, admin, "交接-空间")
    db_session.commit()
    mine = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == admin.id)
    )

    resp = client.delete(
        f"/api/space-memberships/{mine.id}", headers=_login_header(client, "交接-admin")
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SPACE_MANAGER_TRANSFER_REQUIRED"
    db_session.expire_all()
    assert db_session.get(SpaceMember, mine.id).status == "active"


def test_leave_is_idempotent_for_already_removed_row(client: TestClient, db_session) -> None:
    """已退出（终态）的行再次退出 → 409 非法转换，不产生第二次写入。"""
    owner = create_user_with_pin(db_session, "幂等-owner", "123456")
    member = create_user_with_pin(db_session, "幂等-member", "123456")
    space = _make_space(db_session, owner, "幂等-空间")
    row = create_space_member(db_session, space.id, member.id)
    db_session.commit()

    headers = _login_header(client, "幂等-member")
    assert client.delete(f"/api/space-memberships/{row.id}", headers=headers).status_code == 204
    second = client.delete(f"/api/space-memberships/{row.id}", headers=headers)
    assert second.status_code == 409, second.text
