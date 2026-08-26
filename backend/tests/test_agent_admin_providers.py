"""Provider 治理端点测试（RT-5）：operator 权限、secret 不回显、策略矩阵无静默 fallback。"""

from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_session,
    create_space_member,
    create_user_with_pin,
    login,
)
from sqlalchemy import select

from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.services.agent_provider import resolve_for_space


def _operator_headers(client, db, name: str = "provider-op"):
    user = create_user_with_pin(db, name, "123456", is_admin=True)
    token_pair = login(client, name, "123456").json()
    return user, auth_header(token_pair)


def _register_cloud(client, headers, *, name="cloud-1", models=None, secret="sk-live-abc123"):
    return client.post(
        "/api/admin/agent/providers",
        json={
            "name": name,
            "kind": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "secret": secret,
            "allowed_models": models or ["model-x"],
            "enabled": True,
        },
        headers=headers,
    )


# ---- operator CRUD 与 secret 边界 ----


def test_provider_crud_requires_platform_operator(client, db_session):
    _owner, _space = create_agent_fixture(db_session, name="plainuser")
    normal = auth_header(login(client, "plainuser", "123456").json())
    cases = [
        (
            "post",
            "/api/admin/agent/providers",
            {
                "name": "x",
                "kind": "local",
                "allowed_models": ["m"],
            },
        ),
        ("get", "/api/admin/agent/providers", None),
        ("patch", "/api/admin/agent/providers/1", {"enabled": False}),
        (
            "put",
            "/api/admin/agent/spaces/1/provider-settings",
            {
                "provider_id": 1,
                "model": "m",
            },
        ),
    ]
    for method, path, payload in cases:
        kwargs: dict = {"headers": normal}
        if payload is not None:
            kwargs["json"] = payload
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403


def test_register_and_list_never_disclose_secret(client, db_session):
    _op, headers = _operator_headers(client, db_session)
    created = _register_cloud(client, headers)
    assert created.status_code == 201
    body = created.json()
    assert body["has_secret"] is True
    assert "secret" not in body
    # 明文与密文都不出现在任何响应里（含列表）
    listing = client.get("/api/admin/agent/providers", headers=headers)
    assert "sk-live-abc123" not in listing.text
    row = db_session.scalar(select(AgentProvider).where(AgentProvider.name == "cloud-1"))
    assert row is not None
    assert row.secret_ciphertext is not None
    assert "sk-live-abc123" not in row.secret_ciphertext

    patched = client.patch(
        f"/api/admin/agent/providers/{row.id}",
        json={"enabled": False},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    missing = client.patch(
        "/api/admin/agent/providers/9999", json={"enabled": True}, headers=headers
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AGENT_PROVIDER_NOT_FOUND"


def test_openai_compatible_requires_base_url(client, db_session):
    _op, headers = _operator_headers(client, db_session, name="op-baseurl")
    response = client.post(
        "/api/admin/agent/providers",
        json={"name": "no-url", "kind": "openai_compatible", "allowed_models": ["m"]},
        headers=headers,
    )
    assert response.status_code == 422


def test_admin_agent_endpoints_disabled_when_flag_off(client, db_session, monkeypatch):
    from app import config as app_config

    _op, headers = _operator_headers(client, db_session, name="op-flagoff")
    monkeypatch.setattr(app_config, "AGENT_RUNTIME_ENABLED", False)
    listed = client.get("/api/admin/agent/providers", headers=headers)
    assert listed.status_code == 503
    assert listed.json()["error"]["code"] == "AGENT_RUNTIME_DISABLED"


# ---- 空间设置 ----


def _space_with_member(db, name: str):
    user, space = create_agent_fixture(db, name=name)
    create_space_member(db, space.id, user.id)
    return user, space


def _member_headers(client, db, user):
    return auth_header(login(client, user.name, "123456").json())


def test_space_settings_validation(client, db_session):
    _op, op_headers = _operator_headers(client, db_session, name="op-settings")
    provider_id = _register_cloud(client, op_headers).json()["id"]
    _user, space = _space_with_member(db_session, "settingspace")

    unknown_space = client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": 99999, "model": "model-x"},
        headers=op_headers,
    )
    assert unknown_space.status_code == 404

    bad_model = client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": provider_id, "model": "not-allowed"},
        headers=op_headers,
    )
    assert bad_model.status_code == 422

    ok = client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": provider_id, "model": "model-x", "cloud_allowed": True},
        headers=op_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["model"] == "model-x"

    cleared = client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": None},
        headers=op_headers,
    )
    assert cleared.status_code == 200
    assert (
        db_session.scalar(
            select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space.id)
        )
        is None
    )


# ---- 策略矩阵：经浏览器消息创建验证可解释拒绝，无静默 fallback ----


def _message_response(client, headers, session_id: int):
    return client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"content": "hi"},
        headers={**headers, "Idempotency-Key": "policy-key"},
    )


def test_policy_no_setting_rejects_unresolved(client, db_session):
    member_user, space = _space_with_member(db_session, "pol-nosetting")
    create_agent_session(db_session, account_id=member_user.account.id, space_id=space.id)
    resolution = resolve_for_space(db_session, space.id)
    assert resolution.policy_result == "denied"
    assert resolution.reason == "no_space_setting"


def test_policy_matrix_via_message_creation(client, db_session):
    _op, op_headers = _operator_headers(client, db_session, name="op-matrix")
    cloud_id = _register_cloud(client, op_headers).json()["id"]
    local_resp = client.post(
        "/api/admin/agent/providers",
        json={
            "name": "local-1",
            "kind": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "allowed_models": ["llama-x"],
        },
        headers=op_headers,
    )
    local_id = local_resp.json()["id"]

    def _case(name: str) -> tuple[object, dict, int]:
        user, space = _space_with_member(db_session, name)
        headers = auth_header(login(client, user.name, "123456").json())
        created = client.post("/api/agent/sessions", json={"space_id": space.id}, headers=headers)
        return space, headers, created.json()["id"]

    # 1) 云选择 + 未开放云 → PROVIDER_UNRESOLVED（可解释：denied_cloud_forbidden）
    space, headers, sid = _case("pol-cloudoff")
    client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": cloud_id, "model": "model-x", "cloud_allowed": False},
        headers=op_headers,
    )
    denied = _message_response(client, headers, sid)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "PROVIDER_UNRESOLVED"
    assert denied.json()["error"]["detail"]["policy_result"] == "denied_cloud_forbidden"

    # 2) 云选择 + 开放云 → 成功入队
    client.put(
        f"/api/admin/agent/spaces/{space.id}/provider-settings",
        json={"provider_id": cloud_id, "model": "model-x", "cloud_allowed": True},
        headers=op_headers,
    )
    allowed = _message_response(client, headers, sid)
    assert allowed.status_code == 200
    assert allowed.json()["replayed"] is False

    # 3) 敏感强制本地但选中云 → PROVIDER_LOCAL_REQUIRED_UNAVAILABLE，绝不换云
    space2, headers2, sid2 = _case("pol-localreq")
    client.put(
        f"/api/admin/agent/spaces/{space2.id}/provider-settings",
        json={
            "provider_id": cloud_id,
            "model": "model-x",
            "cloud_allowed": True,
            "local_required": True,
        },
        headers=op_headers,
    )
    rejected = _message_response(client, headers2, sid2)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "PROVIDER_LOCAL_REQUIRED_UNAVAILABLE"
    assert rejected.json()["error"]["detail"]["reason"] == "selected_provider_not_local"

    # 4) 强制本地且选择本地 → 成功；解析结果为本地 Provider
    space3, headers3, sid3 = _case("pol-localok")
    client.put(
        f"/api/admin/agent/spaces/{space3.id}/provider-settings",
        json={"provider_id": local_id, "model": "llama-x", "local_required": True},
        headers=op_headers,
    )
    ok_local = _message_response(client, headers3, sid3)
    assert ok_local.status_code == 200
    resolution = resolve_for_space(db_session, space3.id)
    assert resolution.policy_result == "allowed"
    assert resolution.kind == "local"

    # 5) Provider 停用后 → PROVIDER_UNRESOLVED（provider_disabled），不静默换云
    client.patch(
        f"/api/admin/agent/providers/{local_id}", json={"enabled": False}, headers=op_headers
    )
    after_disable = client.post(
        f"/api/agent/sessions/{sid3}/messages",
        json={"content": "again"},
        headers={**headers3, "Idempotency-Key": "after-disable"},
    )
    assert after_disable.status_code == 409
    assert after_disable.json()["error"]["code"] == "PROVIDER_UNRESOLVED"
    assert after_disable.json()["error"]["detail"]["reason"] == "provider_disabled"
