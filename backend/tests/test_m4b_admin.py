"""平台后台合同（09-04 隔离后）。

- 家庭 listener 不再注册任何 /api/admin/* 治理路由：家庭用户（含旧 is_admin
  投影）与系统管理员在 8000 上看到的都是与随机未知路径一致的普通 404；
- 系统管理员只能经 8002 /admin-api（用户名 + 强密码）认证；
- 重置 PIN、改档案、custody 移交属系统管理员 break-glass 家庭数据能力，
  PRD「Out of scope」明确要求另立审计强化任务，当前无任何主体可执行，
  对应测试保留测试体并 skip，等该任务落地后接回。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import admin_app, app
from conftest import (
    admin_session_headers,
    create_system_admin,
    create_user_with_pin,
    login,
)

BREAK_GLASS_PENDING = "系统管理员 break-glass 家庭数据能力按 PRD 另立任务；当前无主体可执行该端点"


def test_family_user_cannot_reach_system_backend(client: TestClient, db_session) -> None:
    """家庭用户访问后台路径得到与随机路径一致的 404；旧 is_admin 投影不再是授权来源。"""
    create_user_with_pin(db_session, "群众", "123123", claim_status="claimed")
    create_user_with_pin(db_session, "旧运营", "456456", claim_status="claimed", is_admin=True)

    for name, pin in (("群众", "123123"), ("旧运营", "456456")):
        tokens = login(client, name, pin)
        assert tokens.status_code == 200, tokens.text
        headers = {"Authorization": f"Bearer {tokens.json()['access_token']}"}
        for path in (
            "/api/admin/accounts",
            "/api/admin/spaces",
            "/api/admin/manager-applications",
            "/api/admin/users",
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 404, path
            random_unknown = client.get("/no-such-family-path", headers=headers)
            assert response.json() == random_unknown.json(), path


def test_system_admin_authenticates_only_via_admin_listener(
    client: TestClient, admin_client: TestClient, db_session
) -> None:
    """系统管理员在 8002 用户名+密码登录可用；家庭登录对管理员用户名统一拒绝。"""
    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 200

    # 家庭 listener 不存在管理员登录通道：任何 name+pin 组合都是普通家庭失败
    family_attempt = client.post("/api/auth/login", json={"name": "admin", "pin": "123456"})
    assert family_attempt.status_code == 401
    assert family_attempt.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_admin_business_routes_absent_from_both_listeners() -> None:
    """admin metadata / manager applications 治理路由在两个 listener 都未注册（子任务 2 接管）。"""
    for target in (app, admin_app):
        registered = {getattr(route, "path", "") for route in target.routes}
        for path in (
            "/api/admin/accounts",
            "/api/admin/spaces",
            "/api/admin/space-managers",
            "/api/admin/spaces/{space_id}/members",
            "/api/admin/manager-applications",
            "/api/admin/manager-transfer-consents",
            "/admin-api/accounts",
            "/admin-api/spaces",
        ):
            assert path not in registered, f"{target.title}: {path}"


@pytest.mark.skip(reason=BREAK_GLASS_PENDING)
def test_reset_pin_one_time_and_sessions_revoked(db_session, client: TestClient) -> None:
    """旧 break-glass 合同占位：等审计强化任务落地后接回（对应 admin.py 永不注册）。"""
    user = create_user_with_pin(db_session, "群众", "123123", claim_status="claimed")
    old_tokens = login(client, "群众", "123123").json()
    old_header = {"Authorization": f"Bearer {old_tokens['access_token']}"}
    assert client.get("/api/me", headers=old_header).status_code == 200

    r = client.post(f"/api/admin/users/{user.id}/reset-pin", json={"confirm": True})
    assert r.status_code == 200, r.text
    new_pin = r.json()["pin"]

    assert client.get("/api/me", headers=old_header).status_code == 401

    fresh = login(client, "群众", new_pin)
    assert fresh.status_code == 200
    assert fresh.json()["user"]["pin_must_change"] is True

    logs = client.get("/api/admin/audit-logs").json()
    assert any(entry["action"] == "pin_reset" for entry in logs)


@pytest.mark.skip(reason=BREAK_GLASS_PENDING)
def test_admin_update_user_transfer_custody(db_session, client: TestClient) -> None:
    """旧 break-glass 合同占位：等审计强化任务落地后接回。"""
    user = create_user_with_pin(db_session, "群众", "123123", claim_status="claimed")
    guardian = create_user_with_pin(db_session, "新管", "456456", claim_status="claimed")

    r = client.patch(
        f"/api/admin/users/{user.id}",
        json={
            "name": "改名群众",
            "privacy_mode": "perpetual",
            "transfer_custody_to": guardian.id,
            "note": "工单#42 数据兑底更正",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": user.id,
        "name": "改名群众",
        "privacy_mode": "perpetual",
        "transferred_to": guardian.id,
    }


@pytest.mark.skip(reason=BREAK_GLASS_PENDING)
def test_admin_update_user_requires_break_glass_note(db_session, client: TestClient) -> None:
    """旧 break-glass 合同占位：缺 note → 422（等审计强化任务接回）。"""
    user = create_user_with_pin(db_session, "群众", "123123", claim_status="claimed")
    url = f"/api/admin/users/{user.id}"

    missing = client.patch(url, json={"name": "改名"})
    assert missing.status_code == 422

    blank = client.patch(url, json={"name": "改名", "note": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "BREAK_GLASS_NOTE_REQUIRED"
