"""首启引导 API：一次性管理员初始化（PRD / Q3 默认方案）。"""

from conftest import login

from app.models.audit_log import AuditLog


def test_status_false_when_empty(client) -> None:
    assert client.get("/api/bootstrap/status").json() == {"initialized": False}


def test_initialize_creates_admin_with_one_time_pin(client, db_session) -> None:
    response = client.post("/api/bootstrap/initialize", json={"name": "族长"})

    assert response.status_code == 200
    body = response.json()
    pin = body["one_time_pin"]
    assert len(pin) == 6 and pin.isdigit()
    assert body["user"]["principal_type"] == "system_admin"
    assert body["user"]["pin_must_change"] is True
    assert client.get("/api/bootstrap/status").json() == {"initialized": True}

    # 初始化创建独立系统主体，不占用家庭 User/Account（PRD R1）
    from app.models.account import Account
    from app.models.system_admin import SystemAdmin, SystemAdminAccount
    from app.models.user import User

    assert db_session.query(User).count() == 0
    assert db_session.query(Account).count() == 0
    admin = db_session.query(SystemAdmin).filter_by(id=body["user"]["id"]).one()
    account = db_session.query(SystemAdminAccount).filter_by(system_admin_id=admin.id).one()
    assert pin not in account.pin_hash
    audit_rows = db_session.query(AuditLog).filter(AuditLog.action == "bootstrap_initialized").all()
    assert len(audit_rows) == 1
    # 审计 detail 不含明文 PIN（脱敏红线）
    assert pin not in str(audit_rows[0].detail)


def test_initialize_rejected_once_users_exist(client, db_session) -> None:
    first = client.post("/api/bootstrap/initialize", json={"name": "族长"})
    assert first.status_code == 200

    second = client.post("/api/bootstrap/initialize", json={"name": "冒名"})
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "BOOTSTRAP_ALREADY_INITIALIZED"


def test_initialized_admin_can_login_and_forced_to_change_pin(client, db_session) -> None:
    body = client.post("/api/bootstrap/initialize", json={"name": "族长"}).json()
    tokens = login(client, "族长", body["one_time_pin"]).json()

    me = client.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 403  # 首登未改 PIN：白名单外一律拦截


def test_initialize_requires_valid_name(client, db_session) -> None:
    assert client.post("/api/bootstrap/initialize", json={"name": ""}).status_code == 422
