"""退出空间的授权边界（09-20）。

邀请/申请方向由 `tests/test_family_space_join_invite.py` 覆盖（家族空间限定）。
本文件只保留退出空间部分：
- 退出空间复用既有 `DELETE /space-memberships/{id}`：本人可退出、非成员不可代
  他人退出、`space_admin` 需先交接；
- `GET /spaces` 暴露 `my_member_id`，供退出入口定位自己的成员行。
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


def _make_space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id, role="space_admin")
    return space


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
