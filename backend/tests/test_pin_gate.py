"""pin_must_change 全局门禁：白名单外全部 403，白名单内可用（PRD 验收）。

白名单 = {PUT /me/pin, POST /auth/logout, POST /auth/refresh}；
health 公开端点不经认证依赖管辖（architecture.md §1）。
"""

from conftest import auth_header, create_user_with_pin, login


def _forced_client(client, db_session):
    """造一个 pin_must_change=true 的账号并完成登录。"""
    create_user_with_pin(db_session, "新人", "123456", pin_must_change=True)
    tokens = login(client, "新人", "123456").json()
    assert tokens["user"]["pin_must_change"] is True
    return tokens


def test_business_endpoint_blocked_with_pin_change_required(client, db_session) -> None:
    tokens = _forced_client(client, db_session)
    response = client.get("/api/me", headers=auth_header(tokens))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PIN_CHANGE_REQUIRED"


def test_rename_blocked_before_pin_change(client, db_session) -> None:
    tokens = _forced_client(client, db_session)
    response = client.put("/api/me/name", headers=auth_header(tokens), json={"name": "新名字"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PIN_CHANGE_REQUIRED"


def test_whitelisted_refresh_usable_when_forced(client, db_session) -> None:
    tokens = _forced_client(client, db_session)
    response = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    # 刷新出的 access 依旧受门禁约束
    me = client.get("/api/me", headers=auth_header(response.json()))
    assert me.status_code == 403


def test_whitelisted_logout_usable_when_forced(client, db_session) -> None:
    tokens = _forced_client(client, db_session)
    response = client.post(
        "/api/auth/logout",
        headers=auth_header(tokens),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert response.status_code == 200
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_whitelisted_me_pin_completes_claim(client, db_session) -> None:
    tokens = _forced_client(client, db_session)
    from app.models.user import User

    managed = db_session.query(User).filter(User.name == "新人").one()
    assert managed.account.status == "managed"

    changed = client.put(
        "/api/me/pin",
        headers=auth_header(tokens),
        json={"old_pin": "123456", "new_pin": "654321"},
    )
    assert changed.status_code == 200
    assert changed.json()["pin_must_change"] is False

    # 首登改 PIN 完成 = 认领完成（AD-1 唯一 managed→claimed 转换点）
    db_session.expire_all()
    assert managed.account.status == "claimed"

    # 改 PIN 后旧 access 即刻失效；旧 refresh 也无法再换新；用新 PIN 登录畅通无阻
    assert client.get("/api/me", headers=auth_header(tokens)).status_code == 401
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    fresh = login(client, "新人", "654321")
    assert fresh.status_code == 200
    me = client.get("/api/me", headers=auth_header(fresh.json()))
    assert me.status_code == 200


def test_health_public_even_without_auth(client) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_login_select_public_paths_unblocked(client, db_session) -> None:
    """login/select/bootstrap 公开端点不受门禁影响。"""
    create_user_with_pin(db_session, "大壮", "123456")
    create_user_with_pin(db_session, "大壮", "123456")
    challenge = login(client, "大壮", "123456")
    assert challenge.status_code == 409  # 未被门禁拦截

    status = client.get("/api/bootstrap/status")
    assert status.status_code == 200


def test_wrong_old_pin_rejected_with_unified_message(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()
    response = client.put(
        "/api/me/pin",
        headers=auth_header(tokens),
        json={"old_pin": "000000", "new_pin": "654321"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "名字或 PIN 码错误"
