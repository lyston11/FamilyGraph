"""Regression coverage for platform-level Memory/RAG switch governance."""

from datetime import datetime

import pytest

from app import config
from app.models.platform_features import PlatformFeatureConfig
from app.services.platform_features import get_platform_feature_state
from conftest import admin_session_headers, create_system_admin, create_user_with_pin


@pytest.mark.parametrize(
    ("memory_enabled", "rag_enabled"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_platform_feature_state_keeps_memory_and_rag_independent(
    db_session, monkeypatch, memory_enabled, rag_enabled
):
    monkeypatch.setattr(config, "MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    db_session.add(
        PlatformFeatureConfig(
            id=1,
            memory_enabled=memory_enabled,
            rag_enabled=rag_enabled,
            updated_at=datetime(2026, 9, 13),
        )
    )
    db_session.commit()

    state = get_platform_feature_state(db_session)
    assert (state.memory_enabled, state.rag_enabled) == (memory_enabled, rag_enabled)
    assert state.memory_source == "platform"
    assert state.rag_source == "platform"


def test_family_status_is_safe_and_uses_environment_bootstrap(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_ENABLED", False)
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    user = create_user_with_pin(db_session, "feature-family", "123456")
    db_session.commit()
    login = client.post("/api/auth/login", json={"name": user.name, "pin": "123456"})
    assert login.status_code == 200

    response = client.get(
        "/api/platform-features",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json() == {"memory_enabled": False, "rag_enabled": True}
    assert "source" not in response.json()


def test_memory_and_rag_api_gates_follow_independent_platform_values(client, db_session):
    user = create_user_with_pin(db_session, "feature-gated-family", "123456")
    db_session.add(
        PlatformFeatureConfig(
            id=1,
            memory_enabled=True,
            rag_enabled=False,
            updated_at=datetime(2026, 9, 13),
        )
    )
    db_session.commit()
    login = client.post("/api/auth/login", json={"name": user.name, "pin": "123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    memory_response = client.get("/api/memory-candidates", headers=headers)
    rag_response = client.get(
        "/api/rag/search", headers=headers, params={"space_id": 1, "q": "test"}
    )
    assert memory_response.status_code == 200
    assert rag_response.status_code == 503
    assert rag_response.json()["error"]["code"] == "RAG_DISABLED"


def test_admin_can_update_both_switches_and_response_contains_metadata_only(
    admin_client, db_session, monkeypatch
):
    monkeypatch.setattr(config, "MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    create_system_admin(db_session)
    db_session.commit()
    headers = admin_session_headers(admin_client)

    initial = admin_client.get("/admin-api/v1/platform-features", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["memory_source"] == "environment"
    assert initial.json()["rag_source"] == "environment"
    assert set(initial.json()) == {
        "memory_enabled",
        "rag_enabled",
        "memory_source",
        "rag_source",
        "steward_assist",
        "steward_memory_scopes",
        "steward_memory_scopes_effective",
        "steward_memory_scopes_source",
        "updated_at",
    }

    updated = admin_client.put(
        "/admin-api/v1/platform-features",
        headers=headers,
        json={"memory_enabled": True, "rag_enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["memory_enabled"] is True
    assert updated.json()["rag_enabled"] is False
    assert updated.json()["memory_source"] == "platform"
    assert updated.json()["rag_source"] == "platform"
    assert updated.json()["updated_at"] is not None
    assert set(updated.json()) == {
        "memory_enabled",
        "rag_enabled",
        "memory_source",
        "rag_source",
        "steward_assist",
        "steward_memory_scopes",
        "steward_memory_scopes_effective",
        "steward_memory_scopes_source",
        "updated_at",
    }

    db_session.expire_all()
    persisted = db_session.get(PlatformFeatureConfig, 1)
    assert persisted is not None
    assert persisted.rag_enabled is False
    reread = admin_client.get("/admin-api/v1/platform-features", headers=headers)
    assert reread.json() == updated.json()
    row = db_session.get(PlatformFeatureConfig, 1)
    assert row is not None
    assert row.memory_enabled is True
    assert row.rag_enabled is False


def test_family_and_invalid_admin_requests_cannot_update_switches(client, admin_client, db_session):
    create_system_admin(db_session)
    family = create_user_with_pin(db_session, "feature-family-writer", "123456")
    db_session.commit()
    family_login = client.post("/api/auth/login", json={"name": family.name, "pin": "123456"})
    family_headers = {"Authorization": f"Bearer {family_login.json()['access_token']}"}

    denied = client.put(
        "/api/platform-features",
        headers=family_headers,
        json={"memory_enabled": True, "rag_enabled": True},
    )
    assert denied.status_code == 405

    admin_headers = admin_session_headers(admin_client)
    extra = admin_client.put(
        "/admin-api/v1/platform-features",
        headers=admin_headers,
        json={"memory_enabled": True, "rag_enabled": True, "unexpected": True},
    )
    assert extra.status_code == 422
    assert get_platform_feature_state(db_session).memory_source == "environment"


def test_admin_put_rejects_unknown_memory_scope(admin_client, db_session, monkeypatch):
    """未知记忆级别必须 422（与空间级 PUT 同口径）。

    静默丢弃的后果是把「关掉某级别」显示成已生效：管理员以为管家读不到了，
    而实际配置里那一项仍在。因此这里选择拒绝整次写入。
    """
    monkeypatch.setattr(config, "STEWARD_MEMORY_SCOPES", "private")
    create_system_admin(db_session)
    db_session.commit()
    headers = admin_session_headers(admin_client)

    bad = admin_client.put(
        "/admin-api/v1/platform-features",
        headers=headers,
        json={
            "memory_enabled": True,
            "rag_enabled": True,
            "steward_memory_scopes": "household,secrets",
        },
    )
    assert bad.status_code == 422, bad.text

    ok = admin_client.put(
        "/admin-api/v1/platform-features",
        headers=headers,
        json={
            "memory_enabled": True,
            "rag_enabled": True,
            "steward_memory_scopes": "household,private",
        },
    )
    assert ok.status_code == 200, ok.text
    # 规范化（按 MEMORY_SCOPES 声明顺序）。设了什么与生效什么是两个值：
    # env 被 monkeypatch 成 "private"，因此生效值只剩 private。
    assert ok.json()["steward_memory_scopes"] == "private,household"
    assert ok.json()["steward_memory_scopes_effective"] == "private"
    assert ok.json()["steward_memory_scopes_source"] == "platform"
