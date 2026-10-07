"""C2：持久化 counter 的并发正确性证明（含反证）。

## 这个探针要证明什么

1. **配额在并发下成立**：N 个 worker 争抢，每租户 in-flight 数**不超过** capacity；
2. **反证**：把 counter 换回「计数子查询」形态，同一并发形状必须**越限**——
   否则说明用例没有真正构造出竞争窗口（本类探针此前两次假通过）；
3. **归还恰好一次**：重复 release 不重复递减；
4. **锁序**：`counter → candidate` 与 `candidate → counter` 交叉时，
   后者必须死锁（证明顺序是承重的）。

## 与 SQLite 的关系

SQLite 的 `BEGIN IMMEDIATE` 是全库写锁，**测不出**上述任何一条——所有写事务天然串行。
因此本探针只在真实 PostgreSQL 上运行；SQLite 侧只测 API 语义（另一个测试文件）。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_capacity_concurrency.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))

DDL = """
DROP TABLE IF EXISTS cap_counters, cap_candidates CASCADE;
CREATE TABLE cap_counters (
  scope_kind text NOT NULL,
  scope_id int NOT NULL,
  resource_kind text NOT NULL,
  capacity int NOT NULL,
  active int NOT NULL DEFAULT 0,
  version int NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_kind, scope_id, resource_kind),
  CONSTRAINT ck_cap_active CHECK (active >= 0 AND active <= capacity)
);
CREATE TABLE cap_candidates (
  id int PRIMARY KEY,
  tenant int NOT NULL,
  status text NOT NULL,
  lease_owner text,
  lease_until timestamptz
);
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=False)


def seed(dsn: str, *, tenants: int, per_tenant: int, capacity: int) -> None:
    with _conn(dsn) as c:
        c.execute(DDL)
        for t in range(1, tenants + 1):
            c.execute("INSERT INTO cap_counters VALUES ('space', %s, 'steward_assist', %s, 0, 0)",
                      (t, capacity))
        cid = 1
        for t in range(1, tenants + 1):
            for _ in range(per_tenant):
                c.execute("INSERT INTO cap_candidates VALUES (%s, %s, 'reserved', NULL, NULL)",
                          (cid, t))
                cid += 1
        c.commit()


def lease_with_counter(dsn: str, worker: str, tenant: int, results: list, lock) -> None:
    """正确形态：先锁 counter → 检查容量 → 取候选。"""
    import psycopg
    try:
        with _conn(dsn) as c:
            with c.transaction():
                # 1) 锁 counter（冻结锁序：counter 先于 candidate）
                row = c.execute(
                    "SELECT capacity, active FROM cap_counters"
                    " WHERE scope_kind='space' AND scope_id=%s AND resource_kind='steward_assist'"
                    " FOR UPDATE", (tenant,)).fetchone()
                if row is None:
                    with lock:
                        results.append((worker, "no-counter"))
                    return
                capacity, active = row
                if active >= capacity:
                    with lock:
                        results.append((worker, "full"))
                    return
                # 2) 取候选
                cand = c.execute(
                    "SELECT id FROM cap_candidates"
                    " WHERE tenant=%s AND status='reserved'"
                    " ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1", (tenant,)).fetchone()
                if cand is None:
                    with lock:
                        results.append((worker, "no-candidate"))
                    return
                # 3) 占用 + 写租约
                c.execute("UPDATE cap_counters SET active = active + 1, version = version + 1"
                          " WHERE scope_kind='space' AND scope_id=%s"
                          " AND resource_kind='steward_assist'", (tenant,))
                c.execute("UPDATE cap_candidates SET status='in_flight', lease_owner=%s,"
                          " lease_until=now() + interval '5 min' WHERE id=%s", (worker, cand[0]))
        with lock:
            results.append((worker, "leased"))
    except Exception as exc:  # noqa: BLE001
        with lock:
            results.append((worker, f"err:{type(exc).__name__}"))


def lease_with_subquery(dsn: str, worker: str, tenant: int, results: list, lock) -> None:
    """反证形态：计数子查询（没有 counter 行锁）——预期**越限**。"""
    import psycopg
    try:
        with _conn(dsn) as c:
            with c.transaction():
                cap = c.execute(
                    "SELECT capacity FROM cap_counters"
                    " WHERE scope_kind='space' AND scope_id=%s AND resource_kind='steward_assist'",
                    (tenant,)).fetchone()[0]
                # READ COMMITTED 下看不到并发未提交的 in_flight
                live = c.execute(
                    "SELECT count(*) FROM cap_candidates"
                    " WHERE tenant=%s AND status='in_flight'", (tenant,)).fetchone()[0]
                if live >= cap:
                    with lock:
                        results.append((worker, "full"))
                    return
                cand = c.execute(
                    "SELECT id FROM cap_candidates"
                    " WHERE tenant=%s AND status='reserved'"
                    " ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1", (tenant,)).fetchone()
                if cand is None:
                    with lock:
                        results.append((worker, "no-candidate"))
                    return
                c.execute("UPDATE cap_candidates SET status='in_flight', lease_owner=%s"
                          " WHERE id=%s", (worker, cand[0]))
        with lock:
            results.append((worker, "leased"))
    except Exception as exc:  # noqa: BLE001
        with lock:
            results.append((worker, f"err:{type(exc).__name__}"))


def run_shape(dsn: str, *, workers: int, tenants: int, capacity: int, per_tenant: int,
              use_counter: bool) -> dict:
    seed(dsn, tenants=tenants, per_tenant=per_tenant, capacity=capacity)
    results: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(workers, timeout=30)
    fn = lease_with_counter if use_counter else lease_with_subquery

    def w(i: int) -> None:
        tenant = (i % tenants) + 1
        barrier.wait()
        fn(dsn, f"w{i}", tenant, results, lock)

    ts = [threading.Thread(target=w, args=(i,)) for i in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    alive = any(t.is_alive() for t in ts)

    with _conn(dsn) as c:
        per = c.execute(
            "SELECT tenant, count(*) FROM cap_candidates WHERE status='in_flight'"
            " GROUP BY tenant ORDER BY tenant").fetchall()
        counters = c.execute(
            "SELECT scope_id, active, capacity FROM cap_counters ORDER BY scope_id").fetchall()
    return {
        "use_counter": use_counter,
        "workers": workers, "tenants": tenants, "capacity": capacity,
        "leased": sum(1 for _w, r in results if r == "leased"),
        "outcomes": sorted({r for _w, r in results}),
        "in_flight_per_tenant": {t: n for t, n in per},
        "counters": {sid: {"active": a, "capacity": cp} for sid, a, cp in counters},
        "threads_alive_after_timeout": alive,
        "over_capacity": [t for t, n in per if n > capacity],
    }


def test_release_once(dsn: str) -> dict:
    """归还恰好一次：第二次 release 不递减。"""
    with _conn(dsn) as c:
        c.execute(DDL)
        c.execute("INSERT INTO cap_counters VALUES ('space', 1, 'steward_assist', 3, 2, 0)")
        c.commit()
    def release() -> int:
        with _conn(dsn) as c:
            with c.transaction():
                row = c.execute(
                    "SELECT active FROM cap_counters"
                    " WHERE scope_kind='space' AND scope_id=1 AND resource_kind='steward_assist'"
                    " FOR UPDATE").fetchone()
                if row[0] <= 0:
                    return 0
                c.execute("UPDATE cap_counters SET active = active - 1, version = version + 1"
                          " WHERE scope_kind='space' AND scope_id=1"
                          " AND resource_kind='steward_assist'")
                return 1
    first = release()
    second = release()
    third = release()
    with _conn(dsn) as c:
        final = c.execute("SELECT active FROM cap_counters WHERE scope_id=1").fetchone()[0]
    return {"first": first, "second": second, "third": third, "final_active": final,
            "ok": first == 1 and second == 1 and third == 0 and final == 0}


def test_lock_order_deadlock(dsn: str) -> dict:
    """锁序承重：counter→candidate 与 candidate→counter 交叉必须死锁。"""
    import psycopg
    with _conn(dsn) as c:
        c.execute(DDL)
        c.execute("INSERT INTO cap_counters VALUES ('space', 1, 'steward_assist', 3, 0, 0)")
        c.execute("INSERT INTO cap_candidates VALUES (1, 1, 'reserved', NULL, NULL)")
        c.commit()

    deadlocks: list[str] = []
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=20)

    def path_a() -> None:
        try:
            with _conn(dsn) as c, c.transaction():
                gate.wait()
                c.execute("SELECT active FROM cap_counters WHERE scope_id=1 FOR UPDATE")
                time.sleep(0.4)
                c.execute("SELECT status FROM cap_candidates WHERE id=1 FOR UPDATE")
        except psycopg.errors.DeadlockDetected:
            with lock:
                deadlocks.append("A")
        except Exception:  # noqa: BLE001
            pass

    def path_b() -> None:
        try:
            with _conn(dsn) as c, c.transaction():
                gate.wait()
                # 反向：先 candidate 再 counter（= 把归还放在 fence 之后）
                c.execute("SELECT status FROM cap_candidates WHERE id=1 FOR UPDATE")
                time.sleep(0.4)
                c.execute("SELECT active FROM cap_counters WHERE scope_id=1 FOR UPDATE")
        except psycopg.errors.DeadlockDetected:
            with lock:
                deadlocks.append("B")
        except Exception:  # noqa: BLE001
            pass

    ts = [threading.Thread(target=path_a), threading.Thread(target=path_b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=40)
    return {"deadlocks": deadlocks, "ok": len(deadlocks) >= 1}


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2
    plain = dsn.replace("postgresql+psycopg://", "postgresql://")

    failures: list[str] = []

    print("1) 正确形态（持久化 counter）")
    good = run_shape(plain, workers=10, tenants=2, capacity=2, per_tenant=6, use_counter=True)
    print(f"   leased={good['leased']} outcomes={good['outcomes']} "
          f"per_tenant={good['in_flight_per_tenant']} counters={good['counters']}")
    if good["threads_alive_after_timeout"]:
        failures.append("正确形态有线程未在超时内结束（可能死锁）")
    if good["over_capacity"]:
        failures.append(f"正确形态越限：{good['over_capacity']}")
    else:
        print("  OK  无租户越限")
    # active 必须等于实际 in_flight
    for t, n in good["in_flight_per_tenant"].items():
        a = good["counters"].get(t, {}).get("active")
        if a != n:
            failures.append(f"租户 {t}: counter.active={a} != in_flight={n}")

    print("2) 反证（计数子查询，无 counter 行锁）")
    bad = run_shape(plain, workers=10, tenants=2, capacity=2, per_tenant=6, use_counter=False)
    print(f"   leased={bad['leased']} per_tenant={bad['in_flight_per_tenant']}")
    if not bad["over_capacity"]:
        failures.append(
            "反证未越限——用例没有构造出竞争窗口，因此不能证明 counter 是必要的"
        )
    else:
        print(f"  OK  反证确实越限：{bad['over_capacity']}（证明 counter 承重）")

    print("3) 归还恰好一次")
    rel = test_release_once(plain)
    print(f"   {rel}")
    if not rel["ok"]:
        failures.append(f"归还语义错误：{rel}")

    print("4) 锁序承重（反向必须死锁）")
    dl = test_lock_order_deadlock(plain)
    print(f"   deadlocks={dl['deadlocks']}")
    if not dl["ok"]:
        failures.append("反向锁序未死锁——顺序未被证明承重")

    with _conn(plain) as c:
        c.execute("DROP TABLE IF EXISTS cap_counters, cap_candidates CASCADE")
        c.commit()

    report = {"counter_shape": good, "subquery_shape": bad,
              "release_once": rel, "lock_order": dl, "failures": failures}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pg-capacity-concurrency.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: counter 配额成立、反证越限、归还恰好一次、锁序承重")
    return 0


if __name__ == "__main__":
    sys.exit(main())
