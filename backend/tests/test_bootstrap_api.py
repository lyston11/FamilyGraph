"""首启引导合同（09-04）：无网页初始化端点；部署启动自动 bootstrap + 0600 凭据文件。

- POST /api/bootstrap/initialize 已移除（8000 返回普通 404）；
- GET /api/bootstrap/status 只反映家庭用户存在性，不探测系统管理员；
- services.admin_bootstrap.run_startup_preflight：旧 PIN 结构 fail-closed、
  唯一 admin 账号、CSPRNG 密码只落 0600 文件、重启不生成第二账号。
"""

from __future__ import annotations

import importlib
import os
import stat

import pytest
from conftest import admin_header, admin_login, create_system_admin, create_user_with_pin
from sqlalchemy import select

from app import config
from app.models.audit_log import AuditLog
from app.models.system_admin import SystemAdmin, SystemAdminAccount, SystemAdminRefreshSession
from app.services import admin_bootstrap
from app.utils import security


def test_status_false_when_empty(client) -> None:
    assert client.get("/api/bootstrap/status").json() == {"initialized": False}


def test_initialize_endpoint_removed(client) -> None:
    """网页初始化端点不复存在：与随机未知路径同为普通 404（无后台语义）。"""
    response = client.post("/api/bootstrap/initialize", json={"name": "族长"})
    assert response.status_code == 404
    assert response.json() == client.post("/no-such-path", json={}).json()


def test_status_does_not_probe_system_admin(client, db_session) -> None:
    """仅存在系统管理员（无家庭 User）时 status 仍为未初始化：不泄露后台存在性。"""
    create_system_admin(db_session)
    assert client.get("/api/bootstrap/status").json() == {"initialized": False}
    create_user_with_pin(db_session, "家庭用户", "123456")
    assert client.get("/api/bootstrap/status").json() == {"initialized": True}


def test_preflight_creates_single_admin_with_0600_file(db_session) -> None:
    """空库 preflight：唯一 admin 账号 + 随机强密码只出现在 0600 文件。"""
    admin_bootstrap.run_startup_preflight(db_session)

    admins = db_session.query(SystemAdmin).all()
    assert len(admins) == 1
    admin = admins[0]
    assert admin.username == "admin"
    assert admin.status == "active"
    account = db_session.query(SystemAdminAccount).filter_by(system_admin_id=admin.id).one()
    assert account.password_must_change is True
    assert account.password_version == 0

    path = admin_bootstrap.credentials_file_path()
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    content = path.read_text(encoding="utf-8")
    assert "username: admin" in content
    file_password = content.split("password: ", 1)[1].strip().splitlines()[0]
    assert len(file_password) >= 16
    assert security.verify_password(file_password, account.password_hash)

    # 密码不进数据库明文与审计
    assert file_password not in account.password_hash
    audit_rows = db_session.query(AuditLog).filter_by(action="admin_bootstrap_created").all()
    assert len(audit_rows) == 1
    assert file_password not in str(audit_rows[0].detail)

    # 幂等：已有管理员的部署重启不生成第二账号
    admin_bootstrap.run_startup_preflight(db_session)
    assert db_session.query(SystemAdmin).count() == 1


def test_bootstrap_admin_can_login_and_is_forced_to_change_password(
    admin_client, db_session
) -> None:
    """bootstrap 生成的凭据可直接登录 8002；首登被强制改密；改密后凭据文件删除。"""
    admin_bootstrap.run_startup_preflight(db_session)
    path = admin_bootstrap.credentials_file_path()
    content = path.read_text(encoding="utf-8")
    password = content.split("password: ", 1)[1].strip().splitlines()[0]

    pair = admin_login(admin_client, password=password)
    assert pair.status_code == 200, pair.text
    assert pair.json()["admin"]["password_must_change"] is True

    headers = admin_header(pair.json())
    changed = admin_client.put(
        "/admin-api/auth/password",
        json={"current_password": password, "new_password": "NewStrong-9zZx"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert not path.exists()


def test_preflight_fails_closed_on_legacy_pin_structure(db_session) -> None:
    """检测到旧 PIN 结构列即拒绝服务（fail-closed，不静默转换）。"""
    from sqlalchemy import text

    exists = db_session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_admin_accounts'")
    ).scalar()
    assert exists
    db_session.execute(text("ALTER TABLE system_admin_accounts ADD COLUMN pin_hash VARCHAR(255)"))
    db_session.commit()
    try:
        with pytest.raises(RuntimeError, match="PIN"):
            admin_bootstrap._assert_no_legacy_pin_schema(db_session)
    finally:
        # 移除模拟列，避免污染同一进程内后续用例（SQLite ≥3.35 支持 DROP COLUMN）
        db_session.execute(text("ALTER TABLE system_admin_accounts DROP COLUMN pin_hash"))
        db_session.commit()


def test_weak_admin_jwt_config_refuses_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADMIN_JWT_* 缺失/过弱/与家庭 SECRET_KEY 相同/issuer 配置缺失 → 拒启（SF-F4）。"""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_JWT_ISSUER", "familygraph-admin-test")
    monkeypatch.setenv("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-test")

    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="ADMIN_JWT_SECRET"):
        reloaded.ensure_ready()

    monkeypatch.setenv("ADMIN_JWT_SECRET", "short")
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="ADMIN_JWT_SECRET"):
        reloaded.ensure_ready()

    # 与家庭 SECRET_KEY 相同（两个变量设同一 ≥32 位值以触达相等性分支）
    monkeypatch.setenv("SECRET_KEY", "same-secret-value-for-both-domains-0123456789")
    monkeypatch.setenv("ADMIN_JWT_SECRET", "same-secret-value-for-both-domains-0123456789")
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="ADMIN_JWT_SECRET"):
        reloaded.ensure_ready()

    # 恢复家庭 SECRET_KEY 后继续 issuer/audience 分支
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_JWT_SECRET", "distinct-admin-jwt-secret-0123456789abcdef")
    monkeypatch.delenv("ADMIN_JWT_ISSUER", raising=False)
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="ADMIN_JWT_ISSUER"):
        reloaded.ensure_ready()

    monkeypatch.setenv("ADMIN_JWT_ISSUER", "familygraph-admin-test")
    monkeypatch.setenv("ADMIN_JWT_AUDIENCE", "familygraph-admin-test")
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="ADMIN_JWT_AUDIENCE"):
        reloaded.ensure_ready()

    # 恢复 conftest 默认配置状态
    monkeypatch.setenv("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-test")
    importlib.reload(config)


def test_recovery_writes_0600_file_and_bumps_version(db_session, admin_client) -> None:
    """运维恢复：一次性密码只落 0600 文件；版本递增 + 全会话撤销 + 审计。"""
    from app.admin_recovery import run_recovery

    admin = create_system_admin(db_session)
    pair = admin_login(admin_client).json()
    headers = admin_header(pair)
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 200
    old_version = admin.account.password_version

    path = run_recovery(db_session, username="admin")
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    content = path.read_text(encoding="utf-8")
    recovery_password = content.split("password: ", 1)[1].strip().splitlines()[0]

    db_session.expire_all()
    assert admin.account.password_version == old_version + 1
    assert admin.account.password_must_change is True
    sessions = db_session.scalars(
        select(SystemAdminRefreshSession).where(
            SystemAdminRefreshSession.system_admin_id == admin.id
        )
    ).all()
    assert sessions
    assert all(row.revoked_at is not None for row in sessions)
    audits = db_session.query(AuditLog).filter_by(action="admin_password_recovery").all()
    assert len(audits) == 1
    assert recovery_password not in str(audits[0].detail)

    # 旧 access 失效；新恢复密码可登录且强制改密
    assert admin_client.get("/admin-api/auth/me", headers=headers).status_code == 401
    relogin = admin_login(admin_client, password=recovery_password)
    assert relogin.status_code == 200
    assert relogin.json()["admin"]["password_must_change"] is True


def test_recovery_unknown_username_fails(db_session) -> None:
    from app.admin_recovery import run_recovery

    with pytest.raises(LookupError):
        run_recovery(db_session, username="ghost")


def test_credential_file_delete_failure_keeps_must_change(
    admin_client, db_session, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0600 凭据文件删除失败：安全告警 + 审计，初始化未标记完成（SF-F3）。"""
    import logging

    admin_bootstrap.run_startup_preflight(db_session)
    path = admin_bootstrap.credentials_file_path()
    password = path.read_text(encoding="utf-8").split("password: ", 1)[1].strip().splitlines()[0]
    pair = admin_login(admin_client, password=password).json()
    headers = admin_header(pair)

    def _refuse_delete() -> None:
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(admin_bootstrap, "delete_credentials_file", _refuse_delete)
    with caplog.at_level(logging.ERROR, logger="app.services.admin_auth"):
        changed = admin_client.put(
            "/admin-api/auth/password",
            json={"current_password": password, "new_password": "NewStrong-9zZx"},
            headers=headers,
        )
    assert changed.status_code == 200, changed.text
    # 文件仍在（删除失败），password_must_change 回置——初始化未标记完成
    assert path.exists()
    assert changed.json()["password_must_change"] is True
    db_session.expire_all()
    account = db_session.query(SystemAdminAccount).one()
    assert account.password_must_change is True
    # 安全告警日志（不包含密码明文）+ 安全审计
    error_lines = [record.getMessage() for record in caplog.records]
    assert any("could not be deleted" in line for line in error_lines)
    assert password not in "\n".join(error_lines)
    audits = (
        db_session.query(AuditLog).filter(AuditLog.action == "admin_credential_file_delete_failed")
    ).all()
    assert len(audits) == 1
    assert password not in str(audits[0].detail)


def test_admin_app_lifespan_boots_empty_database_and_serves_health(db_session) -> None:
    """lifespan 接线：admin_app 启动即执行 preflight（空库建 admin + 0600 文件）。"""
    from fastapi.testclient import TestClient

    from app.main import admin_app

    with TestClient(admin_app) as booted:
        response = booted.get("/admin-api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    assert db_session.query(SystemAdmin).count() == 1
    path = admin_bootstrap.credentials_file_path()
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
