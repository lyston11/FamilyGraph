"""Admin 延迟观测端点回归（09-13-agent-latency-tuning；仅 admin listener :8002）。

覆盖：分 kind 统计与 nearest-rank 分位数、timeout 单列计数、窗口过滤、
assistant run 总时长分位数、只读口径（无业务内容字段）与审计落库。
"""

from __future__ import annotations

import json
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
        "assistant_phases",
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


def _run_with_events(
    db_session,
    *,
    user,
    space,
    events: list[tuple[str, float, dict | None]],
    settle_after_s: float,
):
    """建一个已结算 run，并按给定偏移（秒）写入持久事件。

    偏移相对 run.created_at；第一个事件通常是 message.user_added（seq 0）。
    用裸 SQL 写事件行：本测试只需 (run_id, seq, type, created_at, public_payload)，
    不经过服务层协议校验，也不产生任何模型请求。
    """
    session_row = _agent_session(db_session, account_id=user.id, space_id=space.id)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()

    base = utcnow() - timedelta(seconds=600)
    db_session.execute(
        sa.text("UPDATE agent_runs SET created_at = :base WHERE id = :rid"),
        {"base": base, "rid": run.id},
    )
    for seq, (event_type, offset, payload) in enumerate(events):
        db_session.execute(
            sa.text(
                "INSERT INTO agent_run_events "
                "(run_id, seq, type, public_payload, created_at) "
                "VALUES (:rid, :seq, :type, :payload, :at)"
            ),
            {
                "rid": run.id,
                "seq": seq,
                "type": event_type,
                "payload": json.dumps(payload or {}),
                "at": base + timedelta(seconds=offset),
            },
        )
    db_session.execute(
        sa.text("UPDATE agent_runs SET status = 'succeeded', settled_at = :at WHERE id = :rid"),
        {"at": base + timedelta(seconds=settle_after_s), "rid": run.id},
    )
    db_session.commit()
    return run


def _egress(
    db_session,
    *,
    run_id: int,
    offsets_s: list[tuple[str, float, int | None]],
) -> None:
    """按偏移写 agent_provider_egress 审计行（target_id 即 run_id）。

    与 _run_with_events 共用同一个 base（utcnow()-600s）以便时间对齐。
    """
    base = utcnow() - timedelta(seconds=600)
    for status, offset, upstream_status in offsets_s:
        db_session.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(actor_id, action, target_id, ip, detail_json, created_at) "
                "VALUES (NULL, 'agent_provider_egress', :rid, NULL, :detail, :at)"
            ),
            {
                "rid": run_id,
                "detail": json.dumps(
                    {
                        "provider_id": 1,
                        "status": status,
                        "upstream_status": upstream_status,
                        "bytes_read": 0,
                    }
                ),
                "at": base + timedelta(seconds=offset),
            },
        )
    db_session.commit()


def test_latency_metrics_decomposes_assistant_phases(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """分段必须由持久事件时间戳推导，且不把多轮/工具/无正文轮算错。

    构造一个两轮 run：入队→取得执行权 11s，第 1 轮生成 53s（后接一次工具），
    第 2 轮生成 40s，最后事件→结算 0.5s。另建一个未取得执行权即结束的 run
    （无 run.started），它只能计入 runs_without_start。
    """
    user, space = create_agent_fixture(db_session, name="lat-phase")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None),
            ("run.started", 11.0, None),
            ("turn.started", 11.0, None),
            ("message.assistant_added", 64.0, {"role": "assistant", "text": "a"}),
            ("tool.execution.started", 64.0, {"tool_call_id": "t1", "tool_name": "x"}),
            ("tool.execution.completed", 65.0, {"tool_call_id": "t1", "tool_name": "x"}),
            ("turn.completed", 65.0, None),
            ("turn.started", 65.0, None),
            ("message.assistant_added", 105.0, {"role": "assistant", "text": "b"}),
            ("turn.completed", 105.0, None),
        ],
        settle_after_s=105.5,
    )
    # 未取得执行权即结束：只有入队事件，没有 run.started。
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[("message.user_added", 0.0, None)],
        settle_after_s=1.0,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]

    assert phases["runs"] == 2
    assert phases["runs_without_start"] == 1
    # 入队 → run.started（取得执行权）
    assert phases["queue_wait"]["n"] == 1
    assert phases["queue_wait"]["p50_ms"] == 11_000
    # 取得执行权 → 首个 assistant 正文（每 run 一个样本，不是每轮）
    assert phases["first_text"]["n"] == 1
    assert phases["first_text"]["p50_ms"] == 53_000
    # 每轮模型生成：两轮各一个样本，不是把两轮合成 94s 一笔
    assert phases["model_turn"]["n"] == 2
    assert phases["model_turn"]["p50_ms"] == 40_000
    assert phases["model_turn"]["max_ms"] == 53_000
    # 工具按 tool_call_id 配对
    assert phases["tool_call"]["n"] == 1
    assert phases["tool_call"]["p50_ms"] == 1_000
    # 最后非终态事件 → 终态（两个 run 各一个样本：500ms 与 1000ms）
    assert phases["settle"]["n"] == 2
    assert phases["settle"]["p50_ms"] == 500
    assert phases["settle"]["max_ms"] == 1_000


def test_latency_metrics_separates_provider_retry_from_generation(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """A-02：上游重试不得被当作单次模型推理。

    第 1 轮 20s 内含一次重试：失败@6s、失败@9s、成功@21s。连续失败段内
    末次失败−首次失败 = 3s 计入 provider_retry（下界；首次失败自身耗时不可知）。
    第 2 轮无重试，不得被计入。
    """
    user, space = create_agent_fixture(db_session, name="lat-retry")
    run = _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None),
            ("run.started", 1.0, None),
            ("turn.started", 1.0, None),
            ("message.assistant_added", 21.0, {"role": "assistant", "text": "a"}),
            ("turn.completed", 21.0, None),
            ("turn.started", 21.0, None),
            ("message.assistant_added", 41.0, {"role": "assistant", "text": "b"}),
            ("turn.completed", 41.0, None),
        ],
        settle_after_s=41.5,
    )
    _egress(
        db_session,
        run_id=run.id,
        offsets_s=[
            ("failed", 6.0, 502),
            ("failed", 9.0, 502),
            ("succeeded", 21.0, 200),
            ("succeeded", 41.0, 200),
        ],
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]

    # model_turn 仍含重试（口径如实），两轮各一个样本。
    assert phases["model_turn"]["n"] == 2
    assert phases["model_turn"]["p50_ms"] == 20_000
    # 重试开销单列：仅第 1 轮的失败段，9s − 6s = 3s（下界）。
    assert phases["provider_retry"]["n"] == 1
    assert phases["provider_retry"]["p50_ms"] == 3_000
    # 失败尝试数无歧义：两次 502。
    assert phases["provider_failed_attempts"] == 2
    assert phases["runs_with_provider_retry"] == 1


def test_latency_metrics_phases_empty_without_events(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """无事件样本时 n=0 且分位为 null（不零填充、不伪造分段）。"""
    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200
    phases = resp.json()["assistant_phases"]
    assert phases["runs"] == 0
    assert phases["queue_wait"] == {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    assert phases["first_text"]["n"] == 0
    assert phases["model_turn"]["n"] == 0


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
