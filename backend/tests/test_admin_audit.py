"""独立审计表与脱敏器合同测试（09-04 RM-F2/RM-F4）。

覆盖：
- 脱敏器单元行为：键黑名单、Bearer/JWT/URL secret/credential-like 清理、
  email/phone PII 遮罩、堆栈不可靠降级、无法脱敏时只留 error_code + 安全位置；
- 审计写入：filters 经脱敏（敏感过滤值不落库）、审计不含响应正文/token；
- 审计永久保留：业务主体删除不级联删除审计行（FK SET NULL）；
- 审计查询端点分页与 auth 保护。
"""

from __future__ import annotations

import pytest
from conftest import admin_session_headers, create_system_admin, create_user_with_pin
from fastapi.testclient import TestClient

from app.services.admin_sanitizer import (
    is_blocked_key,
    sanitize_error_payload,
    sanitize_filters,
    sanitize_text,
    sanitize_value,
)
from app.utils import timeutil

V1 = "/admin-api/v1"


@pytest.fixture()
def v1_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


# ---- 脱敏器单元 ----


def test_blocked_keys() -> None:
    for key in (
        "token",
        "access_token",
        "SECRET_KEY",
        "apiKey",
        "authorization",
        "prompt",
        "message",
        "content",
        "email",
        "phone",
        "address",
        "pin",
        "password",
    ):
        assert is_blocked_key(key), key
    for key in ("error_code", "component", "stack_location", "attempt", "status"):
        assert not is_blocked_key(key), key


def test_sanitize_text_patterns() -> None:
    # 含秘密的文本：值被替换，但整条字符串判为不可靠（fail-closed，不部分放行）
    bearer = sanitize_text("Authorization: Bearer abcdef123456")
    assert not bearer.reliable
    assert "abcdef123456" not in bearer.value

    jwt_value = (
        "header: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0." "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
    )
    jwt_out = sanitize_text(jwt_value)
    assert not jwt_out.reliable
    assert "eyJ" not in jwt_out.value

    email = sanitize_text("联系 admin@example.com 尽快")
    assert email.value == "联系 [REDACTED_EMAIL] 尽快"
    assert not email.reliable

    phone = sanitize_text("电话 13800138000")
    assert phone.value == "电话 [REDACTED_PHONE]"
    assert not phone.reliable

    # 无敏感模式的普通文本：可靠
    plain = sanitize_text("服务暂不可用，请稍后重试")
    assert plain.reliable and plain.value == "服务暂不可用，请稍后重试"

    url = sanitize_text("https://x.example.com/a?token=abcd1234&ok=1")
    assert "abcd1234" not in url.value
    assert "token" not in url.value
    assert not url.reliable
    assert "ok=1" in url.value

    credential = sanitize_text("key is abcd1234abcd1234abcd1234")
    assert "abcd1234abcd1234abcd1234" not in credential.value
    assert not credential.reliable

    kv = sanitize_text("见 token=abcd1234 配置")
    assert "abcd1234" not in kv.value
    assert not kv.reliable

    stack = sanitize_text("Traceback (most recent call last): File ...")
    assert not stack.reliable

    control = sanitize_text("a\x00b\x1fc")
    assert control.value == "abc" and control.reliable


def test_sanitize_value_drops_blocked_keys() -> None:
    outcome = sanitize_value(
        {"error_code": "X", "api_key": "sk-123", "nested": {"token": "t", "note": "n"}}
    )
    assert outcome.value == {"error_code": "X", "nested": {}}
    # 黑名单键被丢弃 → 结构性不可靠（fail-closed 信号）
    assert outcome.reliable is False


def test_sanitize_error_payload_fail_closed() -> None:
    # 全部键被黑名单丢弃 → 只有 error_code，无摘要
    payload = sanitize_error_payload({"prompt": "原始提示词"}, error_code="E1")
    assert payload == {
        "error_code": "E1",
        "component": None,
        "stack_location": None,
        "summary": None,
    }

    # 可靠文本 → 保留摘要；component/位置为安全标识
    payload = sanitize_error_payload(
        {"component": "provider_gateway", "stack_location": "app/a.py:12", "reason": "超时"},
        error_code="E2",
    )
    assert payload == {
        "error_code": "E2",
        "component": "provider_gateway",
        "stack_location": "app/a.py:12",
        "summary": "超时",
    }

    # 含 PII 文本 → summary None（只返回 error_code + 安全位置）
    payload = sanitize_error_payload({"reason": "邮箱 a@b.com 通知失败"}, error_code="E3")
    assert payload is not None
    assert payload["summary"] is None
    assert payload["error_code"] == "E3"

    # 完整堆栈 → 不进摘要
    payload = sanitize_error_payload(
        {"stack": "Traceback (most recent call last):..."}, error_code="E4"
    )
    assert payload is not None
    assert payload["summary"] is None

    # 位置带可疑内容 → 位置也丢弃
    payload = sanitize_error_payload({"location": "/etc/passwd?token=x"}, error_code="E5")
    assert payload is not None
    assert payload["stack_location"] is None

    # 无 error_code 且原文为 None → None
    assert sanitize_error_payload(None, error_code=None) is None


def test_sanitize_filters_only_scalar_safe_values() -> None:
    filters = sanitize_filters(
        {
            "search": "正常名称",
            "authorization": "Bearer x",
            "email": "a@b.com",
            "page": 2,
            "flag": True,
            "extra": {"nested": 1},
            "skip": None,
        }
    )
    assert filters == {"search": "正常名称", "page": 2, "flag": True}


# ---- 审计写入行为 ----


def test_audit_does_not_store_secrets_or_response_bodies(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    from app.models.admin_access import AdminAccessAudit

    user = create_user_with_pin(db_session, "审计用户", "121212")
    db_session.commit()

    # 带 secret 样子的 search 过滤值不应原样落库
    response = admin_client.get(
        f"{V1}/space-admins?search=Bearer+abcd1234abcd1234abcd1234", headers=v1_headers
    )
    assert response.status_code == 200

    create = admin_client.post(
        f"{V1}/access-sessions",
        json={"target_type": "user", "target_id": user.id, "reason": "核对档案"},
        headers=v1_headers,
    )
    assert create.status_code == 200
    raw_token = create.json()["session_id"]

    rows = db_session.query(AdminAccessAudit).all()
    serialized_rows = str([row.filters_json for row in rows])
    assert "abcd1234abcd1234abcd1234" not in serialized_rows
    # 明文票据永不进入审计（token_hash 唯一落库处）
    for row in rows:
        assert raw_token not in str(row.filters_json)
        assert raw_token not in (row.request_id or "")
        assert row.action in (
            "read.space_admins",
            "access_session.created",
        )


def test_audit_survives_admin_and_business_deletion(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    from app.models.account import Account
    from app.models.admin_access import AdminAccessAudit

    user = create_user_with_pin(db_session, "删除用户", "131313")
    db_session.commit()
    created = admin_client.post(
        f"{V1}/access-sessions",
        json={"target_type": "user", "target_id": user.id, "reason": "删除前核对"},
        headers=v1_headers,
    )
    assert created.status_code == 200
    admin_client.get(f"{V1}/overview", headers=v1_headers)
    assert db_session.query(AdminAccessAudit).count() >= 2

    # 业务对象删除（硬删）：审计行保留，引用列 SET NULL
    db_session.query(Account).filter(Account.user_id == user.id).delete()
    db_session.commit()
    _delete_admin_chain(db_session)

    remaining = db_session.query(AdminAccessAudit).all()
    assert len(remaining) >= 2
    for row in remaining:
        assert row.system_admin_id is None
        assert row.session_id is None
        assert row.action in ("access_session.created", "read.overview")


def _delete_admin_chain(db_session) -> None:  # type: ignore[no-untyped-def]
    """模拟运维硬删管理员主体：sessions 随 CASCADE 删除，审计保留（SET NULL）。"""
    from app.models.admin_access import AdminAccessSession
    from app.models.system_admin import SystemAdmin, SystemAdminAccount

    db_session.query(AdminAccessSession).delete()
    db_session.query(SystemAdminAccount).delete()
    db_session.query(SystemAdmin).delete()
    db_session.commit()


def test_audit_query_endpoint_rejects_unauthenticated(admin_client: TestClient) -> None:
    assert admin_client.get(f"{V1}/audit/access").status_code == 401


def test_audit_rows_carry_request_metadata(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    from app.models.admin_access import AdminAccessAudit

    response = admin_client.get(f"{V1}/overview", headers=v1_headers)
    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    row = (
        db_session.query(AdminAccessAudit)
        .filter(AdminAccessAudit.action == "read.overview")
        .order_by(AdminAccessAudit.id.desc())
        .first()
    )
    assert row is not None
    assert row.request_id == request_id
    assert row.created_at is not None
    assert row.created_at <= timeutil.utcnow()
    # overview 无具体目标：target 为空
    assert row.target_type is None and row.target_id is None
