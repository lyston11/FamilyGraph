"""后台维护循环（P1 收口）：Agent reaper 接线 + canonical StewardJob 生产泵。

此前 lease/run/settle/reaper 领域执行器只有测试调用，缺生产 scheduler/worker
闭环。本文件验证 run_maintenance_tick 的调度语义；执行器本身的领域行为
（幂等、跨空间隔离、FSM）由 test_steward.py 覆盖。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.models.space import FamilySpace
from app.models.steward import StewardJob, StewardSpaceSchedule
from app.services import maintenance, steward
from app.utils import timeutil
from conftest import create_user_with_pin


def _space(session, name: str) -> FamilySpace:
    owner = create_user_with_pin(session, f"{name}-own", "123456")
    space = FamilySpace(name=name, kind="household", owner_id=owner.id, created_at=owner.created_at)
    session.add(space)
    session.commit()
    return space


@pytest.fixture()
def _worker_enabled(monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    # 测试把扫描间隔压到 0：调度行每 tick 立即到期（生产默认 300s，见 config 校验）
    monkeypatch.setattr(config, "STEWARD_SCAN_INTERVAL_SECONDS", 0)


def test_tick_executes_queued_steward_job_end_to_end(db_session, _worker_enabled):
    """queued 作业经 maintenance tick 被 lease→execute→succeeded 结算。"""
    space = _space(db_session, "maint-run")
    job, created = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=3
    )
    assert created and job.status == "queued"
    db_session.commit()

    counters = maintenance.run_maintenance_tick()

    assert counters["steward_executed"] == 1
    db_session.expire_all()
    settled = db_session.get(StewardJob, job.id)
    assert settled.status == "succeeded"
    assert settled.last_event_cursor == 3
    assert settled.checkpoint_json["last_event_cursor"] == 3


def test_tick_requeues_expired_lease_then_executes(db_session, _worker_enabled):
    """过期 leased 作业先被 reaper 回队，下一轮 tick 重新执行。"""
    space = _space(db_session, "maint-reap")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=2
    )
    granted = steward.lease_next_steward_job(db_session, leased_by="dead-worker")
    assert granted.id == job.id
    # 模拟 worker 崩溃：lease 过期
    granted.lease_expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()

    counters = maintenance.run_maintenance_tick()

    assert counters["steward_reaped"] == 1
    assert counters["steward_executed"] == 1
    db_session.expire_all()
    settled = db_session.get(StewardJob, job.id)
    assert settled.status == "succeeded"
    assert settled.attempt == 2  # 首次 lease +1，reaper 回队后再次 lease +1


def test_tick_noop_when_worker_disabled(db_session, monkeypatch):
    """STEWARD_WORKER_ENABLED 关闭（默认）：queued 作业不被进程内泵执行。"""
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    space = _space(db_session, "maint-off")
    steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    counters = maintenance.run_maintenance_tick()

    assert counters == {
        "agent_reaped": 0,
        "steward_reaped": 0,
        "steward_scanned": 0,
        "steward_executed": 0,
        "steward_failed": 0,
        "steward_assist_recovered": 0,
        "steward_assist_scheduled": 0,
    }
    assert db_session.scalar(select(StewardJob.status)) == "queued"


def test_tick_agent_reaper_invoked_when_runtime_enabled(db_session, monkeypatch):
    """AGENT_RUNTIME_ENABLED 时 tick 调用 agent_queue.reaper_pass（回收接线）。"""
    calls: list[int] = []
    from app.services import agent_queue

    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ENABLED", False)

    original = agent_queue.reaper_pass
    monkeypatch.setattr(
        agent_queue, "reaper_pass", lambda db, **kw: (calls.append(1), original(db, **kw))[1]
    )

    counters = maintenance.run_maintenance_tick()
    assert calls == [1]
    assert counters["agent_reaped"] == 0


def test_failed_job_settles_failed_not_crash_loop(db_session, _worker_enabled, monkeypatch):
    """执行异常被 execute_steward_job 结算为 failed 并继续泵后续作业，循环不死。"""
    space = _space(db_session, "maint-fail")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    def _boom(db, job_obj, **kw):
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(steward, "run_steward_job", _boom)
    counters = maintenance.run_maintenance_tick()
    db_session.expire_all()
    settled = db_session.get(StewardJob, job.id)
    assert settled.status == "failed"
    # F16：错误对象只落安全分类码，异常原文不进入 error_json
    assert settled.error_json["code"] == "STEWARD_EXECUTION_FAILED"
    assert "simulated" not in str(settled.error_json)
    assert counters["steward_executed"] == 0


# ---- 09-11 生产调度：周期扫描 / 有限恢复 / 租约栅栏（AC-2/3/4）----


def _jobs_for(session, space_id: int, *, cause: str | None = None) -> list[StewardJob]:
    stmt = select(StewardJob).where(StewardJob.space_id == space_id)
    if cause is not None:
        stmt = stmt.where(StewardJob.cause == cause)
    return list(session.scalars(stmt.order_by(StewardJob.id)).all())


def test_scan_backfill_enqueues_due_space_job_on_first_enable(db_session, _worker_enabled):
    """首次启用（无调度行）：扫描立即到期并经 canonical enqueue 登记追补作业。"""
    space = _space(db_session, "scan-first")
    counters = maintenance.run_maintenance_tick()
    assert counters["steward_scanned"] == 1
    assert counters["steward_executed"] == 1
    jobs = _jobs_for(db_session, space.id, cause="integrity_scan")
    assert len(jobs) == 1 and jobs[0].status == "succeeded"
    schedule = db_session.get(StewardSpaceSchedule, space.id)
    assert schedule is not None
    # 测试把扫描间隔压到 0：下一个 tick 立即到期（生产默认 300s，见 config 校验）
    assert schedule.next_scan_at >= schedule.updated_at


def test_scan_same_cursor_still_registers_due_check_job(db_session, _worker_enabled):
    """空闲空间相同事件水位：到期检查不被历史 succeeded 幂等短路（AC-2）。"""
    space = _space(db_session, "scan-idle")
    maintenance.run_maintenance_tick()
    counters = maintenance.run_maintenance_tick()
    assert counters["steward_scanned"] == 1
    jobs = _jobs_for(db_session, space.id, cause="integrity_scan")
    assert len(jobs) == 2  # 每个周期各登记一个作业，第二个 tick 仍然执行
    assert all(job.status == "succeeded" for job in jobs)


def test_events_during_downtime_backfilled_by_scan(db_session, monkeypatch):
    """停机期间事件（STEWARD_ENABLED 关闭时未登记作业）：重新启用后两个 tick 内追补。"""
    from app.services.domain_events import emit as emit_domain_event

    monkeypatch.setattr(config, "STEWARD_ENABLED", False)
    space = _space(db_session, "scan-downtime")
    event = emit_domain_event(
        db_session,
        event_type="source_fact.updated",
        aggregate_type="source_fact",
        aggregate_id=1,
        space_id=space.id,
        actor_account_id=None,
    )
    db_session.commit()
    assert db_session.scalar(select(StewardJob.id).limit(1)) is None

    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)
    maintenance.run_maintenance_tick()
    jobs = _jobs_for(db_session, space.id)
    assert len(jobs) == 1
    assert jobs[0].trigger_cursor >= event.id
    assert jobs[0].status == "succeeded"


def test_policy_version_change_triggers_backfill(db_session, _worker_enabled, monkeypatch):
    """policy_version 变化：调度行置为立即到期，追补作业按新版本登记。"""
    space = _space(db_session, "scan-policy")
    maintenance.run_maintenance_tick()
    old = db_session.get(StewardSpaceSchedule, space.id)
    old_policy = old.policy_version

    monkeypatch.setattr(steward, "POLICY_VERSION", "v-next-policy")
    counters = maintenance.run_maintenance_tick()
    assert counters["steward_scanned"] == 1
    db_session.expire_all()
    schedule = db_session.get(StewardSpaceSchedule, space.id)
    assert schedule.policy_version == "v-next-policy"
    assert schedule.policy_version != old_policy
    jobs = _jobs_for(db_session, space.id, cause="integrity_scan")
    assert jobs[-1].policy_version == "v-next-policy"


def test_lock_conflict_retries_with_backoff_then_succeeds(db_session, _worker_enabled, monkeypatch):
    """数据库锁冲突（可重试）：退避回队而非终态；退避到期后重试成功（AC-3）。"""
    import sqlite3

    space = _space(db_session, "retry-lock")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    real = steward._execute_locked
    calls: list[int] = []

    def flaky(db, job_obj, *, now, upper=None):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(db, job_obj, now=now, upper=upper)

    monkeypatch.setattr(steward, "_execute_locked", flaky)
    maintenance.run_maintenance_tick()
    db_session.expire_all()
    first = db_session.get(StewardJob, job.id)
    assert first.status == "queued"
    assert first.attempt == 1
    assert first.error_code == "STEWARD_TRANSIENT_DB_LOCK"
    assert first.available_at is not None and first.available_at > timeutil.utcnow()

    # 退避未到：不自动重试
    counters = maintenance.run_maintenance_tick()
    assert counters["steward_executed"] == 0

    first.available_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()
    maintenance.run_maintenance_tick()
    db_session.expire_all()
    settled = db_session.get(StewardJob, job.id)
    assert settled.status == "succeeded"
    assert settled.attempt == 2


def test_retry_exhausted_no_more_auto_retry(db_session, _worker_enabled, monkeypatch):
    """可重试失败耗尽预算：failed 终态且不再自动重试（AC-3）。"""
    import sqlite3

    space = _space(db_session, "retry-exhaust")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1, max_attempts=1
    )
    db_session.commit()

    def always_locked(db, job_obj, *, now, upper=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(steward, "_execute_locked", always_locked)
    maintenance.run_maintenance_tick()
    db_session.expire_all()
    failed = db_session.get(StewardJob, job.id)
    assert failed.status == "failed"
    assert failed.error_code == "STEWARD_TRANSIENT_DB_LOCK"

    maintenance.run_maintenance_tick()
    db_session.expire_all()
    still = db_session.get(StewardJob, job.id)
    assert still.status == "failed"
    assert still.attempt == 1  # 未被自动复活
    # 周期扫描仍会为该空间登记新作业（AC-2 到期检查持续），但不是失败作业复活
    later = db_session.scalar(
        select(StewardJob).where(
            StewardJob.space_id == space.id, StewardJob.cause == "integrity_scan"
        )
    )
    assert later is not None and later.id != job.id


def test_deterministic_failure_isolated_per_space(db_session, _worker_enabled, monkeypatch):
    """确定性输入错误：该空间进入 failed，其他空间作业继续成功（AC-3）。"""

    from app.errors import raise_api_error

    space_a = _space(db_session, "det-a")
    space_b = _space(db_session, "det-b")
    job_a, _ = steward.enqueue_steward_job(
        db_session, space_id=space_a.id, cause="source_fact", trigger_cursor=1
    )
    job_b, _ = steward.enqueue_steward_job(
        db_session, space_id=space_b.id, cause="source_fact", trigger_cursor=1
    )
    db_session.commit()

    real = steward._execute_locked

    def selective(db, job_obj, *, now, upper=None):
        if job_obj.space_id == space_a.id:
            raise_api_error(422, "BAD_INPUT", "非法输入")
        return real(db, job_obj, now=now, upper=upper)

    monkeypatch.setattr(steward, "_execute_locked", selective)
    maintenance.run_maintenance_tick()
    db_session.expire_all()
    failed = db_session.get(StewardJob, job_a.id)
    assert failed.status == "failed"
    assert failed.error_code == "BAD_INPUT"
    assert "非法输入" not in str(failed.error_json)
    ok = db_session.get(StewardJob, job_b.id)
    assert ok.status == "succeeded"
    # failed 是终态：HTTPException 的确定性错误不触发退避回队
    assert failed.available_at is None


def test_stale_lease_execution_and_settlement_rejected(db_session):
    """旧执行者用过期/易主租约执行或结算被拒绝（F17 / AC-4）。"""
    from fastapi import HTTPException

    from app.errors import extract_api_error

    space = _space(db_session, "fence")
    job, _ = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    granted = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert granted is not None and granted.id == job.id
    db_session.commit()

    # 租约易主（模拟 reaper 重派给 w2）：w1 的旧执行上下文失效
    granted.leased_by = "w2"
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        steward.run_steward_job(
            db_session, granted, worker_id="w1", expected_attempt=granted.attempt
        )
    api_error = extract_api_error(exc_info.value.detail)
    assert api_error is not None and api_error["code"] == "STEWARD_LEASE_STALE"
    db_session.rollback()

    def _reset_to_queued() -> None:
        db_session.expire_all()
        row = db_session.get(StewardJob, job.id)
        row.status = "queued"
        row.leased_by = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        db_session.commit()

    # lease deadline 已过：结算同样被拒
    _reset_to_queued()
    granted2 = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert granted2 is not None
    db_session.commit()
    granted2.lease_expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(HTTPException) as settle_info:
        steward.settle_steward_job(db_session, granted2, status="succeeded", worker_id="w1")
    settle_error = extract_api_error(settle_info.value.detail)
    assert settle_error is not None and settle_error["code"] == "STEWARD_LEASE_STALE"
    db_session.rollback()

    # attempt 不一致（旧 attempt 的结算）被拒
    _reset_to_queued()
    granted3 = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert granted3 is not None
    db_session.commit()
    with pytest.raises(HTTPException) as attempt_info:
        steward.settle_steward_job(
            db_session, granted3, status="succeeded", worker_id="w1", expected_attempt=99
        )
    attempt_error = extract_api_error(attempt_info.value.detail)
    assert attempt_error is not None and attempt_error["code"] == "STEWARD_LEASE_STALE"


def test_maintenance_loop_single_instance_across_listeners():
    """多 listener 共享 lifespan：循环进程级单例，最后一个 holder 停止才结束（AC-4）。"""
    import asyncio

    from app import config as cfg

    async def scenario() -> None:
        task1 = maintenance.start_maintenance_loop()
        task2 = maintenance.start_maintenance_loop()
        assert task1 is not None
        assert task1 is task2
        await maintenance.stop_maintenance_loop()
        await maintenance.stop_maintenance_loop()
        assert maintenance._holders == 0
        assert maintenance._task is None

    old_task, old_holders = maintenance._task, maintenance._holders
    maintenance._task, maintenance._holders = None, 0
    try:
        assert cfg.AGENT_RUNTIME_ENABLED or cfg.STEWARD_ENABLED
        asyncio.run(scenario())
    finally:
        maintenance._task, maintenance._holders = old_task, old_holders
