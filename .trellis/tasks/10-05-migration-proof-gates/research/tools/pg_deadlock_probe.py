"""可复现的反向锁序死锁探针（把 L3 从断言变成产物）。

用法：

    PGTEST_DSN=postgresql://postgres:probe@127.0.0.1:55435/familygraph \\
        ./backend/.venv/bin/python research/tools/pg_deadlock_probe.py

两条事务路径：

  A 租约： counter(space) -> run/attempt 行
  B 结算： run 行 -> counter(space)

**正确锁序**（counter 永远先于 run 行）必须无死锁；
**反向锁序**必须产生 `DeadlockDetected`。

若反向顺序没有触发死锁，本探针报 FAIL —— 那说明用例没有真正构造出交叉窗口，
而不是「安全」。
"""
from __future__ import annotations

import os
import sys
import threading
import time


def _run(dsn: str, reverse: bool) -> tuple[list[str], list[tuple[str, str]]]:
    import psycopg

    with psycopg.connect(dsn) as c:
        c.execute("DROP TABLE IF EXISTS dc_counters, dc_runs CASCADE")
        c.execute("CREATE TABLE dc_counters (space_id int PRIMARY KEY, active int NOT NULL DEFAULT 0)")
        c.execute("CREATE TABLE dc_runs (id int PRIMARY KEY, status text)")
        c.execute("INSERT INTO dc_counters VALUES (1, 0)")
        c.execute("INSERT INTO dc_runs VALUES (10, 'running')")
        c.commit()

    deadlocks: list[str] = []
    results: list[tuple[str, str]] = []
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=10)

    def path_a() -> None:
        try:
            with psycopg.connect(dsn) as conn, conn.transaction():
                gate.wait()
                # 租约：counter 先
                conn.execute("SELECT active FROM dc_counters WHERE space_id=1 FOR UPDATE")
                time.sleep(0.4)
                conn.execute("SELECT status FROM dc_runs WHERE id=10 FOR UPDATE")
            with lock:
                results.append(("A", "ok"))
        except psycopg.errors.DeadlockDetected:
            with lock:
                deadlocks.append("A")
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(("A", type(exc).__name__))

    def path_b() -> None:
        try:
            with psycopg.connect(dsn) as conn, conn.transaction():
                gate.wait()
                if reverse:
                    # 反向：run 行先，再 counter（= 把 counter 归还放在 fence 之后）
                    conn.execute("SELECT status FROM dc_runs WHERE id=10 FOR UPDATE")
                    time.sleep(0.4)
                    conn.execute("SELECT active FROM dc_counters WHERE space_id=1 FOR UPDATE")
                else:
                    # 正确：counter 先，再 run 行
                    conn.execute("SELECT active FROM dc_counters WHERE space_id=1 FOR UPDATE")
                    time.sleep(0.4)
                    conn.execute("SELECT status FROM dc_runs WHERE id=10 FOR UPDATE")
            with lock:
                results.append(("B", "ok"))
        except psycopg.errors.DeadlockDetected:
            with lock:
                deadlocks.append("B")
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(("B", type(exc).__name__))

    threads = [threading.Thread(target=path_a), threading.Thread(target=path_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    with psycopg.connect(dsn) as cleanup:
        cleanup.execute("DROP TABLE IF EXISTS dc_counters, dc_runs CASCADE")
        cleanup.commit()
    return deadlocks, results


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

    failures = []

    dl, res = _run(dsn, reverse=True)
    print(f"  反向锁序（run -> counter）: deadlocks={dl} results={res}")
    if not dl:
        failures.append("反向锁序未触发死锁——用例没有构造出交叉窗口，不能据此认为安全")
    else:
        print("  OK  反向锁序确实死锁（证明顺序是承重的）")

    dl2, res2 = _run(dsn, reverse=False)
    print(f"  正确锁序（counter -> run）: deadlocks={dl2} results={res2}")
    if dl2:
        failures.append("正确锁序竟触发死锁——锁序设计不成立")
    else:
        print("  OK  正确锁序无死锁")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 锁序是承重的，且固定顺序无死锁")
    return 0


if __name__ == "__main__":
    sys.exit(main())
