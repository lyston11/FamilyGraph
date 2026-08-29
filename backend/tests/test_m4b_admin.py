"""m4b 管理员后台：权限门禁、重置 PIN 失效链、审计可见性。"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.v2_foundation import DomainEvent


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


@pytest.fixture()
def admin_and_user(db_session):
    admin = create_user_with_pin(
        db_session, "管长", "000000", is_admin=True, claim_status="claimed"
    )
    user = create_user_with_pin(
        db_session,
        "群众",
        "123123",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1980-08-08"},
    )
    db_session.commit()
    return admin, user


def test_non_admin_403_everywhere(client: TestClient, admin_and_user):
    _admin, user = admin_and_user
    hu = _login(client, "群众", "123123")
    assert client.get("/api/admin/users", headers=hu).status_code == 403
    r = client.post(f"/api/admin/users/{user.id}/reset-pin", json={"confirm": True}, headers=hu)
    assert r.status_code == 403
    assert client.get("/api/admin/audit-logs", headers=hu).status_code == 403


def test_operator_user_list_excludes_family_pii(client: TestClient, admin_and_user):
    """F3/R-03：普通管理列表仅平台元数据，不含家庭姓名/性别/出生。"""
    admin, user = admin_and_user
    ha = _login(client, "管长", "000000")
    rows = client.get("/api/admin/users", headers=ha).json()
    row = next(r for r in rows if r["id"] == user.id)
    assert "name" not in row
    assert "gender" not in row
    assert "privacy_mode" not in row
    assert "birth" not in row
    assert row["claim_status"] == "claimed"
    assert row["profile_status"] == "identity_confirmed"


def test_reset_pin_one_time_and_sessions_revoked(db_session, client: TestClient, admin_and_user):
    admin, user = admin_and_user
    ha = _login(client, "管长", "000000")

    # 群众先登录拿 access
    old_tokens = login(client, "群众", "123123").json()
    old_header = auth_header(old_tokens)
    assert client.get("/api/me", headers=old_header).status_code == 200

    # 管理员重置
    r = client.post(f"/api/admin/users/{user.id}/reset-pin", json={"confirm": True}, headers=ha)
    assert r.status_code == 200, r.text
    new_pin = r.json()["pin"]

    # 旧 access 即刻失效（token_version+1）
    assert client.get("/api/me", headers=old_header).status_code == 401

    # 新 PIN 可登录且强制改 PIN
    fresh = login(client, "群众", new_pin)
    assert fresh.status_code == 200
    assert fresh.json()["user"]["pin_must_change"] is True

    # 审计留痕
    logs = client.get("/api/admin/audit-logs", headers=ha).json()
    assert any(entry["action"] == "pin_reset" for entry in logs)


def test_admin_update_user_transfer_custody(db_session, client: TestClient, admin_and_user):
    _admin, user = admin_and_user
    guardian = create_user_with_pin(db_session, "新管", "456456", claim_status="claimed")
    ha = _login(client, "管长", "000000")
    db_session.commit()

    r = client.patch(
        f"/api/admin/users/{user.id}",
        json={
            "name": "改名群众",
            "privacy_mode": "perpetual",
            "transfer_custody_to": guardian.id,
            "note": "工单#42 数据兑底更正",
        },
        headers=ha,
    )
    assert r.status_code == 200, r.text
    # 响应形状保持兼容：id + 变更字段键
    assert r.json() == {
        "id": user.id,
        "name": "改名群众",
        "privacy_mode": "perpetual",
        "transferred_to": guardian.id,
    }
    db_session.expire_all()
    # v2：operator 无家庭数据读取权 → 成员 API 404；改名结果经 break-glass 检索核实
    member_view = client.get(f"/api/users/{user.id}", headers=_login(client, "管长", "000000"))
    assert member_view.status_code == 404
    admin_rows = client.get("/api/admin/users/lookup", params={"name": "改名"}, headers=ha).json()
    row = next(r for r in admin_rows if r["id"] == user.id)
    assert row["name"] == "改名群众"

    # break-glass 审计：理由入库且完整（changes + operator 账号）
    audit_row = db_session.query(AuditLog).filter(AuditLog.action == "admin_user_updated").one()
    assert audit_row.target_id == user.id
    assert audit_row.detail["note"] == "工单#42 数据兑底更正"
    assert audit_row.detail["break_glass"] is True
    assert audit_row.detail["operator_account"] == _admin.account.id

    # 领域事件同事务落库：档案更新 + custody 主体变更（F-5）
    events = {e.type: e for e in db_session.scalars(select(DomainEvent)).all()}
    assert {"profile.updated", "profile.custody.transferred"} <= set(events)
    updated_payload = events["profile.updated"].payload
    assert sorted(updated_payload["fields"]) == ["name", "privacy_mode"]
    custody_payload = events["profile.custody.transferred"].payload
    assert custody_payload["to_user"] == guardian.id
    assert custody_payload["by_operator_account"] == _admin.account.id


def test_admin_update_user_requires_break_glass_note(client: TestClient, admin_and_user):
    """缺 note → schema 422；纯空白 note → 命令层 BREAK_GLASS_NOTE_REQUIRED 422。"""
    _admin, user = admin_and_user
    ha = _login(client, "管长", "000000")
    url = f"/api/admin/users/{user.id}"

    missing = client.patch(url, json={"name": "改名"}, headers=ha)
    assert missing.status_code == 422

    blank = client.patch(url, json={"name": "改名", "note": "   "}, headers=ha)
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "BREAK_GLASS_NOTE_REQUIRED"

    # 失败路径不落任何修改（break-glass 检索不得命中新名字）
    rows = client.get("/api/admin/users/lookup", params={"name": "改名"}, headers=ha).json()
    assert all(r["id"] != user.id for r in rows)
