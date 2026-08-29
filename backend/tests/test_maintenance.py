"""后台维护循环（P1 收口）：Agent reaper 接线 + canonical StewardJob 生产泵。

此前 lease/run/settle/reaper 领域执行器只有测试调用，缺生产 scheduler/worker
闭环。本文件验证 run_maintenance_tick 的调度语义；执行器本身的领域行为
（幂等、跨空间隔离、FSM）由 test_steward.py 覆盖。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import create_user_with_pin
from sqlalchemy import select

from app import config
from app.models.space import FamilySpace
from app.models.steward import StewardJob
from app.services import maintenance, steward
from app.utils import timeutil


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
        "steward_executed": 0,
        "steward_failed": 0,
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
    assert settled.error_json["code"] == "RuntimeError"
    assert counters["steward_executed"] == 0
