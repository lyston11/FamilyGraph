"""Steward 路径日志/审计脱敏回归（09-11 R3/AC-3）。

合成 secret 哨兵（假 token + 假姓名）被植入异常原文后，断言：
- 应用日志（任何 logger 输出）与 StewardJob 落库内容、admin 8002 响应均无哨兵原文；
- 落库/响应只含白名单安全错误码与关联 ID。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_steward import _emit_fact_event, _fact, _person

from app import config
from app.models.space import FamilySpace
from app.models.steward import (
    StewardDeliveryIntent,
    StewardGeneration,
    StewardJob,
    StewardPublication,
    StewardViewTarget,
)
from app.services import maintenance, steward, steward_runtime, steward_suggestions
from conftest import admin_session_headers, create_system_admin, create_user_with_pin

# 合成哨兵：故意同时包含 token 形态、SQL 形态与中文姓名形态
SENTINEL_TOKEN = "SUPER-SECRET-TOKEN-9f8e7d6c-sk-live"
SENTINEL_NAME = "哨兵姓名王大锤"
SENTINEL_SQL = "INSERT INTO accounts (pin_hash) VALUES ('SUPER-SECRET-TOKEN-9f8e7d6c')"
SENTINELS = (SENTINEL_TOKEN, SENTINEL_NAME, SENTINEL_SQL)


def _assert_no_sentinel(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text, f"哨兵泄漏到日志/响应: {sentinel[:24]}..."


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.levelname}:{r.name}:{r.getMessage()}" for r in caplog.records)


@pytest.fixture()
def _sentinel_space(db_session):
    owner = _person(db_session, None, "hyg-owner")
    space = FamilySpace(
        name="hygiene", kind="household", owner_id=owner.id, created_at=owner.created_at
    )
    db_session.add(space)
    db_session.commit()
    return space


def test_suggestion_projection_failure_never_leaks(
    db_session, _sentinel_space, caplog, monkeypatch, admin_client
) -> None:
    """发布后的建议交付失败只记录安全码；日志与管理响应无异常原文。"""
    calls = []

    def _boom(session, job, **kwargs):
        calls.append(job.id)
        raise RuntimeError(
            f"sqlite error: {SENTINEL_SQL} for {SENTINEL_NAME} token={SENTINEL_TOKEN}"
        )

    monkeypatch.setattr(config, "STEWARD_STAGE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(steward_suggestions, "project_for_job", _boom)
    caplog.set_level(logging.DEBUG)

    # A confirmed sibling fact with no shared parents produces a real gap
    # finding, so the publication prepares an actual suggestion delivery.
    fact = _fact(
        db_session,
        "direct_sibling",
        _person(db_session, _sentinel_space.id, "hyg-a").id,
        _person(db_session, _sentinel_space.id, "hyg-b").id,
        space_id=_sentinel_space.id,
    )
    _emit_fact_event(db_session, fact)
    db_session.commit()
    job = db_session.scalar(select(StewardJob).where(StewardJob.space_id == _sentinel_space.id))
    assert job is not None
    granted = steward.lease_next_steward_job(db_session, leased_by="w")
    assert granted is not None
    result = steward.run_steward_job(db_session, granted)

    text = _log_text(caplog)
    _assert_no_sentinel(text)
    assert calls == [granted.id]
    assert result["delivery"]["delivery_failed"] == 1
    failed = db_session.scalar(
        select(StewardDeliveryIntent).where(
            StewardDeliveryIntent.generation_id == result["generation_id"],
            StewardDeliveryIntent.kind == "suggestion_finding",
        )
    )
    assert failed is not None and failed.status == "failed" and failed.attempt == 1
    assert failed.error_code == "delivery_failed"
    generation = db_session.get(StewardGeneration, failed.generation_id)
    assert generation.job_id == granted.id and generation.status == "published"
    assert db_session.get(StewardPublication, _sentinel_space.id).generation_id == generation.id
    # 确定性 core 不受影响
    db_session.refresh(granted)
    assert granted.status == "succeeded"
    _assert_no_sentinel(str(failed.payload_json))
    _assert_no_sentinel(str(granted.error_json))
    _assert_no_sentinel(str(granted.checkpoint_json))

    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    response = admin_client.get(
        "/admin-api/v1/steward/deliveries",
        params={"space_id": _sentinel_space.id},
        headers=headers,
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["intent_id"] == failed.id and item["error_code"] == "delivery_failed"
    assert set(item) == {
        "intent_id",
        "generation_id",
        "space_id",
        "kind",
        "status",
        "attempt",
        "available_at",
        "error_code",
    }
    _assert_no_sentinel(response.text)
    _assert_no_sentinel(_log_text(caplog))


def test_execute_failure_persists_only_safe_code(
    db_session, _sentinel_space, caplog, monkeypatch
) -> None:
    """执行路径异常：落库只有白名单安全错误码；日志无哨兵；终态 failed。"""
    calls = []

    def _boom(state, **kwargs):
        calls.append(state.graph.viewer_user_id)
        raise RuntimeError(f"constraint failed: {SENTINEL_SQL} name={SENTINEL_NAME}")

    monkeypatch.setattr(steward_runtime, "run_slice", _boom)
    caplog.set_level(logging.DEBUG)

    fact = _fact(
        db_session,
        "spouse",
        _person(db_session, _sentinel_space.id, "compute-a").id,
        _person(db_session, _sentinel_space.id, "compute-b").id,
        space_id=_sentinel_space.id,
    )
    event = _emit_fact_event(db_session, fact)
    db_session.commit()
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=_sentinel_space.id, cause="source_fact", trigger_cursor=event.id
    )
    granted = steward.lease_next_steward_job(db_session, leased_by="w")
    assert granted is not None
    with pytest.raises(RuntimeError) as raised:
        steward.execute_steward_job(db_session, granted, worker_id="w", expected_attempt=1)

    db_session.expire_all()
    assert len(calls) == 1
    _assert_no_sentinel(str(raised.value))
    row = db_session.get(StewardJob, job.id)
    assert row.status == "failed"
    assert row.error_code == steward.ERROR_EXECUTION_FAILED
    assert row.error_json is not None and set(row.error_json) == {"code"}
    assert row.error_json == {"code": steward.ERROR_EXECUTION_FAILED}
    generation = db_session.scalar(
        select(StewardGeneration).where(StewardGeneration.job_id == job.id)
    )
    assert generation.status == "failed" and generation.error_code == steward.ERROR_EXECUTION_FAILED
    target = db_session.scalar(
        select(StewardViewTarget).where(StewardViewTarget.status == "failed")
    )
    assert target is not None and target.reason_code == "target_failed"
    assert db_session.get(StewardPublication, _sentinel_space.id) is None
    _assert_no_sentinel(str(row.error_json))
    _assert_no_sentinel(str(row.checkpoint_json))
    _assert_no_sentinel(str(generation.stats_json))
    _assert_no_sentinel(_log_text(caplog))
    db_session.expunge_all()


def test_maintenance_tick_failure_never_leaks(db_session, caplog, monkeypatch) -> None:
    """maintenance tick / 辅助调度异常只记异常类名，不外泄异常原文。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)

    def _boom(session):
        raise RuntimeError(f"operational error: {SENTINEL_SQL} {SENTINEL_NAME}")

    monkeypatch.setattr(steward, "scan_due_spaces", _boom)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError):
        maintenance.run_maintenance_tick()
    _assert_no_sentinel(_log_text(caplog))

    def _boom2(session):
        raise RuntimeError(f"dispatch failed: {SENTINEL_TOKEN}")

    monkeypatch.setattr(steward, "scan_due_spaces", lambda session: 0)
    monkeypatch.setattr(steward, "reaper_pass", lambda session: 0)
    monkeypatch.setattr(maintenance.steward_assist, "recover_stuck_batches", _boom2)
    caplog.clear()
    maintenance.run_maintenance_tick()
    text = _log_text(caplog)
    _assert_no_sentinel(text)
    assert "assist dispatch failed" in text


def test_admin_status_and_jobs_responses_never_leak(
    admin_client: TestClient, db_session, _sentinel_space, caplog, monkeypatch
) -> None:
    """异常路径后，8002 status/jobs 响应与日志均无哨兵（AC-3）。"""
    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=_sentinel_space.id, cause="source_fact", trigger_cursor=1
    )
    granted = steward.lease_next_steward_job(db_session, leased_by="w")

    def _boom(*args, **kwargs):
        raise RuntimeError(f"boom {SENTINEL_TOKEN} {SENTINEL_NAME}")

    monkeypatch.setattr(steward, "_detect_findings", _boom)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError):
        steward.execute_steward_job(db_session, granted, worker_id="w", expected_attempt=1)
    db_session.expire_all()

    status = admin_client.get("/admin-api/v1/steward/status", headers=headers)
    assert status.status_code == 200
    jobs = admin_client.get(
        "/admin-api/v1/steward/jobs", params={"space_id": _sentinel_space.id}, headers=headers
    )
    assert jobs.status_code == 200
    _assert_no_sentinel(status.text)
    _assert_no_sentinel(jobs.text)
    body = status.json()
    # 观测指标存在且是纯计数/时间戳形态（R2）
    assert set(body["metrics"]) == {
        "core_queue_depth",
        "oldest_queued_age_seconds",
        "last_scan_at",
        "last_worker_tick_at",
        "core_failed",
        "assist_failed",
        "assist_degraded",
        "assist_unknown",
        "budget_reserved_tokens",
        "budget_consumed_tokens",
        "pfv_stale",
        "cards_created",
        "cards_superseded",
    }
    assert all(alert["code"] in ("queue_backlog", "queue_stalled") for alert in body["alerts"])
    _assert_no_sentinel(_log_text(caplog))
    _ = create_user_with_pin  # 保持导入被引用（conftest 契约）
