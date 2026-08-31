"""api/auth.py 全流程集成测试：登录/锁定/消歧/刷新/登出（implement.md #5、#10）。"""

from conftest import auth_header, create_user_with_pin, login

from app.models.audit_log import AuditLog


def test_login_success_returns_token_pair(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    response = login(client, "张三", "123456")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "user"}
    assert body["user"] == {
        "id": body["user"]["id"],
        "name": "张三",
        "is_admin": False,
        "pin_must_change": False,
        "claim_status": "claimed",
        "profile_status": "identity_confirmed",
        # 主体类型由服务端权威投影，家庭用户永不带平台角色
        "principal_type": "family_user",
        "platform_role": None,
    }


def test_me_carries_identity_status(client, db_session) -> None:
    """v2 Gap2：/me 直出两条独立状态机（accounts.status + users.profile_status），
    前端路由守卫据此判定确档向导，不再由 fact-reviews 推断。"""
    # 组合一：managed + provisional（未认领；pin_must_change=False 以通过 PIN 门禁）
    create_user_with_pin(
        db_session,
        "新人",
        "123456",
        claim_status="managed",
        profile_status="provisional",
    )
    tokens = login(client, "新人", "123456").json()
    assert tokens["user"]["claim_status"] == "managed"
    assert tokens["user"]["profile_status"] == "provisional"

    me = client.get("/api/me", headers=auth_header(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["claim_status"] == "managed"
    assert body["profile_status"] == "provisional"

    # 组合二：claimed + identity_confirmed（既有建档默认态），两状态分别直出
    create_user_with_pin(db_session, "老人", "654321")
    old_tokens = login(client, "老人", "654321").json()
    assert old_tokens["user"]["claim_status"] == "claimed"
    assert old_tokens["user"]["profile_status"] == "identity_confirmed"


def test_wrong_pin_unified_message_401(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    wrong = login(client, "张三", "999999")
    missing = login(client, "不存在", "123456")

    for response in (wrong, missing):
        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "AUTH_INVALID_CREDENTIALS"
        assert error["message"] == "名字或 PIN 码错误"  # 统一文案防枚举
    # 账号存在与否的响应体完全一致
    assert wrong.json() == missing.json()


def test_invalid_payload_rejected(client) -> None:
    short_pin = client.post("/api/auth/login", json={"name": "x", "pin": "12345"})
    non_digit = client.post("/api/auth/login", json={"name": "x", "pin": "12a456"})
    empty_name = client.post("/api/auth/login", json={"name": "", "pin": "123456"})
    for response in (short_pin, non_digit, empty_name):
        assert response.status_code == 422


def test_fifth_failure_locks_account_for_window(client, db_session, monkeypatch) -> None:
    from datetime import timedelta

    from app import config
    from app.utils import timeutil

    user = create_user_with_pin(db_session, "张三", "123456")
    # 前 4 次失败：普通 401；第 5 次失败触发锁定（本次仍为凭据错误 401）
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        assert login(client, "张三", "000000").status_code == 401
    db_session.expire_all()
    assert user.account.locked_until is not None

    # 锁定生效后：即使 PIN 正确也 429 拒绝
    locked = login(client, "张三", "123456")
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert int(locked.headers["Retry-After"]) > 0

    # 窗口过后恢复，失败预算清零（先捕获当前时刻避免递归）
    base = timeutil.utcnow()
    monkeypatch.setattr(
        timeutil,
        "utcnow",
        lambda: base + timedelta(minutes=config.AUTH_LOCK_MINUTES, seconds=1),
    )
    assert login(client, "张三", "123456").status_code == 200
    db_session.expire_all()
    assert user.account.failed_attempts == 0


def test_same_name_same_pin_triggers_challenge_flow(client, db_session) -> None:
    """同名同 PIN 双账号 → 409 challenge → select 签发 token；重放被拒。"""
    u1 = create_user_with_pin(db_session, "大壮", "123456")
    u2 = create_user_with_pin(db_session, "大壮", "123456")
    db_session.refresh(u1)
    db_session.refresh(u2)

    first = login(client, "大壮", "123456")
    assert first.status_code == 409
    body = first.json()
    candidate_ids = {c["id"] for c in body["candidates"]}
    assert candidate_ids == {u1.id, u2.id}
    target = str(u2.id)

    selected = client.post(
        "/api/auth/login/select",
        json={"challenge_id": body["challenge_id"], "user_id": int(target)},
    )
    assert selected.status_code == 200
    assert selected.json()["user"]["id"] == u2.id

    # 重放同一 challenge_id 二次 select：拒绝 + 审计留痕
    replay = client.post(
        "/api/auth/login/select",
        json={"challenge_id": body["challenge_id"], "user_id": int(target)},
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "CHALLENGE_INVALID"
    audits = db_session.query(AuditLog).filter(AuditLog.action == "challenge_rejected").all()
    assert len(audits) == 1


def test_select_with_foreign_user_id_rejected(client, db_session) -> None:
    create_user_with_pin(db_session, "大壮", "123456")
    create_user_with_pin(db_session, "大壮", "123456")
    intruder = create_user_with_pin(db_session, "路人", "654321")

    first = login(client, "大壮", "123456")
    assert first.status_code == 409
    hijack = client.post(
        "/api/auth/login/select",
        json={"challenge_id": first.json()["challenge_id"], "user_id": intruder.id},
    )
    assert hijack.status_code == 401


def test_refresh_rotation_and_reuse_detection_e2e(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()

    rotated = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # 新 access 可用
    me = client.get("/api/me", headers=auth_header(new_tokens))
    assert me.status_code == 200

    # 提交已轮换的旧 refresh → 重用攻击 → 全会话撤销 + 审计告警
    reuse = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401
    audits = db_session.query(AuditLog).filter(AuditLog.action == "refresh_reuse_detected").all()
    assert len(audits) == 1

    # 全会话撤销：最新 refresh 也已失效
    newest = client.post("/api/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert newest.status_code == 401


def test_logout_revokes_refresh_only_path(client, db_session) -> None:
    create_user_with_pin(db_session, "张三", "123456")
    tokens = login(client, "张三", "123456").json()

    out = client.post(
        "/api/auth/logout",
        headers=auth_header(tokens),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert out.status_code == 200
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_without_auth_401_envelope(client) -> None:
    response = client.post("/api/auth/logout", json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_UNAUTHORIZED"
