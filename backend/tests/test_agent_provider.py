"""Provider 配置与 Policy 推导测试（RT-5）+ secretbox 密文 roundtrip。"""

import pytest
from sqlalchemy import select

from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.services.agent_provider import (
    POLICY_ALLOWED,
    POLICY_DENIED,
    POLICY_DENIED_CLOUD_FORBIDDEN,
    POLICY_DENIED_NO_LOCAL,
    resolve_for_run,
    resolve_for_space,
)
from app.utils import timeutil
from app.utils.secretbox import SecretBoxError, decrypt_secret, encrypt_secret
from conftest import create_agent_fixture


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


def _setting(
    db,
    space_id,
    provider_id,
    *,
    model="model-x",
    cloud=False,
    local=False,
    enabled=True,
    agent_kind="assistant",
):
    row = AgentSpaceProviderSetting(
        space_id=space_id,
        agent_kind=agent_kind,
        provider_id=provider_id,
        model=model,
        cloud_allowed=cloud,
        local_required=local,
        enabled=enabled,
    )
    db.add(row)
    db.commit()
    return row


def test_secretbox_roundtrip_and_tamper_detection():
    ciphertext = encrypt_secret("sk-live-abc123")
    assert ciphertext != "sk-live-abc123"
    assert "sk-live" not in ciphertext
    assert decrypt_secret(ciphertext) == "sk-live-abc123"
    # 随机 nonce：同一明文两次密文不同
    assert encrypt_secret("sk-live-abc123") != ciphertext
    # 篡改任一字符均 fail-closed
    tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")
    with pytest.raises(SecretBoxError):
        decrypt_secret(tampered)
    with pytest.raises(SecretBoxError):
        decrypt_secret("garbage")


def test_policy_cloud_allowed_when_open(db_session):
    _, space = create_agent_fixture(db_session, name="pc1")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=True)
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_ALLOWED
    assert result.model == "model-x"
    assert result.kind == "openai_compatible"
    assert result.secret_ref == f"agent_providers/{provider.id}/secret"


def test_policy_denied_when_cloud_forbidden(db_session):
    _, space = create_agent_fixture(db_session, name="pc2")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=False)
    assert resolve_for_space(db_session, space.id).policy_result == POLICY_DENIED_CLOUD_FORBIDDEN


def test_policy_no_local_when_local_required_but_cloud_selected(db_session):
    _, space = create_agent_fixture(db_session, name="pc3")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=True, local=True)
    assert resolve_for_space(db_session, space.id).policy_result == POLICY_DENIED_NO_LOCAL


def test_policy_allowed_for_local_provider_even_if_required(db_session):
    _, space = create_agent_fixture(db_session, name="pc4")
    provider = _provider(db_session, kind="local")
    _setting(db_session, space.id, provider.id, local=True)
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_ALLOWED
    assert result.kind == "local"


def test_policy_denied_when_model_not_in_provider_allowlist(db_session):
    """空间管理员只能选 allowlist 内模型（RT-5 运营者白名单合同）。"""
    _, space = create_agent_fixture(db_session, name="pc5")
    provider = _provider(db_session, models=["model-x"])
    _setting(db_session, space.id, provider.id, model="model-zz", cloud=True)
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED
    assert result.model is None  # 越权模型不下发


@pytest.mark.parametrize(
    ("setting_enabled", "provider_enabled"),
    [(False, True), (True, False)],
)
def test_policy_denied_when_disabled(db_session, setting_enabled, provider_enabled):
    _, space = create_agent_fixture(
        db_session, name=f"pc6{int(setting_enabled)}{int(provider_enabled)}"
    )
    provider = _provider(db_session, enabled=provider_enabled)
    _setting(db_session, space.id, provider.id, cloud=True, enabled=setting_enabled)
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED


def test_policy_denied_when_no_setting(db_session):
    """未配置 Provider 的空间返回可解释 denied，不抛错。"""
    _, space = create_agent_fixture(db_session, name="pc7")
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED
    assert result.provider_id is None and result.model is None


def test_provider_secret_roundtrip_through_db(db_session):
    """密文落库→读回→解密 roundtrip；DB 中不存在明文。"""
    _, space = create_agent_fixture(db_session, name="pc8")
    provider = _provider(db_session)
    provider.secret_ciphertext = encrypt_secret("sk-db-roundtrip")
    db_session.commit()
    stored = db_session.scalar(select(AgentProvider).where(AgentProvider.id == provider.id))
    assert stored is not None and stored.secret_ciphertext is not None
    assert "sk-db-roundtrip" not in stored.secret_ciphertext
    assert decrypt_secret(stored.secret_ciphertext) == "sk-db-roundtrip"


def test_third_party_cloud_provider_allowed_but_still_gated_by_cloud_consent(db_session):
    """删掉供应商白名单后的语义：任意合规云 Provider 可用，但云同意仍必预。

    原以为「严格模式只接受 liu-dada」的断言已被有意废弃：数据能不能离开本机
    由空间级 cloud_allowed（所有者显式同意）决定，不由「是不是某个供应商」决定。
    这里同时验证两件事 —— 第三方可解析为 allowed，且缺云同意时仍被拒。
    """
    third_party = _provider(db_session, name="buddy2api", models=["workbuddy/gpt-5.4"])
    third_party.api = "openai-completions"
    third_party.base_url = "http://100.71.18.78:8787/v1"
    db_session.flush()

    # 已同意云 → allowed
    _, consenting = create_agent_fixture(db_session, name="third-party-consented")
    _setting(
        db_session,
        consenting.id,
        third_party.id,
        model="workbuddy/gpt-5.4",
        cloud=True,
    )
    allowed = resolve_for_space(db_session, consenting.id)
    assert allowed.policy_result == POLICY_ALLOWED
    assert allowed.provider_name == "buddy2api"
    assert allowed.api == "openai-completions"

    # 未同意云 → 仍拒绝（证明删门禁没有旁路云同意）
    _, withholding = create_agent_fixture(db_session, name="third-party-withheld")
    _setting(
        db_session,
        withholding.id,
        third_party.id,
        model="workbuddy/gpt-5.4",
        cloud=False,
    )
    denied = resolve_for_space(db_session, withholding.id)
    assert denied.policy_result == POLICY_DENIED_CLOUD_FORBIDDEN
    assert denied.reason == "cloud_not_allowed"


def test_denied_runtime_snapshot_cannot_be_revived_by_later_setting_change(db_session):
    """A queued Run keeps its original denied policy even after reconfiguration."""
    from app.services import agent_queue
    from conftest import create_agent_message, create_agent_session

    owner, space = create_agent_fixture(db_session, name="snapshot-denied")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=False)
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    message = create_agent_message(db_session, session)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
        message=message,
    )
    assert run.runtime_snapshot_json is not None
    assert run.runtime_snapshot_json["policy_result"] == POLICY_DENIED_CLOUD_FORBIDDEN

    setting = db_session.scalar(
        select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space.id)
    )
    assert setting is not None
    setting.cloud_allowed = True
    db_session.commit()

    resolved = resolve_for_run(db_session, run, space.id)
    assert resolved.policy_result == POLICY_DENIED_CLOUD_FORBIDDEN
    assert resolved.reason == "runtime_snapshot_policy_denied"


def test_denied_no_provider_snapshot_cannot_be_revived(db_session):
    """A no-setting denial is also immutable after a provider is configured."""
    from app.services import agent_queue
    from conftest import create_agent_message, create_agent_session

    owner, space = create_agent_fixture(db_session, name="snapshot-no-provider")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    message = create_agent_message(db_session, session)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
        message=message,
    )
    assert run.runtime_snapshot_json == {
        "provider_id": None,
        "model": None,
        "kind": None,
        "api": None,
        "compat": {},
        "context_window": None,
        "max_tokens": None,
        "reasoning": None,
        "input_modalities": [],
        "thinking_levels": [],
        "policy_result": POLICY_DENIED,
        "provider_revision": None,
        "provider_name": None,
    }

    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=True)
    resolved = resolve_for_run(db_session, run, space.id)
    assert resolved.policy_result == POLICY_DENIED
    assert resolved.reason == "runtime_snapshot_policy_denied"


def test_allowed_runtime_snapshot_rejects_boolean_numeric_fields(db_session):
    """JSON booleans must not pass Python's int subclass checks."""
    from conftest import create_agent_session

    owner, space = create_agent_fixture(db_session, name="snapshot-types")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=True)
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    from app.models.agent import AgentRun

    run = AgentRun(
        session_id=session.id,
        kind="assistant",
        status="queued",
        policy_version="p1",
        tool_allowlist_json=[],
        runtime_snapshot_json={
            "provider_id": provider.id,
            "model": "model-x",
            "kind": "openai_compatible",
            "api": "openai-completions",
            "compat": {},
            "context_window": True,
            "max_tokens": 60_000,
            "reasoning": True,
            "input_modalities": ["text"],
            "thinking_levels": ["low"],
            "policy_result": POLICY_ALLOWED,
            "provider_revision": provider.updated_at.isoformat(),
        },
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(run)
    db_session.commit()
    resolved = resolve_for_run(db_session, run, space.id)
    assert resolved.policy_result == POLICY_DENIED
    assert resolved.reason == "runtime_snapshot_invalid"


def test_allowed_runtime_snapshot_requires_provider_revision(db_session):
    """A mutable provider row must not silently revive a revision-less snapshot."""
    from conftest import create_agent_session

    owner, space = create_agent_fixture(db_session, name="snapshot-no-revision")
    provider = _provider(db_session)
    _setting(db_session, space.id, provider.id, cloud=True)
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    from app.models.agent import AgentRun

    run = AgentRun(
        session_id=session.id,
        kind="assistant",
        status="queued",
        policy_version="p1",
        tool_allowlist_json=[],
        runtime_snapshot_json={
            "provider_id": provider.id,
            "model": "model-x",
            "kind": "openai_compatible",
            "api": "openai-completions",
            "compat": {},
            "context_window": 272_000,
            "max_tokens": 60_000,
            "reasoning": True,
            "input_modalities": ["text"],
            "thinking_levels": ["low"],
            "policy_result": POLICY_ALLOWED,
            # provider_revision intentionally omitted
        },
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(run)
    db_session.commit()
    resolved = resolve_for_run(db_session, run, space.id)
    assert resolved.policy_result == POLICY_DENIED
    assert resolved.reason == "runtime_snapshot_invalid"


# ---- 09-06 治理迁移：agent_kind 维度与平台默认回退（design §7）----


def _platform_default(db, *, assistant=None, steward=None):
    from app.models.agent_provider import AgentPlatformDefault

    row = AgentPlatformDefault(
        id=1,
        assistant_provider_id=assistant[0] if assistant else None,
        assistant_model=assistant[1] if assistant else None,
        steward_provider_id=steward[0] if steward else None,
        steward_model=steward[1] if steward else None,
        updated_at=timeutil.utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def test_agent_kind_isolation_between_kinds(db_session):
    """steward 行不影响 assistant 解析：维度各自独立（互不替补）。"""
    _, space = create_agent_fixture(db_session, name="kind-iso")
    cloud = _provider(db_session, name="cloud-iso")
    local = _provider(db_session, name="local-iso", kind="local", models=["llama-x"])
    _setting(db_session, space.id, cloud.id, cloud=True)
    _setting(db_session, space.id, local.id, model="llama-x", agent_kind="steward")

    assistant = resolve_for_space(db_session, space.id, agent_kind="assistant")
    assert assistant.policy_result == POLICY_ALLOWED
    assert assistant.provider_id == cloud.id

    steward = resolve_for_space(db_session, space.id, agent_kind="steward")
    assert steward.policy_result == POLICY_ALLOWED
    assert steward.provider_id == local.id

    # steward 有平台默认/行，assistant 无行且无默认 → no_space_setting（不跨维替补）
    _, bare = create_agent_fixture(db_session, name="kind-iso-bare")
    _platform_default(db_session, steward=(local.id, "llama-x"))
    bare_resolution = resolve_for_space(db_session, bare.id, agent_kind="assistant")
    assert bare_resolution.policy_result == POLICY_DENIED
    assert bare_resolution.reason == "no_space_setting"


def test_platform_default_local_provider_directly_allowed(db_session):
    """local 默认 Provider 不受 cloud_allowed 约束：直接 allowed。"""
    _, space = create_agent_fixture(db_session, name="pd-local")
    local = _provider(db_session, name="local-pd", kind="local", models=["llama-x"])
    _platform_default(db_session, assistant=(local.id, "llama-x"))

    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_ALLOWED
    assert result.provider_id == local.id
    assert result.model == "llama-x"
    assert result.platform_default_configured is True
    assert result.kind == "local"


def test_platform_default_cloud_provider_requires_owner_cloud_consent(db_session):
    """云默认 Provider：平台默认不替 owner 打开云同意 → denied_cloud_forbidden。"""
    _, space = create_agent_fixture(db_session, name="pd-cloud")
    cloud = _provider(db_session, name="cloud-pd")
    _platform_default(db_session, assistant=(cloud.id, "model-x"))

    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED_CLOUD_FORBIDDEN
    assert result.reason == "cloud_not_allowed"
    assert result.platform_default_configured is True


def test_no_platform_default_stays_no_space_setting(db_session):
    """无空间行且无有效默认 → no_space_setting（现状合同不变）。"""
    _, space = create_agent_fixture(db_session, name="pd-none")
    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED
    assert result.reason == "no_space_setting"
    assert result.platform_default_configured is False


@pytest.mark.parametrize(
    "scenario",
    ["provider_deleted", "provider_disabled", "orphan_model"],
)
def test_invalid_platform_default_is_ignored(db_session, scenario):
    """默认 Provider 缺失/停用/孤 model → 视为无默认，绝不静默改选。"""
    from app.models.agent_provider import AgentPlatformDefault as PlatformDefaultModel

    _, space = create_agent_fixture(db_session, name=f"pd-invalid-{scenario}")
    cloud = _provider(db_session, name="cloud-invalid")
    if scenario == "provider_deleted":
        # Provider 删除（FK ondelete=SET NULL）后：provider_id 清空、model 孤留
        _platform_default(db_session, assistant=(cloud.id, "model-x"))
        db_session.delete(cloud)
        db_session.commit()
        assert db_session.get(PlatformDefaultModel, 1).assistant_provider_id is None
    elif scenario == "provider_disabled":
        cloud.enabled = False
        db_session.commit()
        _platform_default(db_session, assistant=(cloud.id, "model-x"))
    else:
        # 孤 model：provider_id 为 NULL 但 model 残留（Provider 删除 SET NULL 结果）
        from app.models.agent_provider import AgentPlatformDefault

        row = AgentPlatformDefault(
            id=1,
            assistant_provider_id=None,
            assistant_model="model-x",
            updated_at=timeutil.utcnow(),
        )
        db_session.add(row)
        db_session.commit()

    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED
    assert result.reason == "no_space_setting"
    assert result.platform_default_configured is False


def test_explicit_disabled_setting_overrides_platform_default(db_session):
    """owner 显式停用优先于平台默认：setting_disabled，绝不回退默认。"""
    _, space = create_agent_fixture(db_session, name="pd-disabled")
    cloud = _provider(db_session, name="cloud-pd2")
    _platform_default(db_session, assistant=(cloud.id, "model-x"))
    _setting(db_session, space.id, cloud.id, enabled=False)

    result = resolve_for_space(db_session, space.id)
    assert result.policy_result == POLICY_DENIED
    assert result.reason == "setting_disabled"
    assert result.platform_default_configured is False


def test_unknown_agent_kind_fails_closed(db_session):
    """未知 agent_kind 一律 422 fail-closed（不静默按 assistant 处理）。"""
    from app.errors import VALIDATION_ERROR, extract_api_error

    _, space = create_agent_fixture(db_session, name="kind-unknown")
    with pytest.raises(Exception) as excinfo:
        resolve_for_space(db_session, space.id, agent_kind="boss")
    api_error = extract_api_error(excinfo.value.detail) or {}
    assert excinfo.value.status_code == 422
    assert api_error.get("code") == VALIDATION_ERROR


def test_platform_defaults_helpers_set_and_clear(db_session):
    """set_platform_defaults：allowlist 校验 + 成对写入/清除（PUT 全量语义）。"""
    from app.services.agent_provider import (
        get_platform_defaults,
        set_platform_defaults,
    )

    cloud = _provider(db_session, name="cloud-helper", models=["model-x", "model-y"])
    local = _provider(db_session, name="local-helper", kind="local", models=["llama-x"])

    row = set_platform_defaults(
        db_session,
        assistant=(cloud.id, "model-x"),
        steward=(local.id, "llama-x"),
        updated_by_admin_id=None,
    )
    db_session.commit()
    assert row.assistant_provider_id == cloud.id and row.assistant_model == "model-x"
    assert row.steward_provider_id == local.id

    # model 不在 allowlist → 422
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        set_platform_defaults(
            db_session,
            assistant=(cloud.id, "not-allowed"),
            steward=None,
            updated_by_admin_id=None,
        )
    assert excinfo.value.status_code == 422

    # 全量覆盖：只传 steward → assistant 清除
    row = set_platform_defaults(
        db_session, assistant=None, steward=(cloud.id, "model-y"), updated_by_admin_id=None
    )
    db_session.commit()
    current = get_platform_defaults(db_session)
    assert current.assistant_provider_id is None and current.assistant_model is None
    assert current.steward_provider_id == cloud.id and current.steward_model == "model-y"
