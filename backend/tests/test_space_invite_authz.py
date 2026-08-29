"""空间邀请写授权回归测试。"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_space_member, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.models.space import FamilySpace, SpaceMember
from app.utils.timeutil import utcnow


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    response = login(client, name, pin)
    assert response.status_code == 200, response.text
    return auth_header(response.json())


@pytest.mark.parametrize("role", ["owner", "space_admin"])
def test_space_manager_can_invite_member(client: TestClient, db_session, role: str) -> None:
    actor = create_user_with_pin(db_session, f"{role}-actor", "111111")
    target = create_user_with_pin(db_session, f"{role}-target", "222222")
    now = utcnow()
    space = FamilySpace(name=f"{role}-space", owner_id=actor.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, actor.id, role=role)
    db_session.commit()

    response = client.post(
        f"/api/spaces/{space.id}/members",
        headers=_login(client, actor.name, "111111"),
        json={"user_id": target.id},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"


@pytest.mark.parametrize("role", ["member", "guest"])
def test_regular_space_members_cannot_invite_member(
    client: TestClient, db_session, role: str
) -> None:
    owner = create_user_with_pin(db_session, f"{role}-owner", "333333")
    actor = create_user_with_pin(db_session, f"{role}-actor", "444444")
    target = create_user_with_pin(db_session, f"{role}-target", "555555")
    now = utcnow()
    space = FamilySpace(name=f"{role}-space", owner_id=owner.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="owner")
    create_space_member(db_session, space.id, actor.id, role=role)
    db_session.commit()

    response = client.post(
        f"/api/spaces/{space.id}/members",
        headers=_login(client, actor.name, "444444"),
        json={"user_id": target.id},
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
    assert db_session.query(SpaceMember).filter(SpaceMember.user_id == target.id).count() == 0


def test_platform_operator_cannot_invite_into_family_space(client: TestClient, db_session) -> None:
    operator = create_user_with_pin(db_session, "平台运营者", "666666", is_admin=True)
    owner = create_user_with_pin(db_session, "空间所有者", "777777")
    target = create_user_with_pin(db_session, "邀请目标", "888888")
    now = utcnow()
    space = FamilySpace(name="运营隔离空间", owner_id=owner.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="owner")
    db_session.commit()

    response = client.post(
        f"/api/spaces/{space.id}/members",
        headers=_login(client, operator.name, "666666"),
        json={"user_id": target.id},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "SPACE_NOT_FOUND"
