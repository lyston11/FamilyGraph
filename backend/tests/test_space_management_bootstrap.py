"""空间管理首屏 Bootstrap 端点的授权与投影合同。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.space import FamilySpace
from app.utils.timeutil import utcnow
from conftest import auth_header, create_space_member, create_user_with_pin, login


def _headers(client: TestClient, name: str, pin: str) -> dict[str, str]:
    response = login(client, name, pin)
    assert response.status_code == 200, response.text
    return auth_header(response.json())


def test_management_bootstrap_returns_one_authorized_snapshot(
    client: TestClient, db_session
) -> None:
    manager = create_user_with_pin(db_session, "管理首屏主", "111111")
    now = utcnow()
    space = FamilySpace(name="管理首屏空间", owner_id=manager.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, manager.id, role="space_admin", status="active")
    db_session.commit()

    response = client.get(
        f"/api/spaces/{space.id}/management-bootstrap",
        headers=_headers(client, manager.name, "111111"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["space"]["id"] == space.id
    assert body["space"]["current_role"] == "space_admin"
    assert body["members"][0]["user_id"] == manager.id
    assert body["transfers"] == []
    assert body["profile_refs"] == []

    spaces_response = client.get("/api/spaces", headers=_headers(client, manager.name, "111111"))
    assert spaces_response.status_code == 200, spaces_response.text
    assert spaces_response.json()[0]["current_role"] == "space_admin"


def test_management_bootstrap_rechecks_manager_authorization(
    client: TestClient, db_session
) -> None:
    manager = create_user_with_pin(db_session, "管理权限主", "222222")
    member = create_user_with_pin(db_session, "普通成员", "333333")
    now = utcnow()
    space = FamilySpace(name="管理权限空间", owner_id=manager.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, manager.id, role="space_admin", status="active")
    create_space_member(db_session, space.id, member.id, role="member", status="active")
    db_session.commit()

    response = client.get(
        f"/api/spaces/{space.id}/management-bootstrap",
        headers=_headers(client, member.name, "333333"),
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
