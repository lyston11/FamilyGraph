"""家庭域空间模型设置端点测试（owner 侧 /api/spaces/{space_id}/model-settings）。

覆盖（design §7）：
- 权限：未登录 401、非空间管理员 403（platform_operator 亦无空间写权限）；
- GET：目录只含 enabled Provider、无密钥形态字段、平台默认为解析同口径有效默认；
- PUT：assistant/steward 两维度互不干扰、enabled=true 成对必填且 model ∈
  allowlist、enabled=false 显式停用（provider/model 可空）；
- DELETE：删行 = 恢复平台默认继承（幂等 204）；
- 解析联动：显式停用行让解析走 setting_disabled（优先于平台默认）。
"""

from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_session,
    create_user_with_pin,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.agent_provider import AgentPlatformDefault, AgentProvider, AgentSpaceProviderSetting
from app.services.agent_provider import (
    POLICY_ALLOWED,
    POLICY_DENIED,
    POLICY_DENIED_CLOUD_FORBIDDEN,
    resolve_for_space,
)
from app.utils import timeutil

BASE = "/api/spaces/{space_id}/model-settings"


def _provider(db, *, name="p1", kind="openai_compatible", enabled=True, models=None):
    row = AgentProvider(
        name=name,
        kind=kind,
        base_url="https://api.example.com/v1" if kind == "openai_compatible" else None,
        secret_ciphertext=None,
        allowed_models_json=models or ["model-x"],
        enabled=enabled,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _put(client, headers, space_id, payload):
    return client.put(BASE.format(space_id=space_id), json=payload, headers=headers)


# ---- 权限 ----


def test_model_settings_require_authenticated_user(client: TestClient) -> None:
    assert client.get(BASE.format(space_id=1)).status_code == 401
    assert (
        client.put(BASE.format(space_id=1), json={"agent_kind": "assistant"}).status_code == 401
    )
    assert client.delete(BASE.format(space_id=1) + "/assistant").status_code == 401


def test_model_settings_require_space_manager(client: TestClient, db_session) -> None:
    _owner, space = create_agent_fixture(db_session, name="ms-owner")
    outsider = create_user_with_pin(db_session, "ms-outsider", "123456")
    db_session.commit()
    headers = auth_header(login(client, outsider.name, "123456").json())
    got = client.get(BASE.format(space_id=space.id), headers=headers)
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"
    put = _put(client, headers, space.id, {"agent_kind": "assistant", "enabled": False})
    assert put.status_code == 403
    removed = client.delete(
        BASE.format(space_id=space.id) + "/assistant", headers=headers
    )
    assert removed.status_code == 403


def test_model_settings_unknown_space_404(client: TestClient, db_session) -> None:
    _owner, _space = create_agent_fixture(db_session, name="ms-unknown")
    headers = auth_header(login(client, _owner.name, "123456").json())
    assert client.get(BASE.format(space_id=99999), headers=headers).status_code == 404


# ---- GET：目录与平台默认 ----


def test_get_catalog_only_enabled_providers(client: TestClient, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="ms-catalog")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    _provider(db_session, name="enabled-p", models=["model-x"])
    _provider(db_session, name="disabled-p", enabled=False, models=["model-y"])
    db_session.commit()

    got = client.get(BASE.format(space_id=space.id), headers=headers)
    assert got.status_code == 200
    body = got.json()
    names = [entry["name"] for entry in body["catalog"]]
    assert names == ["enabled-p"]
    entry = body["catalog"][0]
    assert set(entry) == {"provider_id", "name", "kind", "api", "models"}
    # 任何响应形态不含密钥字段
    assert "secret" not in got.text


# ---- PUT：两维度 upsert 互不干扰 ----


def test_put_two_kinds_independent_and_allowlist_validation(
    client: TestClient, db_session
) -> None:
    owner, space = create_agent_fixture(db_session, name="ms-kinds")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    cloud = _provider(db_session, name="cloud-p", models=["model-x"])
    local = _provider(db_session, name="local-p", kind="local", models=["llama-x"])
    db_session.commit()

    # assistant 选云、steward 选本地：互不干扰
    put_assistant = _put(
        client,
        headers,
        space.id,
        {
            "agent_kind": "assistant",
            "provider_id": cloud.id,
            "model": "model-x",
            "cloud_allowed": True,
        },
    )
    assert put_assistant.status_code == 200, put_assistant.text
    put_steward = _put(
        client,
        headers,
        space.id,
        {"agent_kind": "steward", "provider_id": local.id, "model": "llama-x"},
    )
    assert put_steward.status_code == 200

    rows = {
        row.agent_kind: row
        for row in db_session.scalars(
            select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space.id)
        ).all()
    }
    assert set(rows) == {"assistant", "steward"}
    assert rows["assistant"].provider_id == cloud.id
    assert rows["assistant"].cloud_allowed is True
    assert rows["steward"].provider_id == local.id
    assert rows["steward"].cloud_allowed is False

    # model 不在该 Provider allowlist 内 → 422
    bad_model = _put(
        client,
        headers,
        space.id,
        {"agent_kind": "assistant", "provider_id": cloud.id, "model": "not-allowed"},
    )
    assert bad_model.status_code == 422
    assert "allowed_models" in bad_model.json()["error"]["detail"]

    # enabled=true 但缺 provider/model → 422（继承请用 DELETE）
    incomplete = _put(client, headers, space.id, {"agent_kind": "assistant"})
    assert incomplete.status_code == 422

    # 停用行带不成对 provider/model → 422
    unpaired = _put(
        client,
        headers,
        space.id,
        {"agent_kind": "assistant", "enabled": False, "provider_id": cloud.id},
    )
    assert unpaired.status_code == 422

    # 未知 agent_kind → 422（schema Literal fail-closed）
    unknown_kind = _put(client, headers, space.id, {"agent_kind": "boss"})
    assert unknown_kind.status_code == 422


def test_put_disabled_is_explicit_off_overriding_platform_default(
    client: TestClient, db_session
) -> None:
    owner, space = create_agent_fixture(db_session, name="ms-disabled")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    cloud = _provider(db_session, name="cloud-p", models=["model-x"])
    db_session.commit()

    # 平台默认存在
    row = AgentPlatformDefault(
        id=1,
        assistant_provider_id=cloud.id,
        assistant_model="model-x",
        updated_at=timeutil.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    # 显式停用（provider/model 可空）
    put = _put(client, headers, space.id, {"agent_kind": "assistant", "enabled": False})
    assert put.status_code == 200
    assert put.json()["enabled"] is False
    assert put.json()["provider_id"] is None

    # 显式停用优先于平台默认：解析 setting_disabled，不回退默认
    resolution = resolve_for_space(db_session, space.id)
    assert resolution.policy_result == POLICY_DENIED
    assert resolution.reason == "setting_disabled"
    assert resolution.platform_default_configured is False


# ---- DELETE：恢复平台默认继承 ----


def test_delete_restores_inheritance(client: TestClient, db_session) -> None:
    owner, space = create_agent_fixture(db_session, name="ms-delete")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    cloud = _provider(db_session, name="cloud-p", models=["model-x"])
    db_session.commit()

    # 无行 DELETE 幂等 204
    first = client.delete(BASE.format(space_id=space.id) + "/assistant", headers=headers)
    assert first.status_code == 204

    _put(
        client,
        headers,
        space.id,
        {"agent_kind": "assistant", "provider_id": cloud.id, "model": "model-x"},
    )
    assert (
        db_session.scalar(
            select(AgentSpaceProviderSetting).where(
                AgentSpaceProviderSetting.space_id == space.id,
                AgentSpaceProviderSetting.agent_kind == "assistant",
            )
        )
        is not None
    )
    second = client.delete(BASE.format(space_id=space.id) + "/assistant", headers=headers)
    assert second.status_code == 204
    assert (
        db_session.scalar(
            select(AgentSpaceProviderSetting).where(
                AgentSpaceProviderSetting.space_id == space.id,
                AgentSpaceProviderSetting.agent_kind == "assistant",
            )
        )
        is None
    )
    # steward 行不受影响（DELETE 只删指定维度）
    _put(
        client,
        headers,
        space.id,
        {"agent_kind": "steward", "provider_id": cloud.id, "model": "model-x"},
    )
    client.delete(BASE.format(space_id=space.id) + "/assistant", headers=headers)
    steward_row = db_session.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space.id,
            AgentSpaceProviderSetting.agent_kind == "steward",
        )
    )
    assert steward_row is not None

    # 未知 agent_kind 路径参数 → 422
    unknown = client.delete(BASE.format(space_id=space.id) + "/boss", headers=headers)
    assert unknown.status_code == 422


# ---- 平台默认回退联动（解析链 + owner 同意云走通）----


def test_platform_default_inheritance_and_owner_cloud_consent(
    client: TestClient, db_session
) -> None:
    """无空间行 + 云默认 → denied_cloud_forbidden；owner 经 PUT 同意云后 allowed。"""
    owner, space = create_agent_fixture(db_session, name="ms-inherit")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    cloud = _provider(db_session, name="cloud-p", models=["model-x"])
    db_session.commit()
    db_session.add(
        AgentPlatformDefault(
            id=1,
            assistant_provider_id=cloud.id,
            assistant_model="model-x",
            updated_at=timeutil.utcnow(),
        )
    )
    db_session.commit()

    inherited = resolve_for_space(db_session, space.id)
    assert inherited.policy_result == POLICY_DENIED_CLOUD_FORBIDDEN
    assert inherited.reason == "cloud_not_allowed"
    assert inherited.platform_default_configured is True
    assert inherited.provider_id == cloud.id

    # owner 一键启用平台默认（PUT 复制默认行 + cloud_allowed=true）后 allowed
    put = _put(
        client,
        headers,
        space.id,
        {
            "agent_kind": "assistant",
            "provider_id": cloud.id,
            "model": "model-x",
            "cloud_allowed": True,
        },
    )
    assert put.status_code == 200
    allowed = resolve_for_space(db_session, space.id)
    assert allowed.policy_result == POLICY_ALLOWED
    assert allowed.platform_default_configured is False  # 空间行存在：显式选择


# ---- 消息创建链路：空间未选/未同意云错误两态可区分（AC-2/R5 detail 合同）----


def test_message_creation_error_detail_distinguishes_states(
    client: TestClient, db_session
) -> None:
    owner, space = create_agent_fixture(db_session, name="ms-errdetail")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    agent_session = create_agent_session(
        db_session, account_id=owner.account.id, space_id=space.id
    )

    def _send() -> dict:
        response = client.post(
            f"/api/agent/sessions/{agent_session.id}/messages",
            json={"content": "hi"},
            headers={**headers, "Idempotency-Key": "err-detail-key"},
        )
        assert response.status_code == 409
        return response.json()["error"]

    # 态一：无空间行且无平台默认 → platform_default_configured=false
    error = _send()
    assert error["code"] == "PROVIDER_UNRESOLVED"
    assert error["detail"]["platform_default_configured"] is False
    assert error["detail"]["reason"] == "no_space_setting"

    # 态二：平台默认存在但未同意云 → platform_default_configured=true
    cloud = _provider(db_session, name="cloud-p", models=["model-x"])
    db_session.commit()
    db_session.add(
        AgentPlatformDefault(
            id=1,
            assistant_provider_id=cloud.id,
            assistant_model="model-x",
            updated_at=timeutil.utcnow(),
        )
    )
    db_session.commit()
    error = _send()
    assert error["code"] == "PROVIDER_UNRESOLVED"
    assert error["detail"]["platform_default_configured"] is True
    assert error["detail"]["reason"] == "cloud_not_allowed"
