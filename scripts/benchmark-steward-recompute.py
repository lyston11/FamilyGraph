#!/usr/bin/env python3
"""Isolated Steward acceptance measurements, including real ASGI route requests.

Examples (repository root, backend virtualenv Python):
  backend/.venv/bin/python scripts/benchmark-steward-recompute.py --sizes 30 50 200 --ming --report /tmp/steward.json
  backend/.venv/bin/python scripts/benchmark-steward-recompute.py --ming --sizes 30 --scan-windows 2 --report /tmp/steward-scans.json

Every case gets a fresh migrated SQLite WAL database. All people have accounts by
default; the observed viewer is IN the connected family, unlike the old probe.
The Ming case reuses the existing six-generation seed relationships verbatim.
No production database, service configuration or external model is used.

Lock timing is separate from request timing: explicit BEGIN IMMEDIATE acquisition
is measured separately; hold time ends AFTER DBAPI commit/rollback returns, so it
includes fsync. Implicit-write timing starts BEFORE the first DML and is reported
as an upper bound (it may include lock acquisition). ASGI client and server times
are separate; they do not claim to measure browser rendering or network latency.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import os
import platform
import secrets
import shutil
import sqlite3
import sqlite3.dbapi2
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv/bin/python"
SQL_VERBS = frozenset(
    {
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "REPLACE",
        "PRAGMA",
    }
)


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50": None, "p95": None, "p99": None, "max": None}

    def percentile(p: float) -> float:
        return round(ordered[max(0, math.ceil(len(ordered) * p) - 1)], 3)

    return {
        "samples": len(ordered),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": round(ordered[-1], 3),
    }


class Measurements:
    def __init__(self) -> None:
        self.phase = ""
        self._lock = threading.Lock()
        self.values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.errors: list[dict[str, Any]] = []
        self.progress: list[dict[str, Any]] = []
        self.slow_write_windows: list[dict[str, Any]] = []
        self.started = 0.0
        self.first_skeleton_ms: float | None = None
        self.first_terms_ms: float | None = None

    def record(self, category: str, ms: float, phase: str | None = None) -> None:
        phase = self.phase if phase is None else phase
        if phase:
            with self._lock:
                self.values[phase][category].append(ms)

    def error(self, category: str, exc: Exception | int) -> None:
        with self._lock:
            self.errors.append(
                {
                    "phase": self.phase,
                    "category": category,
                    "error": type(exc).__name__ if isinstance(exc, Exception) else exc,
                }
            )

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                phase: {
                    name: distribution(values) for name, values in categories.items()
                }
                for phase, categories in self.values.items()
            }


METRICS = Measurements()


class MeasuredCursor(sqlite3.Cursor):
    def execute(self, sql: str, parameters: Any = ()) -> Any:
        return self._execute(super().execute, sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> Any:
        return self._execute(super().executemany, sql, parameters)

    def _execute(self, execute: Any, sql: str, parameters: Any) -> Any:
        connection = self.connection
        assert isinstance(connection, MeasuredConnection)
        command = sql.lstrip().upper()
        explicit = command.startswith("BEGIN IMMEDIATE")
        first_write = command.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        started = time.perf_counter()
        implicit_start = first_write and connection.write_started is None
        if implicit_start:
            connection.start_measurement(started, "implicit_write_upper_bound_ms")
        try:
            result = execute(sql, parameters)
        except BaseException:
            ended = time.perf_counter()
            # SQLite can roll back the entire transaction on statement failure
            # (for example INSERT OR ROLLBACK). Do not count later cleanup or
            # error reporting after the writer was already released.
            if not connection.in_transaction:
                connection.finish_measurement(ended_at=ended)
            raise
        finished = time.perf_counter()
        if connection.write_started is not None:
            sql_ms = (finished - started) * 1000
            connection.statement_count += 1
            connection.statement_ms += sql_ms
            if sql_ms > connection.largest_statement_ms:
                connection.largest_statement_ms = sql_ms
                words = command.split(maxsplit=1)
                verb = words[0] if words else ""
                connection.largest_statement_kind = (
                    verb if verb in SQL_VERBS else "OTHER"
                )
        if implicit_start:
            connection.implicit_first_statement_finished = finished
            METRICS.record(
                "implicit_acquisition_and_first_statement_ms",
                (connection.implicit_first_statement_finished - started) * 1000,
                connection.write_phase,
            )
        if explicit:
            acquired = finished
            connection.start_measurement(acquired, "write_lock_hold_ms")
            connection.begin_wait_ms = (acquired - started) * 1000
            METRICS.record("begin_immediate_wait_ms", (acquired - started) * 1000)
        if command.startswith(("COMMIT", "ROLLBACK")) and not command.startswith(
            "ROLLBACK TO"
        ):
            connection.finish_measurement(ended_at=finished)
        return result


class MeasuredConnection(sqlite3.Connection):
    write_started: float | None = None
    implicit_first_statement_finished: float | None = None
    write_phase = ""
    write_category = ""
    thread_cpu_started = 0.0
    statement_count = 0
    statement_ms = 0.0
    largest_statement_ms = 0.0
    largest_statement_kind = ""
    begin_wait_ms: float | None = None

    def start_measurement(self, started: float, category: str) -> None:
        self.write_started = started
        self.write_phase = METRICS.phase
        self.write_category = category
        self.thread_cpu_started = time.thread_time()
        self.statement_count = 0
        self.statement_ms = self.largest_statement_ms = 0.0
        self.largest_statement_kind = ""
        self.begin_wait_ms = None

    def cursor(self, factory: Any = MeasuredCursor) -> Any:
        return super().cursor(factory)

    def commit(self) -> None:
        started = time.perf_counter()
        try:
            super().commit()
        finally:
            ended = time.perf_counter()
            # Capture the DB-API boundary before any statistics lock, allocation
            # or logging. Reporting may wait after SQLite releases its writer.
            # A failed COMMIT that retains the transaction is timed to rollback.
            if not self.in_transaction:
                self.finish_measurement(
                    ended_at=ended, commit_ms=(ended - started) * 1000
                )

    def rollback(self) -> None:
        try:
            super().rollback()
        finally:
            ended = time.perf_counter()
            if not self.in_transaction:
                self.finish_measurement(ended_at=ended)

    def finish_measurement(
        self, *, ended_at: float, commit_ms: float | None = None
    ) -> None:
        if self.write_started is not None:
            held_ms = (ended_at - self.write_started) * 1000
            cpu_ms = (time.thread_time() - self.thread_cpu_started) * 1000
            phase, category = self.write_phase, self.write_category
            implicit_finished = self.implicit_first_statement_finished
            self.write_started = self.implicit_first_statement_finished = None
            if commit_ms is not None:
                METRICS.record("commit_ms", commit_ms, phase)
                METRICS.record(
                    "post_commit_reporting_ms",
                    (time.perf_counter() - ended_at) * 1000,
                    phase,
                )
            METRICS.record(
                category,
                held_ms,
                phase,
            )
            if implicit_finished is not None:
                # This excludes acquisition and the first DML, so it is a lower
                # bound on the real hold. A >500ms sample is conclusive even
                # when a competing writer delayed acquisition.
                METRICS.record(
                    "implicit_write_hold_lower_bound_ms",
                    (ended_at - implicit_finished) * 1000,
                    phase,
                )
            if held_ms >= 100 and phase:
                frames = []
                frame = sys._getframe(1)
                while frame is not None and len(frames) < 8:
                    filename = frame.f_code.co_filename.replace("\\", "/")
                    if "/app/" in filename:
                        frames.append(
                            f"app/{filename.split('/app/', 1)[1]}:"
                            f"{frame.f_lineno}:{frame.f_code.co_name}"
                        )
                    frame = frame.f_back
                sample = {
                    "phase": phase,
                    "category": category,
                    "hold_ms": round(held_ms, 3),
                    "thread_cpu_ms": round(cpu_ms, 3),
                    "begin_wait_ms": round(self.begin_wait_ms, 3)
                    if self.begin_wait_ms is not None
                    else None,
                    "commit_ms": round(commit_ms, 3) if commit_ms is not None else None,
                    "statement_count": self.statement_count,
                    "statement_ms": round(self.statement_ms, 3),
                    "largest_statement_ms": round(self.largest_statement_ms, 3),
                    "largest_statement_kind": self.largest_statement_kind,
                    "callers": frames,
                }
                with METRICS._lock:
                    if len(METRICS.slow_write_windows) < 1024:
                        METRICS.slow_write_windows.append(sample)
                if held_ms >= 500:
                    print(json.dumps({"slow_writer": sample}), flush=True)


def prepare_environment(data_dir: Path, bcrypt_rounds: int) -> None:
    # All credentials are generated in this process and are never reported.
    os.environ.update(
        {
            "DATA_DIR": str(data_dir),
            "STEWARD_ENABLED": "1",
            "STEWARD_WORKER_ENABLED": "0",
            "PERSONAL_FAMILY_VIEW_ENABLED": "1",
            "AGENT_RUNTIME_ENABLED": "1",
            "AGENT_SERVICE_SECRET": secrets.token_hex(32),
            "SECRET_KEY": secrets.token_hex(32),
            "ADMIN_JWT_SECRET": secrets.token_hex(32),
            "ADMIN_JWT_ISSUER": "steward-benchmark",
            "ADMIN_JWT_AUDIENCE": "steward-benchmark-admin",
            "DEV_SEED_DEMO_DATA": "0",
            "BCRYPT_ROUNDS": str(bcrypt_rounds),
            "STEWARD_SCAN_INTERVAL_SECONDS": "300",
            "STEWARD_ASSIST_CANDIDATE": "0",
            "STEWARD_ASSIST_RANKING": "0",
            "STEWARD_ASSIST_EXPLANATION": "0",
        }
    )
    sys.path.insert(0, str(BACKEND))
    result = subprocess.run(
        [str(PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("isolated migration failed: " + result.stderr[-1600:])
    original_connect = sqlite3.dbapi2.connect

    def measured_connect(*args: Any, **kwargs: Any) -> MeasuredConnection:
        kwargs["factory"] = MeasuredConnection
        return original_connect(*args, **kwargs)

    sqlite3.dbapi2.connect = measured_connect
    from app import config

    config.ensure_ready()
    logging.disable(logging.CRITICAL)


def seed_case(kind: str, size: int, account_count: int | None) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.models.account import Account
    from app.models.agent import AgentSession
    from app.models.relationship_facts import SourceFact
    from app.models.space import FamilySpace, SpaceMember
    from app.models.user import User
    from app.services.personal_family_view import initialize_account_views
    from app.utils.security import hash_pin
    from app.utils.timeutil import utcnow

    if kind == "ming":
        from app.dev_seed import _SEED_BIRTHS, _SEED_EDGES, _SEED_SPACE_MEMBERS

        members = list(_SEED_SPACE_MEMBERS["朱氏皇族"])
        relationships = [
            (to_name, from_name, "biological_parent")
            if direction == "elder"
            else (from_name, to_name, "spouse")
            for from_name, to_name, direction in _SEED_EDGES
            if direction in ("elder", "spouse")
        ]
        births = _SEED_BIRTHS
    else:
        members = [
            (f"benchmark-person-{i}", "m" if i % 2 == 0 else "f") for i in range(size)
        ]
        relationships = [
            (members[(i - 1) // 2][0], members[i][0], "biological_parent")
            for i in range(1, size)
        ]
        births = {
            name: (1870 + int(math.log2(i + 1)) * 20, 1, 1)
            for i, (name, _) in enumerate(members)
        }
    total_accounts = (
        len(members) if account_count is None else min(account_count, len(members))
    )
    pin = f"{secrets.randbelow(1_000_000):06d}"
    pin_hash = hash_pin(pin)
    now = utcnow()
    with SessionLocal() as session:
        people = {}
        for name, gender in members:
            birth = births.get(name)
            user = User(
                name=name,
                gender=gender,
                privacy_mode="handover",
                profile_status="identity_confirmed",
                profile_confirmed_at=now,
                created_at=now,
                birth={
                    "cal_type": "solar",
                    "date": f"{birth[0]:04d}-{birth[1]:02d}-{birth[2]:02d}",
                }
                if birth
                else None,
            )
            session.add(user)
            people[name] = user
        session.flush()
        owner = people[members[0][0]]
        space = FamilySpace(
            name="benchmark-family", kind="lineage", owner_id=owner.id, created_at=now
        )
        session.add(space)
        session.flush()
        accounts = []
        for index, user in enumerate(people.values()):
            session.add(
                SpaceMember(
                    space_id=space.id,
                    user_id=user.id,
                    added_by=owner.id,
                    role="space_admin" if index == 0 else "member",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            if index < total_accounts:
                account = Account(
                    user_id=user.id,
                    pin_hash=pin_hash,
                    status="claimed",
                    pin_must_change=False,
                    claimed_at=now,
                )
                session.add(account)
                accounts.append(account)
        session.flush()
        fact_count = 0
        seen = set()
        for a, b, relation in relationships:
            if a not in people or b not in people:
                continue
            pair = tuple(sorted((a, b))) if relation == "spouse" else (a, b)
            if (pair, relation) in seen:
                continue
            seen.add((pair, relation))
            session.add(
                SourceFact(
                    fact_type=relation,
                    subject_user_id=people[a].id,
                    object_user_id=people[b].id,
                    state="confirmed",
                    provenance="manual_entry",
                    space_id=space.id,
                    created_at=now,
                    updated_at=now,
                )
            )
            fact_count += 1
        session.flush()
        for account in accounts:
            initialize_account_views(
                session, account_id=account.id, user_id=account.user_id
            )
        agent_session = AgentSession(
            account_id=accounts[0].id,
            space_id=space.id,
            agent_kind="assistant",
            created_at=now,
            updated_at=now,
        )
        session.add(agent_session)
        session.commit()
        return {
            "space_id": space.id,
            "account_id": accounts[0].id,
            "viewer_user_id": owner.id,
            "agent_session_id": agent_session.id,
            "name": owner.name,
            "pin": pin,
            "people": len(people),
            "accounts": len(accounts),
            "facts": fact_count,
        }


class OnlineLoad:
    def __init__(self, fixture: dict[str, Any]) -> None:
        from fastapi.testclient import TestClient
        from app.main import app, internal_app

        self.fixture = fixture
        self.public = TestClient(app, raise_server_exceptions=False)
        self.internal = TestClient(internal_app, raise_server_exceptions=False)
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.viewer_ready_reported = False

        @app.middleware("http")
        async def measure_server(request: Any, call_next: Any) -> Any:
            phase = METRICS.phase
            started = time.perf_counter()
            response = await call_next(request)
            category = (
                "pfv_server_ms"
                if request.url.path == "/api/personal-family-view"
                else "login_server_ms"
            )
            METRICS.record(category, (time.perf_counter() - started) * 1000, phase)
            return response

        # No lifespan: the benchmark explicitly drives the real maintenance tick.
        response = self.public.post(
            "/api/auth/login", json={"name": fixture["name"], "pin": fixture["pin"]}
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"fixture authentication failed ({response.status_code})"
            )
        self.headers = {"Authorization": "Bearer " + response.json()["access_token"]}

    def start(self) -> None:
        for name, operation, interval in (
            ("login", self.login, 0.15),
            ("lease", self.lease, 0.15),
            ("pfv", self.view, 0.1),
            ("maintenance", self.reap, 0.2),
        ):

            def loop(
                fn: Any = operation, pause: float = interval, category: str = name
            ) -> None:
                while not self.stop.is_set():
                    try:
                        fn()
                    except Exception as exc:
                        METRICS.error(category, exc)
                    self.stop.wait(pause)

            thread = threading.Thread(
                target=loop, name=f"benchmark-{name}", daemon=True
            )
            thread.start()
            self.threads.append(thread)

    def login(self) -> None:
        started = time.perf_counter()
        phase = METRICS.phase
        response = self.public.post(
            "/api/auth/login",
            json={"name": self.fixture["name"], "pin": self.fixture["pin"]},
        )
        METRICS.record("login_client_ms", (time.perf_counter() - started) * 1000, phase)
        if response.status_code != 200:
            METRICS.error("login", response.status_code)

    def lease(self) -> None:
        from app.db import SessionLocal
        from app.models.agent import AgentRun, AgentSession
        from app.services import agent_queue, agent_tokens
        from app import config

        with SessionLocal() as session:
            agent_session = session.get(AgentSession, self.fixture["agent_session_id"])
            agent_queue.enqueue_run(
                session,
                agent_session=agent_session,
                kind="assistant",
                policy_version=config.POLICY_VERSION,
                tool_allowlist=[],
            )
        started = time.perf_counter()
        phase = METRICS.phase
        response = self.internal.post(
            "/internal/agent/jobs/lease",
            json={"kind": "assistant", "leased_by": "benchmark-sidecar"},
            headers={"Authorization": "Bearer " + agent_tokens.issue_service_token()},
        )
        METRICS.record("lease_client_ms", (time.perf_counter() - started) * 1000, phase)
        if response.status_code != 200:
            METRICS.error("lease", response.status_code)
            return
        with SessionLocal() as session:
            run = session.get(AgentRun, response.json()["run_id"])
            agent_queue.settle_run(session, run, status="succeeded")

    def reap(self) -> None:
        from app.db import SessionLocal
        from app.services import agent_queue

        started = time.perf_counter()
        phase = METRICS.phase
        with SessionLocal() as session:
            agent_queue.reaper_pass(session)
        METRICS.record(
            "maintenance_reaper_ms", (time.perf_counter() - started) * 1000, phase
        )

    def view(self) -> None:
        started = time.perf_counter()
        phase = METRICS.phase
        response = self.public.get(
            f"/api/personal-family-view?space_id={self.fixture['space_id']}&progressive=true",
            headers=self.headers,
        )
        elapsed = (time.perf_counter() - started) * 1000
        METRICS.record("pfv_client_ms", elapsed, phase)
        if response.status_code != 200:
            METRICS.error("pfv", response.status_code)
            return
        payload = response.json()
        progress = payload.get("progress") or {}
        if phase == "cold":
            elapsed_from_start = (time.perf_counter() - METRICS.started) * 1000
            if len(payload.get("nodes", [])) > 1 and METRICS.first_skeleton_ms is None:
                METRICS.first_skeleton_ms = elapsed_from_start
                print(
                    json.dumps(
                        {
                            "milestone": "skeleton",
                            "elapsed_ms": round(elapsed_from_start, 3),
                        }
                    ),
                    flush=True,
                )
            if payload.get("edges") and METRICS.first_terms_ms is None:
                METRICS.first_terms_ms = elapsed_from_start
            sample = {
                "elapsed_ms": round(elapsed_from_start, 3),
                "nodes": len(payload.get("nodes", [])),
                "terms": len(payload.get("edges", [])),
                "status": payload.get("status"),
                "phase": progress.get("phase"),
                "generation": progress.get("generation"),
                "revision": progress.get("revision"),
                "completed": progress.get("completed_count"),
                "total": progress.get("total_count"),
            }
            METRICS.progress.append(sample)
            if progress.get("phase") == "ready" and not self.viewer_ready_reported:
                self.viewer_ready_reported = True
                print(
                    json.dumps(
                        {
                            "milestone": "viewer_ready",
                            "elapsed_ms": sample["elapsed_ms"],
                        }
                    ),
                    flush=True,
                )

    def close(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=15)
            if thread.is_alive():
                METRICS.error("probe_shutdown", TimeoutError())
        self.public.close()
        self.internal.close()


def run_once(space_id: int) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services import steward

    with SessionLocal() as session:
        # GET/demand can legitimately leave a queued successor after publication.
        # Consume canonical queued work before requesting another scan.
        grant = steward.lease_next_steward_job(
            session, leased_by="benchmark-steward", space_id=space_id
        )
        if grant is None:
            steward.enqueue_steward_job(
                session,
                space_id=space_id,
                cause="integrity_scan",
                trigger_cursor=steward.current_event_watermark(session),
            )
            grant = steward.lease_next_steward_job(
                session, leased_by="benchmark-steward", space_id=space_id
            )
        if grant is None:
            raise RuntimeError("benchmark could not lease its job")
        started = time.perf_counter()
        summary = steward.execute_steward_job(
            session,
            grant,
            worker_id="benchmark-steward",
            expected_attempt=grant.attempt,
        )
        return {
            "seconds": round(time.perf_counter() - started, 4),
            "stats": summary.get("stats", {}),
            "job_id": grant.id,
        }


def scan_windows(windows: int) -> dict[str, Any]:
    from app import config
    from app.services import maintenance
    from app.db import SessionLocal
    from app.models.steward import StewardJob
    from sqlalchemy import func, select

    with SessionLocal() as session:
        prior_job_id = session.scalar(select(func.max(StewardJob.id))) or 0
    if config.STEWARD_SCAN_INTERVAL_SECONDS != 300:
        raise RuntimeError("acceptance scan interval must remain 300 seconds")
    config.STEWARD_WORKER_ENABLED = True
    started = time.monotonic()
    scheduled: list[float] = []
    ticks = 0
    try:
        while time.monotonic() - started < windows * 300 + 10:
            tick_started = time.perf_counter()
            counts = maintenance.run_maintenance_tick()
            METRICS.record(
                "maintenance_tick_ms", (time.perf_counter() - tick_started) * 1000
            )
            if counts.get("steward_scanned", 0):
                scheduled.append(round(time.monotonic() - started, 3))
                print(
                    json.dumps(
                        {
                            "milestone": "scheduled_scan",
                            "elapsed_seconds": scheduled[-1],
                        }
                    ),
                    flush=True,
                )
            ticks += 1
            remaining = windows * 300 + 10 - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(
                    min(max(0.05, config.MAINTENANCE_INTERVAL_SECONDS), remaining)
                )
    finally:
        config.STEWARD_WORKER_ENABLED = False
    with SessionLocal() as session:
        jobs = [
            {"id": job_id, "status": status}
            for job_id, status in session.execute(
                select(StewardJob.id, StewardJob.status)
                .where(StewardJob.id > prior_job_id)
                .order_by(StewardJob.id)
            )
        ]
    return {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "ticks": ticks,
        "scheduled_at_seconds": scheduled,
        "requested_windows": windows,
        "maintenance_interval_seconds": config.MAINTENANCE_INTERVAL_SECONDS,
        "jobs": jobs,
        "all_jobs_published": bool(jobs)
        and all(job["status"] == "succeeded" for job in jobs),
        "observed_intervals_seconds": [
            round(b - a, 3) for a, b in zip(scheduled, scheduled[1:])
        ],
    }


def single_case(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="fg-steward-benchmark-", delete=False
    ) as directory:
        prepare_environment(Path(directory), args.bcrypt_rounds)
        fixture = seed_case(args.case, args.size, args.accounts)
        load = OnlineLoad(fixture)
        METRICS.phase = "cold"
        METRICS.started = time.perf_counter()
        load.start()
        try:
            cold = run_once(fixture["space_id"])
            print(
                json.dumps({"milestone": "cold_published", "seconds": cold["seconds"]}),
                flush=True,
            )
            load.view()
            METRICS.phase = "hot"
            hot = run_once(fixture["space_id"])
            print(
                json.dumps({"milestone": "hot_published", "seconds": hot["seconds"]}),
                flush=True,
            )
            windows = None
            if args.scan_windows:
                METRICS.phase = "scans"
                windows = scan_windows(args.scan_windows)
        finally:
            load.close()
            # The historical baseline has no runtime module. New executors must
            # stop every writer before their isolated database is removed.
            if importlib.util.find_spec("app.services.steward_runtime") is not None:
                from app.services.steward_runtime import shutdown_runtime

                if not shutdown_runtime(timeout_seconds=10):
                    raise RuntimeError(
                        f"shutdown incomplete; isolated data retained at {directory}"
                    )
            METRICS.phase = ""
        from app.db import engine

        with engine.connect() as connection:
            pragmas = {
                key: connection.exec_driver_sql(f"PRAGMA {key}").scalar()
                for key in (
                    "journal_mode",
                    "busy_timeout",
                    "synchronous",
                    "wal_autocheckpoint",
                )
            }
        engine.dispose()
        shutil.rmtree(directory)
        measurements = METRICS.report()
        held = [
            value
            for categories in METRICS.values.values()
            for value in categories.get("write_lock_hold_ms", [])
        ]
        implicit_windows = [
            value
            for categories in METRICS.values.values()
            for value in categories.get("implicit_write_upper_bound_ms", [])
        ]
        writer_windows = held + implicit_windows
        scan_ok = (
            windows is None
            or sum(
                299 <= interval <= 320
                for interval in windows["observed_intervals_seconds"]
            )
            >= args.scan_windows
            and windows["all_jobs_published"]
        )
        return {
            "case": args.case,
            "people": fixture["people"],
            "accounts": fixture["accounts"],
            "facts": fixture["facts"],
            "cold": cold,
            "hot": hot,
            "first_skeleton_ms": METRICS.first_skeleton_ms,
            "first_terms_ms": METRICS.first_terms_ms,
            "progress_samples": METRICS.progress,
            "measurements_ms": measurements,
            "errors": METRICS.errors,
            "slow_write_windows": METRICS.slow_write_windows,
            "scans": windows,
            "environment": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "bcrypt_rounds": args.bcrypt_rounds,
                "transport": "in-process ASGI, real routes and independent DB sessions",
                **pragmas,
            },
            "writer_window_basis": "explicit hold plus implicit upper bound including acquisition",
            "measurement_contract": "dbapi-return-before-reporting-v3",
            "checks": {
                "no_request_failures": not METRICS.errors,
                "writer_p99_le_100ms": bool(writer_windows)
                and distribution(writer_windows)["p99"] <= 100,
                "no_writer_over_500ms": bool(writer_windows)
                and max(writer_windows) <= 500,
                "real_scan_windows": scan_ok,
                "skeleton_observed": METRICS.first_skeleton_ms is not None,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="*", type=int, default=[30, 50, 200])
    parser.add_argument("--ming", action="store_true")
    parser.add_argument(
        "--accounts",
        type=int,
        default=None,
        help="default: every person has an account",
    )
    parser.add_argument("--bcrypt-rounds", type=int, default=12)
    parser.add_argument(
        "--scan-windows",
        type=int,
        default=0,
        help="actual 300s windows, first case only",
    )
    parser.add_argument("--cold-repetitions", type=int, default=1)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--case", choices=("ming", "sparse"), help=argparse.SUPPRESS)
    parser.add_argument("--size", type=int, default=30, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.accounts is not None and args.accounts < 1:
        parser.error("accounts must be positive")
    if args.case:
        result = single_case(args)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.report:
            args.report.write_text(encoded + "\n")
        print(
            json.dumps(
                {
                    "case": result["case"],
                    "people": result["people"],
                    "cold_s": result["cold"]["seconds"],
                    "hot_s": result["hot"]["seconds"],
                    "checks": result["checks"],
                }
            )
        )
        return 0 if all(result["checks"].values()) else 1
    cases = ([("ming", 30)] if args.ming else []) + [("sparse", n) for n in args.sizes]
    results = []
    exit_code = 0
    with tempfile.TemporaryDirectory(prefix="fg-steward-reports-") as directory:
        for index, (case, size) in enumerate(cases * args.cold_repetitions):
            report = Path(directory) / f"case-{index}.json"
            command = [
                str(PYTHON),
                str(Path(__file__).resolve()),
                "--case",
                case,
                "--size",
                str(size),
                "--bcrypt-rounds",
                str(args.bcrypt_rounds),
                "--report",
                str(report),
            ]
            if args.accounts is not None:
                command += ["--accounts", str(args.accounts)]
            if index == 0 and args.scan_windows:
                command += ["--scan-windows", str(args.scan_windows)]
            print(f"Measuring {case}/{size}, trial {index + 1}", flush=True)
            process = subprocess.run(command, cwd=ROOT)
            if report.exists():
                results.append(json.loads(report.read_text()))
            else:
                results.append(
                    {"case": case, "people": size, "error": "case_failed_before_report"}
                )
            exit_code = max(exit_code, int(process.returncode != 0))
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(
                    json.dumps(
                        {
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "results": results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
    if not args.report:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
