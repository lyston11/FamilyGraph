"""Admin 延迟观测端点回归（09-13-agent-latency-tuning；仅 admin listener :8002）。

覆盖：分 kind 统计与 nearest-rank 分位数、timeout 单列计数、窗口过滤、
assistant run 总时长分位数、只读口径（无业务内容字段）与审计落库。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import config
from app.models.agent import AgentSession
from app.models.steward import StewardModelCall
from app.services import agent_queue, steward
from app.utils.timeutil import utcnow
from conftest import admin_session_headers, create_agent_fixture, create_system_admin


@pytest.fixture()
def _admin_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _agent_session(db_session, *, account_id: int, space_id: int) -> AgentSession:
    """裸 SQL 建会话行：0040 起 updated_at NOT NULL 且模型未映射该列，
    ORM 插入会违反约束；本测试对 conftest 助手的修复合入前后都稳健。"""
    now = utcnow()
    sid = db_session.execute(
        sa.text(
            "INSERT INTO agent_sessions "
            "(account_id, space_id, agent_kind, term_usage_consent, created_at, updated_at) "
            "VALUES (:account_id, :space_id, 'assistant', 0, :now, :now) RETURNING id"
        ),
        {"account_id": account_id, "space_id": space_id, "now": now},
    ).scalar_one()
    # enqueue_run 要求无 pending 写的干净会话，这里先落库再取行。
    db_session.commit()
    row = db_session.get(AgentSession, int(sid))
    assert row is not None
    return row


def _model_call(
    db_session,
    *,
    job_id: int,
    space_id: int,
    kind: str,
    status: str,
    error_code: str | None,
    latency_ms: int,
    age_days: float = 0.0,
    seq: int = 1,
) -> None:
    db_session.add(
        StewardModelCall(
            space_id=space_id,
            job_id=job_id,
            policy_version=steward.POLICY_VERSION,
            assist_kind=kind,
            prompt_digest=f"digest-{kind}-{latency_ms}",
            prompt_chars=128,
            status=status,
            error_code=error_code,
            latency_ms=latency_ms,
            seq=seq,
            created_at=utcnow() - timedelta(days=age_days),
        )
    )


def test_latency_metrics_shape_and_percentiles(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    user, space = create_agent_fixture(db_session, name="lat-shape")
    # 一个会话同时只允许一个 active run（部分唯一索引），两条 run 分属两个会话。
    session_row = _agent_session(db_session, account_id=user.id, space_id=space.id)
    run_a = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    session_row_b = _agent_session(db_session, account_id=user.id, space_id=space.id)
    run_b = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row_b,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.flush()

    now = utcnow()
    run_a.settled_at = now
    run_b.settled_at = now
    run_a.status = "succeeded"
    run_b.status = "succeeded"
    run_a.created_at = now - timedelta(seconds=10)
    run_b.created_at = now - timedelta(seconds=50)
    _model_call(
        db_session,
        job_id=job.id,
        space_id=space.id,
        kind="candidate",
        status="succeeded",
        error_code=None,
        latency_ms=20000,
        seq=1,
    )
    _model_call(
        db_session,
        job_id=job.id,
        space_id=space.id,
        kind="candidate",
        status="succeeded",
        error_code=None,
        latency_ms=30000,
        seq=2,
    )
    _model_call(
        db_session,
        job_id=job.id,
        space_id=space.id,
        kind="candidate",
        status="unknown",
        error_code="timeout",
        latency_ms=30500,
        seq=3,
    )
    # 窗口外样本：7 天窗口不应计入
    _model_call(
        db_session,
        job_id=job.id,
        space_id=space.id,
        kind="candidate",
        status="succeeded",
        error_code=None,
        latency_ms=5000,
        age_days=8.0,
        seq=4,
    )
    db_session.commit()

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "generated_at",
        "window_days",
        "steward_assist",
        "assistant_runs",
        "runs_by_status",
        "notes",
    }
    candidate = body["steward_assist"]["candidate"]
    assert candidate["total"] == 3
    assert candidate["succeeded"] == 2
    assert candidate["timeout"] == 1
    assert candidate["other"] == 0
    # nearest-rank：[20000, 30000, 30500] → p50=第2名, p95=第3名
    assert candidate["latency_p50_ms"] == 30000
    assert candidate["latency_p95_ms"] == 30500
    assert candidate["latency_max_ms"] == 30500
    runs = body["assistant_runs"]
    assert runs["n"] == 2
    assert runs["total_p50_s"] == 10
    assert runs["total_p95_s"] == 50
    assert body["runs_by_status"] == {"succeeded": 2}
    assert body["notes"]


def test_latency_metrics_window_filter_and_validation(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    _user, space = create_agent_fixture(db_session, name="lat-window")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.flush()
    _model_call(
        db_session,
        job_id=job.id,
        space_id=space.id,
        kind="candidate",
        status="succeeded",
        error_code=None,
        latency_ms=21000,
        age_days=8.0,
    )
    db_session.commit()

    resp = admin_client.get(
        "/admin-api/v1/agent/latency", headers=_admin_headers, params={"days": 1}
    )
    assert resp.status_code == 200
    body = resp.json()
    # 8 天前的样本不在 1 天窗口内：无 candidate 键
    assert "candidate" not in body["steward_assist"]

    resp = admin_client.get(
        "/admin-api/v1/agent/latency", headers=_admin_headers, params={"days": 0}
    )
    assert resp.status_code == 422


def test_latency_metrics_requires_admin(admin_client: TestClient) -> None:
    resp = admin_client.get("/admin-api/v1/agent/latency")
    assert resp.status_code in (401, 403)


def test_latency_metrics_records_audit(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    from sqlalchemy import select

    from app.models.admin_access import AdminAccessAudit

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200
    rows = (
        db_session.execute(
            select(AdminAccessAudit).where(AdminAccessAudit.action == "agent_latency.read")
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_timeout_budget_config_guard() -> None:
    """config 对超时预算的上下界校验保持 fail-closed（0.1..300）。"""
    assert 0.1 <= config.STEWARD_ASSIST_TIMEOUT_SECONDS <= 300
