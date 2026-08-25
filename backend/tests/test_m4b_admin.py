"""m4b 管理员后台：权限门禁、重置 PIN 失效链、审计可见性。"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


@pytest.fixture()
def admin_and_user(db_session):
    admin = create_user_with_pin(
        db_session, "管长", "000000", is_admin=True, claim_status="claimed"
    )
    user = create_user_with_pin(
        db_session,
        "群众",
        "123123",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1980-08-08"},
    )
    db_session.commit()
    return admin, user


def test_non_admin_403_everywhere(client: TestClient, admin_and_user):
    _admin, user = admin_and_user
    hu = _login(client, "群众", "123123")
    assert client.get("/api/admin/users", headers=hu).status_code == 403
    r = client.post(f"/api/admin/users/{user.id}/reset-pin", json={"confirm": True}, headers=hu)
    assert r.status_code == 403
    assert client.get("/api/admin/audit-logs", headers=hu).status_code == 403


def test_reset_pin_one_time_and_sessions_revoked(db_session, client: TestClient, admin_and_user):
    admin, user = admin_and_user
    ha = _login(client, "管长", "000000")

    # 群众先登录拿 access
    old_tokens = login(client, "群众", "123123").json()
    old_header = auth_header(old_tokens)
    assert client.get("/api/me", headers=old_header).status_code == 200

    # 管理员重置
    r = client.post(f"/api/admin/users/{user.id}/reset-pin", json={"confirm": True}, headers=ha)
    assert r.status_code == 200, r.text
    new_pin = r.json()["pin"]

    # 旧 access 即刻失效（token_version+1）
    assert client.get("/api/me", headers=old_header).status_code == 401

    # 新 PIN 可登录且强制改 PIN
    fresh = login(client, "群众", new_pin)
    assert fresh.status_code == 200
    assert fresh.json()["user"]["pin_must_change"] is True

    # 审计留痕
    logs = client.get("/api/admin/audit-logs", headers=ha).json()
    assert any(entry["action"] == "pin_reset" for entry in logs)


def test_admin_update_user_transfer_custody(db_session, client: TestClient, admin_and_user):
    _admin, user = admin_and_user
    guardian = create_user_with_pin(db_session, "新管", "456456", claim_status="claimed")
    ha = _login(client, "管长", "000000")
    db_session.commit()

    r = client.patch(
        f"/api/admin/users/{user.id}",
        json={"name": "改名群众", "transfer_custody_to": guardian.id},
        headers=ha,
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    refreshed = client.get(f"/api/users/{user.id}", headers=_login(client, "管长", "000000")).json()
    assert refreshed["name"] == "改名群众"
