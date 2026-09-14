"""The lock measurement ends when SQLite releases its writer, before reporting."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_measurement(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[float]]:
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark-steward-recompute.py"
    spec = importlib.util.spec_from_file_location("steward_measurement_test", script)
    assert spec is not None and spec.loader is not None
    measurement = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measurement)
    measurement.METRICS.phase = "calibration"

    clock = [0.0]

    def counter() -> float:
        clock[0] += 0.001
        return clock[0]

    # Only the measuring clock is controlled; every SQL operation remains real.
    monkeypatch.setattr(
        measurement, "time", SimpleNamespace(perf_counter=counter, thread_time=time.thread_time)
    )
    return measurement, clock


def _delay_commit(connection: sqlite3.Connection, clock: list[float]) -> list[bool]:
    commit_calls: list[bool] = []

    def authorize(action: int, argument: str | None, *_unused: str | None) -> int:
        if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
            # This runs inside the DB-API commit, before it returns to the meter.
            clock[0] += 0.250
            commit_calls.append(True)
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return commit_calls


@pytest.mark.parametrize("explicit", [True, False])
def test_reporting_delay_is_not_measured_as_a_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    measurement, clock = _load_measurement(monkeypatch)
    original_record = measurement.METRICS.record
    db_path = tmp_path / "writer-boundary.db"
    another_writer_completed = []

    def delayed_record(category: str, ms: float, phase: str | None = None) -> None:
        if category == "commit_ms":
            with sqlite3.connect(db_path, timeout=0.1) as second:
                second.execute("BEGIN IMMEDIATE")
                second.execute("INSERT INTO proof VALUES (2)")
                second.commit()
            another_writer_completed.append(True)
            clock[0] += 0.6
        original_record(category, ms, phase)

    monkeypatch.setattr(measurement.METRICS, "record", delayed_record)
    first = sqlite3.connect(db_path, factory=measurement.MeasuredConnection)
    try:
        first.cursor().execute("CREATE TABLE proof (value INTEGER)")
        commit_calls = _delay_commit(first, clock)
        if explicit:
            first.cursor().execute("BEGIN IMMEDIATE")
        first.cursor().execute("INSERT INTO proof VALUES (1)")
        first.commit()
        assert first.cursor().execute("SELECT count(*) FROM proof").fetchone()[0] == 2
    finally:
        first.close()

    assert commit_calls == another_writer_completed == [True]
    metrics = measurement.METRICS.report()["calibration"]
    category = "write_lock_hold_ms" if explicit else "implicit_write_upper_bound_ms"
    assert metrics[category]["samples"] == 1
    assert metrics["commit_ms"]["samples"] == 1
    assert 250 <= metrics["commit_ms"]["max"] <= metrics[category]["max"] < 300
    assert metrics["post_commit_reporting_ms"]["max"] >= 600
    if not explicit:
        assert 250 <= metrics["implicit_write_hold_lower_bound_ms"]["max"] < 300


def test_failed_commit_keeps_timing_until_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measurement, clock = _load_measurement(monkeypatch)
    db_path = tmp_path / "failed-commit.db"
    first = sqlite3.connect(db_path, factory=measurement.MeasuredConnection)
    try:
        first.cursor().execute("PRAGMA foreign_keys=ON")
        first.cursor().execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        first.cursor().execute(
            "CREATE TABLE child (parent_id INTEGER, "
            "FOREIGN KEY (parent_id) REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        first.cursor().execute("BEGIN IMMEDIATE")
        first.cursor().execute("INSERT INTO child VALUES (7)")
        commit_calls = _delay_commit(first, clock)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            first.commit()
        assert commit_calls == [True]
        assert first.in_transaction
        assert "write_lock_hold_ms" not in measurement.METRICS.report()["calibration"]

        # A failed deferred constraint leaves the original writer in charge.
        with sqlite3.connect(db_path, timeout=0) as second:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.execute("BEGIN IMMEDIATE")
        clock[0] += 0.400
        first.rollback()
        assert not first.in_transaction
        with sqlite3.connect(db_path, timeout=0.1) as second:
            second.execute("BEGIN IMMEDIATE")
            second.execute("INSERT INTO parent VALUES (7)")
            second.commit()
        assert first.cursor().execute("SELECT count(*) FROM child").fetchone()[0] == 0
    finally:
        first.close()

    metrics = measurement.METRICS.report()["calibration"]["write_lock_hold_ms"]
    assert metrics["samples"] == 1
    assert 650 <= metrics["max"] < 700


@pytest.mark.parametrize("explicit", [True, False])
def test_statement_auto_rollback_ends_timing_before_later_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    measurement, clock = _load_measurement(monkeypatch)
    db_path = tmp_path / "auto-rollback.db"
    with sqlite3.connect(db_path) as setup:
        setup.execute("CREATE TABLE proof (value INTEGER UNIQUE)")
        setup.execute("INSERT INTO proof VALUES (1)")
    first = sqlite3.connect(db_path, factory=measurement.MeasuredConnection)
    category = "write_lock_hold_ms" if explicit else "implicit_write_upper_bound_ms"
    try:
        if explicit:
            first.cursor().execute("BEGIN IMMEDIATE")

        def delay_statement(sql: str) -> None:
            if sql.startswith("INSERT OR ROLLBACK"):
                clock[0] += 0.250

        first.set_trace_callback(delay_statement)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            first.cursor().execute("INSERT OR ROLLBACK INTO proof VALUES (1)")
        assert not first.in_transaction
        at_error = measurement.METRICS.report()["calibration"][category]
        assert at_error["samples"] == 1
        assert 250 <= at_error["max"] < 300
        with sqlite3.connect(db_path, timeout=0.1) as second:
            second.execute("BEGIN IMMEDIATE")
            second.execute("INSERT INTO proof VALUES (2)")
            second.commit()

        clock[0] += 0.600
        first.rollback()
        assert measurement.METRICS.report()["calibration"][category] == at_error
        assert first.cursor().execute("SELECT count(*) FROM proof").fetchone()[0] == 2
    finally:
        first.close()


@pytest.mark.parametrize(
    ("prefix", "expected_kind"),
    [("INSERT", "INSERT"), ("/*SQL_COMMENT_CANARY*/ INSERT", "OTHER")],
)
def test_slow_trace_contains_only_safe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prefix: str,
    expected_kind: str,
) -> None:
    measurement, clock = _load_measurement(monkeypatch)
    first = sqlite3.connect(tmp_path / "safe-trace.db", factory=measurement.MeasuredConnection)
    try:
        first.cursor().execute("CREATE TABLE proof (value TEXT)")
        first.cursor().execute("BEGIN IMMEDIATE")

        def delay_statement(sql: str) -> None:
            if "INSERT" in sql:
                clock[0] += 0.600

        first.set_trace_callback(delay_statement)
        first.cursor().execute(f"{prefix} INTO proof VALUES (?)", ("bound_parameter_canary",))
        first.commit()
        assert first.cursor().execute("SELECT value FROM proof").fetchone()[0] == (
            "bound_parameter_canary"
        )
    finally:
        first.close()

    assert len(measurement.METRICS.slow_write_windows) == 1
    sample = measurement.METRICS.slow_write_windows[0]
    assert set(sample) == {
        "phase",
        "category",
        "hold_ms",
        "thread_cpu_ms",
        "begin_wait_ms",
        "commit_ms",
        "statement_count",
        "statement_ms",
        "largest_statement_ms",
        "largest_statement_kind",
        "callers",
    }
    assert sample["largest_statement_kind"] == expected_kind
    printed = capsys.readouterr().out
    assert json.loads(printed) == {"slow_writer": sample}
    encoded = (json.dumps(sample) + printed).lower()
    assert "sql_comment_canary" not in encoded
    assert "bound_parameter_canary" not in encoded
