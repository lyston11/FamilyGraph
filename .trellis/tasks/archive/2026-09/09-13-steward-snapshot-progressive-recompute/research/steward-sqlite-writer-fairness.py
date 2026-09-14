"""Bounded SQLite WAL writer fairness experiment; synthetic data only.

Each thread owns a real independent sqlite3 connection. Background writers use
BEGIN IMMEDIATE -> one INSERT -> optional controlled bookkeeping delay -> COMMIT.
The online connection uses Python sqlite3's implicit DEFERRED transaction for
one INSERT followed by COMMIT, with the real SQLite busy_timeout=5000 handler.

The ledger's integer primary key records actual successful SQLite write order.
Timing records are appended only after commit/rollback returns. Entry/return
timestamps are DBAPI observations, not internal SQLite lock/unlock probes.
No production modules, family calculation, HTTP routes, or benchmark machinery
are imported. Databases are temporary; the script and JSON are the artifacts.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sqlite3
import statistics
import tempfile
import threading
import time
from pathlib import Path


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0}
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered), 6),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 6),
        "max_ms": round(ordered[-1], 6),
    }


def run_trial(directory: Path, case: dict, duration_ms: int, trial_index: int) -> dict:
    database = directory / f"trial-{trial_index}.sqlite"
    keeper = sqlite3.connect(database, timeout=5.0, isolation_level=None)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("PRAGMA synchronous=NORMAL")
    keeper.execute("PRAGMA busy_timeout=5000")
    keeper.execute("PRAGMA wal_autocheckpoint=1000")
    keeper.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY, actor TEXT NOT NULL, "
        "local_sequence INTEGER NOT NULL, payload TEXT NOT NULL)"
    )
    environment = {
        name: keeper.execute(f"PRAGMA {name}").fetchone()[0]
        for name in ("journal_mode", "synchronous", "busy_timeout", "wal_autocheckpoint")
    }
    background_count = case["background_connections"]
    barrier = threading.Barrier(background_count + 2)
    first_writer_acquired = threading.Event()
    start = threading.Event()
    stop = threading.Event()
    origin: list[int] = []
    traces: dict[str, list[dict]] = {}
    setup_errors: list[dict] = []
    payload = "synthetic:" + "x" * 247
    background_admission = threading.Lock()
    shared_occupied_ns = [0]

    def relative_now() -> int:
        return time.perf_counter_ns() - origin[0]

    def connection(implicit: bool) -> sqlite3.Connection:
        value = sqlite3.connect(
            database, timeout=5.0, isolation_level="DEFERRED" if implicit else None
        )
        value.execute("PRAGMA busy_timeout=5000")
        value.execute("PRAGMA synchronous=NORMAL")
        value.execute("PRAGMA wal_autocheckpoint=1000")
        return value

    def background(actor: str) -> None:
        records: list[dict] = []
        traces[actor] = records
        owns_admission = False
        try:
            db = connection(False)
        except Exception as exc:
            setup_errors.append({"actor": actor, "error": repr(exc)})
            barrier.abort()
            return
        try:
            barrier.wait(timeout=5)
            start.wait(timeout=5)
            local_sequence = 0
            slice_started = relative_now()
            while not stop.is_set() and relative_now() < duration_ms * 1_000_000:
                local_sequence += 1
                record = {"actor": actor, "local_sequence": local_sequence}
                mode = case["pacing"]
                if mode.startswith("shared_"):
                    record["admission_call_ns"] = relative_now()
                    background_admission.acquire()
                    owns_admission = True
                    record["admission_return_ns"] = relative_now()
                cpu_started = time.thread_time_ns()
                record["begin_call_ns"] = relative_now()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    record["begin_return_ns"] = relative_now()
                    first_writer_acquired.set()
                    record["insert_call_ns"] = relative_now()
                    cursor = db.execute(
                        "INSERT INTO ledger (actor,local_sequence,payload) VALUES (?,?,?)",
                        (actor, local_sequence, payload),
                    )
                    record["insert_return_ns"] = relative_now()
                    success_id = cursor.lastrowid
                    if case["controlled_hold_ms"]:
                        time.sleep(case["controlled_hold_ms"] / 1000)
                    record["commit_call_ns"] = relative_now()
                    db.commit()
                    record["commit_return_ns"] = relative_now()
                    record["success_id"] = success_id
                except Exception as exc:
                    record["error_return_ns"] = relative_now()
                    record["error"] = {"type": type(exc).__name__, "message": str(exc)}
                    db.rollback()
                    record["rollback_return_ns"] = relative_now()
                record["thread_cpu_ns"] = time.thread_time_ns() - cpu_started
                records.append(record)
                if mode.startswith("shared_"):
                    # No second background BEGIN can already be waiting inside
                    # SQLite when this common pause begins. Online writes never
                    # use this admission lock. shared_serial_none is the control
                    # for the serialization itself, without a common pause.
                    if "success_id" in record:
                        shared_occupied_ns[0] += record["commit_return_ns"] - record["begin_return_ns"]
                    budget_ms = case.get("shared_hold_budget_ms", 0)
                    if budget_ms and shared_occupied_ns[0] >= budget_ms * 1_000_000:
                        assert not db.in_transaction
                        pause = {
                            "pause_call_ns": relative_now(),
                            "requested_pause_ms": 100,
                            "in_transaction": db.in_transaction,
                            "background_occupied_ns": shared_occupied_ns[0],
                        }
                        time.sleep(0.100)
                        pause["pause_return_ns"] = relative_now()
                        record["outside_pause"] = pause
                        shared_occupied_ns[0] = 0
                    background_admission.release()
                    owns_admission = False
                    continue
                # All pacing is outside the committed transaction. No shared
                # application mutex or SQLite busy-handler replacement is used.
                sleep_ms = 0
                if mode == "every_commit_5ms":
                    sleep_ms = 5
                elif mode == "every_commit_10ms":
                    sleep_ms = 10
                elif mode == "slice_10ms_yield_5ms" and relative_now() - slice_started >= 10_000_000:
                    sleep_ms = 5
                if sleep_ms:
                    pause = {
                        "pause_call_ns": relative_now(),
                        "requested_pause_ms": sleep_ms,
                        "in_transaction": db.in_transaction,
                    }
                    time.sleep(sleep_ms / 1000)
                    pause["pause_return_ns"] = relative_now()
                    # This annotation is intentionally after the outside-lock wait.
                    record["outside_pause"] = pause
                    slice_started = relative_now()
        except Exception as exc:
            setup_errors.append({"actor": actor, "error": repr(exc)})
        finally:
            if owns_admission:
                background_admission.release()
            db.close()

    def online() -> None:
        actor = "online-insert"
        records: list[dict] = []
        traces[actor] = records
        try:
            db = connection(True)
        except Exception as exc:
            setup_errors.append({"actor": actor, "error": repr(exc)})
            barrier.abort()
            return
        try:
            barrier.wait(timeout=5)
            start.wait(timeout=5)
            if not first_writer_acquired.wait(timeout=5):
                raise RuntimeError("no background writer acquired its first transaction")
            local_sequence = 0
            while not stop.is_set() and relative_now() < duration_ms * 1_000_000:
                local_sequence += 1
                record = {
                    "actor": actor,
                    "local_sequence": local_sequence,
                    "transaction_before_insert": db.in_transaction,
                }
                cpu_started = time.thread_time_ns()
                record["insert_call_ns"] = relative_now()
                try:
                    cursor = db.execute(
                        "INSERT INTO ledger (actor,local_sequence,payload) VALUES (?,?,?)",
                        (actor, local_sequence, payload),
                    )
                    record["insert_return_ns"] = relative_now()
                    success_id = cursor.lastrowid
                    record["transaction_after_insert"] = db.in_transaction
                    record["commit_call_ns"] = relative_now()
                    db.commit()
                    record["commit_return_ns"] = relative_now()
                    record["success_id"] = success_id
                except Exception as exc:
                    record["error_return_ns"] = relative_now()
                    record["error"] = {"type": type(exc).__name__, "message": str(exc)}
                    db.rollback()
                    record["rollback_return_ns"] = relative_now()
                record["thread_cpu_ns"] = time.thread_time_ns() - cpu_started
                records.append(record)
                time.sleep(0.020)
        except Exception as exc:
            setup_errors.append({"actor": actor, "error": repr(exc)})
        finally:
            db.close()

    threads = [
        threading.Thread(target=background, args=(f"background-{index}",), daemon=True)
        for index in range(background_count)
    ]
    threads.append(threading.Thread(target=online, daemon=True))
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=5)
    except threading.BrokenBarrierError:
        stop.set()
    origin.append(time.perf_counter_ns())
    start.set()
    hard_deadline = time.monotonic() + duration_ms / 1000 + 7
    for thread in threads:
        thread.join(timeout=max(0, hard_deadline - time.monotonic()))
    if any(thread.is_alive() for thread in threads):
        stop.set()
        raise RuntimeError("experiment exceeded its bounded deadline")
    wall_duration_ms = relative_now() / 1_000_000
    success_order = [
        {"success_id": row[0], "actor": row[1], "local_sequence": row[2]}
        for row in keeper.execute("SELECT id,actor,local_sequence FROM ledger ORDER BY id")
    ]
    keeper.close()

    background_records = [
        record
        for actor, records in traces.items()
        if actor.startswith("background-")
        for record in records
    ]
    successful_background = [record for record in background_records if "success_id" in record]
    online_records = traces.get("online-insert", [])
    successful_online = [record for record in online_records if "success_id" in record]
    for record in successful_online:
        start_ns, end_ns = record["insert_call_ns"], record["insert_return_ns"]
        completed = [
            item for item in successful_background
            if start_ns <= item["commit_return_ns"] <= end_ns
            and item["success_id"] < record["success_id"]
        ]
        overtaking = [item for item in completed if item["begin_call_ns"] >= start_ns]
        record["background_commits_during_insert_call"] = len(completed)
        record["background_new_begins_and_commits_during_insert_call"] = len(overtaking)
        record["first_overtaking_success_id"] = min(
            (item["success_id"] for item in overtaking), default=None
        )
        record["last_overtaking_success_id"] = max(
            (item["success_id"] for item in overtaking), default=None
        )
    ordered_background = sorted(successful_background, key=lambda item: item["success_id"])
    exposed_gaps_us = [
        (after["begin_call_ns"] - before["commit_return_ns"]) / 1000
        for before, after in zip(ordered_background, ordered_background[1:])
        if after["begin_call_ns"] >= before["commit_return_ns"]
    ]
    longest_online = max(
        successful_online,
        key=lambda item: item["insert_return_ns"] - item["insert_call_ns"],
        default=None,
    )
    summary = {
        "background_commits": len(successful_background),
        "online_commits": len(successful_online),
        "failed_operations": sum("error" in item for rows in traces.values() for item in rows),
        "setup_errors": setup_errors,
        "wall_duration_ms": round(wall_duration_ms, 3),
        "background_begin_wait": quantiles([
            (item["begin_return_ns"] - item["begin_call_ns"]) / 1_000_000
            for item in successful_background
        ]),
        "background_explicit_hold": quantiles([
            (item["commit_return_ns"] - item["begin_return_ns"]) / 1_000_000
            for item in successful_background
        ]),
        "background_commit": quantiles([
            (item["commit_return_ns"] - item["commit_call_ns"]) / 1_000_000
            for item in successful_background
        ]),
        "online_insert_acquisition_and_statement": quantiles([
            (item["insert_return_ns"] - item["insert_call_ns"]) / 1_000_000
            for item in successful_online
        ]),
        "online_thread_cpu": quantiles([
            item["thread_cpu_ns"] / 1_000_000 for item in successful_online
        ]),
        "online_commit": quantiles([
            (item["commit_return_ns"] - item["commit_call_ns"]) / 1_000_000
            for item in successful_online
        ]),
        "observable_positive_background_gaps": {
            "samples": len(exposed_gaps_us),
            "median_us": round(statistics.median(exposed_gaps_us), 3) if exposed_gaps_us else None,
            "note": "DBAPI commit return to next BEGIN call; not an internal lock timing trace.",
        },
        "longest_online_request": longest_online,
    }
    return {
        "trial_index": trial_index,
        "case": case,
        "configured_burst_ms": duration_ms,
        "perf_counter_origin_ns": origin[0],
        "environment": environment,
        "summary": summary,
        "trace": traces,
        "sqlite_success_order": success_order,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-ms", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=84660)
    parser.add_argument("--suite", choices=("base", "shared_gap"), default="base")
    parser.add_argument(
        "--output", type=Path, default=Path("/private/tmp/steward-sqlite-writer-fairness.json")
    )
    args = parser.parse_args()
    if not 100 <= args.duration_ms <= 1500:
        parser.error("duration must be between 100 and 1500 ms per bounded trial")
    cases = []
    if args.suite == "base":
        for repeat in range(3):
            for pacing in ("none", "every_commit_5ms", "every_commit_10ms", "slice_10ms_yield_5ms"):
                cases.append({"background_connections": 1, "controlled_hold_ms": 1, "pacing": pacing, "repeat": repeat})
        for repeat in range(2):
            for pacing in ("none", "slice_10ms_yield_5ms"):
                cases.append({"background_connections": 2, "controlled_hold_ms": 1, "pacing": pacing, "repeat": repeat})
                cases.append({"background_connections": 1, "controlled_hold_ms": 0, "pacing": pacing, "repeat": repeat})
    else:
        for background_count in (1, 2):
            for budget_ms in (50, 100):
                cases.append({
                    "background_connections": background_count,
                    "controlled_hold_ms": 1,
                    "pacing": "shared_gap_100ms",
                    "shared_hold_budget_ms": budget_ms,
                    "repeat": 0,
                })
        for repeat in range(2):
            cases.append({"background_connections": 2, "controlled_hold_ms": 1, "pacing": "shared_serial_none", "repeat": repeat})
    random.Random(args.seed).shuffle(cases)
    results = []
    with tempfile.TemporaryDirectory(prefix="steward-sqlite-fairness-", dir="/private/tmp") as temporary:
        for index, case in enumerate(cases):
            result = run_trial(Path(temporary), case, args.duration_ms, index)
            results.append(result)
            summary = result["summary"]
            print(json.dumps({
                "trial": index, **case,
                "background_commits": summary["background_commits"],
                "online_commits": summary["online_commits"],
                "online_max_ms": summary["online_insert_acquisition_and_statement"].get("max_ms"),
                "background_hold_max_ms": summary["background_explicit_hold"].get("max_ms"),
                "failures": summary["failed_operations"],
            }), flush=True)
    report = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "production_revision_under_investigation": "84660c8",
        "production_code_imported": False,
        "synthetic_data_only": True,
        "seed": args.seed,
        "suite": args.suite,
        "timing_basis": "perf_counter_ns immediately around DBAPI calls; records appended after transaction end",
        "success_order_basis": "integer primary keys assigned in the same committed SQLite INSERT transactions",
        "limitations": [
            "Controlled 1 ms delay represents short application bookkeeping; zero-delay SQL-only controls are retained.",
            "Equal burst duration, not equal transaction throughput; outside yielding intentionally reduces writer throughput.",
            "Python scheduling can widen observed API entry/return bounds; internal SQLite retry instants are not exposed.",
            "The SQL-only test contains no family graph, auth bcrypt, ASGI, real demand traffic, or production scheduler.",
            "A reproduction demonstrates this mechanism is possible; it does not identify the production trace's causal transaction.",
            "The shared-gap suite serializes background admissions and pauses after cumulative observed hold; its no-pause serialization control is separate.",
        ],
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary_path = args.output.with_name(args.output.stem + ".summary.json")
    summary_report = {key: value for key, value in report.items() if key != "results"}
    summary_report["results"] = [
        {key: value for key, value in result.items() if key not in ("trace", "sqlite_success_order")}
        for result in results
    ]
    summary_path.write_text(json.dumps(summary_report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "summary": str(summary_path), "trials": len(results)}), flush=True)


if __name__ == "__main__":
    main()
