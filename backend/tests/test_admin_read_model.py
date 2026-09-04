"""/admin-api/v1 只读模型合同测试（09-04 子任务 2）。

覆盖：
- 认证矩阵：无 token / family token / 首登未改密一律拒绝；
- 字段白名单：每个响应 schema 的精确集合断言（extra=forbid 兜底）；
- 分页上限与非法参数 422、稳定排序、防存在性枚举（安全 404 / 空列表）；
- admin → 所管空间聚合正确、异常队列（无管理员/管理员锁定）只读可见；
- Agent 诊断投影：error_json 原文不泄漏，只有脱敏后的安全字段。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from conftest import (
    admin_session_headers,
    create_agent_fixture,
    create_agent_session,
    create_space_member,
    create_system_admin,
    create_user_with_pin,
    seed_space_with_owner,
)
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import text

from app.db import engine
from app.utils import timeutil

V1 = "/admin-api/v1"


@pytest.fixture()
def v1_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _space_without_admin(db_session, *, name: str, owner_user_id: int):
    """无 active 管理员的空间（异常队列数据，不自动修复）。"""
    from app.models.space import FamilySpace

    space = FamilySpace(
        name=name, kind="lineage", owner_id=owner_user_id, created_at=timeutil.utcnow()
    )
    db_session.add(space)
    db_session.commit()
    return space


# ---- 认证矩阵 ----


def test_v1_routes_reject_missing_or_family_tokens(
    admin_client: TestClient, client: TestClient, db_session
) -> None:
    create_system_admin(db_session)
    user = create_user_with_pin(db_session, "家庭用户", "123456")
    db_session.commit()

    for path in (f"{V1}/overview", f"{V1}/space-admins", f"{V1}/audit/access"):
        assert admin_client.get(path).status_code == 401, path
        assert admin_client.get(path).json()["error"]["code"] == "ADMIN_UNAUTHORIZED"

    family_login = client.post("/api/auth/login", json={"name": "家庭用户", "pin": "123456"})
    family_headers = {"Authorization": f"Bearer {family_login.json()['access_token']}"}
    assert user is not None
    response = admin_client.get(f"{V1}/overview", headers=family_headers)
    assert response.status_code == 401


def test_v1_routes_blocked_until_initial_password_changed(
    admin_client: TestClient, db_session
) -> None:
    """password_must_change=true：业务读端点 403，仅密码/白名单可用。"""
    create_system_admin(db_session, password_must_change=True, password="FirstPass-1aA")
    login = admin_client.post(
        "/admin-api/auth/login", json={"username": "admin", "password": "FirstPass-1aA"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = admin_client.get(f"{V1}/overview", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_PASSWORD_CHANGE_REQUIRED"


# ---- 字段白名单：overview / space-admins / spaces ----


def test_overview_exact_field_sets(admin_client: TestClient, db_session, v1_headers) -> None:
    manager = create_user_with_pin(db_session, "总览管理", "303030")
    extra_member = create_user_with_pin(db_session, "普通成员", "303035")
    healthy = seed_space_with_owner(db_session, manager.id, name="健康空间", kind="lineage")
    create_space_member(db_session, healthy.id, extra_member.id)
    orphan = _space_without_admin(db_session, name="无主空间", owner_user_id=manager.id)

    response = admin_client.get(f"{V1}/overview", headers=v1_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more", "totals"}
    assert set(body["totals"]) == {
        "spaces_total",
        "healthy_spaces",
        "anomaly_spaces",
        "active_space_admins",
        "pending_applications",
    }
    assert body["totals"] == {
        "spaces_total": 2,
        "healthy_spaces": 1,
        "anomaly_spaces": 1,
        "active_space_admins": 1,
        "pending_applications": 0,
    }
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert set(item) == {
            "space_id",
            "name",
            "kind",
            "created_at",
            "manager_user_id",
            "manager_name",
            "member_count",
            "status",
            "anomalies",
        }
    by_id = {item["space_id"]: item for item in body["items"]}
    assert by_id[healthy.id]["status"] == "healthy"
    assert by_id[healthy.id]["anomalies"] == []
    assert by_id[orphan.id]["status"] == "anomaly"
    assert by_id[orphan.id]["anomalies"] == ["no_active_admin"]
    assert by_id[orphan.id]["manager_user_id"] is None


def test_overview_filters_and_pagination_bounds(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "筛选管理", "303031")
    seed_space_with_owner(db_session, manager.id, name="甲空间", kind="household")
    _space_without_admin(db_session, name="乙空间", owner_user_id=manager.id)

    assert admin_client.get(f"{V1}/overview?page=0", headers=v1_headers).status_code == 422
    assert admin_client.get(f"{V1}/overview?page_size=101", headers=v1_headers).status_code == 422
    assert admin_client.get(f"{V1}/overview?page_size=0", headers=v1_headers).status_code == 422

    healthy = admin_client.get(f"{V1}/overview?status=healthy", headers=v1_headers).json()
    assert [item["name"] for item in healthy["items"]] == ["甲空间"]
    anomaly = admin_client.get(f"{V1}/overview?status=anomaly", headers=v1_headers).json()
    assert [item["name"] for item in anomaly["items"]] == ["乙空间"]
    assert anomaly["totals"]["spaces_total"] == 2

    searched = admin_client.get(f"{V1}/overview?search=甲", headers=v1_headers).json()
    assert [item["name"] for item in searched["items"]] == ["甲空间"]

    # 稳定排序：space_id 升序
    ordered = admin_client.get(f"{V1}/overview", headers=v1_headers).json()
    ids = [item["space_id"] for item in ordered["items"]]
    assert ids == sorted(ids)


def test_anomaly_queue_flags_locked_and_deleted_admins(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    """锁定 / 已删除管理员的空间进入异常队列，只读展示不自动修复（PRD RM-F1）。"""
    locked_manager = create_user_with_pin(db_session, "被锁管理", "313032")
    locked_space = seed_space_with_owner(
        db_session, locked_manager.id, name="锁定空间", kind="lineage"
    )
    deleted_manager = create_user_with_pin(db_session, "被删管理", "313033")
    deleted_space = seed_space_with_owner(
        db_session, deleted_manager.id, name="被删空间", kind="lineage"
    )
    assert locked_manager.account is not None
    locked_manager.account.locked_until = timeutil.utcnow() + timedelta(hours=1)
    deleted_manager.deleted_at = timeutil.utcnow()
    db_session.commit()

    response = admin_client.get(f"{V1}/overview", headers=v1_headers)
    assert response.status_code == 200
    by_id = {item["space_id"]: item for item in response.json()["items"]}
    assert by_id[locked_space.id]["status"] == "anomaly"
    assert by_id[locked_space.id]["anomalies"] == ["admin_locked"]
    assert by_id[deleted_space.id]["anomalies"] == ["admin_deleted"]

    queue = admin_client.get(f"{V1}/operations/queue?kind=space_anomaly", headers=v1_headers).json()
    flagged = {item["reference_id"]: item["anomaly"] for item in queue["items"]}
    assert flagged[locked_space.id] == "admin_locked"
    assert flagged[deleted_space.id] == "admin_deleted"


def test_anomaly_classifier_flags_duplicate_active_admin() -> None:
    """唯一索引不变量被绕过（双 active space_admin）也必须进入异常队列。"""
    from app.models.account import Account
    from app.models.space import SpaceMember
    from app.models.user import User
    from app.services.admin_read_model import _anomalies_for

    def _admin_row() -> tuple[SpaceMember, User, Account]:
        return (SpaceMember(role="space_admin", status="active"), User(), Account())

    assert _anomalies_for([_admin_row()], timeutil.utcnow()) == []
    duplicated = _anomalies_for([_admin_row(), _admin_row()], timeutil.utcnow())
    assert duplicated == ["duplicate_active_admin"]


def test_overview_query_count_is_constant_no_n1(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    """页面数据量增长时查询数恒定（无 N+1）。"""
    manager = create_user_with_pin(db_session, "计数管理", "303032")
    for index in range(6):
        seed_space_with_owner(db_session, manager.id, name=f"计数空间{index}", kind="lineage")

    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _count)
    try:
        admin_client.get(f"{V1}/overview", headers=v1_headers)
        first = len(statements)
        for index in range(6, 9):
            seed_space_with_owner(db_session, manager.id, name=f"计数空间{index}", kind="lineage")
        statements.clear()
        admin_client.get(f"{V1}/overview", headers=v1_headers)
        second = len(statements)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _count)
    assert first == second


def test_space_admins_aggregation_and_whitelist(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    multi = create_user_with_pin(db_session, "多空间管理", "404040", bio="管理员简介")
    single = create_user_with_pin(db_session, "单空间管理", "404041")
    seed_space_with_owner(db_session, multi.id, name="空间一", kind="lineage")
    seed_space_with_owner(db_session, multi.id, name="空间二", kind="lineage")
    seed_space_with_owner(db_session, single.id, name="空间三", kind="household")

    response = admin_client.get(f"{V1}/space-admins", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more"}
    assert body["total"] == 2
    by_user = {item["admin_user_id"]: item for item in body["items"]}
    assert by_user[multi.id]["space_count"] == 2
    assert by_user[single.id]["space_count"] == 1
    for item in body["items"]:
        assert set(item) == {
            "admin_user_id",
            "name",
            "gender",
            "profile_status",
            "account_status",
            "avatar_available",
            "space_count",
            "created_at",
        }
        # 管理员行不携带档案内容字段（bio/birth/death/avatar_path 等）
        assert "bio" not in item
        assert "birth" not in item

    searched = admin_client.get(f"{V1}/space-admins?search=单空间", headers=v1_headers).json()
    assert [item["admin_user_id"] for item in searched["items"]] == [single.id]
    claimed = admin_client.get(f"{V1}/space-admins?status=claimed", headers=v1_headers).json()
    assert claimed["total"] == 2


def test_space_admin_spaces_and_safe_empty(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "列空间管理", "505050")
    first = seed_space_with_owner(db_session, manager.id, name="列空间一", kind="lineage")
    second = seed_space_with_owner(db_session, manager.id, name="列空间二", kind="lineage")

    response = admin_client.get(f"{V1}/space-admins/{manager.id}/spaces", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more"}
    assert [item["space_id"] for item in body["items"]] == [first.id, second.id]
    for item in body["items"]:
        assert set(item) == {
            "space_id",
            "name",
            "kind",
            "created_at",
            "manager_user_id",
            "manager_name",
        }

    # 未知管理员：与空结果不可区分（200 空页，不泄露存在性）
    unknown = admin_client.get(f"{V1}/space-admins/999999/spaces", headers=v1_headers)
    assert unknown.status_code == 200
    assert unknown.json()["items"] == []
    assert unknown.json()["total"] == 0


def test_space_detail_whitelist_and_safe_404(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "详情管理", "606060")
    extra_member = create_user_with_pin(db_session, "详情成员", "606061")
    space = seed_space_with_owner(db_session, manager.id, name="详情空间", kind="lineage")
    create_space_member(db_session, space.id, extra_member.id)
    orphan = _space_without_admin(db_session, name="孤儿空间", owner_user_id=manager.id)

    response = admin_client.get(f"{V1}/spaces/{space.id}", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "space_id",
        "name",
        "kind",
        "created_at",
        "manager_user_id",
        "manager_name",
        "member_count",
        "anomalies",
    }
    assert body["member_count"] == 2
    assert body["anomalies"] == []

    orphan_body = admin_client.get(f"{V1}/spaces/{orphan.id}", headers=v1_headers).json()
    assert orphan_body["anomalies"] == ["no_active_admin"]

    missing = admin_client.get(f"{V1}/spaces/999999", headers=v1_headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ADMIN_RESOURCE_NOT_FOUND"


def test_space_members_whitelist_and_unknown_space_empty(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    manager = create_user_with_pin(db_session, "成员管理", "707070")
    member = create_user_with_pin(db_session, "普通成员", "707071")
    space = seed_space_with_owner(db_session, manager.id, name="成员空间", kind="household")
    create_space_member(db_session, space.id, member.id)

    response = admin_client.get(f"{V1}/spaces/{space.id}/members", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more"}
    assert body["total"] == 2
    for item in body["items"]:
        assert set(item) == {"user_id", "name", "role", "status", "created_at", "updated_at"}
    roles = {item["user_id"]: item["role"] for item in body["items"]}
    assert roles[manager.id] == "space_admin"
    assert roles[member.id] == "member"

    unknown = admin_client.get(f"{V1}/spaces/999999/members", headers=v1_headers)
    assert unknown.status_code == 200
    assert unknown.json() == {
        "items": [],
        "page": 1,
        "page_size": 50,
        "total": 0,
        "has_more": False,
    }


def test_space_members_filters(admin_client: TestClient, db_session, v1_headers) -> None:
    manager = create_user_with_pin(db_session, "过滤管理", "808080")
    pending_user = create_user_with_pin(db_session, "待定成员", "808081")
    space = seed_space_with_owner(db_session, manager.id, name="过滤空间", kind="household")
    create_space_member(db_session, space.id, pending_user.id, status="pending")

    active = admin_client.get(
        f"{V1}/spaces/{space.id}/members?status=active", headers=v1_headers
    ).json()
    assert [item["user_id"] for item in active["items"]] == [manager.id]
    pending = admin_client.get(
        f"{V1}/spaces/{space.id}/members?status=pending", headers=v1_headers
    ).json()
    assert [item["user_id"] for item in pending["items"]] == [pending_user.id]
    assert (
        admin_client.get(
            f"{V1}/spaces/{space.id}/members?page_size=101", headers=v1_headers
        ).status_code
        == 422
    )


# ---- Agent 诊断白名单 ----


def _seed_failed_run(db_session):  # type: ignore[no-untyped-def]
    """构造带敏感 error_json 的失败 run+job（原文永不进入响应）。"""
    from app.models.agent import AgentJob, AgentRun

    user, space = create_agent_fixture(db_session, name="诊断用户")
    agent_session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    now = timeutil.utcnow()
    run = AgentRun(
        session_id=agent_session.id,
        kind="assistant",
        status="failed",
        attempt=1,
        error_code="AGENT_PROVIDER_UNAVAILABLE",
        error_json={
            "component": "provider_gateway",
            "stack_location": "services/agent_provider.py:120",
            "reason": "contact admin@example.com or 13800138000",
            "api_key": "sk-live-abcdefghijklmnop1234",
            "prompt": "系统提示词原文",
            "message": "私人消息正文",
            "authorization": "Bearer abc.def.ghi",
        },
        policy_version="p1",
        tool_allowlist_json=[],
        created_at=now,
        updated_at=now,
    )
    db_session.add(run)
    db_session.flush()
    job = AgentJob(
        run_id=run.id,
        space_id=space.id,
        account_id=user.account.id,
        kind="assistant",
        status="failed",
        attempt=1,
        error_json={
            "error_code": "AGENT_PROVIDER_UNAVAILABLE",
            "component": "provider_gateway",
            "detail": "token=abcd1234 abcd1234abcd1234abcd1234",
        },
        policy_version="p1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.commit()
    return run, job, space


def test_agent_runs_whitelist_and_sanitized_error(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    run, _job, space = _seed_failed_run(db_session)

    response = admin_client.get(f"{V1}/agent/runs?space_id={space.id}", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more"}
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == {
        "id",
        "session_id",
        "job_id",
        "space_id",
        "account_id",
        "kind",
        "status",
        "attempt",
        "max_attempts",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested",
        "error_code",
        "error",
        "created_at",
        "updated_at",
        "settled_at",
    }
    assert item["error_code"] == "AGENT_PROVIDER_UNAVAILABLE"
    # error_json 原文键与敏感值永不出现；只有二次脱敏诊断
    assert set(item["error"]) == {"error_code", "component", "stack_location", "summary"}
    assert item["error"]["component"] == "provider_gateway"
    assert item["error"]["stack_location"] == "services/agent_provider.py:120"
    # 含 PII 的 reason / 黑名单键（api_key/prompt/message/authorization）→ 不可靠 → summary None
    assert item["error"]["summary"] is None
    serialized = str(body)
    assert "sk-live" not in serialized
    assert "admin@example.com" not in serialized
    assert "13800138000" not in serialized
    assert "系统提示词原文" not in serialized
    assert "私人消息正文" not in serialized
    assert "api_key" not in serialized

    # 状态与时间筛选（from 取过去时间点，覆盖 created_at >= 语义）
    assert (
        admin_client.get(f"{V1}/agent/runs?status=failed", headers=v1_headers).json()["total"] == 1
    )
    assert (
        admin_client.get(f"{V1}/agent/runs?status=queued", headers=v1_headers).json()["total"] == 0
    )
    assert (
        admin_client.get(f"{V1}/agent/runs?from=2020-01-01T00:00:00", headers=v1_headers).json()[
            "total"
        ]
        == 1
    )
    future = timeutil.utcnow() + timedelta(days=1)
    assert (
        admin_client.get(f"{V1}/agent/runs?from={future.isoformat()}", headers=v1_headers).json()[
            "total"
        ]
        == 0
    )


def test_agent_jobs_whitelist_and_sanitized_error(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    _run, job, space = _seed_failed_run(db_session)

    response = admin_client.get(f"{V1}/agent/jobs?space_id={space.id}", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == {
        "id",
        "run_id",
        "space_id",
        "account_id",
        "kind",
        "status",
        "attempt",
        "max_attempts",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested",
        "error",
        "created_at",
        "updated_at",
    }
    assert item["run_id"] == job.run_id
    assert set(item["error"]) == {"error_code", "component", "stack_location", "summary"}
    assert item["error"]["error_code"] == "AGENT_PROVIDER_UNAVAILABLE"
    # credential-like 长串 → 不可靠 → summary None
    assert item["error"]["summary"] is None
    assert "abcd1234abcd1234abcd1234" not in str(body)


# ---- 运营队列 / 通知 ----


def test_operations_queue_lists_anomalies_and_applications(
    admin_client: TestClient, db_session, client, v1_headers
) -> None:
    from conftest import login

    manager = create_user_with_pin(db_session, "队列管理", "909090")
    applicant = create_user_with_pin(db_session, "队列申请人", "909091")
    orphan = _space_without_admin(db_session, name="异常空间", owner_user_id=manager.id)
    lineage = seed_space_with_owner(db_session, manager.id, name="申请空间", kind="lineage")
    create_space_member(db_session, lineage.id, applicant.id)

    login_resp = login(client, "队列申请人", "909091")
    headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}
    submitted = client.post(
        "/api/spaces/manager-applications", json={"space_id": lineage.id}, headers=headers
    )
    assert submitted.status_code == 201, submitted.text

    response = admin_client.get(f"{V1}/operations/queue", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "has_more"}
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"space_anomaly", "manager_application"}
    for item in body["items"]:
        assert set(item) == {
            "kind",
            "status",
            "reference_id",
            "space_id",
            "space_name",
            "space_kind",
            "anomaly",
            "applicant_user_id",
            "applicant_name",
            "request_kind",
            "created_at",
        }
    anomaly_items = [i for i in body["items"] if i["kind"] == "space_anomaly"]
    assert anomaly_items[0]["reference_id"] == orphan.id
    assert anomaly_items[0]["anomaly"] == "no_active_admin"

    only_anomaly = admin_client.get(
        f"{V1}/operations/queue?kind=space_anomaly", headers=v1_headers
    ).json()
    assert {item["kind"] for item in only_anomaly["items"]} == {"space_anomaly"}
    pending = admin_client.get(
        f"{V1}/operations/queue?kind=manager_application&status=pending", headers=v1_headers
    ).json()
    assert [item["applicant_name"] for item in pending["items"]] == ["队列申请人"]


def test_operations_notifications_whitelist(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    from app.models.notification import Notification

    manager, space = create_agent_fixture(db_session, name="通知管理")
    now = timeutil.utcnow()
    db_session.add(
        Notification(
            kind="space_membership",
            space_id=space.id,
            recipient_account_id=manager.account.id,
            title="成员申请待处理",
            summary="有新成员等待确认",
            created_at=now,
            read_at=None,
        )
    )
    db_session.commit()

    response = admin_client.get(f"{V1}/operations/notifications", headers=v1_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == {
        "id",
        "kind",
        "space_id",
        "recipient_account_id",
        "actor_user_id",
        "title",
        "summary",
        "created_at",
        "read_at",
    }
    unread = admin_client.get(
        f"{V1}/operations/notifications?read=false&space_id={space.id}", headers=v1_headers
    ).json()
    assert unread["total"] == 1


# ---- 审计时间线查询 ----


def test_audit_access_query_is_paginated_and_whitelisted(
    admin_client: TestClient, db_session, v1_headers
) -> None:
    admin_client.get(f"{V1}/overview", headers=v1_headers)
    admin_client.get(f"{V1}/space-admins?search=无", headers=v1_headers)

    response = admin_client.get(f"{V1}/audit/access", headers=v1_headers)
    assert response.status_code == 200
    body = response.json()
    # 两次读各写一行审计；本查询自身的审计行在响应计数之后写入（下一页可见）
    assert body["total"] >= 2
    for item in body["items"]:
        assert set(item) == {
            "id",
            "system_admin_id",
            "session_id",
            "action",
            "target_type",
            "target_id",
            "endpoint",
            "filters",
            "result_count",
            "request_id",
            "ip",
            "created_at",
        }
    actions = [item["action"] for item in body["items"]]
    assert "read.overview" in actions
    assert "read.space_admins" in actions

    filtered = admin_client.get(f"{V1}/audit/access?target_type=space", headers=v1_headers)
    assert filtered.status_code == 200


def test_database_migration_0029_tables_exist(db_session) -> None:
    """迁移链升级后两张新表存在且审计行可持久化。"""
    rows = db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'admin_access%'")
    ).all()
    assert {row[0] for row in rows} == {"admin_access_sessions", "admin_access_audits"}


def _extract_error(response_json: dict[str, Any]) -> dict[str, Any]:
    error = response_json.get("error")
    assert isinstance(error, dict)
    return error
