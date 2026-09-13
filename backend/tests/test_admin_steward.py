"""Admin Steward 运维 API 回归（09-11；仅 admin listener :8002）。

覆盖 PRD AC-1（开关组合可解释状态）、AC-5（重跑幂等/监听隔离/字段白名单）
与 rerun 门禁矩阵（503/409/429/422/404/401）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.models.space import FamilySpace
from app.models.steward import StewardJob
from app.services import steward
from conftest import admin_session_headers, create_system_admin

RERUN_HEADERS = {"Idempotency-Key": "key-1"}


def _space(session, name: str) -> FamilySpace:
    from conftest import create_user_with_pin

    owner = create_user_with_pin(session, f"{name}-own", "123456")
    space = FamilySpace(name=name, kind="household", owner_id=owner.id, created_at=owner.created_at)
    session.add(space)
    session.commit()
    return space


@pytest.fixture()
def _admin_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


# ---- AC-1：状态对全部开关组合可解释 ----


def test_status_disabled_even_when_model_settings_on(
    admin_client: TestClient, db_session, _admin_headers, monkeypatch
) -> None:
    """只开模型设置不能显示引擎已运行（AC-1）。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", True)

    resp = admin_client.get("/admin-api/v1/steward/status", headers=_admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "disabled"
    explanations = {s["key"]: s for s in body["switches"]}
    assert explanations["core"]["enabled"] is False
    assert ("模型" in explanations["core"]["explanation"]) or (
        "核心" in explanations["core"]["explanation"]
    )
    assert explanations["model_assist_platform"]["enabled"] is True
    assert body["queue_counts"] == {"queued": 0, "leased": 0, "running": 0}


def test_status_paused_then_running(
    admin_client: TestClient, db_session, _admin_headers, monkeypatch
) -> None:
    """核心开 + worker 关 = paused；worker 开 = running（无失败时）。"""
    space = _space(db_session, "status-run")
    steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    resp = admin_client.get("/admin-api/v1/steward/status", headers=_admin_headers)
    assert resp.json()["state"] == "paused"
    assert resp.json()["queue_counts"]["queued"] == 1
    assert resp.json()["oldest_queued_seconds"] is not None

    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    resp = admin_client.get("/admin-api/v1/steward/status", headers=_admin_headers)
    assert resp.json()["state"] in ("running", "degraded")
    assert resp.json()["worker_heartbeat_at"] is None  # 尚无 worker 心跳（未执行）


def test_status_degraded_reports_recent_safe_error_codes(
    admin_client: TestClient, db_session, _admin_headers, monkeypatch
) -> None:  # noqa: E501 — 保持签名一致
    """最近失败作业暴露安全错误码（不含异常原文）。"""
    space = _space(db_session, "status-degraded")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w")
    assert grant is not None
    steward.settle_steward_job(
        db_session,
        grant,
        status="failed",
        error={"code": "BAD_INPUT"},
        error_code="BAD_INPUT",
    )
    db_session.commit()

    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    resp = admin_client.get("/admin-api/v1/steward/status", headers=_admin_headers)
    body = resp.json()
    assert body["state"] == "degraded"
    assert "BAD_INPUT" in body["recent_error_codes"]


# ---- AC-5：jobs 字段白名单 ----


def test_jobs_response_field_whitelist(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """作业响应只含白名单元数据字段（精确集合断言）。"""
    space = _space(db_session, "whitelist")
    steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    resp = admin_client.get("/admin-api/v1/steward/jobs", headers=_admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1 and body["page_size"] == 20
    assert len(body["items"]) == 1
    assert set(body["items"][0]) == {
        "job_id",
        "space_id",
        "cause",
        "status",
        "attempt",
        "available_at",
        "error_code",
    }
    assert set(body) == {"items", "page", "page_size"}


def test_jobs_page_size_cap_and_status_filter(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    space = _space(db_session, "pagesize")
    from app.utils import timeutil

    for _ in range(3):
        db_session.add(
            StewardJob(
                space_id=space.id,
                cause="source_fact",
                trigger_cursor=1,
                status="failed",
                attempt=1,
                max_attempts=1,
                policy_version=steward.POLICY_VERSION,
                checkpoint_json={},
                created_at=timeutil.utcnow(),
                updated_at=timeutil.utcnow(),
                settled_at=timeutil.utcnow(),
                error_code="STEWARD_EXECUTION_FAILED",
            )
        )
    db_session.commit()
    resp = admin_client.get(
        "/admin-api/v1/steward/jobs", headers=_admin_headers, params={"page_size": 1000}
    )
    assert resp.status_code == 422  # 超过 page_size 上界 fail-closed
    resp = admin_client.get(
        "/admin-api/v1/steward/jobs",
        headers=_admin_headers,
        params={"status": "not-a-status"},
    )
    assert resp.status_code == 422
    resp = admin_client.get(
        "/admin-api/v1/steward/jobs", headers=_admin_headers, params={"page_size": 2}
    )
    assert len(resp.json()["items"]) == 2


# ---- AC-5：rerun 幂等 / 门禁矩阵 ----


def _rerun(
    client: TestClient,
    headers: dict[str, str],
    space_id: int,
    *,
    key: str = "key-1",
    policy_version: str | None = None,
    reason: str = "补登停机期间遗漏的事件",
) -> object:
    body = {
        "reason": reason,
        "expected_policy_version": (
            policy_version if policy_version is not None else steward.POLICY_VERSION
        ),
    }
    return client.post(
        f"/admin-api/v1/steward/spaces/{space_id}/rerun",
        json=body,
        headers={**headers, "Idempotency-Key": key},
    )


def test_rerun_idempotent_same_key_one_job(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """重放相同重跑请求仅一个关联 job（AC-5）。"""
    space = _space(db_session, "idem-rerun")
    resp1 = _rerun(admin_client, _admin_headers, space.id, key="idem-42")
    assert resp1.status_code == 202, resp1.text
    body1 = resp1.json()
    assert body1 == {"job_id": body1["job_id"], "coalesced": False}

    resp2 = _rerun(admin_client, _admin_headers, space.id, key="idem-42")
    assert resp2.status_code == 202
    assert resp2.json() == {"job_id": body1["job_id"], "coalesced": True}

    reruns = list(
        db_session.scalars(
            select(StewardJob).where(
                StewardJob.space_id == space.id, StewardJob.cause == "admin_rerun"
            )
        )
    )
    assert len(reruns) == 1


def test_rerun_active_job_coalesces(admin_client: TestClient, db_session, _admin_headers) -> None:
    """已有活跃作业时重跑合并到该作业（每空间至多一个活跃，R4）。"""
    space = _space(db_session, "coalesce")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()
    resp = _rerun(admin_client, _admin_headers, space.id, key="coalesce-1")
    assert resp.status_code == 202
    assert resp.json() == {"job_id": job.id, "coalesced": True}


def test_rerun_links_previous_terminal_job(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """终态历史作业不复活：重跑创建关联新作业（retry_of_job_id）。"""
    space = _space(db_session, "link-rerun")
    old, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w")
    assert grant is not None
    steward.run_steward_job(db_session, grant)
    db_session.commit()

    resp = _rerun(admin_client, _admin_headers, space.id, key="link-1")
    assert resp.status_code == 202
    new_job_id = resp.json()["job_id"]
    assert new_job_id != old.id
    new_job = db_session.get(StewardJob, new_job_id)
    assert new_job.retry_of_job_id == old.id
    db_session.expire(old)
    assert old.status == "succeeded"  # 终态未复活


def test_rerun_policy_conflict_409(admin_client: TestClient, db_session, _admin_headers) -> None:
    space = _space(db_session, "policy-conflict")
    resp = _rerun(admin_client, _admin_headers, space.id, key="policy-1", policy_version="v-other")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STEWARD_POLICY_CONFLICT"


def test_rerun_cooldown_429(admin_client: TestClient, db_session, _admin_headers) -> None:
    space = _space(db_session, "cooldown")
    resp1 = _rerun(admin_client, _admin_headers, space.id, key="cd-1")
    assert resp1.status_code == 202
    resp2 = _rerun(admin_client, _admin_headers, space.id, key="cd-2")
    assert resp2.status_code == 429
    assert resp2.json()["error"]["code"] == "STEWARD_RERUN_TOO_FREQUENT"


def test_rerun_disabled_503(
    admin_client: TestClient, db_session, _admin_headers, monkeypatch
) -> None:
    """重跑受 STEWARD_ENABLED 门禁；读状态不受引擎门禁（design 合同）。"""
    space = _space(db_session, "disabled-rerun")
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    resp = _rerun(admin_client, _admin_headers, space.id, key="off-1")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "STEWARD_DISABLED"
    # 读端点不受影响
    status = admin_client.get("/admin-api/v1/steward/status", headers=_admin_headers)
    assert status.status_code == 200


def test_rerun_missing_key_422_and_unknown_space_404(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    space = _space(db_session, "missing-key")
    resp = admin_client.post(
        f"/admin-api/v1/steward/spaces/{space.id}/rerun",
        json={"reason": "r", "expected_policy_version": steward.POLICY_VERSION},
        headers=_admin_headers,
    )
    assert resp.status_code == 422
    unknown = _rerun(admin_client, _admin_headers, 999999, key="unknown-1")
    assert unknown.status_code == 404


def test_family_token_rejected_on_steward_routes(
    client: TestClient, admin_client: TestClient, db_session, _admin_headers
) -> None:
    """family token 无法调用 8002 steward 路由；8000 不暴露 steward 路由（AC-5）。"""
    from conftest import auth_header, create_user_with_pin, login

    user = create_user_with_pin(db_session, "family-steward", "123456")
    pair = login(client, user.name, "123456").json()
    family_headers = auth_header(pair)

    resp = admin_client.get("/admin-api/v1/steward/status", headers=family_headers)
    assert resp.status_code == 401
    resp = admin_client.get("/admin-api/v1/steward/jobs", headers=family_headers)
    assert resp.status_code == 401
    resp = admin_client.post(
        "/admin-api/v1/steward/spaces/1/rerun",
        json={"reason": "r", "expected_policy_version": "x"},
        headers={**family_headers, "Idempotency-Key": "fam-1"},
    )
    assert resp.status_code == 401

    # 8000 家庭 listener：steward 运维路由与未知路径同为普通 404
    assert client.get("/admin-api/v1/steward/status").status_code == 404
    assert client.get("/admin-api/v1/steward/jobs").status_code == 404


def test_rerun_reason_never_persisted(admin_client: TestClient, db_session, _admin_headers) -> None:
    """审计/作业只存安全理由分类，reason 原文永不落库（R5）。"""

    space = _space(db_session, "reason-safe")
    secret_reason = "机密理由：包含内部人员姓名 张三 与联系方式 13800000000"
    resp = _rerun(admin_client, _admin_headers, space.id, key="reason-1", reason=secret_reason)
    assert resp.status_code == 202
    db_session.commit()
    from app.models.admin_access import AdminAccessAudit

    audits = list(db_session.scalars(select(AdminAccessAudit)))
    assert audits
    for audit in audits:
        assert secret_reason not in str(audit.filters_json)
        assert "张三" not in str(audit.filters_json)
    jobs = list(db_session.scalars(select(StewardJob)))
    for job in jobs:
        assert secret_reason not in str(job.checkpoint_json)
