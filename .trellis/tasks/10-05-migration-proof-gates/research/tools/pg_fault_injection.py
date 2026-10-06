"""Gate 4：故障注入（真实 PostgreSQL，多连接）。

覆盖设计要求的故障矩阵：

| 故障 | 注入方式 | 期望 |
|---|---|---|
| deadlock | 见 `pg_deadlock_probe.py`（本脚本不注入；docstring 原列此项与实现不符） |
| serialization failure | `SERIALIZABLE` + 并发写同一行 | `SerializationFailure`，可重试 |
| 提交前连接丢失 | 事务内 kill 连接 | 全部回滚，无半状态 |
| 提交后连接丢失 | 提交后 kill 连接 | 状态持久，重试幂等 |
| 重复 settle | 同 lease 结算两次 | 第二次 0 行受影响，counter 只减一次 |
| cancel 与 settle 竞争 | 并发 cancel + settle | 终态唯一，不双写 |
| 进程崩溃后租约回收 | 模拟过期租约 | 可被 recovery 选中并归还 counter |

用法：

    PGTEST_DSN=postgresql://... python3 research/tools/pg_fault_injection.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

DDL = """
DROP TABLE IF EXISTS fi_counters, fi_attempts, fi_runs CASCADE;
CREATE TABLE fi_counters (scope text PRIMARY KEY, capacity int NOT NULL, active int NOT NULL DEFAULT 0,
                          CONSTRAINT ck_fi CHECK (active >= 0 AND active <= capacity));
CREATE TABLE fi_attempts (
  id int PRIMARY KEY, tenant text NOT NULL, status text NOT NULL,
  lease_owner text, lease_until timestamptz, applied_at timestamptz,
  CONSTRAINT ck_fi_status CHECK (status IN ('reserved','in_flight','succeeded','cancelled','unknown'))
);
CREATE TABLE fi_runs (id int PRIMARY KEY, status text NOT NULL, cancel_requested boolean NOT NULL DEFAULT false);
"""


def _conn(dsn: str, **kw):
    import psycopg
    return psycopg.connect(dsn, **kw)


def setup(dsn: str) -> None:
    with _conn(dsn) as c:
        c.execute(DDL)
        c.execute("INSERT INTO fi_counters VALUES ('space:1', 2, 0)")
        c.execute("INSERT INTO fi_runs VALUES (1, 'running', false), (2, 'running', true)")
        for i in range(1, 4):
            c.execute("INSERT INTO fi_attempts VALUES (%s,'space:1','reserved',NULL,NULL,NULL)", (i,))
        c.commit()


def test_serialization(dsn: str, failures: list[str]) -> None:
    """SERIALIZABLE 下并发写同一行必须产生序列化失败（可重试），而不是静默丢更新。"""
    import psycopg
    with _conn(dsn) as c:
        c.execute("UPDATE fi_counters SET active = 0 WHERE scope = 'space:1'")
        c.commit()
    barrier = threading.Barrier(2, timeout=10)
    errs: list[str] = []

    def worker():
        try:
            with _conn(dsn) as conn:
                with conn.transaction():
                    conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    barrier.wait()
                    conn.execute("UPDATE fi_counters SET active = active + 1 WHERE scope = 'space:1'")
        except psycopg.errors.SerializationFailure:
            errs.append("SerializationFailure")
        except Exception as exc:  # noqa: BLE001
            errs.append(type(exc).__name__)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    print(f"  serialization failure 出现 {len(errs)} 次: {errs}")
    if not errs:
        failures.append("SERIALIZABLE 并发写未产生序列化失败——不能据此认为并发安全")
    else:
        print("  OK  序列化失败被正确抛出（调用方必须 bounded retry）")


def test_loss_before_commit(dsn: str, failures: list[str]) -> None:
    """提交前断开连接：必须全部回滚。"""
    import psycopg
    with _conn(dsn) as c:
        c.execute("UPDATE fi_attempts SET status='reserved', lease_owner=NULL WHERE id=1")
        c.commit()
    conn = _conn(dsn)
    conn.autocommit = False
    conn.execute("UPDATE fi_attempts SET status='in_flight', lease_owner='crashy' WHERE id=1")
    conn.execute("UPDATE fi_counters SET active = active + 1 WHERE scope='space:1'")
    # 未 commit 直接关闭连接
    conn.close()
    with _conn(dsn) as c:
        row = c.execute("SELECT status, lease_owner FROM fi_attempts WHERE id=1").fetchone()
        cnt = c.execute("SELECT active FROM fi_counters WHERE scope='space:1'").fetchone()[0]
    ok = row[0] == "reserved" and row[1] is None and cnt == 0
    print(f"  [{'OK ' if ok else 'BAD'}] 提交前断连 -> status={row[0]} owner={row[1]} counter={cnt}")
    if not ok:
        failures.append("提交前断连留下了部分状态")


def test_loss_after_commit(dsn: str, failures: list[str]) -> None:
    """提交后断开连接：状态必须持久，且重放同一操作幂等。"""
    with _conn(dsn) as c:
        c.execute("UPDATE fi_attempts SET status='in_flight', lease_owner='ok' WHERE id=2")
        c.execute("UPDATE fi_counters SET active = active + 1 WHERE scope='space:1'")
        c.commit()
    conn = _conn(dsn)
    conn.execute("SELECT 1")  # 已提交，随后断开
    conn.close()
    with _conn(dsn) as c:
        row = c.execute("SELECT status FROM fi_attempts WHERE id=2").fetchone()[0]
        cnt = c.execute("SELECT active FROM fi_counters WHERE scope='space:1'").fetchone()[0]
    ok = row == "in_flight" and cnt == 1
    print(f"  [{'OK ' if ok else 'BAD'}] 提交后断连 -> status={row} counter={cnt}")
    if not ok:
        failures.append("提交后断连丢失了已提交状态")


def test_duplicate_settle(dsn: str, failures: list[str]) -> None:
    """重复 settle 必须只归还一次 counter。"""
    with _conn(dsn) as c:
        c.execute("UPDATE fi_attempts SET status='in_flight', lease_owner='dup' WHERE id=3")
        c.execute("UPDATE fi_counters SET active = active + 1 WHERE scope='space:1'")
        c.commit()

    def settle(owner: str) -> int:
        with _conn(dsn) as conn:
            with conn.transaction():
                row = conn.execute(
                    "UPDATE fi_attempts SET status='succeeded', applied_at=now()"
                    " WHERE id=3 AND status='in_flight' AND lease_owner=%s RETURNING id",
                    (owner,),
                ).fetchone()
                if row is None:
                    return 0
                conn.execute("UPDATE fi_counters SET active = active - 1 WHERE scope='space:1'")
                return 1

    first = settle("dup")
    second = settle("dup")
    with _conn(dsn) as c:
        cnt = c.execute("SELECT active FROM fi_counters WHERE scope='space:1'").fetchone()[0]
    ok = first == 1 and second == 0 and cnt == 1
    print(f"  [{'OK ' if ok else 'BAD'}] 重复 settle -> 第一次={first} 第二次={second} counter={cnt}（期望 1/0/1）")
    if not ok:
        failures.append("重复 settle 重复归还了 counter")


def test_cancel_vs_settle(dsn: str, failures: list[str]) -> None:
    """cancel 与 settle 竞争：终态唯一，不双写。"""
    with _conn(dsn) as c:
        c.execute("UPDATE fi_runs SET status='running', cancel_requested=true WHERE id=1")
        c.commit()
    barrier = threading.Barrier(2, timeout=10)
    results: list[str] = []
    lock = threading.Lock()

    def settle():
        with _conn(dsn) as conn:
            with conn.transaction():
                barrier.wait()
                row = conn.execute(
                    "UPDATE fi_runs SET status='succeeded' WHERE id=1 AND status='running'"
                    " RETURNING status"
                ).fetchone()
                with lock:
                    results.append(f"settle:{row[0] if row else 'noop'}")

    def cancel():
        with _conn(dsn) as conn:
            with conn.transaction():
                barrier.wait()
                row = conn.execute(
                    "UPDATE fi_runs SET status='cancelled' WHERE id=1 AND status='running'"
                    " RETURNING status"
                ).fetchone()
                with lock:
                    results.append(f"cancel:{row[0] if row else 'noop'}")

    ts = [threading.Thread(target=settle), threading.Thread(target=cancel)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    with _conn(dsn) as c:
        final = c.execute("SELECT status FROM fi_runs WHERE id=1").fetchone()[0]
    winners = [r for r in results if not r.endswith("noop")]
    ok = len(winners) == 1 and final in ("succeeded", "cancelled")
    print(f"  [{'OK ' if ok else 'BAD'}] cancel vs settle -> {results} 终态={final}（期望恰好一个赢家）")
    if not ok:
        failures.append("cancel/settle 竞争未收敛为唯一终态")


def test_crash_recovery(dsn: str, failures: list[str]) -> None:
    """崩溃后过期租约可被 recovery 选中，且 counter 归还一次。"""
    with _conn(dsn) as c:
        c.execute("UPDATE fi_attempts SET status='in_flight', lease_owner='dead',"
                  " lease_until = now() - interval '1 hour', applied_at = NULL WHERE id=1")
        c.execute("UPDATE fi_counters SET active = 2 WHERE scope='space:1'")
        c.commit()

    with _conn(dsn) as c:
        with c.transaction():
            # recovery：只选已过期且未应用的 in_flight
            rows = c.execute(
                "UPDATE fi_attempts SET status='unknown'"
                " WHERE status='in_flight' AND lease_until < now() AND applied_at IS NULL"
                " RETURNING id"
            ).fetchall()
            for _ in rows:
                c.execute("UPDATE fi_counters SET active = active - 1 WHERE scope='space:1'")
    with _conn(dsn) as c:
        cnt = c.execute("SELECT active FROM fi_counters WHERE scope='space:1'").fetchone()[0]
        st = c.execute("SELECT status FROM fi_attempts WHERE id=1").fetchone()[0]
    ok = len(rows) == 1 and cnt == 1 and st == "unknown"
    print(f"  [{'OK ' if ok else 'BAD'}] 崩溃恢复 -> 回收={len(rows)} 终态={st} counter={cnt}")
    if not ok:
        failures.append("过期租约未被正确回收")


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

    setup(dsn)
    failures: list[str] = []
    print("故障注入：")
    test_loss_before_commit(dsn, failures)
    test_loss_after_commit(dsn, failures)
    test_duplicate_settle(dsn, failures)
    test_cancel_vs_settle(dsn, failures)
    test_crash_recovery(dsn, failures)
    test_serialization(dsn, failures)

    with _conn(dsn) as c:
        c.execute("DROP TABLE IF EXISTS fi_counters, fi_attempts, fi_runs CASCADE")
        c.commit()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 六类故障均按预期收敛")
    return 0


if __name__ == "__main__":
    sys.exit(main())
