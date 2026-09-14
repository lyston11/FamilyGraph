"""Reproduce the frozen delivery claim SELECT on synthetic in-memory rows.

Run with the project's Python, passing --backend for its import path. No app
database or family recomputation is used. The intent table comes from ORM DDL;
only the three indexes present at 3cb4550 are created for the baseline. The
generation table is a two-column surrogate because this SELECT uses only its
id and status. Foreign-key enforcement is off in this isolated sqlite3 database.
The dependency expression is loaded from the frozen source AST, not HEAD.
"""

from __future__ import annotations

import argparse
import ast
import json
import platform
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--revision", default="3cb4550")
    parser.add_argument(
        "--output", type=Path, default=Path("/private/tmp/steward-delivery-query-plan.json")
    )
    args = parser.parse_args()
    backend = args.backend.resolve()
    sys.path.insert(0, str(backend))

    from sqlalchemy import and_, or_, select
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.orm import aliased
    from sqlalchemy.schema import CreateIndex, CreateTable
    from sqlalchemy.sql.elements import ColumnElement

    from app.models.steward import StewardDeliveryIntent, StewardGeneration

    source = subprocess.check_output(
        ["git", "-C", str(backend.parent), "show", f"{args.revision}:backend/app/services/steward_delivery.py"],
        text=True,
    )
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_prerequisites_ready"
    )
    namespace = {
        "aliased": aliased,
        "StewardDeliveryIntent": StewardDeliveryIntent,
        "select": select,
        "or_": or_,
        "and_": and_,
        "ColumnElement": ColumnElement,
        "_TERMINOLOGY_COMPLETE_KEY": "terminology:complete",
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<frozen prerequisites>", "exec"), namespace)
    ready = namespace["_prerequisites_ready"]
    now = datetime(2026, 9, 14, 14)
    dialect = sqlite.dialect()

    def query(scoped: bool, prerequisites: bool = True) -> str:
        # Matches _claim_due at the recorded revision, before Python source validation.
        statement = select(StewardDeliveryIntent).join(StewardGeneration).where(
            StewardGeneration.status == "published",
            StewardDeliveryIntent.status == "pending",
            StewardDeliveryIntent.kind != "inferred_overlay",
            (StewardDeliveryIntent.available_at.is_(None))
            | (StewardDeliveryIntent.available_at <= now),
            (StewardDeliveryIntent.lease_until.is_(None))
            | (StewardDeliveryIntent.lease_until <= now),
        )
        if prerequisites:
            statement = statement.where(ready())
        if scoped:
            statement = statement.where(StewardDeliveryIntent.generation_id == 1)
        return str(
            statement.order_by(StewardDeliveryIntent.id)
            .limit(1)
            .compile(dialect=dialect, compile_kwargs={"literal_binds": True})
        )

    def measure(connection: sqlite3.Connection, sql: str) -> dict:
        samples = []
        for _ in range(21):
            started = time.perf_counter()
            connection.execute(sql).fetchone()
            samples.append((time.perf_counter() - started) * 1000)
        samples = samples[1:]  # Exclude the first prepare/cache warmup.
        return {
            "median_ms": round(statistics.median(samples), 6),
            "max_ms": round(max(samples), 6),
            "samples_ms": [round(sample, 6) for sample in samples],
            "plan": [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)],
        }

    baseline_indexes = {"ix_sdi_due", "ix_sdi_generation", "ix_sdi_effect"}
    reports = []
    for people in (50, 200):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE steward_generations (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        connection.execute("INSERT INTO steward_generations VALUES (1, 'published')")
        connection.execute(str(CreateTable(StewardDeliveryIntent.__table__).compile(dialect=dialect)))
        created_indexes = set()
        for index in StewardDeliveryIntent.__table__.indexes:
            if index.name in baseline_indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=dialect)))
                created_indexes.add(index.name)
        assert created_indexes == baseline_indexes

        # Production order: recommendations, all target batches, all refreshes,
        # complete, then assist. Every target is synthetic and initially pending.
        rows = [
            ("recommend", f"recommend:{index}", {"fact_id": index, "revision": 1})
            for index in range(1, people)
        ]
        for viewer in range(1, people + 1):
            targets = [target for target in range(1, people + 1) if target != viewer]
            for offset in range(0, len(targets), 8):
                payload = {
                    "phase": "target",
                    "viewer_account_id": viewer,
                    "root_user_id": viewer,
                    "targets": [
                        {"target_user_id": target, "path": [{"type": "parent", "from": viewer, "to": target}]}
                        for target in targets[offset : offset + 8]
                    ],
                }
                rows.append(("terminology", f"terminology:{viewer}:{offset // 8}", payload))
        rows.extend(
            ("terminology", f"terminology:refresh:{viewer}", {"phase": "refresh", "viewer_account_id": viewer})
            for viewer in range(1, people + 1)
        )
        rows.extend(
            [("terminology", "terminology:complete", {"phase": "complete"}), ("assist", "assist", {})]
        )
        connection.executemany(
            "INSERT INTO steward_delivery_intents "
            "(generation_id,space_id,intent_key,kind,payload_json,effect_fingerprint,status,attempt,created_at,updated_at) "
            "VALUES (1,1,?,?,?,NULL,'pending',0,?,?)",
            [
                (key, kind, json.dumps(payload), now.isoformat(" "), now.isoformat(" "))
                for kind, key, payload in rows
            ],
        )
        connection.commit()
        cases = {}
        for scoped in (True, False):
            for prerequisites in (False, True):
                name = f'{"scoped" if scoped else "global"}_{"prerequisites" if prerequisites else "baseline"}'
                cases[name] = measure(connection, query(scoped, prerequisites))
        connection.execute("CREATE INDEX synthetic_sdi_status_id ON steward_delivery_intents(status, id)")
        for scoped in (True, False):
            name = f'{"scoped" if scoped else "global"}_with_status_id'
            cases[name] = measure(connection, query(scoped))
        reports.append({"people_shape": people, "intent_count": len(rows), "cases": cases})
        connection.close()

    result = {
        "frozen_revision": args.revision,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "real_family_data": False,
        "family_recompute": False,
        "database": "in-memory sqlite3; model intent table and baseline indexes; minimal generation table",
        "interpretation_limit": "Dense synthetic manifest with all targets meaningful. Isolates SQL cost, not actual benchmark causality or writer wall time.",
        "sql": {"scoped": query(True), "global": query(False)},
        "results": reports,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "results": [
        {"people_shape": report["people_shape"], "intent_count": report["intent_count"],
         "median_ms": {name: case["median_ms"] for name, case in report["cases"].items()}}
        for report in reports
    ]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
