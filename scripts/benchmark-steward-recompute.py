#!/usr/bin/env python3
"""Steward 短事务重算基准（09-13 AC1 性能证据脚本）。

在隔离 DATA_DIR（临时目录 + 独立 SQLite WAL 库）中构造稀疏多代树
（每人是下一层两个孩子的家长，近似二叉谱系），测量：

- 冷重算：首个作业（全部配对解析 + 缓存行写入 + 视图重建）墙钟时间；
- 热重算：输入未变化（指纹命中短路）的第二个作业墙钟时间；
- 并发写延迟：冷重算进行中，独立连接提交一条 probe 写入的耗时
  （短事务合同的核心证据：写锁上界 ≈ 分块 upsert，而非整族计算）。

用法（仓库根目录）：
    python3 scripts/benchmark-steward-recompute.py --sizes 30 50
输出 JSON 报告（含环境、人数、耗时、并发写 p95/最大值），不触碰生产库。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def prepare_environment(data_dir: Path) -> None:
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["STEWARD_ENABLED"] = "1"
    os.environ["STEWARD_WORKER_ENABLED"] = "0"
    os.environ.setdefault("STEWARD_LEASE_TTL_SECONDS", "3600")
    # 基准只在临时隔离库运行：密钥用进程内随机值，绝不复用任何真实凭据
    os.environ.setdefault("SECRET_KEY", os.urandom(48).hex())
    os.environ.setdefault("ADMIN_JWT_SECRET", os.urandom(48).hex())
    os.environ.setdefault("ADMIN_JWT_ISSUER", "familygraph-bench")
    os.environ.setdefault("ADMIN_JWT_AUDIENCE", "familygraph-bench-admin")
    sys.path.insert(0, str(BACKEND_DIR))
    result = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed: {result.stderr[-2000:]}")


def seed_sparse_tree(session, space, total_people: int) -> None:
    """稀疏二叉谱系：node i 的父母是 2i+1 / 2i+2（人数不足时只挂存在的父母）。"""
    from app.models.relationship_facts import SourceFact
    from app.models.space import SpaceMember
    from app.models.user import User
    from app.services.source_facts import transition_source_fact

    people = [
        User(
            name=f"bench-person-{index}",
            gender="m" if index % 2 == 0 else "f",
            profile_status="identity_confirmed",
            created_at=datetime.now(timezone.utc),
        )
        for index in range(total_people)
    ]
    session.add_all(people)
    session.flush()
    for person in people:
        session.add(
            SpaceMember(
                space_id=space.id,
                user_id=person.id,
                added_by=people[0].id,
                role="member",
                status="active",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
    session.flush()
    facts = 0
    for child_index in range(total_people):
        for parent_index in (2 * child_index + 1, 2 * child_index + 2):
            if parent_index >= total_people:
                continue
            fact = SourceFact(
                fact_type="biological_parent",
                subject_user_id=people[parent_index].id,
                object_user_id=people[child_index].id,
                state="confirmed",
                provenance="manual_entry",
                space_id=space.id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(fact)
            facts += 1
    session.commit()
    print(f"  seeded {total_people} people, {facts} confirmed parent facts")


def measure_concurrent_writes(
    run_flag: threading.Event, samples: list[float], failures: list[str]
) -> None:
    """计算期间用独立连接提交 probe 写入，记录每次提交耗时。"""
    from app.db import SessionLocal
    from app.models.user import User
    from app.utils.timeutil import utcnow

    index = 0
    while not run_flag.is_set():
        time.sleep(0.05)
        started = time.monotonic()
        try:
            with SessionLocal() as session:
                session.add(User(name=f"bench-probe-{index}", created_at=utcnow()))
                session.commit()
            samples.append((time.monotonic() - started) * 1000)
        except Exception as exc:  # noqa: BLE001 — 记录失败即报告（不吞异常正文进报告）
            samples.append(float("inf"))
            failures.append(f"{type(exc).__name__}: {exc}")
            if len(failures) <= 1:
                print(f"  concurrent write FAILED: {type(exc).__name__}: {exc}")
        index += 1


def run_once(session, space) -> dict:
    from app.services import steward

    job, _created = steward.enqueue_steward_job(
        session, space_id=space.id, cause="integrity_scan", trigger_cursor=10**9
    )
    grant = steward.lease_next_steward_job(session, leased_by="bench-worker")
    assert grant is not None
    started = time.monotonic()
    summary = steward.run_steward_job(
        session, grant, worker_id="bench-worker", expected_attempt=grant.attempt
    )
    elapsed = time.monotonic() - started
    return {"seconds": round(elapsed, 3), "stats": summary["stats"]}


def benchmark_size(total_people: int) -> dict:
    from app.db import SessionLocal
    from app.models.space import FamilySpace
    from app.models.user import User
    from app.services import personal_family_view

    with SessionLocal() as session:
        owner = User(
            name="bench-owner",
            gender="m",
            profile_status="identity_confirmed",
            created_at=datetime.now(timezone.utc),
        )
        session.add(owner)
        session.flush()
        from app.models.account import Account

        session.add(
            Account(user_id=owner.id, pin_hash="bench-not-a-real-pin")
        )
        session.flush()
        space = FamilySpace(
            name="bench-space", kind="lineage", owner_id=owner.id, created_at=datetime.now(timezone.utc)
        )
        session.add(space)
        session.flush()
        from app.models.space import SpaceMember

        session.add(
            SpaceMember(
                space_id=space.id,
                user_id=owner.id,
                added_by=owner.id,
                role="space_admin",
                status="active",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
        seed_sparse_tree(session, space, total_people)
        personal_family_view.initialize_account_views(
            session, account_id=_account_id(session, owner.id), user_id=owner.id
        )
        session.commit()

        run_flag = threading.Event()
        samples: list[float] = []
        failures: list[str] = []
        writer = threading.Thread(
            target=measure_concurrent_writes, args=(run_flag, samples, failures)
        )
        writer.start()
        time.sleep(0.1)
        cold = run_once(session, space)
        run_flag.set()
        writer.join(timeout=30)
        hot = run_once(session, space)

        from sqlalchemy import select as _select
        from app.models.personal_family_view import PersonalFamilyView

        view_rows = list(session.scalars(_select(PersonalFamilyView.status)).all())
        report_views = sorted(view_rows)

    write_samples = sorted(samples)
    report = {
        "people": total_people,
        "cold_seconds": cold["seconds"],
        "hot_seconds": hot["seconds"],
        "cold_stats": cold["stats"],
        "hot_short_circuit": hot["stats"].get("fingerprint_short_circuit") == 1,
        "concurrent_writes": len(write_samples),
        "concurrent_write_ms_p95": (
            round(write_samples[int(len(write_samples) * 0.95)], 1) if write_samples else None
        ),
        "concurrent_write_ms_p99": (
            round(write_samples[min(int(len(write_samples) * 0.99), len(write_samples) - 1)], 1)
            if write_samples
            else None
        ),
        "concurrent_write_ms_max": (
            round(max(write_samples), 1) if write_samples and max(write_samples) != float("inf") else None
        ),
        "concurrent_write_failures": len(failures),
        "concurrent_write_errors": failures[:3],
        "pfv_view_statuses": report_views,
    }
    print(
        f"  cold={report['cold_seconds']}s hot={report['hot_seconds']}s "
        f"concurrent_writes={report['concurrent_writes']} "
        f"p95={report['concurrent_write_ms_p95']}ms max={report['concurrent_write_ms_max']}ms "
        f"failures={report['concurrent_write_failures']}"
    )
    return report


def _account_id(session, user_id: int) -> int:
    from sqlalchemy import select

    from app.models.account import Account

    return int(session.scalar(select(Account.id).where(Account.user_id == user_id)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[30])
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="fg-bench-") as tmp:
        prepare_environment(Path(tmp))
        from app import config

        config.ensure_ready()
        print(f"benchmark sizes={args.sizes} data_dir={tmp}")
        reports = [benchmark_size(size) for size in args.sizes]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "environment": {
            "python": sys.version.split()[0],
            "sqlite_busy_timeout_ms": 5000,
            "derived_commit_chunk": os.environ.get("STEWARD_DERIVED_COMMIT_CHUNK", "50"),
        },
        "results": reports,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
