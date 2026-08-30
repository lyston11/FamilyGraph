"""Provider 配置与 Policy 推导测试（RT-5）+ secretbox 密文 roundtrip。"""

import pytest
from conftest import create_agent_fixture
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


def _setting(db, space_id, provider_id, *, model="model-x", cloud=False, local=False, enabled=True):
    row = AgentSpaceProviderSetting(
        space_id=space_id,
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


def test_standard_liu_dada_profile_is_enforced_in_strict_mode(db_session, monkeypatch):
    """生产门禁拒绝任意云 profile，但保留可选 local Provider。"""
    from app import config
    from app.services.agent_provider import STANDARD_MODEL, STANDARD_PROVIDER_NAME

    monkeypatch.setattr(config, "AGENT_PROVIDER_STANDARD_PROFILE_ONLY", True)
    _, space = create_agent_fixture(db_session, name="strict-profile")
    provider = _provider(db_session, name="other-cloud", models=["model-x"])
    _setting(db_session, space.id, provider.id, cloud=True)
    denied = resolve_for_space(db_session, space.id)
    assert denied.policy_result == POLICY_DENIED
    assert denied.reason == "provider_name_not_allowed"

    _, canonical_space = create_agent_fixture(db_session, name="strict-profile-ok")
    canonical = AgentProvider(
        name=STANDARD_PROVIDER_NAME,
        kind="openai_compatible",
        api="openai-responses",
        base_url="https://api.liu-dada.com/v1",
        compat_json={},
        context_window=272000,
        max_tokens=60000,
        reasoning=True,
        input_modalities_json=["text", "image"],
        thinking_levels_json=["low", "medium", "high", "xhigh", "max"],
        allowed_models_json=[STANDARD_MODEL],
        enabled=True,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(canonical)
    db_session.flush()
    _setting(db_session, canonical_space.id, canonical.id, model=STANDARD_MODEL, cloud=True)
    allowed = resolve_for_space(db_session, canonical_space.id)
    assert allowed.policy_result == POLICY_ALLOWED
    assert allowed.provider_name == STANDARD_PROVIDER_NAME


def test_denied_runtime_snapshot_cannot_be_revived_by_later_setting_change(db_session):
    """A queued Run keeps its original denied policy even after reconfiguration."""
    from conftest import create_agent_message, create_agent_session

    from app.services import agent_queue

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
    from conftest import create_agent_message, create_agent_session

    from app.services import agent_queue

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
