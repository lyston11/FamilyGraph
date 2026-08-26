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
