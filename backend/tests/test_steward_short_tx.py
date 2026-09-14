"""短事务执行器合同测试（09-13 R1：重算不得持续占有 SQLite 写锁）。

覆盖：
- 慢计算（路径解析被 monkeypatch 为阻塞）期间，独立连接的写事务能在
  busy_timeout 内完成提交——旧整族立即事务形态下该写入必然超时失败；
- 计算完成后原子发布版本化视图，按人物对读者仍可获得完整结果；
- 发布阶段的租约栅栏：执行中途租约过期时发布被拒（409），作业由
  reaper 收敛，不冒充成功。
"""

from __future__ import annotations

import threading
import time

import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models.account import Account
from app.models.steward import (
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardViewTarget,
)
from app.services import steward, steward_runtime
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow


@pytest.fixture()
def _steward_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)


def _seed_family(session, name: str):
    """1 viewer + 2 parent 成员：构成有路径的小图。"""
    viewer, space = create_agent_fixture(session, name=name)
    from app.models.account import Account

    account = session.scalar(select(Account).where(Account.user_id == viewer.id))
    parent_a = create_user_with_pin(session, f"{name}-pa", "123456", gender="m")
    parent_b = create_user_with_pin(session, f"{name}-pb", "123456", gender="f")
    create_space_member(session, space.id, parent_a.id)
    create_space_member(session, space.id, parent_b.id)
    for subject, obj in ((parent_a, viewer), (parent_b, viewer)):
        fact = create_source_fact(
            session,
            fact_type="biological_parent",
            subject_user_id=subject.id,
            object_user_id=obj.id,
            provenance="manual_entry",
            space_id=space.id,
        )
        transition_source_fact(session, fact, "confirm")
    session.commit()
    return (viewer, account), space


def test_slow_compute_does_not_block_concurrent_writer(
    db_session, monkeypatch: pytest.MonkeyPatch, _steward_enabled
) -> None:
    """路径解析阻塞期间，独立连接写入在 busy_timeout=5000ms 内完成。"""
    (_viewer, account), space = _seed_family(db_session, "shorttx")
    job, _created = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="integrity_scan", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert grant is not None

    job_id, attempt, account_id = job.id, grant.attempt, account.id
    entered, release = threading.Event(), threading.Event()
    original = steward_runtime.run_slice

    def slow_slice(state, **kwargs):
        entered.set()
        assert release.wait(timeout=10), "test did not release the CPU slice"
        return original(state, **kwargs)

    monkeypatch.setattr(steward_runtime, "run_slice", slow_slice)
    errors: list[Exception] = []
    error_lock = threading.Lock()

    def run_job() -> None:
        try:
            with SessionLocal() as worker_session:
                current = worker_session.get(StewardJob, job_id)
                assert current is not None
                steward.run_steward_job(
                    worker_session, current, worker_id="w1", expected_attempt=attempt
                )
        except Exception as exc:  # pragma: no cover - 失败路径由断言披露
            with error_lock:
                errors.append(exc)

    worker = threading.Thread(target=run_job)
    worker.start()
    try:
        assert entered.wait(timeout=10), "worker never entered the actual search path"
        # 独立连接写事务：模拟并发登录/lease 写入
        started = time.monotonic()
        with SessionLocal() as other:
            probe = other.get(Account, account_id)
            assert probe is not None
            probe.failed_attempts += 1
            other.commit()
            assert other.get(Account, account_id).failed_attempts == 1
        elapsed_ms = (time.monotonic() - started) * 1000
        assert elapsed_ms < 5000, f"并发写入被写锁阻塞 {elapsed_ms:.0f}ms"
    finally:
        release.set()
        worker.join(timeout=30)
    assert not worker.is_alive()
    assert errors == []


def test_short_tx_publishes_results(db_session, _steward_enabled) -> None:
    """发布指针只指向完整版本化结果，普通读取获得两位亲人的称谓。"""
    (viewer, account), space = _seed_family(db_session, "shorttx-publish")
    from app.services import personal_family_view as pfv

    pfv.initialize_account_views(db_session, account_id=account.id, user_id=viewer.id)
    db_session.commit()
    job, _created = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="integrity_scan", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w1")
    assert grant is not None
    steward.run_steward_job(db_session, job, worker_id="w1", expected_attempt=grant.attempt)

    settled = db_session.get(type(job), job.id)
    assert settled is not None and settled.status == "succeeded"
    assert (settled.last_event_cursor or 0) >= 1
    rows = list(
        db_session.scalars(
            select(StewardViewTarget)
            .join(StewardGenerationView)
            .join(
                StewardPublication,
                StewardPublication.generation_id == StewardGenerationView.generation_id,
            )
            .where(
                StewardPublication.space_id == space.id,
                StewardGenerationView.root_user_id == viewer.id,
            )
        )
    )
    assert len(rows) == 2 and all(row.status == "ready" for row in rows)
    payload = pfv.current_view_payload(db_session, account=account, space_id=space.id)
    assert payload is not None and payload["status"] == "current"
    assert len(payload["edges"]) == 2


def test_publish_rejected_when_lease_expired(db_session, _steward_enabled) -> None:
    """执行中途租约到期：发布被拒（409），不冒充成功。"""
    (viewer, account), space = _seed_family(db_session, "shorttx-expired")
    job, _created = steward.enqueue_steward_job(
        db_session, space_id=space.id, cause="integrity_scan", trigger_cursor=1
    )
    grant = steward.lease_next_steward_job(db_session, leased_by="w1", ttl_seconds=3600)
    assert grant is not None
    # 模拟计算期间租约到期（reaper 尚未回收）
    job.lease_expires_at = utcnow()
    db_session.commit()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        steward.run_steward_job(db_session, job, worker_id="w1", expected_attempt=grant.attempt)
    detail = excinfo.value.detail
    assert (
        isinstance(detail, dict)
        and detail.get("__api_error__", {}).get("code") == "STEWARD_LEASE_STALE"
    )
