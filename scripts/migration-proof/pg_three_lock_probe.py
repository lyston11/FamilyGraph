"""Gate 2：三把以上锁的锁序探针（补 Gate 2 缺口 2）。

只验证过两把交叉（counter vs run row）。本探针验证**四把**：
`global → kind → tenant → run`，并同时证明：

1. 遵守冻结顺序（global → kind → tenant → run）时无死锁；
2. 违反顺序（先 run 再 counter）时产生 `DeadlockDetected`；
3. 多租户下不同 counter 行不互相阻塞（并行度）。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_three_lock_probe.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

DDL = """
DROP TABLE IF EXISTS tl_counters, tl_runs CASCADE;
CREATE TABLE tl_counters (
  scope_kind text NOT NULL, scope_id int NOT NULL,
  capacity int NOT NULL, active int NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_kind, scope_id)
);
CREATE TABLE tl_runs (id int PRIMARY KEY, status text NOT NULL);
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn)


def setup(dsn: str) -> None:
    with _conn(dsn) as c:
        c.execute(DDL)
        c.execute("INSERT INTO tl_counters VALUES ('global',0,4,0),('kind',1,4,0),"
                  "('tenant',1,2,0),('tenant',2,2,0)")
        c.execute("INSERT INTO tl_runs VALUES (1,'queued'),(2,'queued')")
        c.commit()


def _acquire_all(c, *, run_id: int, counter_tenant: int, reverse: bool) -> None:
    """取锁。

    `reverse=False`：global → kind → tenant → run（冻结顺序）。
    `reverse=True` ：run → tenant（违反顺序）。

    **两个 worker 必须争用同一组资源**（同一 counter + 同一 run），否则资源不相交、
    永远不会形成环——第一版让 A 用 tenant 1、B 用 tenant 2，因此「未死锁」是
    用例构造错误，不是安全性证明。
    """
    if reverse:
        c.execute("SELECT status FROM tl_runs WHERE id=%s FOR UPDATE", (run_id,))
        time.sleep(0.35)
        for kind, sid in (("global", 0), ("kind", 1), ("tenant", counter_tenant)):
            c.execute("SELECT active FROM tl_counters WHERE scope_kind=%s AND scope_id=%s"
                      " FOR UPDATE", (kind, sid))
    else:
        for kind, sid in (("global", 0), ("kind", 1), ("tenant", counter_tenant)):
            c.execute("SELECT active FROM tl_counters WHERE scope_kind=%s AND scope_id=%s"
                      " FOR UPDATE", (kind, sid))
        time.sleep(0.35)
        c.execute("SELECT status FROM tl_runs WHERE id=%s FOR UPDATE", (run_id,))


def _run_pair(dsn: str, *, reverse: bool) -> tuple[list[str], list[str]]:
    """跑两个并发事务。

    `reverse=False`：**两个都按冻结顺序**（global→kind→tenant→run）——同序永不形成环，
    期望无死锁。

    `reverse=True`：**一个按冻结顺序（模拟租约：counter 先），另一个反向
    （模拟结算：run 先）**——这才是真实交叉。让两个都反向是同序，永远不会死锁
    （本探针第一版即因此假通过）。
    """
    import psycopg
    deadlocks: list[str] = []
    results: list[str] = []
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=10)

    def worker(name: str, *, run_id: int, counter_tenant: int, rev: bool) -> None:
        try:
            with _conn(dsn) as c, c.transaction():
                gate.wait()
                _acquire_all(c, run_id=run_id, counter_tenant=counter_tenant, reverse=rev)
            with lock:
                results.append(name)
        except psycopg.errors.DeadlockDetected:
            with lock:
                deadlocks.append(name)
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(f"{name}:{type(exc).__name__}")

    # 争用**同一** counter 与**同一** run。
    # reverse=True 时 A 走冻结顺序（租约形态），B 走反向（结算形态）——
    # 只有一正一反才会形成等待环。
    ts = [threading.Thread(target=worker, args=("A",),
                           kwargs={"run_id": 1, "counter_tenant": 1, "rev": False}),
          threading.Thread(target=worker, args=("B",),
                           kwargs={"run_id": 1, "counter_tenant": 1, "rev": reverse})]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
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

    setup(dsn)
    failures: list[str] = []

    # 1) 正确顺序：无死锁
    dl, res = _run_pair(dsn, reverse=False)
    print(f"  正确顺序（global→kind→tenant→run）: deadlocks={dl} results={res}")
    if dl:
        failures.append("四把锁的正确顺序产生了死锁")
    elif len(res) != 2:
        failures.append(f"正确顺序下并非两方都完成：{res}")
    else:
        print("  OK  四把锁按冻结顺序无死锁")

    # 2) 违反顺序：必须死锁
    dl2, res2 = _run_pair(dsn, reverse=True)
    print(f"  违反顺序（run→counter）: deadlocks={dl2} results={res2}")
    if not dl2:
        failures.append("违反顺序未触发死锁——用例未构造出交叉窗口，不能据此认为安全")
    else:
        print("  OK  违反顺序确实死锁（证明四把锁的顺序是承重的）")

    # 3) 多租户并行度：不同 tenant counter 不互相阻塞
    #
    # 不能与绝对阈值比较：经 SSH 隧道时连接建立本身就有百毫秒级开销，
    # 「并行 0.3s」可能测成 2s 而被误判为阻塞（第一版即如此）。
    # 改为**相对基线**：先测串行两次的耗时，再测并行的耗时，要求并行显著更短。
    with _conn(dsn) as c:
        c.execute("UPDATE tl_counters SET active=0")
        c.commit()

    HOLD = 0.5  # 持锁时长，取足够大以盖过连接开销

    def hold(tenant: int) -> None:
        with _conn(dsn) as c, c.transaction():
            c.execute("SELECT active FROM tl_counters WHERE scope_kind='tenant'"
                      " AND scope_id=%s FOR UPDATE", (tenant,))
            time.sleep(HOLD)

    def measure_serial() -> float:
        t0 = time.perf_counter()
        hold(1)
        hold(2)
        return time.perf_counter() - t0

    def measure_parallel() -> float:
        t0 = time.perf_counter()
        threads = [threading.Thread(target=hold, args=(t,)) for t in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        return time.perf_counter() - t0

    # 取两次的**最小值**：经隧道时单次测量波动可达秒级（已实测批量运行时误报），
    # 最小值更接近真实开销下界，判定才不会随环境抖动。
    serial = min(measure_serial(), measure_serial())
    parallel = min(measure_parallel(), measure_parallel())

    print(f"  两个不同 tenant counter：串行 {serial:.2f}s / 并行 {parallel:.2f}s"
          f"（持锁 {HOLD}s，串行下界 {2 * HOLD:.1f}s）")
    print("    注：经 SSH 隧道时连接建立开销可达秒级，绝对数值不可跨环境比较；"
          "判定只用相对关系。")
    # 串行下界 = 2×HOLD；并行若真正重叠，应显著低于该下界。
    if parallel >= serial * 0.85:
        failures.append(f"不同 tenant counter 疑似互相阻塞（串行 {serial:.2f}s，"
                        f"并行 {parallel:.2f}s）")
    else:
        print("  OK  不同租户 counter 可并行（并行耗时显著低于串行）")

    with _conn(dsn) as c:
        c.execute("DROP TABLE IF EXISTS tl_counters, tl_runs CASCADE")
        c.commit()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 四把锁的顺序是承重的，冻结顺序无死锁，跨租户不阻塞")
    return 0


if __name__ == "__main__":
    sys.exit(main())
