"""Bounded coordinators and one spawn CPU worker, shared by both listeners.

Maintenance dispatches and returns. Each coordinator submits one bounded search
slice at a time, so other spaces get FIFO turns instead of waiting for a whole
family or a hard target. No Session or ORM instance crosses the process boundary.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from multiprocessing.process import BaseProcess
from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.services.relationship_resolver import SearchSlice, SearchState

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cpu: ProcessPoolExecutor | None = None
_coordinators: ThreadPoolExecutor | None = None
_active: dict[int, Future[None]] = {}
_owner = f"steward-{os.getpid()}-{uuid.uuid4().hex[:16]}"
_stopping = threading.Event()
_retiring_processes: list[BaseProcess] = []
_retiring_managers: list[threading.Thread] = []


class RuntimeStopping(InterruptedError):
    """Recoverable coordinator cancellation; no partial result is published."""


def run_slice(state: SearchState) -> SearchSlice:
    """One finite CPU slice; state/result are frozen/picklable graph data."""
    from app.services.relationship_resolver import advance_search

    global _cpu
    if _stopping.is_set():
        raise RuntimeStopping("steward runtime is stopping")
    with _lock:
        if _stopping.is_set():
            raise RuntimeStopping("steward runtime is stopping")
        if _cpu is None:
            _cpu = ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn")
            )
        executor = _cpu
        future = executor.submit(
            advance_search, state, max_expansions=config.STEWARD_SEARCH_SLICE_EXPANSIONS
        )
    try:
        return future.result(timeout=30)
    except (BrokenProcessPool, TimeoutError) as exc:
        if _stopping.is_set():
            raise RuntimeStopping("steward runtime is stopping") from exc
        with _lock:
            if _cpu is executor:
                _cpu = None
                processes = list((getattr(executor, "_processes", None) or {}).values())
                _retiring_processes.extend(processes)
                manager = getattr(executor, "_executor_manager_thread", None)
                if manager is not None:
                    _retiring_managers.append(manager)
                for process in processes:
                    if process.is_alive():
                        process.terminate()
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError("steward CPU worker exited") from exc


def _execute(job_id: int, attempt: int) -> None:
    from app.db import SessionLocal
    from app.services import steward

    try:
        with SessionLocal() as session:
            job = steward.require_steward_job(session, job_id)
            steward.execute_steward_job(
                session,
                job,
                worker_id=_owner,
                expected_attempt=attempt,
                drain_delivery=False,
            )
    except Exception as exc:
        logger.warning("steward coordinator stopped job=%s error=%s", job_id, type(exc).__name__)


def launch_due(*, space_id: int | None = None, limit: int | None = None) -> int:
    """Lease and launch only available coordinator slots; no unbounded queue."""
    from app.db import SessionLocal
    from app.services import steward, steward_overlay

    global _coordinators
    if not (config.STEWARD_ENABLED and config.STEWARD_WORKER_ENABLED) or _stopping.is_set():
        return 0
    launched = 0
    with _lock:
        for job_id in list(_active):
            if _active[job_id].done():
                del _active[job_id]
        capacity = config.STEWARD_MAX_CONCURRENT_JOBS - len(_active)
        if _coordinators is None:
            _coordinators = ThreadPoolExecutor(
                max_workers=config.STEWARD_MAX_CONCURRENT_JOBS, thread_name_prefix="steward-core"
            )
        for _ in range(min(capacity, limit if limit is not None else capacity)):
            with SessionLocal() as session:
                job = steward.lease_next_steward_job(session, leased_by=_owner, space_id=space_id)
                if job is None:
                    overlay = steward_overlay.claim_due(
                        session.get_bind(), owner=_owner, space_id=space_id
                    )
                    if overlay is None:
                        break
                    _active[-overlay.intent_id] = _coordinators.submit(
                        steward_overlay.execute, session.get_bind(), overlay
                    )
                    launched += 1
                    continue
                _active[job.id] = _coordinators.submit(_execute, job.id, job.attempt)
                launched += 1
    return launched


def is_stopping() -> bool:
    return _stopping.is_set()


def start_runtime() -> None:
    with _lock:
        if any(not future.done() for future in _active.values()):
            if _stopping.is_set():
                raise RuntimeError("previous steward coordinators are still stopping")
            return
        _active.clear()
        _stopping.clear()


def shutdown_runtime(*, timeout_seconds: float = 10.0) -> bool:
    """Stop work within a deadline; true means no coordinator can still write.

    Pure CPU children can be terminated safely. Coordinator threads must finish
    their current bounded database operation and fenced failure settlement. A
    false result tells callers to retain the database until a later drain.
    """
    global _cpu, _coordinators
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    _stopping.set()
    with _lock:
        cpu, _cpu = _cpu, None
        coordinators, _coordinators = _coordinators, None
        futures = list(_active.values())
        if cpu is not None:
            # Python 3.12 has no public terminate_workers API. Capture children
            # before shutdown clears its process registry, then reap explicitly.
            _retiring_processes.extend((getattr(cpu, "_processes", None) or {}).values())
            manager = getattr(cpu, "_executor_manager_thread", None)
            if manager is not None:
                _retiring_managers.append(manager)
        processes = list(_retiring_processes)
        managers = list(_retiring_managers)
    if coordinators is not None:
        coordinators.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        if process.is_alive():
            process.terminate()
    if cpu is not None:
        cpu.shutdown(wait=False, cancel_futures=True)
    # The executor manager owns Process.join(). Joining the same child here
    # races its waitpid on macOS and can leave a dead child reporting alive.
    for manager in managers:
        manager.join(timeout=max(0.0, deadline - time.monotonic()))
    for process in processes:
        if process.is_alive():
            process.kill()
    pending = [future for future in futures if not future.done()]
    if pending:
        wait(pending, timeout=max(0.0, deadline - time.monotonic()))
    with _lock:
        for job_id in list(_active):
            if _active[job_id].done():
                del _active[job_id]
        _retiring_processes[:] = [process for process in processes if process.is_alive()]
        _retiring_managers[:] = [manager for manager in managers if manager.is_alive()]
        return not _active and not _retiring_processes and not _retiring_managers
