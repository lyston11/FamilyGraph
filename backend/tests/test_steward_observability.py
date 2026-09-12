"""Steward 可观测指标与告警回归（09-11 R2/AC-2/AC-4）。

- 指标来自真实 DB 状态（job/batch/call/PFV/卡片行）；
- worker 停止但 HTTP 存活（连续两个扫描窗口无进展）→ state=degraded + queue_stalled；
- 配置关闭 ≠ 故障：disabled/paused 不产生 stall 告警；
- 积压阈值可配置（STEWARD_ALERT_QUEUE_SECONDS）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import admin_session_headers, create_system_admin
from fastapi.testclient import TestClient
from test_steward import _space

from app import config
from app.models.steward import StewardAssistBatch, StewardJob, StewardModelCall
from app.services import steward
from app.utils import timeutil


@pytest.fixture()
def _admin_headers(admin_client: TestClient, db_session) -> dict[str, str]:
    create_system_admin(db_session)
    return admin_session_headers(admin_client)


def _status(admin_client: TestClient, headers: dict[str, str]) -> dict:
    resp = admin_client.get("/admin-api/v1/steward/status", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _enqueue_backdated(db_session, space, *, age_seconds: int, settled_age_seconds: int | None):
    """造一个 age 秒前入队的 queued 作业；可选把最近一次结算回拨到更早。"""
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    past = timeutil.utcnow() - timedelta(seconds=age_seconds)
    db_session.execute(
        StewardJob.__table__.update()
        .where(StewardJob.id == job.id)
        .values(created_at=past, updated_at=past)
    )
    if settled_age_seconds is not None:
        db_session.execute(
            StewardJob.__table__.update()
            .where(StewardJob.id == job.id)
            .values(
                status="failed",
                error_code="STEWARD_EXECUTION_FAILED",
                settled_at=timeutil.utcnow() - timedelta(seconds=settled_age_seconds),
            )
        )
    db_session.commit()
    return job


def test_metrics_counts_from_real_rows(
    admin_client, db_session, _admin_headers, monkeypatch
) -> None:
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    space = _space(db_session, "obs-metrics")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.add(
        StewardModelCall(
            space_id=space.id,
            job_id=job.id,
            policy_version=steward.POLICY_VERSION,
            assist_kind="candidate",
            prompt_digest="0" * 64,
            prompt_chars=10,
            status="degraded",
            error_code="timeout",
            created_at=timeutil.utcnow(),
            billed_tokens=120,
            reserved_input_tokens=50,
            reserved_output_tokens=20,
        )
    )
    db_session.commit()

    body = _status(admin_client, _admin_headers)
    m = body["metrics"]
    assert m["core_queue_depth"] == 1
    assert m["oldest_queued_age_seconds"] is not None
    assert m["assist_degraded"] == 1
    assert m["budget_consumed_tokens"] == 120
    assert m["cards_created"] >= 0
    # core succeeded 与 assist failed 可分别观察：无 failed core，但辅助 degraded
    assert m["core_failed"] == 0


def test_stalled_worker_is_degraded_not_healthy(
    admin_client, db_session, _admin_headers, monkeypatch
) -> None:
    """worker 启用但连续两个扫描窗口（2×间隔）无结算进展 → degraded + 告警。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_SCAN_INTERVAL_SECONDS", 60)
    space = _space(db_session, "obs-stalled")
    # 作业 10 分钟前入队；此后没有任何结算（最近结算同样 10 分钟前）
    _enqueue_backdated(db_session, space, age_seconds=600, settled_age_seconds=None)

    body = _status(admin_client, _admin_headers)
    assert body["state"] == "degraded"
    codes = [a["code"] for a in body["alerts"]]
    assert "queue_stalled" in codes and "queue_backlog" in codes


def test_config_off_is_not_fault(admin_client, db_session, _admin_headers, monkeypatch) -> None:
    """引擎关闭：积压行仍在也不产生 stall 告警，state=disabled；worker 关 = paused 不降级。"""
    space = _space(db_session, "obs-off")
    _enqueue_backdated(db_session, space, age_seconds=9999, settled_age_seconds=None)

    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    body = _status(admin_client, _admin_headers)
    assert body["state"] == "disabled"
    assert body["alerts"] == []

    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    body = _status(admin_client, _admin_headers)
    assert body["state"] == "paused"  # 积压可见但不升级 degraded（config 区分的故障形态）
    assert "queue_stalled" not in [a["code"] for a in body["alerts"]]


def test_alert_threshold_configurable(
    admin_client, db_session, _admin_headers, monkeypatch
) -> None:
    """默认阈值 = max(2×扫描间隔, 60)；配置显式阈值后按配置触发。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_SCAN_INTERVAL_SECONDS", 300)
    space = _space(db_session, "obs-threshold")
    _enqueue_backdated(db_session, space, age_seconds=120, settled_age_seconds=None)

    monkeypatch.setattr(config, "STEWARD_ALERT_QUEUE_SECONDS", 0)
    body = _status(admin_client, _admin_headers)
    assert "queue_backlog" not in [a["code"] for a in body["alerts"]]

    monkeypatch.setattr(config, "STEWARD_ALERT_QUEUE_SECONDS", 60)
    body = _status(admin_client, _admin_headers)
    assert "queue_backlog" in [a["code"] for a in body["alerts"]]
    detail = next(a for a in body["alerts"] if a["code"] == "queue_backlog")["detail"]
    assert detail["threshold_seconds"] == 60


def test_assist_batch_unknown_counts(admin_client, db_session, _admin_headers, monkeypatch) -> None:
    """unknown（无法证明上游未处理）辅助调用单独可观察。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    space = _space(db_session, "obs-unknown")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.add(
        StewardModelCall(
            space_id=space.id,
            job_id=job.id,
            policy_version=steward.POLICY_VERSION,
            assist_kind="ranking",
            prompt_digest="1" * 64,
            prompt_chars=10,
            status="unknown",
            error_code="timeout",
            created_at=timeutil.utcnow(),
            billed_tokens=30,
        )
    )
    db_session.commit()
    body = _status(admin_client, _admin_headers)
    assert body["metrics"]["assist_unknown"] == 1
    _ = StewardAssistBatch  # 保留模型导入引用
