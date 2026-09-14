"""Steward write bursts must leave actual SQLite access for online writers."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from test_steward_snapshot_fences import pending_view, world

from app import config
from app.services import steward_pipeline, steward_write_budget
from app.utils.timeutil import utcnow


@pytest.fixture
def ledger(db_session):
    bind = db_session.get_bind()
    db_session.rollback()
    with bind.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE writer_budget_ledger (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
        )
    try:
        yield bind
    finally:
        with bind.begin() as connection:
            connection.exec_driver_sql("DROP TABLE writer_budget_ledger")


def _insert(bind, label):
    with steward_pipeline.write_transaction(bind) as session:
        session.execute(
            text("INSERT INTO writer_budget_ledger(label) VALUES (:label)"), {"label": label}
        )


def test_shared_quiet_period_leaves_no_session_and_allows_online_writer(ledger, monkeypatch):
    monkeypatch.setattr(steward_write_budget, "WRITE_BURST_SECONDS", 0)
    monkeypatch.setattr(steward_write_budget, "WRITE_QUIET_SECONDS", 0.2)
    _insert(ledger, "first")
    budget = steward_write_budget._budgets[ledger]
    waiting = threading.Event()
    original_wait = budget.condition.wait

    def observe_wait(timeout=None):
        if timeout is not None:
            waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(budget.condition, "wait", observe_wait)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(_insert, ledger, "background-a")
        assert waiting.wait(timeout=2)
        second = workers.submit(_insert, ledger, "background-b")
        # Both coordinators share the same quiet window. Neither has acquired
        # a pooled DB connection or started an SQLite transaction while waiting.
        assert ledger.pool.checkedout() == 0
        with ledger.begin() as online:
            assert online.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            online.exec_driver_sql("INSERT INTO writer_budget_ledger(label) VALUES ('online')")
        first.result(timeout=2)
        second.result(timeout=2)
    with ledger.connect() as reader:
        assert reader.exec_driver_sql(
            "SELECT label FROM writer_budget_ledger ORDER BY id"
        ).scalars().all() == ["first", "online", "background-a", "background-b"]


def test_rollback_releases_admission_and_does_not_publish_partial_work(ledger, monkeypatch):
    monkeypatch.setattr(steward_write_budget, "WRITE_BURST_SECONDS", 0)
    with pytest.raises(ValueError, match="abort"):
        with steward_pipeline.write_transaction(ledger) as session:
            session.execute(text("INSERT INTO writer_budget_ledger(label) VALUES ('rolled-back')"))
            raise ValueError("abort")
    _insert(ledger, "committed")
    with ledger.connect() as reader:
        assert reader.exec_driver_sql("SELECT label FROM writer_budget_ledger").scalars().all() == [
            "committed"
        ]


def test_nested_writer_is_rejected_without_blocking_later_work(ledger):
    with steward_pipeline.write_transaction(ledger):
        with pytest.raises(RuntimeError, match="cannot be nested"):
            with steward_pipeline.write_transaction(ledger):
                pytest.fail("nested writer must not acquire another transaction")
    _insert(ledger, "after-rejection")


def test_unrelated_engine_is_not_queued_behind_another_database(ledger):
    other = create_engine("sqlite://")
    try:
        with steward_write_budget.writer_turn(ledger):
            with steward_write_budget.writer_turn(other):
                with other.begin() as connection:
                    assert connection.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        other.dispose()


def test_natural_idle_satisfies_the_quiet_period(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(steward_write_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    budget = steward_write_budget._WriteBudget()
    with budget.turn():
        clock[0] += steward_write_budget.WRITE_BURST_SECONDS + 0.001
    clock[0] += steward_write_budget.WRITE_QUIET_SECONDS + 0.001

    def unexpected_wait(*_args, **_kwargs):
        pytest.fail("a natural compute/read idle period must not incur another delay")

    monkeypatch.setattr(budget.condition, "wait", unexpected_wait)
    with budget.turn():
        pass


@pytest.mark.parametrize("expiry", ["lease", "generation"])
def test_heartbeat_rechecks_time_after_admission(db_session, monkeypatch, expiry):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    fixture = world(db_session)
    generation, _work, _snapshot = pending_view(db_session, fixture)
    before = utcnow()
    boundary = before + timedelta(seconds=1)
    fixture.job.lease_expires_at = (
        boundary if expiry == "lease" else boundary + timedelta(minutes=1)
    )
    generation.valid_until = boundary if expiry == "generation" else boundary + timedelta(minutes=1)
    db_session.commit()
    original_deadline = fixture.job.lease_expires_at
    current = [before]
    monkeypatch.setattr(steward_pipeline, "utcnow", lambda: current[0])
    original_turn = steward_write_budget.writer_turn

    @contextmanager
    def delayed_admission(bind):
        with original_turn(bind):
            current[0] = boundary + timedelta(milliseconds=1)
            yield

    monkeypatch.setattr(steward_write_budget, "writer_turn", delayed_admission)
    expected = HTTPException if expiry == "lease" else steward_pipeline.SnapshotChanged
    with pytest.raises(expected):
        steward_pipeline.heartbeat(db_session, fixture.binding, ttl=120)
    db_session.refresh(fixture.job)
    assert fixture.job.lease_expires_at == original_deadline
