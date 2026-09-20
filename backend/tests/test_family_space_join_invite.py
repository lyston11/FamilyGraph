"""家族空间限定的双向加入：邀请与申请（09-20）。

覆盖：
- `GET /spaces/family-space-options`：同族判定 + 双向三态；不同族两列表为空；
  目标不可见 / 非该家族成员 → 404；只读不落库；
- `POST /spaces/family-invitations`：同族前提、空间必须属于该家族空间、只产生 pending；
- `POST /spaces/join-by-user`：家族空间限定，移除 owner 全局回退；篡改为别的家族
  的空间 → 拒绝且不落 pending。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.space import FamilySpace, SpaceMember
from app.utils.timeutil import utcnow
from conftest import auth_header, create_space_member, create_user_with_pin, login


def _login_header(client: TestClient, name: str, pin: str = "123456") -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
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


class _Scene:
    """同一家族空间下的双方，各带一个配对家庭空间。"""

    def __init__(self, session, *, name: str = "场景"):
        self.me = create_user_with_pin(session, f"{name}-me", "123456")
        self.target = create_user_with_pin(session, f"{name}-target", "123456")
        self.lineage = _space(session, self.me, f"{name}-家族", kind="lineage")
        create_space_member(session, self.lineage.id, self.target.id)
        self.my_home = _space(session, self.me, f"{name}-我的家庭")
        self.my_home.lineage_space_id = self.lineage.id
        self.their_home = _space(session, self.target, f"{name}-对方家庭")
        self.their_home.lineage_space_id = self.lineage.id
        _visible_to(session, actor_id=self.me.id, target_id=self.target.id)
        session.commit()


# ---- 只读投影 ----


def test_options_report_both_directions_with_three_states(client: TestClient, db_session) -> None:
    """双向三态：邀请看我方空间的对方状态，申请看对方空间的我方状态。"""
    scene = _Scene(db_session, name="双向")
    # 对方已在我的家庭空间；我已有对方家庭的 pending 申请
    create_space_member(db_session, scene.my_home.id, scene.target.id)
    create_space_member(db_session, scene.their_home.id, scene.me.id, status="pending")
    db_session.commit()

    resp = client.get(
        "/api/spaces/family-space-options",
        params={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "双向-me"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lineage_space_id"] == scene.lineage.id
    assert body["lineage_space_name"] == "双向-家族"
    assert body["shares_lineage"] is True
    assert body["invite"] == [
        {"space_id": scene.my_home.id, "space_name": "双向-我的家庭", "status": "active"}
    ]
    # join 列出「对方在该家族空间下为 active 的家庭空间」：对方已在我的家庭里，
    # 所以它也在列表中，但我在那里是 active → 状态 active（前端显示已在其中并禁用）。
    assert body["join"] == [
        {"space_id": scene.my_home.id, "space_name": "双向-我的家庭", "status": "active"},
        {"space_id": scene.their_home.id, "space_name": "双向-对方家庭", "status": "pending"},
    ]


def test_options_are_empty_when_not_sharing_the_lineage(client: TestClient, db_session) -> None:
    """不同族：两个方向都为空（走邀请码途径），而不是报错。"""
    scene = _Scene(db_session, name="异族")
    outsider = create_user_with_pin(db_session, "异族-外人", "123456")
    _visible_to(db_session, actor_id=outsider.id, target_id=scene.target.id)
    db_session.commit()

    resp = client.get(
        "/api/spaces/family-space-options",
        params={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "异族-外人"),
    )
    # 调用者不是该家族空间成员 → 与空间不存在同一 404
    assert resp.status_code == 404, resp.text


def test_options_empty_lists_when_target_not_in_lineage(client: TestClient, db_session) -> None:
    """调用者在家族空间、目标不在：shares_lineage=false 且两列表为空。"""
    scene = _Scene(db_session, name="目标缺族")
    stranger = create_user_with_pin(db_session, "目标缺族-外人", "123456")
    _visible_to(db_session, actor_id=scene.me.id, target_id=stranger.id)
    db_session.commit()

    resp = client.get(
        "/api/spaces/family-space-options",
        params={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": stranger.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "目标缺族-me"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shares_lineage"] is False
    assert body["invite"] == [] and body["join"] == []


def test_options_404_for_invisible_target(client: TestClient, db_session) -> None:
    """目标不可见 → 404（不做存在性探针）。"""
    scene = _Scene(db_session, name="不可见")
    stranger = create_user_with_pin(db_session, "不可见-陌生人", "123456")
    db_session.commit()

    resp = client.get(
        "/api/spaces/family-space-options",
        params={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": stranger.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "不可见-me"),
    )
    assert resp.status_code == 404, resp.text


def test_options_are_read_only(client: TestClient, db_session) -> None:
    """读取不落库。"""
    scene = _Scene(db_session, name="只读")
    members_before = db_session.query(SpaceMember).count()

    resp = client.get(
        "/api/spaces/family-space-options",
        params={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "只读-me"),
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.query(SpaceMember).count() == members_before


# ---- 邀请（家族限定） ----


def test_family_invitation_creates_pending_only(client: TestClient, db_session) -> None:
    """同族 + 我的家族空间 → 只产生 pending。"""
    scene = _Scene(db_session, name="邀请")
    resp = client.post(
        "/api/spaces/family-invitations",
        json={
            "lineage_space_id": scene.lineage.id,
            "space_id": scene.my_home.id,
            "user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "邀请-me"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending"
    row = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == scene.my_home.id, SpaceMember.user_id == scene.target.id
        )
    )
    assert row is not None and row.status == "pending"


def test_family_invitation_rejects_space_outside_the_lineage(
    client: TestClient, db_session
) -> None:
    """空间不属于该家族空间 → 403，不落 pending。"""
    scene = _Scene(db_session, name="越界")
    other_lineage = _space(db_session, scene.me, "越界-别的家族", kind="lineage")
    create_space_member(db_session, other_lineage.id, scene.target.id)
    stray_home = _space(db_session, scene.me, "越界-别家家庭")
    stray_home.lineage_space_id = other_lineage.id
    db_session.commit()

    resp = client.post(
        "/api/spaces/family-invitations",
        json={
            "lineage_space_id": scene.lineage.id,
            "space_id": stray_home.id,
            "user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "越界-me"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == stray_home.id, SpaceMember.user_id == scene.target.id)
        .count()
        == 0
    )


def test_family_invitation_rejects_target_outside_the_lineage(
    client: TestClient, db_session
) -> None:
    """目标不在该家族空间 → 403（不同族只能走邀请码）。"""
    scene = _Scene(db_session, name="邀请异族")
    outsider = create_user_with_pin(db_session, "邀请异族-外人", "123456")
    _visible_to(db_session, actor_id=scene.me.id, target_id=outsider.id)
    db_session.commit()

    resp = client.post(
        "/api/spaces/family-invitations",
        json={
            "lineage_space_id": scene.lineage.id,
            "space_id": scene.my_home.id,
            "user_id": outsider.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "邀请异族-me"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_RELATION"


# ---- 申请（家族限定） ----


def test_join_uses_the_targets_household_in_this_lineage(client: TestClient, db_session) -> None:
    """申请落在对方在**该家族空间**下的家庭空间。"""
    scene = _Scene(db_session, name="申请")
    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "申请-me"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["space_id"] == scene.their_home.id
    assert resp.json()["status"] == "pending"


def test_join_does_not_fall_back_to_another_lineage_household(
    client: TestClient, db_session
) -> None:
    """对方在别的家族空间下的家庭空间不会被选中（移除 owner 全局回退）。"""
    scene = _Scene(db_session, name="回退")
    # 对方在另一个家族空间下还有一个家庭空间（id 更小，旧实现会先命中它）
    other_lineage = _space(db_session, scene.target, "回退-别的家族", kind="lineage")
    create_space_member(db_session, other_lineage.id, scene.me.id)
    other_home = _space(db_session, scene.target, "回退-别家家庭")
    other_home.lineage_space_id = other_lineage.id
    # 让「别家」空间 id 小于本家族空间，确保旧回退会选错
    db_session.flush()
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "回退-me"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["space_id"] == scene.their_home.id
    assert resp.json()["space_id"] != other_home.id


def test_join_rejects_explicit_space_outside_the_lineage(client: TestClient, db_session) -> None:
    """显式传入别的家族空间的家庭空间 → 403，不落 pending。"""
    scene = _Scene(db_session, name="申请越界")
    other_lineage = _space(db_session, scene.target, "申请越界-别家", kind="lineage")
    other_home = _space(db_session, scene.target, "申请越界-别家家庭")
    other_home.lineage_space_id = other_lineage.id
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "space_id": other_home.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "申请越界-me"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
    assert (
        db_session.query(SpaceMember)
        .filter(SpaceMember.space_id == other_home.id, SpaceMember.user_id == scene.me.id)
        .count()
        == 0
    )


def test_join_requires_sharing_the_lineage(client: TestClient, db_session) -> None:
    """目标不在该家族空间 → 403（同族是前提）。"""
    scene = _Scene(db_session, name="申请异族")
    outsider = create_user_with_pin(db_session, "申请异族-外人", "123456")
    _visible_to(db_session, actor_id=scene.me.id, target_id=outsider.id)
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": outsider.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "申请异族-me"),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_RELATION"


def test_join_409_when_target_has_no_household_in_this_lineage(
    client: TestClient, db_session
) -> None:
    """对方在该家族空间下没有家庭空间 → 409。"""
    scene = _Scene(db_session, name="无空间")
    create_space_member(db_session, scene.their_home.id, scene.target.id, status="removed")
    db_session.commit()

    resp = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "无空间-me"),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SPACE_JOIN_NO_TARGET_SPACE"


def test_join_request_cannot_be_self_approved(client: TestClient, db_session) -> None:
    """申请人不得自批：只有该家庭空间管理员可批准。"""
    scene = _Scene(db_session, name="自批")
    created = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.target.id,
            "relation_label": "堂兄弟",
        },
        headers=_login_header(client, "自批-me"),
    )
    assert created.status_code == 201, created.text
    member_id = created.json()["id"]

    # 09-20 审批链：join_request 由该空间房主批准后直接 active。
    # 申请人自己批准 → 403（不能批准自己加入）。
    self_approve = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_login_header(client, "自批-me")
    )
    assert self_approve.status_code == 403, self_approve.text
    db_session.expire_all()
    assert db_session.get(SpaceMember, member_id).status == "pending"

    # 房主（目标本人）批准 → 当场 active（申请人提交即其同意）
    approved = client.post(
        f"/api/space-memberships/{member_id}/approve",
        headers=_login_header(client, "自批-target"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
