"""家庭空间与家族空间分离的安全边界（09-19 越权修复）。

核心不变量：加入别人的家庭空间（household）不等于加入别人的家族空间
（lineage）。家族树读取必须有该 lineage 的 active membership，且该 membership
只能由申请目标本人批准——申请人不得自批，也不得靠 household membership 绕过。

覆盖：
- household 成员读取目标 lineage 的 PFV 与旧 /graph/me 均安全 404；
- 家族空间申请是独立的第二条 pending，由目标本人批准；
- 批准后才可读取，且申请人不能自批；
- 已同属一个 household 时不再新建家庭（命令层最终防线）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.models.space import FamilySpace, SpaceMember
from app.utils.timeutil import utcnow
from conftest import auth_header, create_space_member, create_user_with_pin, login


def _login_header(client: TestClient, name: str, pin: str = "123456") -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _make_space(session, owner, name: str, *, kind: str) -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id, role="space_admin")
    return space


def _household_member_scene(session):
    """朱元璋式场景：viewer 加入 target 的家庭空间，但不在其家族空间。"""
    target = create_user_with_pin(session, "边界-target", "123456")
    viewer = create_user_with_pin(session, "边界-viewer", "123456")
    lineage = _make_space(session, target, "边界-家族空间", kind="lineage")
    household = _make_space(session, target, "边界-家庭空间", kind="household")
    household.lineage_space_id = lineage.id
    session.flush()
    session.commit()
    return viewer, target, household, lineage


def test_household_membership_does_not_grant_lineage_tree(
    client: TestClient, db_session, monkeypatch
) -> None:
    """只加入家庭空间不获得家族树读取权：PFV 与 /graph/me 均安全 404。"""
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    viewer, target, household, lineage = _household_member_scene(db_session)
    # viewer 是该 household 的 active 成员（等价于申请已被 target 批准）
    create_space_member(db_session, household.id, viewer.id)
    db_session.commit()

    headers = _login_header(client, "边界-viewer")

    pfv = client.get("/api/personal-family-view", params={"space_id": lineage.id}, headers=headers)
    assert pfv.status_code == 404, pfv.text
    assert pfv.json()["error"]["code"] == "PERSONAL_FAMILY_VIEW_NOT_FOUND"

    graph = client.get(f"/api/graph/me?scope=family&depth=2&space_id={lineage.id}", headers=headers)
    assert graph.status_code == 404, graph.text
    # 家庭空间本身仍可读（分离不等于取消家庭卡）
    household_read = client.get(
        "/api/personal-family-view", params={"space_id": household.id}, headers=headers
    )
    assert household_read.status_code in (200, 304), household_read.text
    # 目标本人（lineage owner）自己的家族树不受影响
    owner_read = client.get(
        "/api/personal-family-view",
        params={"space_id": lineage.id},
        headers=_login_header(client, "边界-target"),
    )
    assert owner_read.status_code in (200, 304), owner_read.text


def test_lineage_access_request_needs_target_approval(
    client: TestClient, db_session, monkeypatch
) -> None:
    """家族空间申请由目标本人审批：申请人不能自批，批准后才可读树。"""
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    viewer, target, household, lineage = _household_member_scene(db_session)
    create_space_member(db_session, household.id, viewer.id)
    db_session.commit()
    viewer_headers = _login_header(client, "边界-viewer")

    created = client.post(
        "/api/spaces/lineage-access-requests",
        json={"household_space_id": household.id},
        headers=viewer_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["space_id"] == lineage.id
    assert body["status"] == "pending"

    # 申请人不得自批
    self_accept = client.post(f"/api/space-memberships/{body['id']}/accept", headers=viewer_headers)
    assert self_accept.status_code == 403, self_accept.text
    db_session.expire_all()
    assert db_session.get(SpaceMember, body["id"]).status == "pending"

    # 未批准前仍不能读家族树
    blocked = client.get(
        "/api/personal-family-view", params={"space_id": lineage.id}, headers=viewer_headers
    )
    assert blocked.status_code == 404, blocked.text

    # 目标本人批准 → active，之后才可读取
    approved = client.post(
        f"/api/space-memberships/{body['id']}/accept",
        headers=_login_header(client, "边界-target"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    allowed = client.get(
        "/api/personal-family-view", params={"space_id": lineage.id}, headers=viewer_headers
    )
    assert allowed.status_code in (200, 304), allowed.text


def test_lineage_access_request_rejected_without_paired_lineage(
    client: TestClient, db_session
) -> None:
    """未配对家族空间的家庭空间不能发起家族树申请（409，不落 pending）。"""
    viewer, target, household, _lineage = _household_member_scene(db_session)
    household.lineage_space_id = None
    create_space_member(db_session, household.id, viewer.id)
    db_session.commit()

    resp = client.post(
        "/api/spaces/lineage-access-requests",
        json={"household_space_id": household.id},
        headers=_login_header(client, "边界-viewer"),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SPACE_LINEAGE_ACCESS_UNAVAILABLE"


def test_create_shared_household_reuses_existing_household(db_session) -> None:
    """命令层最终防线：双方已是同一 household active 成员时复用，不新建空间。"""
    from app.commands.context import ActorContext
    from app.commands.spaces import create_shared_household

    left = create_user_with_pin(db_session, "复用-left", "123456")
    right = create_user_with_pin(db_session, "复用-right", "123456")
    shared = _make_space(db_session, left, "复用-共同家庭", kind="household")
    create_space_member(db_session, shared.id, right.id)
    db_session.commit()

    spaces_before = db_session.query(FamilySpace).count()
    members_before = db_session.query(SpaceMember).count()
    ctx = ActorContext.from_identity(left, left.account)
    space, _event_id = create_shared_household(db_session, ctx, other_user_id=right.id)

    assert space.id == shared.id
    assert db_session.query(FamilySpace).count() == spaces_before
    assert db_session.query(SpaceMember).count() == members_before
    rows = db_session.scalars(
        select(SpaceMember).where(
            SpaceMember.space_id == shared.id,
            SpaceMember.user_id.in_((left.id, right.id)),
        )
    ).all()
    assert {row.status for row in rows} == {"active"}


def test_create_shared_household_does_not_merge_different_households(db_session) -> None:
    """双方已在**不同** household 时新建一个，不合并、不改既有成员。"""
    from app.commands.context import ActorContext
    from app.commands.spaces import create_shared_household

    left = create_user_with_pin(db_session, "分居-left", "123456")
    right = create_user_with_pin(db_session, "分居-right", "123456")
    left_home = _make_space(db_session, left, "分居-left家", kind="household")
    right_home = _make_space(db_session, right, "分居-right家", kind="household")
    db_session.commit()

    spaces_before = db_session.query(FamilySpace).count()
    ctx = ActorContext.from_identity(left, left.account)
    space, _event_id = create_shared_household(db_session, ctx, other_user_id=right.id)

    assert space.id not in (left_home.id, right_home.id)
    assert db_session.query(FamilySpace).count() == spaces_before + 1
    # 既有两个家庭各自成员不变（不静默搬家）
    assert db_session.query(SpaceMember).filter_by(space_id=left_home.id).count() == 1
    assert db_session.query(SpaceMember).filter_by(space_id=right_home.id).count() == 1


def test_create_shared_household_ignores_pending_membership(db_session) -> None:
    """仅 pending 的共同 household 不算已有共同家庭：照常新建。"""
    from app.commands.context import ActorContext
    from app.commands.spaces import create_shared_household

    left = create_user_with_pin(db_session, "待定-left", "123456")
    right = create_user_with_pin(db_session, "待定-right", "123456")
    shared = _make_space(db_session, left, "待定-家庭", kind="household")
    create_space_member(db_session, shared.id, right.id, status="pending")
    db_session.commit()

    spaces_before = db_session.query(FamilySpace).count()
    ctx = ActorContext.from_identity(left, left.account)
    space, _event_id = create_shared_household(db_session, ctx, other_user_id=right.id)

    assert space.id != shared.id
    assert db_session.query(FamilySpace).count() == spaces_before + 1
    # pending 行未被顺手激活
    pending = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == shared.id, SpaceMember.user_id == right.id
        )
    )
    assert pending is not None and pending.status == "pending"
