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
    events: list[tuple[str, float, dict | None, dict | None]],
    settle_after_s: float,
    first_lease_after_s: float | None = None,
):
    """建一个已结算 run，并按给定偏移（秒）写入持久事件。

    偏移相对 run.created_at；第一个事件通常是 message.user_added（seq 0）。
    第四个元组项是该事件的 sidecar 源计时（``timing_json``），None 表示历史行。
    用裸 SQL 写事件行：本测试只需协议列，不经过服务层校验，也不产生模型请求。
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
    if first_lease_after_s is not None:
        db_session.execute(
            sa.text("UPDATE agent_runs SET first_leased_at = :at WHERE id = :rid"),
            {"at": base + timedelta(seconds=first_lease_after_s), "rid": run.id},
        )
    for seq, (event_type, offset, payload, timing) in enumerate(events):
        db_session.execute(
            sa.text(
                "INSERT INTO agent_run_events "
                "(run_id, seq, type, public_payload, timing_json, created_at) "
                "VALUES (:rid, :seq, :type, :payload, :timing, :at)"
            ),
            {
                "rid": run.id,
                "seq": seq,
                "type": event_type,
                "payload": json.dumps(payload or {}),
                "timing": None if timing is None else json.dumps(timing),
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

    构造一个两轮 run：入队→首次取得执行权 9s，准备 2s，第 1 轮生成 53s
    （后接一次工具），第 2 轮生成 40s，最后事件→结算 0.5s。另建一个未取得
    执行权即结束的 run（无 run.started），它只能计入 runs_without_start。
    """
    user, space = create_agent_fixture(db_session, name="lat-phase")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            ("run.started", 11.0, None, None),
            ("turn.started", 11.0, None, None),
            ("message.assistant_added", 64.0, {"role": "assistant", "text": "a"}, None),
            ("tool.execution.started", 64.0, {"tool_call_id": "t1", "tool_name": "x"}, None),
            ("tool.execution.completed", 65.0, {"tool_call_id": "t1", "tool_name": "x"}, None),
            ("turn.completed", 65.0, None, None),
            ("turn.started", 65.0, None, None),
            ("message.assistant_added", 105.0, {"role": "assistant", "text": "b"}, None),
            ("turn.completed", 105.0, None, None),
        ],
        settle_after_s=105.5,
        first_lease_after_s=9.0,
    )
    # 未取得执行权即结束：只有入队事件，没有 run.started。
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[("message.user_added", 0.0, None, None)],
        settle_after_s=1.0,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]

    assert phases["runs"] == 2
    assert phases["runs_without_first_lease"] == 1
    assert phases["runs_without_start"] == 1
    # 入队 → 首次取得执行权（不可变列，不是续租字段）
    assert phases["queue_wait"]["n"] == 1
    assert phases["queue_wait"]["p50_ms"] == 9_000
    assert phases["queue_wait"]["basis"] == "source_clock"
    # 取得执行权 → run.started（context + session 准备）：不得归入排队
    assert phases["prepare"]["n"] == 1
    assert phases["prepare"]["p50_ms"] == 2_000
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
    # 历史行无源计时：如实标注样本来源，不冒充精确执行耗时
    assert phases["model_turn"]["basis"] == "persisted_interval"
    assert phases["model_turn"]["native_n"] == 0
    assert phases["model_turn"]["derived_n"] == 2
    assert phases["settle"]["basis"] == "source_clock"


def test_latency_metrics_prefers_source_clock_over_flush_batching(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC1：批量 flush 不得把 125ms 工具工作显示为约 1ms。

    同一批入库使工具起止事件只相差 1.3ms；producer 源计时给出真实 125ms。
    报告必须取源计时，并如实标注 basis/native_n，不把持久间隔当执行耗时。
    """
    user, space = create_agent_fixture(db_session, name="lat-source")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            (
                "run.started",
                1.0,
                None,
                {"source": "sidecar-v1", "duration_ms": 4_500},
            ),
            ("turn.started", 1.0, None, None),
            (
                "message.assistant_added",
                21.0,
                {"role": "assistant", "text": "a"},
                {"source": "sidecar-v1", "duration_ms": 20_000},
            ),
            ("tool.execution.started", 21.0, {"tool_call_id": "t1", "tool_name": "x"}, None),
            (
                "tool.execution.completed",
                21.0013,
                {"tool_call_id": "t1", "tool_name": "x"},
                {"source": "sidecar-v1", "duration_ms": 125},
            ),
            ("turn.completed", 21.0013, None, None),
        ],
        settle_after_s=21.5,
        first_lease_after_s=0.5,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]

    # 工具：源计时 125ms，不是 1.3ms 的持久间隔
    assert phases["tool_call"]["n"] == 1
    assert phases["tool_call"]["p50_ms"] == 125
    assert phases["tool_call"]["basis"] == "source_clock"
    assert phases["tool_call"]["native_n"] == 1
    assert phases["tool_call"]["derived_n"] == 0
    # 准备阶段：源计时优先
    assert phases["prepare"]["n"] == 1
    assert phases["prepare"]["p50_ms"] == 4_500
    assert phases["prepare"]["basis"] == "source_clock"
    # 该轮生成：源计时优先
    assert phases["model_turn"]["n"] == 1
    assert phases["model_turn"]["p50_ms"] == 20_000
    assert phases["model_turn"]["native_n"] == 1


def test_latency_metrics_separates_compaction_from_generation(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC2：轮内压缩是 model_turn 的子成分，必须可单独归属。

    两轮：第 1 轮内有 8s 压缩（该轮 model_turn=20s），第 2 轮无压缩
    （model_turn=30s）。若不单列 compaction，就会把 8s 摘要请求当成纯生成。
    """
    user, space = create_agent_fixture(db_session, name="lat-compaction")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
            (
                "message.assistant_added",
                21.0,
                {"role": "assistant", "text": "a"},
                {"source": "sidecar-v1", "duration_ms": 20_000, "compaction_ms": 8_000},
            ),
            ("turn.completed", 21.0, None, None),
            ("turn.started", 21.0, None, None),
            (
                "message.assistant_added",
                51.0,
                {"role": "assistant", "text": "b"},
                {"source": "sidecar-v1", "duration_ms": 30_000},
            ),
            ("turn.completed", 51.0, None, None),
        ],
        settle_after_s=51.5,
        first_lease_after_s=0.5,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]

    # 两轮各一个 model_turn 样本，压缩是其中一轮的子成分。
    assert phases["model_turn"]["n"] == 2
    # nearest-rank p50：ceil(0.5*2)=1 名 → 较小值 20s，最大值是 30s。
    assert phases["model_turn"]["p50_ms"] == 20_000
    assert phases["model_turn"]["max_ms"] == 30_000
    # 只有确实压缩过的那一轮贡献样本（无压缩的轮不得用 0 填充）。
    assert phases["compaction"]["n"] == 1
    assert phases["compaction"]["p50_ms"] == 8_000
    assert phases["compaction"]["basis"] == "source_clock"


def test_latency_metrics_compaction_empty_without_source_timing(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """历史行无源计时：压缩必须报 n=0，不能从持久间隔反推。"""
    user, space = create_agent_fixture(db_session, name="lat-compaction-legacy")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
            ("message.assistant_added", 21.0, {"role": "assistant", "text": "a"}, None),
            ("turn.completed", 21.0, None, None),
        ],
        settle_after_s=21.5,
        first_lease_after_s=0.5,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    phases = resp.json()["assistant_phases"]
    assert phases["compaction"]["n"] == 0
    assert phases["compaction"]["p50_ms"] is None
    assert phases["compaction"]["basis"] == "none"


def test_latency_metrics_counts_runs_without_events_in_denominator(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC3：零事件 run 不能从分母消失（不从事件集合反推 run 集合）。"""
    user, space = create_agent_fixture(db_session, name="lat-noevent")
    session_row = _agent_session(db_session, account_id=user.id, space_id=space.id)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    run.status = "failed"
    run.settled_at = utcnow()
    db_session.commit()

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]
    assert phases["runs"] == 1
    assert phases["runs_without_events"] == 1
    assert phases["runs_without_first_lease"] == 1
    # 无 run.started 的 run 不得被当成“无事件以外的失败”或伪造分段
    assert phases["queue_wait"]["n"] == 0
    assert phases["queue_wait"]["basis"] == "none"


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
            ("message.user_added", 0.0, None, None),
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
            ("message.assistant_added", 21.0, {"role": "assistant", "text": "a"}, None),
            ("turn.completed", 21.0, None, None),
            ("turn.started", 21.0, None, None),
            ("message.assistant_added", 41.0, {"role": "assistant", "text": "b"}, None),
            ("turn.completed", 41.0, None, None),
        ],
        settle_after_s=41.5,
        first_lease_after_s=0.5,
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
    retry = phases["provider_retry"]
    # 重试开销单列：仅第 1 轮的失败段，9s − 6s = 3s（下界）。
    assert retry["retry_segments"] == 1
    assert retry["duration_lower_bound"]["n"] == 1
    assert retry["duration_lower_bound"]["p50_ms"] == 3_000
    # 失败尝试数与影响范围无歧义：两次 502，均属同一 run。
    assert retry["failed_attempts"] == 2
    assert retry["runs_with_failure"] == 1
    assert retry["runs_with_retry"] == 1
    # 段长为 2，不是“单次失败后成功”。
    assert retry["unmeasured_retries"] == 0


def test_latency_metrics_counts_single_failure_then_success(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC3：一次失败后成功确实发生了重试，但窗口长度为 0。

    旧实现把它计成 0 时长样本，读者会误读为“无重试”。现在单列
    ``unmeasured_retries``，时长下界不包含它。
    """
    user, space = create_agent_fixture(db_session, name="lat-single-retry")
    run = _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
            ("message.assistant_added", 21.0, {"role": "assistant", "text": "a"}, None),
            ("turn.completed", 21.0, None, None),
        ],
        settle_after_s=21.5,
        first_lease_after_s=0.5,
    )
    _egress(
        db_session,
        run_id=run.id,
        offsets_s=[("failed", 5.0, 503), ("succeeded", 21.0, 200)],
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    retry = resp.json()["assistant_phases"]["provider_retry"]
    assert retry["failed_attempts"] == 1
    assert retry["unmeasured_retries"] == 1
    assert retry["retry_segments"] == 0
    assert retry["duration_lower_bound"]["n"] == 0
    assert retry["runs_with_failure"] == 1


def _egress_classified(
    db_session,
    *,
    run_id: int,
    rows: list[tuple[str, float, int | None, str | None, bool | None]],
) -> None:
    """写带 E 安全分类的 egress 审计行（error_class/retryable）。"""
    base = utcnow() - timedelta(seconds=600)
    for status, offset, upstream_status, error_class, retryable in rows:
        detail: dict[str, object] = {
            "provider_id": 1,
            "status": status,
            "upstream_status": upstream_status,
            "bytes_read": 0,
        }
        if error_class is not None:
            detail["error_class"] = error_class
        if retryable is not None:
            detail["retryable"] = retryable
        db_session.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(actor_id, action, target_id, ip, detail_json, created_at) "
                "VALUES (NULL, 'agent_provider_egress', :rid, NULL, :detail, :at)"
            ),
            {
                "rid": run_id,
                "detail": json.dumps(detail),
                "at": base + timedelta(seconds=offset),
            },
        )
    db_session.commit()


def test_latency_metrics_excludes_non_retryable_failures(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """E-AC5：D 能消费 E 的新审计而不造出虚假重试次数。

    同一 run 内：一次可重试的 502、一次永久拒绝（retryable=false）、
    一次取消终态。只有可重试的 502 属于 provider_retry；旧语义会把三者
    当成一个长度 3 的失败段并声称有重试。
    """
    user, space = create_agent_fixture(db_session, name="lat-nonretryable")
    run = _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
        ],
        settle_after_s=30.0,
        first_lease_after_s=0.5,
    )
    _egress_classified(
        db_session,
        run_id=run.id,
        rows=[
            ("failed", 5.0, 502, "upstream_transient", True),
            ("failed", 9.0, 401, "upstream_rejected", False),
            ("failed", 12.0, None, "run_cancelled", False),
        ],
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    retry = resp.json()["assistant_phases"]["provider_retry"]
    # 只有可重试的那次进入重试统计（单次失败→无可测窗口）。
    assert retry["failed_attempts"] == 1
    assert retry["runs_with_failure"] == 1
    assert retry["runs_with_retry"] == 0
    assert retry["unmeasured_retries"] == 1
    assert retry["retry_segments"] == 0
    assert retry["exhausted_segments"] == 0


def test_latency_metrics_counts_exhausted_retry_streak(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC3：失败耗尽的尾部段不得因“没有后续成功”而丢失。"""
    user, space = create_agent_fixture(db_session, name="lat-exhausted")
    run = _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
        ],
        settle_after_s=30.0,
        first_lease_after_s=0.5,
    )
    _egress(
        db_session,
        run_id=run.id,
        offsets_s=[
            ("failed", 5.0, 502),
            ("failed", 9.0, 502),
            ("failed", 15.0, 502),
        ],
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    retry = resp.json()["assistant_phases"]["provider_retry"]
    assert retry["failed_attempts"] == 3
    assert retry["exhausted_segments"] == 1
    assert retry["retry_segments"] == 1
    # 尾部段仍有可测窗口：15s − 5s = 10s（下界）。
    assert retry["duration_lower_bound"]["p50_ms"] == 10_000


def test_latency_metrics_rejects_negative_timing(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """D-AC4：畸形 timing（负值）按 unknown 处理，不进入分布也不报错。"""
    user, space = create_agent_fixture(db_session, name="lat-bad-timing")
    _run_with_events(
        db_session,
        user=user,
        space=space,
        events=[
            ("message.user_added", 0.0, None, None),
            ("run.started", 1.0, None, None),
            ("turn.started", 1.0, None, None),
            (
                "message.assistant_added",
                21.0,
                {"role": "assistant", "text": "a"},
                {"source": "sidecar-v1", "duration_ms": -5},
            ),
            ("turn.completed", 21.0, None, None),
        ],
        settle_after_s=21.5,
        first_lease_after_s=0.5,
    )

    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200, resp.text
    phases = resp.json()["assistant_phases"]
    # 负值不是时长：回退为持久间隔并标注来源，不伪造 0 也不 500。
    assert phases["model_turn"]["n"] == 1
    assert phases["model_turn"]["native_n"] == 0
    assert phases["model_turn"]["p50_ms"] == 20_000


def test_latency_metrics_phases_empty_without_events(
    admin_client: TestClient, db_session, _admin_headers
) -> None:
    """无事件样本时 n=0 且分位为 null（不零填充、不伪造分段）。"""
    resp = admin_client.get("/admin-api/v1/agent/latency", headers=_admin_headers)
    assert resp.status_code == 200
    phases = resp.json()["assistant_phases"]
    assert phases["runs"] == 0
    assert phases["queue_wait"]["n"] == 0
    assert phases["queue_wait"]["p50_ms"] is None
    assert phases["queue_wait"]["basis"] == "none"
    assert phases["first_text"]["n"] == 0
    assert phases["model_turn"]["n"] == 0
    assert phases["provider_retry"]["failed_attempts"] == 0
    assert phases["provider_retry"]["duration_lower_bound"]["n"] == 0


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
