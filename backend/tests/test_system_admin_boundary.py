"""系统管理员认证隔离边界回归（09-04 合同）。

覆盖四类断言：
- 路由注册：8000 无任何 admin/bootstrap-管理路由；8002 只含 /admin-api 认证面
  七条路由；旧 admin.py 保持不注册；
- 主体互斥矩阵：admin token 与 family token 跨 listener 一律拒绝；
  无 token / 伪造 / 过期 / 版本失效在 8002 统一 401；
- 字段白名单：admin 会话投影只含 id/username/password_must_change/status；
- 防后台语义泄露：8000 对 /admin-api/* 的 404 与随机未知路径完全一致，
  OpenAPI 不出现任何后台前缀。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import (
    admin_header,
    admin_login,
    admin_session_headers,
    create_system_admin,
    create_user_with_pin,
)
from fastapi.testclient import TestClient

from app import config
from app.main import admin_app, app
from app.utils import admin_security, timeutil

ADMIN_AUTH_ROUTES = {
    "/admin-api/auth/login",
    "/admin-api/auth/refresh",
    "/admin-api/auth/logout",
    "/admin-api/auth/me",
    "/admin-api/auth/password",
    "/admin-api/auth/username",
    "/admin-api/health",
}

# 09-04 子任务 2：/admin-api/v1 只读模型 + 访问会话 + 审批唯一写例外。
# 写端点只有 access-sessions（签发会话）与 manager-applications approve/reject。
ADMIN_V1_ROUTES = {
    "/admin-api/v1/overview",
    "/admin-api/v1/space-admins",
    "/admin-api/v1/space-admins/{admin_user_id}/spaces",
    "/admin-api/v1/spaces/{space_id}",
    "/admin-api/v1/spaces/{space_id}/members",
    "/admin-api/v1/spaces/{space_id}/relations",
    "/admin-api/v1/spaces/{space_id}/facts",
    "/admin-api/v1/users/{user_id}/profile",
    "/admin-api/v1/users/{user_id}/avatar/thumbnail",
    "/admin-api/v1/users/{user_id}/attachments",
    "/admin-api/v1/operations/queue",
    "/admin-api/v1/operations/notifications",
    "/admin-api/v1/agent/runs",
    "/admin-api/v1/agent/jobs",
    # 09-06 治理迁移：Provider 治理迁入 admin 域（/admin-api/v1/agent/*）
    "/admin-api/v1/agent/providers",
    "/admin-api/v1/agent/providers/{provider_id}",
    "/admin-api/v1/agent/platform-defaults",
    "/admin-api/v1/agent/spaces/{space_id}/provider-settings",
    "/admin-api/v1/audit/access",
    "/admin-api/v1/access-sessions",
    "/admin-api/v1/manager-applications/{application_id}/approve",
    "/admin-api/v1/manager-applications/{application_id}/reject",
    # 09-11 Steward 运维（仅 admin listener；读不受引擎门禁，rerun 受 STEWARD_ENABLED 门禁）
    "/admin-api/v1/steward/status",
    "/admin-api/v1/steward/jobs",
    "/admin-api/v1/steward/spaces/{space_id}/rerun",
    "/admin-api/v1/platform-features",
    # 09-13 观测：agent 延迟指标（只读，admin listener）
    "/admin-api/v1/agent/latency",
}

LEGACY_BREAK_GLASS_PATHS = {
    "/api/admin/users",
    "/api/admin/users/lookup",
    "/api/admin/users/{user_id}",
    "/api/admin/users/{user_id}/reset-pin",
    "/api/admin/audit-logs",
    "/api/admin/owner-invitations",
    "/api/admin/owner-invitations/{invitation_id}/revoke",
    "/api/admin/data-rights",
    "/api/admin/data-rights/{request_id}/resolve-correction",
    "/api/admin/claim-disputes",
    "/api/admin/claim-disputes/{dispute_id}/resolve",
}


# ---- 路由注册 ----


def test_family_app_registers_no_admin_routes(client: TestClient) -> None:
    """8000 路由表与 OpenAPI 不得出现 system-admin / admin-api / bootstrap 管理能力。

    /api/admin/web（Controlled Web 平台配置）是既有家庭 platform_operator 面
    （feature flag 门禁），不在本任务移除清单；/api/admin/agent（Provider 治理）
    已于 09-06 迁入 admin 域（/admin-api/v1/agent/*），家庭挂载必须消失（404）。
    """
    registered = {getattr(route, "path", "") for route in app.routes}

    def _is_removed_admin_path(path: str) -> bool:
        if not path.startswith("/api/admin"):
            return False
        return not path.startswith("/api/admin/web")

    assert not any(_is_removed_admin_path(path) for path in registered)
    # /admin-api 在 8000 只允许普通 404 catch-all，不得出现任何真实后台路由
    admin_api_paths = {
        path
        for path in registered
        if path.startswith("/admin-api") and path != "/admin-api/{rest:path}"
    }
    assert admin_api_paths == set()
    assert "/api/bootstrap/initialize" not in registered
    assert registered.isdisjoint(LEGACY_BREAK_GLASS_PATHS)
    # 兜底：旧 admin.py 的家庭数据路由任何形态都不允许存在
    assert not any(path.startswith("/api/admin/users") for path in registered)
    # 09-06：旧家庭 agent 治理挂载已删除——任何 token（含有效家庭 token）都 404
    assert client.get("/api/admin/agent/providers").status_code == 404
    assert client.post("/api/admin/agent/providers", json={}).status_code == 404

    openapi = app.openapi()
    assert not any(_is_removed_admin_path(path) for path in openapi["paths"])
    assert "/admin-api/auth/login" not in openapi["paths"]
    assert not any("system" in path.lower() for path in openapi["paths"])
    assert "admin" not in openapi.get("title", "").lower()


def test_admin_app_registers_only_admin_api_routes() -> None:
    """8002 业务路由 == 认证面七条 + /admin-api/v1 模型路由，无其他任何路由。

    写端点仅限 access-sessions、manager-applications approve/reject 与 09-06
    迁入的 agent 治理（providers 注册/更新、platform-defaults 覆盖）；
    家庭 /api 路由与旧 admin.py 不得出现在 admin listener。
    """
    registered = {getattr(route, "path", "") for route in admin_app.routes}
    assert ADMIN_AUTH_ROUTES <= registered
    assert ADMIN_V1_ROUTES <= registered
    framework_routes = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    business = registered - ADMIN_AUTH_ROUTES - ADMIN_V1_ROUTES - framework_routes
    assert not any(
        path.startswith("/api/") or path.startswith("/admin") for path in business
    ), sorted(business)
    # 家庭面路由不得反向出现在 admin listener
    assert not any(path.startswith("/api/") for path in registered)
    # 审批与 agent 治理之外无任何写能力：v1 路由面写端点全集如下
    writes = {
        route.path
        for route in admin_app.routes
        if getattr(route, "methods", None)
        and route.methods - {"GET", "HEAD", "OPTIONS"}
        and getattr(route, "path", "").startswith("/admin-api/v1")
    }
    assert writes == {
        "/admin-api/v1/access-sessions",
        "/admin-api/v1/manager-applications/{application_id}/approve",
        "/admin-api/v1/manager-applications/{application_id}/reject",
        "/admin-api/v1/agent/providers",
        "/admin-api/v1/agent/providers/{provider_id}",
        "/admin-api/v1/agent/platform-defaults",
        # 09-11：admin steward 唯一写端点（单空间重跑，STEWARD_ENABLED 门禁 + 幂等键）
        "/admin-api/v1/steward/spaces/{space_id}/rerun",
        "/admin-api/v1/platform-features",
    }


def test_legacy_break_glass_admin_routes_not_registered_anywhere() -> None:
    """旧 admin.py 的家庭 break-glass 路由在两个 listener 都保持未注册。"""
    for target in (app, admin_app):
        registered = {getattr(route, "path", "") for route in target.routes}
        assert registered.isdisjoint(LEGACY_BREAK_GLASS_PATHS), target.title


# ---- 8000 后台路径防泄露 ----


def test_admin_api_prefix_on_family_listener_is_ordinary_404(client: TestClient) -> None:
    """/admin-api/* 在 8000 与随机未知路径响应完全一致（无后台语义）。"""
    probes = (
        "/admin-api/auth/login",
        "/admin-api/auth/me",
        "/admin-api/health",
        "/admin-api/anything/else",
    )
    for probe in probes:
        random_unknown = client.get(f"/definitely-not-here-{probe.strip('/').replace('/', '-')}")
        response = client.get(probe)
        assert response.status_code == 404, probe
        assert response.json() == random_unknown.json(), probe
    # POST 同样普通 404
    assert client.post("/admin-api/auth/login", json={}).status_code == 404
    assert client.post("/definitely-not-here", json={}).status_code == 404


# ---- 主体互斥矩阵（跨 listener）----


def test_family_token_rejected_on_admin_listener(
    client: TestClient, admin_client: TestClient, db_session
) -> None:
    """family token 在 8002 任意 admin 路由统一 401；admin token 在 8000 家庭路由 401。"""
    create_system_admin(db_session)
    create_user_with_pin(db_session, "家庭用户", "123456")
    db_session.commit()

    family_login = client.post("/api/auth/login", json={"name": "家庭用户", "pin": "123456"})
    assert family_login.status_code == 200
    family_headers = {"Authorization": f"Bearer {family_login.json()['access_token']}"}
    for method, route in (("get", "/admin-api/auth/me"), ("put", "/admin-api/auth/username")):
        response = getattr(admin_client, method)(route, headers=family_headers)
        assert response.status_code == 401, route
        assert response.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"

    admin_headers = admin_session_headers(admin_client)
    assert client.get("/api/me", headers=admin_headers).status_code == 401
    assert client.get("/api/spaces", headers=admin_headers).status_code == 401


def test_admin_routes_reject_missing_or_mistyped_tokens(
    admin_client: TestClient, db_session
) -> None:
    """无 token / family 声明 / 未知 principal_type 的 admin 域 token 一律 401。"""
    import jwt

    from app import config
    from app.utils import security

    admin = create_system_admin(db_session)
    account = admin.account
    now = timeutil.utcnow()

    def _admin_domain_token(principal_type: str, secret: str) -> str:
        claims = {
            "sub": str(admin.id),
            admin_security.VERSION_CLAIM: account.password_version,
            "typ": admin_security.ADMIN_ACCESS_TOKEN_TYPE,
            "jti": "forged-jti",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "iss": config.ADMIN_JWT_ISSUER,
            "aud": config.ADMIN_JWT_AUDIENCE,
            "principal_type": principal_type,
        }
        return jwt.encode(claims, secret, algorithm="HS256")

    # 无 token
    assert admin_client.get("/admin-api/auth/me").status_code == 401

    # 管理员签发域 + 正确 principal_type 才能通过；错误声明/跨域签名一律 401
    for bogus in (
        _admin_domain_token("family_user", config.ADMIN_JWT_SECRET),
        _admin_domain_token("space_admin", config.ADMIN_JWT_SECRET),
        # 家庭 SECRET_KEY 签发的 system_admin 声明 token：跨签发域必须失败
        security.create_access_token(
            admin.id, account.password_version, principal_type="system_admin"
        ),
    ):
        headers = {"Authorization": f"Bearer {bogus}"}
        response = admin_client.get("/admin-api/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"


def test_expired_or_stale_version_admin_token_rejected(
    admin_client: TestClient, db_session
) -> None:
    """过期 token / password_version 失配的 admin token 一律 401。"""
    admin = create_system_admin(db_session)
    stale = admin_security.create_admin_access_token(admin.id, admin.account.password_version + 5)
    response = admin_client.get("/admin-api/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert response.status_code == 401

    original_ttl = config.ADMIN_ACCESS_TOKEN_TTL_SECONDS
    config.ADMIN_ACCESS_TOKEN_TTL_SECONDS = -10
    try:
        expired = admin_security.create_admin_access_token(admin.id, admin.account.password_version)
    finally:
        config.ADMIN_ACCESS_TOKEN_TTL_SECONDS = original_ttl
    response = admin_client.get(
        "/admin-api/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401


def test_wrong_issuer_or_audience_rejected(admin_client: TestClient, db_session) -> None:
    """iss/aud 不匹配的 admin token（同密钥签发）也必须拒绝（签发域三元组合同）。"""
    import jwt

    from app import config as cfg

    admin = create_system_admin(db_session)
    now = int(timeutil.utcnow().timestamp())
    base = {
        "sub": str(admin.id),
        admin_security.VERSION_CLAIM: admin.account.password_version,
        "typ": "access",
        "jti": "jti-x",
        "iat": now,
        "exp": now + 600,
        "principal_type": "system_admin",
    }
    wrong_issuer = dict(base, iss="other-issuer", aud=cfg.ADMIN_JWT_AUDIENCE)
    wrong_audience = dict(base, iss=cfg.ADMIN_JWT_ISSUER, aud="other-audience")
    for claims in (wrong_issuer, wrong_audience):
        token = jwt.encode(claims, cfg.ADMIN_JWT_SECRET, algorithm="HS256")
        response = admin_client.get(
            "/admin-api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


# ---- 管理员会话投影白名单 ----


def test_admin_session_projection_field_whitelist(admin_client: TestClient, db_session) -> None:
    """登录/me 投影只含 id/username/password_must_change/status（design §3）。"""
    create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    assert set(pair) == {"access_token", "refresh_token", "token_type", "admin"}
    assert set(pair["admin"]) == {"id", "username", "password_must_change", "status"}
    assert pair["admin"]["username"] == "admin"
    assert "password_hash" not in pair["admin"]
    assert "failed_attempts" not in pair["admin"]
    assert "locked_until" not in pair["admin"]

    headers = admin_header(pair)
    me = admin_client.get("/admin-api/auth/me", headers=headers)
    assert me.status_code == 200
    assert set(me.json()) == {"id", "username", "password_must_change", "status"}


# ---- 登录错误矩阵 ----


def test_admin_login_uniform_error_does_not_leak_existence(
    admin_client: TestClient, db_session
) -> None:
    """未知用户名 / 密码错误 / 大小写变形：统一 401 文案与响应体。"""
    create_system_admin(db_session)
    wrong_password = admin_client.post(
        "/admin-api/auth/login", json={"username": "admin", "password": "WrongPass-9999z"}
    )
    unknown_user = admin_client.post(
        "/admin-api/auth/login", json={"username": "ghost", "password": "WrongPass-9999z"}
    )
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert wrong_password.json()["error"]["code"] == "ADMIN_INVALID_CREDENTIALS"
    assert wrong_password.json()["error"]["message"] == "用户名或密码错误"


def test_admin_login_lockout_returns_429_with_retry_after(
    admin_client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败达到阈值锁定 429 + Retry-After；仍用统一文案不泄露账号存在性。"""
    from datetime import timedelta

    create_system_admin(db_session)
    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        assert (
            admin_client.post(
                "/admin-api/auth/login", json={"username": "admin", "password": "Nope-Nope-1a"}
            ).status_code
            == 401
        )
    locked = admin_client.post(
        "/admin-api/auth/login", json={"username": "admin", "password": "FixtureAdmin-2026x"}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.json()["error"]["message"] == "用户名或密码错误"

    # 锁定窗口过后恢复（timeutil 统一 naive UTC）
    base = timeutil.utcnow()
    monkeypatch.setattr(
        timeutil,
        "utcnow",
        lambda: base + timedelta(minutes=config.AUTH_LOCK_MINUTES + 1),
    )
    recovered = admin_login(admin_client)
    assert recovered.status_code == 200


def test_admin_lockout_revoked_on_password_change(admin_client: TestClient, db_session) -> None:
    """改密后 password_version+1：旧 access 全部失效，旧 refresh 不可续期。"""
    create_system_admin(db_session, password_must_change=True, password="FirstPass-1aA")
    pair = admin_login(admin_client, password="FirstPass-1aA").json()
    headers = admin_header(pair)

    # 首登未改密：me/username 白名单外一律 403
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 403
    changed = admin_client.put(
        "/admin-api/auth/password",
        json={"current_password": "FirstPass-1aA", "new_password": "SecondPass-2bB"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["password_must_change"] is False

    # 版本失效：旧 access/refresh 全部不可用
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 401
    reuse = admin_client.post(
        "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert reuse.status_code == 401

    relogin = admin_login(admin_client, password="SecondPass-2bB")
    assert relogin.status_code == 200


def test_admin_refresh_rotation_and_reuse_detection(admin_client: TestClient, db_session) -> None:
    """refresh 轮换；重用旧 refresh 撤销全部会话并审计。"""
    from app.models.audit_log import AuditLog

    create_system_admin(db_session)
    pair = admin_login(admin_client).json()

    rotated = admin_client.post(
        "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    new_pair = rotated.json()
    assert new_pair["refresh_token"] != pair["refresh_token"]

    headers = admin_header(new_pair)
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 200

    reuse = admin_client.post(
        "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert reuse.status_code == 401
    audits = (
        db_session.query(AuditLog).filter(AuditLog.action == "admin_refresh_reuse_detected").all()
    )
    assert len(audits) == 1
    # 全会话撤销：最新 refresh 也已失效
    newest = admin_client.post(
        "/admin-api/auth/refresh", json={"refresh_token": new_pair["refresh_token"]}
    )
    assert newest.status_code == 401


def test_admin_refresh_absolute_expiry_and_rotation_not_extended(
    admin_client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """refresh 轮换不续期（绝对有效期）；会话到期后一律 401（SF-F4）。"""
    from app.models.system_admin import SystemAdminRefreshSession

    create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    first_row = db_session.query(SystemAdminRefreshSession).one()
    original_expiry = first_row.expires_at

    rotated = admin_client.post(
        "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    db_session.expire_all()
    second_row = (
        db_session.query(SystemAdminRefreshSession)
        .order_by(SystemAdminRefreshSession.id.desc())
        .first()
    )
    assert second_row is not None and second_row.id != first_row.id
    assert second_row.rotated_from == first_row.id
    # 轮换不续期：新会话 expires_at 与原会话完全一致（绝对有效期合同）
    assert second_row.expires_at == original_expiry

    # 行已到期而 JWT 尚未到真实时钟过期：提交仍被拒绝（不签发新会话）
    monkeypatch.setattr(timeutil, "utcnow", lambda: original_expiry + timedelta(seconds=1))
    expired = admin_client.post(
        "/admin-api/auth/refresh",
        json={"refresh_token": rotated.json()["refresh_token"]},
    )
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"
    db_session.expire_all()
    assert (
        db_session.query(SystemAdminRefreshSession)
        .filter(SystemAdminRefreshSession.rotated_from == second_row.id)
        .count()
        == 0
    )


def test_admin_lockout_revokes_existing_sessions(admin_client: TestClient, db_session) -> None:
    """SF-F4：登录失败达到阈值锁定时立即撤销全部既有 refresh/access 会话。"""
    from app.models.audit_log import AuditLog

    create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    headers = admin_header(pair)
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 200

    for _ in range(config.AUTH_MAX_FAILED_ATTEMPTS):
        assert (
            admin_client.post(
                "/admin-api/auth/login", json={"username": "admin", "password": "Nope-Nope-1a"}
            ).status_code
            == 401
        )
    # 锁定生效：正确密码也 429 + Retry-After
    locked = admin_login(admin_client)
    assert locked.status_code == 429
    assert locked.headers.get("Retry-After") is not None

    # 旧 access 即刻失效（password_version+1），旧 refresh 全部撤销
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 401
    assert (
        admin_client.post(
            "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )
    audits = (
        db_session.query(AuditLog).filter(AuditLog.action == "admin_lockout_sessions_revoked").all()
    )
    assert len(audits) == 1
    assert audits[0].detail["revoked_count"] == 1


def test_admin_logout_revokes_refresh(admin_client: TestClient, db_session) -> None:
    create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    out = admin_client.post(
        "/admin-api/auth/logout",
        json={"refresh_token": pair["refresh_token"]},
        headers=admin_header(pair),
    )
    assert out.status_code == 200
    assert (
        admin_client.post(
            "/admin-api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )


def test_admin_username_change_revokes_sessions(admin_client: TestClient, db_session) -> None:
    """改用户名需当前密码；成功后全部会话失效（SF-F5）。"""
    create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    headers = admin_header(pair)

    wrong = admin_client.put(
        "/admin-api/auth/username",
        json={"current_password": "nope-nope-1aA", "username": "operator"},
        headers=headers,
    )
    assert wrong.status_code == 401

    changed = admin_client.put(
        "/admin-api/auth/username",
        json={"current_password": "FixtureAdmin-2026x", "username": "operator"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["username"] == "operator"

    # 旧会话全部失效
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 401
    relogin = admin_login(admin_client, username="operator")
    assert relogin.status_code == 200
    # 旧用户名不再可登录（统一文案）
    stale = admin_login(admin_client, username="admin")
    assert stale.status_code == 401
