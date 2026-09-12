#!/usr/bin/env python3
"""Steward 容量采样（09-11 R4/AC-4）。

隔离性：临时 DATA_DIR（不触碰业务库与 .env），Alembic 迁移链建库；
不启动调度器/worker/PFV，直接测量确定性重算内核 —— canonical job 内的
full-pair DerivedFact 重算（``steward._rebuild_space_derived`` 逐对调用
``derived_facts.get_or_compute``，与生产执行器同一代码路径）。

样本：N 人合成空间 + 全配对 confirmed direct_sibling 事实（N*(N-1)/2 条）+
全员 active 成员（生产可见性语义；可见性判定按生产 evaluate 路径真实执行）。
测量：
- cold：清空 DerivedFact 缓存后全量重算（时间 + SQL 语句数）；
- warm：事实不变的第二遍（缓存应全命中，但仍付 load_graph 成本——这正是
  需要用数据记录的瓶颈形态）。

规模策略：
- 50 人：全部 n*(n-1) 有序对完整测量；
- 200 人：按时间预算采样若干 viewer 的整行（每行 n-1 对），超预算即停，
  全矩阵数字按实测对数线性外推并在证据中标注 extrapolated=true。
  （200 人全矩阵实测在本机为小时级，不符合会话内可完成的验证预算；
  外推基于同一空间同一代码路径的实测对，样本量随证据记录。）

证据：backend/.steward-capacity-evidence.json —— 只含硬件/数据规模/时长/
查询计数/派生行数/DB 字节数与结论标记，绝无个人内容（人名为合成编号）。

用法：cd backend && .venv/bin/python scripts/steward_capacity.py
      [--sample-viewers 5] [--sample-seconds 240]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

# ---- 环境必须在导入 app 之前就绪（与 tests/conftest.py 同一合同）----
_TMP = tempfile.mkdtemp(prefix="familygraph-steward-capacity-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "capacity-secret-key-not-a-real-secret")
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("ADMIN_JWT_SECRET", "capacity-admin-jwt-secret-0123456789abcdef")
os.environ.setdefault("ADMIN_JWT_ISSUER", "familygraph-admin-capacity")
os.environ.setdefault("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-capacity")
os.environ.setdefault("AGENT_RUNTIME_ENABLED", "0")
# 直接测重算内核：不启动调度/worker，排除调度噪声
os.environ["STEWARD_ENABLED"] = "0"
os.environ["STEWARD_WORKER_ENABLED"] = "0"

EVIDENCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".steward-capacity-evidence.json"
)

# 生产默认租约（job 必须在 lease TTL 内完成，否则 reaper 判死回队）
LEASE_TTL_DEFAULT_SECONDS = 300


def main() -> int:
    parser = argparse.ArgumentParser(description="Steward full-pair recompute capacity sample")
    parser.add_argument("--sample-viewers", type=int, default=5, help="200 人样本 viewer 行数上限")
    parser.add_argument(
        "--sample-seconds", type=float, default=240.0, help="200 人样本时间预算（秒）"
    )
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"], cwd=root, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

    from sqlalchemy import delete, event, func, select
    from sqlalchemy.engine import Engine

    from app import config
    from app.db import SessionLocal
    from app.models.account import Account
    from app.models.derived_fact import DerivedFact
    from app.models.relationship_facts import SourceFact
    from app.models.space import FamilySpace, SpaceMember
    from app.models.user import User
    from app.services import steward
    from app.services.derived_facts import get_or_compute
    from app.utils import security, timeutil

    # ---- SQL 语句计数（进程级；本脚本单线程使用）----
    counters = {"statements": 0}

    def _count_statements(_conn, _cursor, _statement, _parameters, _context, _executemany):  # noqa: ANN001
        counters["statements"] += 1

    event.listen(Engine, "before_cursor_execute", _count_statements)

    evidence: dict = {
        "data_dir": _TMP,
        "hardware": {
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version.split()[0],
            "os": platform.platform(),
        },
        "lease_ttl_default_seconds": LEASE_TTL_DEFAULT_SECONDS,
        "scenarios": [],
    }
    print(f"[capacity] data_dir={_TMP}", flush=True)

    def mk_user(db, name: str) -> User:
        now = timeutil.utcnow()
        user = User(
            name=name,
            created_at=now,
            gender="unknown",
            privacy_mode="handover",
            profile_status="identity_confirmed",
            profile_confirmed_at=now,
        )
        user.account = Account(
            pin_hash=security.hash_pin("123456"),
            pin_must_change=False,
            token_version=0,
            failed_attempts=0,
            locked_until=None,
            status="claimed",
            claimed_at=now,
        )
        db.add(user)
        return user

    def build_space(db, n: int, tag: str) -> tuple[int, float]:
        print(f"[capacity] building n={n}...", flush=True)
        """N 人合成空间：全员 active 成员 + 全配对 confirmed direct_sibling。"""
        now = timeutil.utcnow()
        owner = mk_user(db, f"{tag}-owner")
        db.flush()  # 取得 owner.id 供空间外键引用
        space = FamilySpace(name=tag, kind="lineage", owner_id=owner.id, created_at=now)
        db.add(space)
        db.flush()
        persons = [mk_user(db, f"{tag}-p{i:04d}") for i in range(n)]
        db.flush()
        print(f"[capacity]   users built ({n + 1})", flush=True)
        db.add_all(
            SpaceMember(
                space_id=space.id,
                user_id=p.id,
                role="member",
                status="active",
                created_at=now,
                updated_at=now,
            )
            for p in persons
        )
        db.flush()
        # 事实播种走 Core 批量插入：19900 行 ORM unit-of-work 的对象构建开销
        # 远超测量本身（播种不是被测操作）；生产可见性语义仍由重算路径真实执行。
        from sqlalchemy import insert

        db.execute(
            insert(SourceFact),
            [
                {
                    "fact_type": "direct_sibling",
                    "subject_user_id": persons[i].id,
                    "object_user_id": persons[j].id,
                    "space_id": space.id,
                    "provenance": "manual_entry",
                    "state": "confirmed",
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for i in range(n)
                for j in range(i + 1, n)
            ],
        )
        print(f"[capacity]   facts bulk-inserted ({n * (n - 1) // 2})", flush=True)
        start = time.perf_counter()
        db.commit()
        print(f"[capacity]   committed in {time.perf_counter() - start:.1f}s", flush=True)
        return space.id, time.perf_counter() - start

    def wipe_all(db) -> None:
        """场景间清库（依赖序），避免上一场景数据干扰下一场景。"""
        db.execute(delete(SourceFact))
        db.execute(delete(SpaceMember))
        db.execute(delete(DerivedFact))
        db.execute(delete(FamilySpace))
        db.execute(delete(Account))
        db.execute(delete(User))
        db.commit()

    def db_bytes() -> int:
        return config.DB_PATH.stat().st_size

    def measure(
        space_id: int, n: int, *, viewer_limit: int | None, budget_seconds: float | None
    ) -> dict:
        # Note: 容量采样方法合同 —
        # 见 .agent-notes/implemented/testing/2026-09-12-steward-release-verification-method.md
        """full-pair 重算测量。viewer_limit=None 测全部有序对；否则按 viewer 行
        采样。budget_seconds 对 cold/warm 两阶段逐对生效（生产路径单对重算可达
        秒级，行粒度预算会放大成小时级，不可用于会话内验证）；提前截断时按
        实测对数线性外推并明确标注 extrapolated/budget_hit。"""
        out: dict = {}
        for phase in ("cold", "warm"):
            if phase == "cold":
                db = SessionLocal()
                try:
                    db.execute(delete(DerivedFact))
                    db.commit()
                finally:
                    db.close()
            db = SessionLocal()
            try:
                persons_sorted = sorted(
                    steward._space_visible_user_ids(db, db.get(FamilySpace, space_id))
                )
                if viewer_limit is None or viewer_limit >= len(persons_sorted):
                    viewers = persons_sorted
                    extrapolated = False
                else:
                    viewers = persons_sorted[:viewer_limit]
                    extrapolated = True
                measured_pairs = 0
                counters["statements"] = 0
                budget_hit = False
                start = time.perf_counter()
                for viewer in viewers:
                    for target in persons_sorted:
                        if target == viewer:
                            continue
                        get_or_compute(
                            db, viewer_user_id=viewer, target_user_id=target, space_id=space_id
                        )
                        measured_pairs += 1
                        if measured_pairs % 5 == 0:
                            print(
                                f"[capacity]   {phase} pair#{measured_pairs} "
                                f"{time.perf_counter() - start:.1f}s",
                                flush=True,
                            )
                        if budget_seconds is not None and (
                            time.perf_counter() - start > budget_seconds
                        ):
                            budget_hit = True
                            break
                    if budget_hit:
                        break
                db.commit()
                elapsed = time.perf_counter() - start
            finally:
                db.close()
            out[phase] = {
                "elapsed_seconds": round(elapsed, 3),
                "sql_statements": counters["statements"],
                "measured_pairs": measured_pairs,
                "budget_hit": budget_hit,
                "seconds_per_pair_ms": round(elapsed / measured_pairs * 1000, 4)
                if measured_pairs
                else None,
                "extrapolated": extrapolated,
            }
        db = SessionLocal()
        try:
            derived_rows = int(
                db.scalar(select(func.count()).where(DerivedFact.space_id == space_id)) or 0
            )
        finally:
            db.close()
        for phase in ("cold", "warm"):
            m = out[phase]
            if m["measured_pairs"] < n * (n - 1):
                ratio = (n * (n - 1)) / m["measured_pairs"]
                m["full_matrix_estimated_seconds"] = round(m["elapsed_seconds"] * ratio, 1)
                m["full_matrix_estimated_sql_statements"] = int(m["sql_statements"] * ratio)
            else:
                m["full_matrix_estimated_seconds"] = m["elapsed_seconds"]
                m["full_matrix_estimated_sql_statements"] = m["sql_statements"]
        out["derived_rows_after_cold"] = derived_rows
        return out

    try:
        for n in (50, 200):
            db = SessionLocal()
            try:
                space_id, build_elapsed = build_space(db, n, f"capacity-{n}")
            finally:
                db.close()
            # 采样策略：两个规模统一按 viewer 行采样（--sample-viewers，默认 5），
            # 50 人可用 --sample-viewers 0 强制全矩阵。外推按实测对数线性折算
            # 并在证据中标注 extrapolated，绝不把估算冒充实测。
            limit = args.sample_viewers or None
            result_n = measure(space_id, n, viewer_limit=limit, budget_seconds=args.sample_seconds)
            scenario = {
                "persons": n,
                "source_facts": n * (n - 1) // 2,
                "ordered_pairs_full": n * (n - 1),
                "build_seconds": round(build_elapsed, 2),
                "db_bytes_after_build": db_bytes(),
                "measurement": result_n,
            }
            evidence["scenarios"].append(scenario)
            cold, warm = result_n["cold"], result_n["warm"]
            print(f"[capacity] n={n} measured", flush=True)
            print(
                f"[capacity] n={n} build={scenario['build_seconds']}s "
                f"cold={cold['elapsed_seconds']}s/{cold['measured_pairs']}pairs"
                f"{'(extrapolated)' if cold['extrapolated'] else ''} "
                f"warm={warm['elapsed_seconds']}s"
            )
            db = SessionLocal()
            try:
                wipe_all(db)
            finally:
                db.close()
    finally:
        event.remove(Engine, "before_cursor_execute", _count_statements)

    # ---- 结论标记（R4：只有数据证明需要才提出独立 worker/增量计算后续项）----
    conclusions = []
    for scenario in evidence["scenarios"]:
        cold = scenario["measurement"]["cold"]
        warm = scenario["measurement"]["warm"]
        est = cold["full_matrix_estimated_seconds"]
        conclusions.append(
            {
                "persons": scenario["persons"],
                "full_matrix_cold_seconds": est,
                "basis": "extrapolated" if cold["extrapolated"] else "measured",
                "exceeds_lease_ttl_300s": est > LEASE_TTL_DEFAULT_SECONDS,
                # warm 每对仍付出 >50% cold 成本 = 缓存命中不省图构建（load_graph）
                "warm_retains_majority_cost": (
                    warm["seconds_per_pair_ms"] is not None
                    and cold["seconds_per_pair_ms"] is not None
                    and warm["seconds_per_pair_ms"] > cold["seconds_per_pair_ms"] * 0.5
                ),
            }
        )
    evidence["conclusions"] = conclusions
    evidence["followup_needed"] = any(c["exceeds_lease_ttl_300s"] for c in conclusions)
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[capacity] evidence written to {EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
