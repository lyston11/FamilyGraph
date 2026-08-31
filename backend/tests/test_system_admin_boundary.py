from conftest import create_user_with_pin
from fastapi.testclient import TestClient

from app.main import app


def test_system_admin_is_independent_and_metadata_only(client: TestClient, db_session) -> None:
    initialized = client.post("/api/bootstrap/initialize", json={"name": "平台管理员"})
    assert initialized.status_code == 200
    one_time_pin = initialized.json()["one_time_pin"]

    first_login = client.post("/api/auth/login", json={"name": "平台管理员", "pin": one_time_pin})
    assert first_login.status_code == 200
    first_token = first_login.json()["access_token"]
    changed = client.put(
        "/api/me/pin",
        json={"old_pin": one_time_pin, "new_pin": "654321"},
        headers={"Authorization": f"Bearer {first_token}"},
    )
    assert changed.status_code == 200

    family_user = create_user_with_pin(db_session, "家庭用户", "123456")
    db_session.commit()
    system_login = client.post("/api/auth/login", json={"name": "平台管理员", "pin": "654321"})
    assert system_login.status_code == 200
    system_token = system_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {system_token}"}

    accounts = client.get("/api/admin/accounts", headers=headers)
    assert accounts.status_code == 200
    assert {row["subject_type"] for row in accounts.json()} == {"system_admin", "family_user"}
    assert all(
        "bio" not in row and "birth" not in row and "gender" not in row for row in accounts.json()
    )

    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.get("/api/spaces", headers=headers).status_code == 401

    family_login = client.post("/api/auth/login", json={"name": family_user.name, "pin": "123456"})
    assert family_login.status_code == 200
    family_headers = {"Authorization": f"Bearer {family_login.json()['access_token']}"}
    assert client.get("/api/admin/accounts", headers=family_headers).status_code == 403


def test_admin_metadata_routes_are_registered_once() -> None:
    paths = [route.path for route in app.routes if route.path.startswith("/api/admin/")]
    assert paths.count("/api/admin/accounts") == 1
    assert paths.count("/api/admin/spaces") == 1
    assert paths.count("/api/admin/space-managers") == 1
