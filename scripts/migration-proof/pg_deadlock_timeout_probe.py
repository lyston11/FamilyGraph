"""Gate 2 缺口 5：deadlock_timeout 对 lease/settle 延迟预算的影响。

## 为什么重要

PostgreSQL 默认 `deadlock_timeout = 1s`：死锁检测**不是立即**的，而是等待
1 秒后才启动检测。这意味着一次真实死锁会让**被中止方**至少多等 ~1s。
若 lease/settle 的延迟预算按毫秒设计，这个量级必须显式记录，不能默认「死锁很快被发现」。

本探针测量：

1. 默认 `deadlock_timeout` 下，死锁从发生到被检测的**墙钟时长**；
2. 会话级调小 `deadlock_timeout`（如 200ms）后的时长，验证可配置性；
3. 两个事务都**未死锁**时的基线耗时（对照，证明差异来自死锁而非固定开销）。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_deadlock_timeout_probe.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

DDL = """
DROP TABLE IF EXISTS dt_rows CASCADE;
CREATE TABLE dt_rows (id int PRIMARY KEY, val text NOT NULL);
INSERT INTO dt_rows VALUES (1,'a'),(2,'b');
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn)


def _measure(dsn: str, *, timeout_ms: int | None, force_deadlock: bool) -> tuple[float, str]:
    """返回 (墙钟秒数, 结果标签)。

    `force_deadlock=True`：两事务交叉取行锁（A: 1→2，B: 2→1）→ 必然死锁。
    `force_deadlock=False`：两事务取**同一顺序**（1→2）→ 只阻塞不死锁。
    """
    import psycopg
    started = threading.Event()
    outcome: list[str] = []
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=15)

    def worker(first: int, second: int) -> None:
        try:
            with _conn(dsn) as c, c.transaction():
                if timeout_ms is not None:
                    c.execute(f"SET LOCAL deadlock_timeout = {int(timeout_ms)}")
                gate.wait()
                started.set()
                c.execute("SELECT val FROM dt_rows WHERE id=%s FOR UPDATE", (first,))
                time.sleep(0.3)  # 放大窗口，确保双方都已持第一把锁
                c.execute("SELECT val FROM dt_rows WHERE id=%s FOR UPDATE", (second,))
            with lock:
                outcome.append("ok")
        except psycopg.errors.DeadlockDetected:
            with lock:
                outcome.append("deadlock")
        except Exception as exc:  # noqa: BLE001
            with lock:
                outcome.append(f"err:{type(exc).__name__}")

    pairs = (1, 2) if not force_deadlock else (1, 2)
    second = (2, 1) if force_deadlock else (2,)
    threads = [threading.Thread(target=worker, args=(pairs[0], second[0] if force_deadlock else 2)),
               threading.Thread(target=worker, args=(2 if not force_deadlock else 2,
                                                     1 if force_deadlock else 2))]
    # 简化：显式构造两条路径
    if force_deadlock:
        paths = [(1, 2), (2, 1)]
    else:
        paths = [(1, 2), (1, 2)]
    threads = [threading.Thread(target=worker, args=p) for p in paths]

    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.perf_counter() - t0
    label = "deadlock" if "deadlock" in outcome else (
        "ok" if all(o == "ok" for o in outcome) else ",".join(outcome))
    return elapsed, label


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

    with _conn(dsn) as c:
        c.execute(DDL)
        c.commit()
        default_ms = c.execute("SHOW deadlock_timeout").fetchone()[0]
    print(f"  服务端 deadlock_timeout = {default_ms}")

    failures: list[str] = []

    # 1) 默认设置下的真实死锁耗时
    t_default, label_default = _measure(dsn, timeout_ms=None, force_deadlock=True)
    print(f"  默认 timeout + 真实死锁 : {t_default:.2f}s  ({label_default})")
    if label_default != "deadlock":
        failures.append(f"未构造出死锁（{label_default}）")
    elif t_default < 1.0:
        failures.append(f"死锁在 {t_default:.2f}s 内被发现，与默认 1s 不符")

    # 2) 会话级调小后
    t_small, label_small = _measure(dsn, timeout_ms=200, force_deadlock=True)
    print(f"  200ms timeout + 真实死锁: {t_small:.2f}s  ({label_small})")
    if label_small != "deadlock":
        failures.append(f"调小后未构造出死锁（{label_small}）")
    elif t_small >= t_default:
        failures.append("调小 deadlock_timeout 未缩短检测耗时")

    # 3) 对照：无死锁时的基线
    t_ok, label_ok = _measure(dsn, timeout_ms=None, force_deadlock=False)
    print(f"  默认 timeout + 无死锁    : {t_ok:.2f}s  ({label_ok})")
    if label_ok != "ok":
        failures.append(f"对照组未正常完成（{label_ok}）")

    print()
    print(f"  结论：一次真实死锁会让被中止方额外等待约 {t_default - t_ok:.2f}s；"
          f"deadlock_timeout 可会话级调整（200ms 时约 {t_small:.2f}s）。")
    print("  因此 lease/settle 的延迟预算必须计入该量级，且重试必须 bounded。")

    with _conn(dsn) as c:
        c.execute("DROP TABLE IF EXISTS dt_rows CASCADE")
        c.commit()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: deadlock_timeout 的影响已量化")
    return 0


if __name__ == "__main__":
    sys.exit(main())
