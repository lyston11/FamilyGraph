"""C3/AC-4：证明进程内 limiter 在多实例下会把配额翻倍，并用持久化 counter 修复。

## 要证明什么

`ResourceLimiter` 的 `active` 是**进程内**状态。两个实例各自 `global_capacity=2`，
则全集群实际并发可达 **4**——每个实例都认为「我只用了 2，没超」。

这不是理论推断，而是可复现的：本探针在**同一进程内模拟两个独立 limiter 实例**
（各自独立的计数），对同一持久化资源做准入，然后统计真实并发。

## 对照

| 形态 | 配额真相 | 两实例下的实际并发 |
|---|---|---|
| 进程内 limiter | 各自 2 | 4（翻倍） |
| 持久化 counter | 共享 1 行 | 2（正确） |

## 为什么必须做反证

只测「counter 形态并发=2」不能证明 limiter 形态有问题——可能只是用例没构造出
并发。因此必须**同时**测两种形态并断言 limiter 形态**确实越限**。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/cross_instance_capacity.py
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
DROP TABLE IF EXISTS xc_counters, xc_leases CASCADE;
CREATE TABLE xc_counters (
  resource text PRIMARY KEY, capacity int NOT NULL, active int NOT NULL DEFAULT 0,
  CONSTRAINT ck_xc CHECK (active >= 0 AND active <= capacity)
);
CREATE TABLE xc_leases (id serial PRIMARY KEY, worker text NOT NULL, resource text NOT NULL);
"""


class InProcessLimiter:
    """`ResourceLimiter` 的最小等价物：进程内计数，无跨实例协调。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self.active >= self.capacity:
                return False
            self.active += 1
            return True


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=False)


def _seed(dsn: str, capacity: int) -> None:
    with _conn(dsn) as c:
        c.execute(DDL)
        c.execute("INSERT INTO xc_counters VALUES ('agent_provider', %s, 0)", (capacity,))
        c.commit()


def _in_process_run(capacity: int, instances: int, workers_per_instance: int) -> dict:
    """两个独立 limiter 实例（模拟两个进程），共享同一持久化资源。"""
    limiters = [InProcessLimiter(capacity) for _ in range(instances)]
    held: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(instances * workers_per_instance, timeout=30)

    def worker(inst: int) -> None:
        barrier.wait()
        if limiters[inst].try_acquire():
            time.sleep(0.3)  # 持有一段时间，制造重叠
            with lock:
                held.append(inst)

    ts = [threading.Thread(target=worker, args=(i % instances,))
          for i in range(instances * workers_per_instance)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    return {
        "shape": "in_process_limiter",
        "configured_per_instance": capacity,
        "instances": instances,
        "observed_concurrency": len(held),
        "cluster_limit_expected": capacity,
    }


def _persistent_run(dsn: str, capacity: int, instances: int, workers_per_instance: int) -> dict:
    """持久化 counter：所有实例共享同一行，占用时取行锁。"""
    import psycopg
    _seed(dsn, capacity)
    held: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(instances * workers_per_instance, timeout=30)

    def worker(inst: int) -> None:
        barrier.wait()
        try:
            with _conn(dsn) as c:
                with c.transaction():
                    row = c.execute(
                        "SELECT capacity, active FROM xc_counters WHERE resource='agent_provider'"
                        " FOR UPDATE"
                    ).fetchone()
                    cap, active = row
                    if active >= cap:
                        return
                    c.execute(
                        "UPDATE xc_counters SET active = active + 1 WHERE resource='agent_provider'"
                    )
                    c.execute(
                        "INSERT INTO xc_leases (worker, resource) VALUES (%s, 'agent_provider')",
                        (f"inst{inst}",),
                    )
            time.sleep(0.3)
            with lock:
                held.append(f"inst{inst}")
        except psycopg.errors.DeadlockDetected:
            pass

    ts = [threading.Thread(target=worker, args=(i % instances,))
          for i in range(instances * workers_per_instance)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=40)
    with _conn(dsn) as c:
        leases = c.execute("SELECT count(*) FROM xc_leases").fetchone()[0]
    return {
        "shape": "persistent_counter",
        "configured_cluster": capacity,
        "instances": instances,
        "observed_concurrency": len(held),
        "lease_rows": leases,
        "cluster_limit_expected": capacity,
    }


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
    CAPACITY = 2
    INSTANCES = 2
    WORKERS = 6

    print("1) 进程内 limiter（当前实现形态）")
    inproc = _in_process_run(CAPACITY, INSTANCES, WORKERS)
    print(f"   每实例配置={CAPACITY} 实例={INSTANCES} 观测并发={inproc['observed_concurrency']}"
          f" 期望集群上限={CAPACITY}")
    if inproc["observed_concurrency"] <= CAPACITY:
        failures.append(
            "进程内 limiter 未越限——用例没有构造出跨实例并发，因此不能证明 counter 必要"
        )
    else:
        print(f"   OK  确实越限（{inproc['observed_concurrency']} > {CAPACITY}），"
              "证明进程内状态无法约束集群配额")

    print("2) 持久化 counter")
    persist = _persistent_run(plain, CAPACITY, INSTANCES, WORKERS)
    print(f"   集群配置={CAPACITY} 实例={INSTANCES} 观测并发={persist['observed_concurrency']}"
          f" lease 行={persist['lease_rows']}")
    if persist["observed_concurrency"] > CAPACITY:
        failures.append(f"持久化 counter 越限：{persist['observed_concurrency']} > {CAPACITY}")
    else:
        print(f"   OK  未越限（{persist['observed_concurrency']} <= {CAPACITY}）")

    with _conn(plain) as c:
        c.execute("DROP TABLE IF EXISTS xc_counters, xc_leases CASCADE")
        c.commit()

    report = {"in_process": inproc, "persistent": persist, "failures": failures}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "cross-instance-capacity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 进程内 limiter 在多实例下确实翻倍；持久化 counter 正确约束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
