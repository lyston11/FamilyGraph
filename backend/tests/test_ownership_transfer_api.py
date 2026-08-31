"""Ownership-transfer read endpoint authorization regressions."""

from __future__ import annotations

from conftest import auth_header, create_space_member, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.models.space import FamilySpace
from app.models.v2_foundation import OwnershipTransfer
from app.utils.timeutil import utcnow


def _headers(client: TestClient, name: str, pin: str) -> dict[str, str]:
    response = login(client, name, pin)
    assert response.status_code == 200, response.text
    return auth_header(response.json())


def test_list_ownership_transfers_requires_active_membership(
    client: TestClient, db_session
) -> None:
    owner = create_user_with_pin(db_session, "移交空间主", "111111")
    removed = create_user_with_pin(db_session, "已移除成员", "222222")
    now = utcnow()
    space = FamilySpace(name="移交读取空间", owner_id=owner.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="owner", status="active")
    create_space_member(db_session, space.id, removed.id, role="member", status="removed")
    transfer = OwnershipTransfer(
        space_id=space.id,
        from_user=owner.id,
        to_user=removed.id,
        status="cancelled",
        created_at=now,
        decided_at=now,
    )
    db_session.add(transfer)
    db_session.commit()

    response = client.get(
        f"/api/spaces/{space.id}/ownership-transfers",
        headers=_headers(client, removed.name, "222222"),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "SPACE_NOT_FOUND"


def test_list_ownership_transfers_allows_active_membership(client: TestClient, db_session) -> None:
    owner = create_user_with_pin(db_session, "有效移交主", "333333")
    member = create_user_with_pin(db_session, "有效移交成员", "444444")
    now = utcnow()
    space = FamilySpace(name="有效移交空间", owner_id=owner.id, kind="household", created_at=now)
    db_session.add(space)
    db_session.flush()
    create_space_member(db_session, space.id, owner.id, role="owner", status="active")
    create_space_member(db_session, space.id, member.id, role="member", status="active")
    transfer = OwnershipTransfer(
        space_id=space.id,
        from_user=owner.id,
        to_user=member.id,
        status="cancelled",
        created_at=now,
        decided_at=now,
    )
    db_session.add(transfer)
    db_session.commit()

    response = client.get(
        f"/api/spaces/{space.id}/ownership-transfers",
        headers=_headers(client, member.name, "444444"),
    )
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [transfer.id]
