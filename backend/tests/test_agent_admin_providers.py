"""Agent Provider 治理端点测试（admin 域 /admin-api/v1/agent/*，09-06 治理迁移）。

覆盖（design §7）：
- 鉴权门禁：无 token 401、password_must_change 403；
- Provider 注册/更新：secret 只写不读（明文/密文双不回显）、base_url 必填、
  strict profile 门禁、flag 关闭 503；
- 平台默认：PUT/GET 全量覆盖、成对与 allowlist 校验；
- 空间设置只读排查视图；
- 审计：admin_access_audits 落库（actor=system_admin）、secret 永不入审计。

旧家庭挂载 /api/admin/agent/* 已删除：其 404 由 test_system_admin_boundary.py 断言。
"""

from conftest import admin_session_headers, create_agent_fixture, create_system_admin
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.admin_access import AdminAccessAudit
from app.models.agent_provider import (
    AgentPlatformDefault,
    AgentProvider,
    AgentSpaceProviderSetting,
)
from app.services.agent_provider import resolve_for_space

V1_AGENT = "/admin-api/v1/agent"
SECRET = "sk-live-abc123"


def _admin_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _register_cloud(
    admin_client: TestClient,
    headers,
    *,
    name="cloud-1",
    models=None,
    secret=SECRET,
) -> dict:
    response = admin_client.post(
        f"{V1_AGENT}/providers",
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
    assert response.status_code == 201, response.text
    return response.json()


# ---- 鉴权门禁 ----


def test_agent_admin_endpoints_require_admin_token(admin_client: TestClient) -> None:
    """无 token / 家庭 token 一律 401 ADMIN_UNAUTHORIZED（admin 域独立签发域）。"""
    for method, path, payload in (
        ("post", f"{V1_AGENT}/providers", {"name": "x"}),
        ("get", f"{V1_AGENT}/providers", None),
        ("patch", f"{V1_AGENT}/providers/1", {"enabled": False}),
        ("get", f"{V1_AGENT}/platform-defaults", None),
        ("put", f"{V1_AGENT}/platform-defaults", {"assistant": None}),
        ("get", f"{V1_AGENT}/spaces/1/provider-settings", None),
    ):
        kwargs: dict = {}
        if payload is not None:
            kwargs["json"] = payload
        response = getattr(admin_client, method)(path, **kwargs)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "ADMIN_UNAUTHORIZED", path


def test_agent_admin_endpoints_blocked_until_password_change(
    admin_client: TestClient, db_session
) -> None:
    """password_must_change=true：登录可过，但治理端点一律 403（require_admin_ready）。"""
    create_system_admin(
        db_session,
        username="mustchange",
        password="FixtureAdmin-2026x",
        password_must_change=True,
    )
    headers = admin_session_headers(
        admin_client, username="mustchange", password="FixtureAdmin-2026x"
    )
    response = admin_client.get(f"{V1_AGENT}/providers", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_PASSWORD_CHANGE_REQUIRED"


# ---- Provider 注册表与 secret 边界 ----


def test_register_and_list_never_disclose_secret(admin_client: TestClient, db_session) -> None:
    headers = _admin_headers(admin_client, db_session)
    created = _register_cloud(admin_client, headers)
    assert created["has_secret"] is True
    assert "secret" not in created
    # 明文与密文都不出现在任何响应里（含列表）
    listing = admin_client.get(f"{V1_AGENT}/providers", headers=headers)
    assert listing.status_code == 200
    assert SECRET not in listing.text
    row = db_session.scalar(select(AgentProvider).where(AgentProvider.name == "cloud-1"))
    assert row is not None
    assert row.secret_ciphertext is not None
    assert SECRET not in row.secret_ciphertext

    # PATCH 语义：仅提交变更字段；secret 非空=轮换、空串=清除
    patched = admin_client.patch(
        f"{V1_AGENT}/providers/{row.id}",
        json={"enabled": False},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["has_secret"] is True

    rotated = admin_client.patch(
        f"{V1_AGENT}/providers/{row.id}",
        json={"secret": "sk-rotated-xyz"},
        headers=headers,
    )
    assert rotated.status_code == 200
    assert rotated.json()["has_secret"] is True
    assert "sk-rotated-xyz" not in rotated.text

    cleared = admin_client.patch(
        f"{V1_AGENT}/providers/{row.id}",
        json={"secret": ""},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_secret"] is False

    missing = admin_client.patch(
        f"{V1_AGENT}/providers/9999", json={"enabled": True}, headers=headers
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AGENT_PROVIDER_NOT_FOUND"


def test_openai_compatible_requires_base_url(admin_client: TestClient, db_session) -> None:
    headers = _admin_headers(admin_client, db_session)
    response = admin_client.post(
        f"{V1_AGENT}/providers",
        json={"name": "no-url", "kind": "openai_compatible", "allowed_models": ["m"]},
        headers=headers,
    )
    assert response.status_code == 422


def test_strict_mode_accepts_only_canonical_liu_dada_profile(
    admin_client: TestClient, db_session, monkeypatch
) -> None:
    from app import config

    monkeypatch.setattr(config, "AGENT_PROVIDER_STANDARD_PROFILE_ONLY", True)
    headers = _admin_headers(admin_client, db_session)
    rejected = admin_client.post(
        f"{V1_AGENT}/providers",
        json={
            "name": "other-cloud",
            "kind": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "allowed_models": ["model-x"],
            "secret": SECRET,
        },
        headers=headers,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["detail"]["reason"] == "provider_name_not_allowed"
    accepted = admin_client.post(
        f"{V1_AGENT}/providers",
        json={
            "name": "liu-dada",
            "kind": "openai_compatible",
            "api": "openai-responses",
            "base_url": "https://api.liu-dada.com/v1",
            "allowed_models": ["gpt-5.6-sol"],
            "secret": SECRET,
        },
        headers=headers,
    )
    assert accepted.status_code == 201


def test_admin_agent_endpoints_disabled_when_flag_off(
    admin_client: TestClient, db_session, monkeypatch
) -> None:
    from app import config as app_config

    headers = _admin_headers(admin_client, db_session)
    monkeypatch.setattr(app_config, "AGENT_RUNTIME_ENABLED", False)
    listed = admin_client.get(f"{V1_AGENT}/providers", headers=headers)
    assert listed.status_code == 503
    assert listed.json()["error"]["code"] == "AGENT_RUNTIME_DISABLED"


# ---- 平台默认 ----


def test_platform_defaults_put_get_and_validation(admin_client: TestClient, db_session) -> None:
    headers = _admin_headers(admin_client, db_session)
    cloud = _register_cloud(admin_client, headers, models=["model-x", "model-y"])

    # 初始为空（GET 不懒建行，未设置即 None）
    initial = admin_client.get(f"{V1_AGENT}/platform-defaults", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["assistant"] is None
    assert initial.json()["steward"] is None

    # 设置 assistant 维度默认
    put = admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"assistant": {"provider_id": cloud["id"], "model": "model-x"}},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["assistant"] == {"provider_id": cloud["id"], "model": "model-x"}
    assert put.json()["steward"] is None

    # GET 回读
    fetched = admin_client.get(f"{V1_AGENT}/platform-defaults", headers=headers)
    assert fetched.json()["assistant"]["provider_id"] == cloud["id"]
    row = db_session.get(AgentPlatformDefault, 1)
    assert row is not None and row.assistant_provider_id == cloud["id"]

    # model 不在该 Provider allowed_models 内 → 422
    bad_model = admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"steward": {"provider_id": cloud["id"], "model": "not-allowed"}},
        headers=headers,
    )
    assert bad_model.status_code == 422
    assert "allowed_models" in bad_model.json()["error"]["detail"]

    # Provider 不存在 → 422
    unknown = admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"assistant": {"provider_id": 99999, "model": "model-x"}},
        headers=headers,
    )
    assert unknown.status_code == 422

    # 全量覆盖语义：只传 steward → assistant 被清除
    overwritten = admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"steward": {"provider_id": cloud["id"], "model": "model-y"}},
        headers=headers,
    )
    assert overwritten.status_code == 200
    assert overwritten.json()["assistant"] is None
    assert overwritten.json()["steward"]["model"] == "model-y"


# ---- 空间设置只读排查视图 ----


def test_space_provider_settings_readonly_view(admin_client: TestClient, db_session) -> None:
    headers = _admin_headers(admin_client, db_session)
    cloud = _register_cloud(admin_client, headers, models=["model-x"])
    _owner, space = create_agent_fixture(db_session, name="readonly-space")
    db_session.commit()

    # 未知空间 404
    missing = admin_client.get(f"{V1_AGENT}/spaces/99999/provider-settings", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SPACE_NOT_FOUND"

    # 无显式行：两维度均为 None + 平台默认状态
    empty = admin_client.get(f"{V1_AGENT}/spaces/{space.id}/provider-settings", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["settings"]["assistant"] is None
    assert empty.json()["settings"]["steward"] is None

    # 写入显式行后按维度呈现（只读视图，无 PUT 写端点）
    assistant_row = AgentSpaceProviderSetting(
        space_id=space.id,
        agent_kind="assistant",
        provider_id=cloud["id"],
        model="model-x",
        cloud_allowed=True,
        enabled=True,
    )
    db_session.add(assistant_row)
    db_session.commit()
    view = admin_client.get(f"{V1_AGENT}/spaces/{space.id}/provider-settings", headers=headers)
    assert view.status_code == 200
    body = view.json()
    assert body["settings"]["assistant"]["model"] == "model-x"
    assert body["settings"]["assistant"]["cloud_allowed"] is True
    assert body["settings"]["steward"] is None
    # 只读排查视图响应不含密钥形态字段
    assert "secret" not in view.text


def test_space_provider_settings_reflects_platform_defaults(
    admin_client: TestClient, db_session
) -> None:
    headers = _admin_headers(admin_client, db_session)
    cloud = _register_cloud(admin_client, headers, models=["model-x"])
    _owner, space = create_agent_fixture(db_session, name="pd-view-space")
    admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"assistant": {"provider_id": cloud["id"], "model": "model-x"}},
        headers=headers,
    )
    view = admin_client.get(f"{V1_AGENT}/spaces/{space.id}/provider-settings", headers=headers)
    assert view.status_code == 200
    assert view.json()["platform_default"]["assistant"]["provider_id"] == cloud["id"]
    assert view.json()["platform_default"]["steward"] is None


# ---- 审计：admin_access_audits 落库、secret 永不入审计 ----


def test_provider_writes_audited_and_secret_never_in_audit(
    admin_client: TestClient, db_session
) -> None:
    headers = _admin_headers(admin_client, db_session)
    created = _register_cloud(admin_client, headers)
    admin_client.patch(
        f"{V1_AGENT}/providers/{created['id']}", json={"enabled": False}, headers=headers
    )
    admin_client.put(
        f"{V1_AGENT}/platform-defaults",
        json={"assistant": {"provider_id": created["id"], "model": "model-x"}},
        headers=headers,
    )
    admin_client.get(f"{V1_AGENT}/providers", headers=headers)

    actions = [
        row.action
        for row in db_session.scalars(select(AdminAccessAudit).order_by(AdminAccessAudit.id)).all()
    ]
    assert "agent.provider.create" in actions
    assert "agent.provider.update" in actions
    assert "agent.platform_defaults.update" in actions
    assert "agent.provider.list" in actions

    # secret 明文/密文绝不进入任何审计行（filters 只放白名单字段）
    audits = db_session.scalars(select(AdminAccessAudit)).all()
    for row in audits:
        assert SECRET not in str(row.filters_json)
        assert "secret" not in row.filters_json
    create_audit = next(row for row in audits if row.action == "agent.provider.create")
    # target_type 词表仅 user/space（既有 CHECK 约束）：provider 主体在 filters
    assert create_audit.target_type is None and create_audit.target_id is None
    assert set(create_audit.filters_json) == {"provider_id", "name", "kind", "enabled"}
    assert create_audit.filters_json["provider_id"] == created["id"]

    update_audit = next(row for row in audits if row.action == "agent.provider.update")
    assert update_audit.filters_json.get("secret_rotated") is not True  # 该次 PATCH 无 secret


def test_family_domain_resolution_unaffected_by_admin_registry(
    admin_client: TestClient, db_session
) -> None:
    """admin 域注册 Provider 本身不改变家庭解析（仍需空间显式行或平台默认）。"""
    headers = _admin_headers(admin_client, db_session)
    _register_cloud(admin_client, headers)
    _owner, space = create_agent_fixture(db_session, name="isolated-space")
    resolution = resolve_for_space(db_session, space.id)
    assert resolution.policy_result == "denied"
    assert resolution.reason == "no_space_setting"
    assert resolution.platform_default_configured is False
