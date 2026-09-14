"""平台级 Steward 辅助开关治理测试（09-13-steward-assist-platform-switch-admin）。

覆盖 PRD AC：平台/空间组合真值表（含 env 部署兜底）、admin 写路径审计、
admin status 生效语义、家庭端 assist 生效字段（提示依据）、None 保留语义。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app import config
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.platform_features import PlatformFeatureConfig
from app.services import platform_features, steward_assist
from conftest import (
    admin_session_headers,
    create_agent_fixture,
    create_system_admin,
)


def _row(session, **overrides) -> PlatformFeatureConfig:
    values: dict = {
        "id": 1,
        "memory_enabled": False,
        "rag_enabled": False,
        "updated_at": datetime(2026, 9, 13),
        **overrides,
    }
    row = PlatformFeatureConfig(**values)
    session.add(row)
    session.commit()
    return row


def _space_assist_flags(
    session, space_id: int, *, candidate=False, ranking=False, explanation=False
):
    session.add(
        AgentSpaceProviderSetting(
            space_id=space_id,
            agent_kind="steward",
            enabled=True,
            assist_candidate=candidate,
            assist_ranking=ranking,
            assist_explanation=explanation,
        )
    )
    session.commit()


# ---- 真值表（DB 治理 ∧ env 部署兜底 ∧ 空间级）----


def test_env_bootstrap_when_row_absent(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", False)
    state = platform_features.get_platform_feature_state(db_session)
    assert state.steward_assist_candidate is True
    assert state.steward_assist_candidate_source == "environment"
    assert state.steward_assist_ranking is False


def test_db_overrides_env_and_deployment_kill_switch(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", True)
    _row(db_session, steward_assist_candidate=True, steward_assist_ranking=True)
    db_session.commit()

    state = platform_features.get_platform_feature_state(db_session)
    assert state.steward_assist_candidate is True
    assert state.steward_assist_candidate_source == "platform"
    assert state.steward_assist_ranking is True

    # env 关闭 = 部署级 kill-switch：DB 开也被压为关（fail-closed）
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", False)
    state = platform_features.get_platform_feature_state(db_session)
    assert state.steward_assist_ranking is False
    assert state.steward_assist_ranking_source == "deployment"


@pytest.mark.parametrize(
    ("platform_on", "space_on", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_assist_enabled_truth_table(db_session, monkeypatch, platform_on, space_on, expected):
    _account, space = create_agent_fixture(db_session, name="assist-truth")
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", platform_on)
    if platform_on:
        _row(db_session, steward_assist_candidate=True)
    if space_on:
        _space_assist_flags(db_session, space.id, candidate=True)
    assert steward_assist.assist_enabled(db_session, space.id, "candidate") is expected


def test_assist_platform_governance_changes_effect_without_restart(db_session, monkeypatch):
    """平台开关经 DB 治理后 assist_enabled 立即生效（无需重启/改 env）。"""
    _account, space = create_agent_fixture(db_session, name="assist-gov")
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    _space_assist_flags(db_session, space.id, candidate=True)
    # 行缺失 = env 回退：env True → 生效（既有语义兼容）
    assert steward_assist.assist_enabled(db_session, space.id, "candidate") is True
    # 治理行显式关 → 生效关（DB 覆盖 env）
    _row(db_session, steward_assist_candidate=False)
    assert steward_assist.assist_enabled(db_session, space.id, "candidate") is False
    # 治理行翻开 → 立即生效
    row = db_session.get(PlatformFeatureConfig, 1)
    row.steward_assist_candidate = True
    db_session.commit()
    assert steward_assist.assist_enabled(db_session, space.id, "candidate") is True


# ---- admin API：GET/PUT + 审计 ----


def test_admin_get_and_put_steward_assist_switches(admin_client, db_session, monkeypatch):
    create_system_admin(db_session)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    headers = admin_session_headers(admin_client)

    got = admin_client.get("/admin-api/v1/platform-features", headers=headers)
    assert got.status_code == 200
    body = got.json()
    assert body["steward_assist"]["candidate"] is True
    assert body["steward_assist"]["candidate_source"] == "environment"

    put = admin_client.put(
        "/admin-api/v1/platform-features",
        json={
            "memory_enabled": False,
            "rag_enabled": False,
            "steward_assist_candidate": True,
            "steward_assist_ranking": False,
            "steward_assist_explanation": False,
        },
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["steward_assist"]["candidate"] is True
    assert put.json()["steward_assist"]["candidate_source"] == "platform"

    # None 保留语义：只写 memory/rag 不重置 steward 开关
    put2 = admin_client.put(
        "/admin-api/v1/platform-features",
        json={"memory_enabled": True, "rag_enabled": False},
        headers=headers,
    )
    assert put2.status_code == 200
    assert put2.json()["steward_assist"]["candidate"] is True


def test_admin_put_writes_audit(db_session, admin_client):
    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    put = admin_client.put(
        "/admin-api/v1/platform-features",
        json={
            "memory_enabled": False,
            "rag_enabled": False,
            "steward_assist_candidate": True,
            "steward_assist_ranking": False,
            "steward_assist_explanation": True,
        },
        headers=headers,
    )
    assert put.status_code == 200
    from app.models.admin_access import AdminAccessAudit

    rows = list(
        db_session.scalars(
            select(AdminAccessAudit).where(AdminAccessAudit.action == "platform_features.update")
        ).all()
    )
    assert rows, "平台开关写路径必须落审计"
    filters = rows[-1].filters_json
    assert filters is not None and "steward_assist_candidate" in filters


# ---- steward status 生效语义 ----


def test_steward_status_reflects_platform_governance(admin_client, db_session, monkeypatch):
    create_system_admin(db_session)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    _row(db_session, steward_assist_candidate=False)
    headers = admin_session_headers(admin_client)

    resp = admin_client.get("/admin-api/v1/steward/status", headers=headers)
    assert resp.status_code == 200
    switches = {item["key"]: item["enabled"] for item in resp.json()["switches"]}
    assert switches["model_assist_platform"] is False  # DB 关：env True 被治理覆盖

    row = db_session.get(PlatformFeatureConfig, 1)
    row.steward_assist_candidate = True
    db_session.commit()
    resp = admin_client.get("/admin-api/v1/steward/status", headers=headers)
    switches = {item["key"]: item["enabled"] for item in resp.json()["switches"]}
    assert switches["model_assist_platform"] is True


# ---- 家庭端：assist 生效字段（面板提示依据）----


def test_family_model_settings_expose_assist_effective(db_session, client, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    _row(db_session, steward_assist_candidate=False)
    manager, space = create_agent_fixture(db_session, name="assist-eff")
    _space_assist_flags(db_session, space.id, candidate=True)
    db_session.commit()

    login = client.post("/api/auth/login", json={"name": manager.name, "pin": "123456"})
    assert login.status_code == 200

    resp = client.get(
        f"/api/spaces/{space.id}/model-settings",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert resp.status_code == 200, resp.text
    steward = resp.json()["settings"]["steward"]
    assert steward is not None
    assert steward["assist_candidate"] is True  # 空间级开
    assert steward["assist_candidate_effective"] is False  # 平台配置关 → 可解释提示依据

    row = db_session.get(PlatformFeatureConfig, 1)
    row.steward_assist_candidate = True
    db_session.commit()
    resp = client.get(
        f"/api/spaces/{space.id}/model-settings",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    steward = resp.json()["settings"]["steward"]
    assert steward["assist_candidate_effective"] is True
