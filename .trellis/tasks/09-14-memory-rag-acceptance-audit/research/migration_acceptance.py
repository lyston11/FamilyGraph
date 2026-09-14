"""Independent populated migration oracle; all inputs are synthetic.

Extract the committed pre-D backend to a temporary directory, create a real
confirmed RAG dependency, and test the candidate's migrations with FK ON/OFF
on the connection Alembic actually uses. Never reads a developer database.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

BASELINE = "bc76e95c7091fcb9977dc6595d5e65e4181f1329"
TABLES = (
    "rag_documents",
    "rag_chunks",
    "rag_chunks_fts",
    "memories",
    "memory_candidates",
)

SEED = r"""
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path.cwd() / 'tests'))
import conftest
import app
assert Path(conftest.__file__).resolve().parent == Path.cwd() / 'tests'
assert Path(app.__file__).resolve().parent == Path.cwd() / 'app'
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from app import config
from app.db import SessionLocal, engine
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGChunk, RAGDocument
from app.services import memory_rag, memory_sources, rag_maintenance
from app.utils.timeutil import utcnow

command.upgrade(Config('alembic.ini'), 'head')
with SessionLocal() as db:
    user, space = conftest.create_agent_fixture(db, name='migration-oracle-synthetic')
    db.add(PlatformFeatureConfig(id=1, memory_enabled=True, rag_enabled=True, updated_at=utcnow()))
    db.flush()
    def confirm(summary, source=None, quote=None):
        candidate = memory_rag.propose_candidate(
            db, author_account_id=user.account.id, source=source or {'kind': 'manual'},
            source_quote=quote if quote is not None else summary, summary=summary,
            suggested_scope='private', purpose='synthetic migration verification',
        )
        return memory_rag.confirm_candidate(db, candidate_id=candidate.id,
            confirmer=user, confirmer_account=user.account, scope='private')

    root = confirm('Synthetic orchard migration reference. ' * 30)
    document = db.scalar(select(RAGDocument).where(RAGDocument.source_type == 'memory',
        RAGDocument.source_id == str(root.id)))
    chunk = db.scalar(select(RAGChunk).where(RAGChunk.document_id == document.id)
        .order_by(RAGChunk.chunk_index))
    saved = confirm('Synthetic saved fragment remains exact', source={
        'kind': 'rag_chunk', 'document_id': document.id, 'chunk_id': chunk.id,
        'revision': document.revision, 'index_version': chunk.index_version, 'space_id': space.id,
    }, quote=chunk.text)
    unverified = confirm('Synthetic unverified source retained')
    unverified.source_verification = 'unverified'
    revoked = confirm('Synthetic revoked source retained')
    memory_rag.revoke_memory(db, memory_id=revoked.id, account_id=user.account.id)
    unknown = confirm('Synthetic unknown tombstone retained')
    unknown_document = db.scalar(select(RAGDocument).where(RAGDocument.source_type == 'memory',
        RAGDocument.source_id == str(unknown.id)))
    unknown_document.status = 'invalidated'
    unknown_document.invalidation_reason = 'unknown_legacy'
    # The old released API accepts this synthetic target. It is historical
    # fixture data, not a claimed production algorithm in the candidate.
    rag_maintenance.stage_index_version(db, target_version='fts5-trigram-v3-review',
        worker_id='synthetic-baseline')
    db.commit()
    access = memory_sources.memory_access(db, saved, actor=user, account=user.account,
        space_id=space.id)
    assert access.readable
    fixture = dict(saved_id=saved.id, chunk_id=chunk.id, document_id=document.id,
        user_id=user.id, space_id=space.id, baseline_readable=access.readable)
engine.dispose()
destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(config.DB_PATH) as source, sqlite3.connect(destination / 'seed.db') as target:
    source.backup(target)
(destination / 'fixture.json').write_text(json.dumps(fixture, indent=2) + '\n')
print(json.dumps({'seeded': True, 'baseline_readable': access.readable}))
"""

MIGRATE = r"""
import json
import re
import sys
import sqlalchemy
from sqlalchemy import event
from alembic import command
from alembic.config import Config

observed = {'foreign_keys': [], 'final_foreign_keys': [], 'writes': [], 'empty_writer_locks': []}
def normalized(statement):
    while True:
        clean = re.sub(r'^\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/)', '', statement, count=1, flags=re.S)
        if clean == statement:
            return ' '.join(statement.strip().upper().split())
        statement = clean
def sample_fk(connection, target):
    cursor = connection.cursor()
    target.append(cursor.execute('PRAGMA foreign_keys').fetchone()[0])
    cursor.close()
original = sqlalchemy.engine_from_config
def engine_with_fk(*args, **kwargs):
    engine = original(*args, **kwargs)
    @event.listens_for(engine, 'connect')
    def fk(connection, _record):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=' + sys.argv[1])
        cursor.close()
        sample_fk(connection, observed['foreign_keys'])
    @event.listens_for(engine, 'checkin')
    def final_fk(connection, _record):
        if connection is not None:
            sample_fk(connection, observed['final_foreign_keys'])
    @event.listens_for(engine, 'before_cursor_execute')
    def statements(connection, _cursor, statement, _parameters, context, _many):
        sql = normalized(statement)
        sample_fk(connection.connection.driver_connection, observed['foreign_keys'])
        context.oracle_empty_writer = sql == 'UPDATE RAG_DOCUMENTS SET ID = ID WHERE 0'
        if context.oracle_empty_writer:
            return
        if re.match(r'^(?:UPDATE|INSERT|DELETE|REPLACE|CREATE|DROP|ALTER|REINDEX|VACUUM|WITH)\b', sql) or (sql.startswith('PRAGMA ') and '=' in sql):
            observed['writes'].append(sql.split()[0])
    @event.listens_for(engine, 'after_cursor_execute')
    def completed(_connection, cursor, _statement, _parameters, context, _many):
        if getattr(context, 'oracle_empty_writer', False):
            observed['empty_writer_locks'].append(cursor.rowcount)
    return engine
sqlalchemy.engine_from_config = engine_with_fk
code = 0
try:
    getattr(command, sys.argv[2])(Config('alembic.ini'), sys.argv[3])
except Exception as error:
    observed['error_type'] = type(error).__name__
    reason = str(error)
    if type(error) is RuntimeError and reason.startswith('RAG canonical identity conflict;'):
        observed['refusal'] = 'canonical_identity_conflict'
    elif type(error) is RuntimeError and reason.startswith('Cannot losslessly downgrade RAG lifecycle:'):
        observed['refusal'] = 'lifecycle_downgrade_incompatible'
        observed['refusal_counts'] = {key: int(value) for key, value in re.findall(r'(historical_chunks|key_collisions|distinct_reasons|saved_dependencies)=(\d+)', reason)}
    else:
        observed['refusal'] = 'unexpected_error'
    code = 1
print('MIGRATION_ORACLE ' + json.dumps(observed))
raise SystemExit(code)
"""

ACCESS = r"""
import json
import sys
from app.db import SessionLocal
from app.models.memory import Memory
from app.models.user import User
from app.services.memory_sources import memory_access
fixture = json.loads(sys.argv[1])
with SessionLocal() as db:
    user = db.get(User, fixture['user_id'])
    memory = db.get(Memory, fixture['saved_id'])
    access = memory_access(db, memory, actor=user, account=user.account, space_id=fixture['space_id'])
    print(json.dumps({'saved_dependency_readable': access.readable}))
"""


def environment(backend: Path, data: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(backend), str(backend / "tests")))
    if data is not None:
        env["DATA_DIR"] = str(data)
    return env


def seed(backend: Path, root: Path) -> None:
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", BASELINE, "backend"], cwd=backend.parent
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(root / "baseline", filter="data")
    baseline = root / "baseline/backend"
    (baseline / ".venv").symlink_to(
        (backend / ".venv").resolve(), target_is_directory=True
    )
    result = subprocess.run(
        [str(backend / ".venv/bin/python"), "-c", SEED, str(root)],
        cwd=baseline,
        env=environment(baseline),
        capture_output=True,
        text=True,
        timeout=90,
    )
    (root / "seed.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("synthetic baseline seed failed; see local seed.log")


def snapshot(database: Path, columns: dict | None = None) -> dict:
    with sqlite3.connect(database) as db:
        if columns is None:
            columns = {
                table: [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
                for table in TABLES
            }
            columns["rag_chunks_fts"].insert(0, "rowid")
        rows = {}
        for table, names in columns.items():
            fields = ",".join('"' + name + '"' for name in names)
            rows[table] = sorted(
                db.execute(f'SELECT {fields} FROM "{table}"').fetchall(), key=repr
            )
        schema = db.execute(
            "SELECT name,type,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        heads = db.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall()
        fk = db.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "columns": columns,
        "rows": rows,
        "schema": schema,
        "heads": heads,
        "fk": fk,
    }


def migrate(
    backend: Path, data: Path, fk: int, action: str, revision: str
) -> tuple[int, dict]:
    result = subprocess.run(
        [str(backend / ".venv/bin/python"), "-c", MIGRATE, str(fk), action, revision],
        cwd=backend,
        env=environment(backend, data),
        capture_output=True,
        text=True,
        timeout=60,
    )
    (data / f"{action}-{revision}.log").write_text(result.stdout + result.stderr)
    observations = [
        line.removeprefix("MIGRATION_ORACLE ")
        for line in result.stdout.splitlines()
        if line.startswith("MIGRATION_ORACLE ")
    ]
    info = json.loads(observations[-1]) if observations else {}
    for line in result.stderr.splitlines():
        marker = "RAG lifecycle preflight: "
        if marker in line:
            report = json.loads(line.split(marker, 1)[1])
            info["preflight"] = {
                key: report[key]
                for key in (
                    "duplicate_groups",
                    "revision_conflicts",
                    "saved_rag_dependencies",
                )
            }
    return result.returncode, info


def readable(backend: Path, data: Path, fixture: dict) -> bool:
    result = subprocess.run(
        [str(backend / ".venv/bin/python"), "-c", ACCESS, json.dumps(fixture)],
        cwd=backend,
        env=environment(backend, data),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        return False
    return json.loads(result.stdout).get("saved_dependency_readable") is True


def constraints_enforced(database: Path, document_id: int) -> dict[str, bool]:
    checks = {}
    with sqlite3.connect(database) as db:
        names = [row[1] for row in db.execute("PRAGMA table_info(rag_documents)")]
        checks["input_digest_column_added"] = "content_sha256" in names
        fields = ",".join(name for name in names if name != "id")
        attempts = {
            "canonical_unique_enforced": (
                f"INSERT INTO rag_documents ({fields}) SELECT {fields} FROM rag_documents WHERE id=?",
                (document_id,),
            ),
            "revision_mirror_enforced": (
                "UPDATE rag_documents SET source_revision=revision+1 WHERE id=?",
                (document_id,),
            ),
        }
        for key, (sql, params) in attempts.items():
            db.execute("SAVEPOINT constraint_probe")
            try:
                db.execute(sql, params)
                checks[key] = False
            except sqlite3.IntegrityError as error:
                accepted_codes = (
                    {"SQLITE_CONSTRAINT_UNIQUE"}
                    if key == "canonical_unique_enforced"
                    else {"SQLITE_CONSTRAINT_TRIGGER", "SQLITE_CONSTRAINT_CHECK"}
                )
                checks[key] = error.sqlite_errorname in accepted_codes
            finally:
                db.execute("ROLLBACK TO constraint_probe")
                db.execute("RELEASE constraint_probe")
    return checks


def run_case(backend: Path, root: Path, fk: int, variant: str) -> dict:
    data = Path(tempfile.mkdtemp(prefix=f"fk{fk}-{variant}-", dir=root))
    database = data / "db/app.db"
    database.parent.mkdir(parents=True)
    with (
        sqlite3.connect(root / "seed.db") as source,
        sqlite3.connect(database) as target,
    ):
        source.backup(target)
    fixture = json.loads((root / "fixture.json").read_text())
    with sqlite3.connect(database) as db:
        if variant == "duplicate":
            names = [
                row[1]
                for row in db.execute("PRAGMA table_info(rag_documents)")
                if row[1] != "id"
            ]
            fields = ",".join(names)
            db.execute(
                f"INSERT INTO rag_documents ({fields}) SELECT {fields} FROM rag_documents WHERE id=?",
                (fixture["document_id"],),
            )
        elif variant == "mirror":
            db.execute(
                "UPDATE rag_documents SET source_revision=revision+1 WHERE id=?",
                (fixture["document_id"],),
            )
        db.commit()
    preparation = {}
    if variant == "old_downgrade":
        original = snapshot(database)
        code, info = migrate(backend, data, fk, "downgrade", "0045_rag_index_lifecycle")
        prepared = snapshot(database, original["columns"])
        preparation = {
            "preparation_succeeded": code == 0,
            "preparation_preserved_rows": original["rows"] == prepared["rows"],
            "preparation_dependency_readable": readable(
                root / "baseline/backend", data, fixture
            ),
            "preparation_reached_0045": prepared["heads"]
            == [("0045_rag_index_lifecycle",)],
        }
    before = snapshot(database)
    action, revision = (
        ("downgrade", "0044_rag_citation_contract")
        if variant == "old_downgrade"
        else ("upgrade", "head")
    )
    code, info = migrate(backend, data, fk, action, revision)
    after = snapshot(database, before["columns"])
    checks = {
        **preparation,
        "actual_connection_fk": bool(info.get("foreign_keys"))
        and set(info["foreign_keys"]) == {fk},
        "final_connection_fk": bool(info.get("final_foreign_keys"))
        and set(info["final_foreign_keys"]) == {fk},
        "allowed_writer_locks_change_no_rows": all(
            value == 0 for value in info.get("empty_writer_locks", [])
        ),
        "all_original_rows_preserved": before["rows"] == after["rows"],
        "foreign_key_check_empty": not after["fk"],
        "expected_exit": (code == 0) if variant == "populated" else (code != 0),
    }
    if variant != "populated":
        checks.update(
            schema_unchanged=before["schema"] == after["schema"],
            revision_unchanged=before["heads"] == after["heads"],
            refused_before_mutation=info.get("writes") == [],
        )
        if variant == "old_downgrade":
            counts = info.get("refusal_counts", {})
            checks["expected_preflight_refusal"] = (
                info.get("refusal") == "lifecycle_downgrade_incompatible"
                and counts.get("historical_chunks", 0) > 0
                and counts.get("key_collisions", 0) > 0
                and counts.get("saved_dependencies", 0) > 0
            )
            checks["saved_dependency_readable"] = readable(
                root / "baseline/backend", data, fixture
            )
        else:
            counts = info.get("preflight", {})
            primary = (
                "duplicate_groups" if variant == "duplicate" else "revision_conflicts"
            )
            secondary = (
                "revision_conflicts" if variant == "duplicate" else "duplicate_groups"
            )
            checks["expected_preflight_refusal"] = (
                info.get("refusal") == "canonical_identity_conflict"
                and counts.get(primary) == 1
                and counts.get(secondary) == 0
            )
    else:
        checks["saved_dependency_readable"] = readable(backend, data, fixture)
        checks.update(constraints_enforced(database, fixture["document_id"]))
        checks["new_revision_reached"] = after["heads"] == [
            ("0047_rag_lifecycle_integrity",)
        ]
    digest = hashlib.sha256(
        json.dumps(before["rows"], sort_keys=True).encode()
    ).hexdigest()
    return {
        "case": f"fk{fk}-{variant}",
        "passed": all(checks.values()),
        "checks": checks,
        "original_rows_sha256": digest,
        "migration": info,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    backend = args.backend.resolve()
    root = args.fixture_root or Path(
        tempfile.mkdtemp(prefix="familygraph-migration-oracle-")
    )
    if not (root / "seed.db").exists():
        seed(backend, root)
    if args.seed_only:
        print(json.dumps({"fixture_root": str(root), "seeded": True}))
        return 0
    cases = [
        run_case(backend, root, fk, variant)
        for fk in (0, 1)
        for variant in ("populated", "duplicate", "mirror", "old_downgrade")
    ]
    report = {
        "oracle_version": 2,
        "oracle_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidate_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=backend, text=True
        ).strip(),
        "candidate_migrations": {
            name: hashlib.sha256(
                (backend / "migrations/versions" / name).read_bytes()
            ).hexdigest()
            if (backend / "migrations/versions" / name).exists()
            else None
            for name in (
                "0045_rag_index_lifecycle.py",
                "0046_context_execution_contract.py",
                "0047_rag_lifecycle_integrity.py",
            )
        },
        "seed_commit": BASELINE,
        "synthetic_only": True,
        "cases": cases,
        "passed": sum(item["passed"] for item in cases),
        "total": len(cases),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "fixture_root": str(root),
            }
        )
    )
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
