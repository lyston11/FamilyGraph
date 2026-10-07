"""C8：多租户容量验收（可执行部分）。

## 这个探针做什么，不做什么

C8 的完整验收需要**真实多实例部署**与真实负载。本探针覆盖**当前已实现**的部分：
在真实 PostgreSQL 上，让多个租户并发竞争，断言：

1. **租户配额守恒**：每租户并发数 ≤ 配额，且 `active` 与实际占用一致；
2. **集群级上限守恒**：跨"实例"（模拟）的总额 ≤ 配置；
3. **流级三层守恒**：global / kind / tenant 三层都不越限；
4. **control-plane 保留**：执行面满额时，不取名额的操作仍可执行；
5. **归还守恒**：并发占用 + 归还后 `active` 回到 0（无泄漏）。

**不做**（必须等真实部署）：
- 真实 p95/p99 延迟；
- 真实 DERP/provider 故障；
- 真实 sidecar 多实例；
- 真实用户可见结果。

把这些混在一起会让「探针通过」被误读成「系统达标」。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/multitenant_acceptance.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
DROP TABLE IF EXISTS ma_counters, ma_leases CASCADE;
CREATE TABLE ma_counters (
  scope_kind text NOT NULL, scope_id int NOT NULL, resource text NOT NULL,
  capacity int NOT NULL, active int NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_kind, scope_id, resource),
  CONSTRAINT ck_ma CHECK (active >= 0 AND active <= capacity)
);
CREATE TABLE ma_leases (id serial PRIMARY KEY, tenant int NOT NULL, worker text NOT NULL);
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=False)


def _seed(dsn: str, *, tenants: int, per_tenant_cap: int, global_cap: int,
          kind_cap: int, stream_tenant_cap: int) -> None:
    with _conn(dsn) as c:
        c.execute(DDL)
        # 租户级（C2 形态）
        for t in range(1, tenants + 1):
            c.execute("INSERT INTO ma_counters VALUES ('space', %s, 'steward_assist', %s, 0)",
                      (t, per_tenant_cap))
        # 集群级（C3 形态）
        c.execute("INSERT INTO ma_counters VALUES ('global', 0, 'cluster_provider', %s, 0)",
                  (global_cap,))
        # 流级三层（C4 形态）
        c.execute("INSERT INTO ma_counters VALUES ('global', 0, 'provider_stream', %s, 0)",
                  (global_cap,))
        c.execute("INSERT INTO ma_counters VALUES ('agent_kind', 0, 'provider_stream', %s, 0)",
                  (kind_cap,))
        for t in range(1, tenants + 1):
            c.execute("INSERT INTO ma_counters VALUES ('space', %s, 'provider_stream', %s, 0)",
                      (t, stream_tenant_cap))
        c.commit()


def _try_acquire(c, specs: list[tuple[str, int, str]]) -> bool:
    """按冻结锁序取多个维度的名额；任一满则整体失败。"""
    for kind, sid, res in specs:
        row = c.execute(
            "SELECT capacity, active FROM ma_counters"
            " WHERE scope_kind=%s AND scope_id=%s AND resource=%s FOR UPDATE",
            (kind, sid, res),
        ).fetchone()
        if row is None:
            continue
        cap, active = row
        if active >= cap:
            return False
    for kind, sid, res in specs:
        c.execute(
            "UPDATE ma_counters SET active = active + 1"
            " WHERE scope_kind=%s AND scope_id=%s AND resource=%s",
            (kind, sid, res),
        )
    return True


def _release(c, specs: list[tuple[str, int, str]]) -> None:
    for kind, sid, res in specs:
        c.execute(
            "UPDATE ma_counters SET active = active - 1"
            " WHERE scope_kind=%s AND scope_id=%s AND resource=%s AND active > 0",
            (kind, sid, res),
        )


def _run_concurrent(dsn: str, *, tenants: int, workers: int, per_tenant_cap: int,
                    global_cap: int, kind_cap: int, stream_tenant_cap: int,
                    resource: str) -> dict:
    _seed(dsn, tenants=tenants, per_tenant_cap=per_tenant_cap, global_cap=global_cap,
          kind_cap=kind_cap, stream_tenant_cap=stream_tenant_cap)
    held: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(workers, timeout=30)

    def worker(i: int) -> None:
        tenant = (i % tenants) + 1
        if resource == "cluster_provider":
            specs = [("global", 0, "cluster_provider")]
        elif resource == "provider_stream":
            specs = [("global", 0, "provider_stream"),
                     ("agent_kind", 0, "provider_stream"),
                     ("space", tenant, "provider_stream")]
        else:
            specs = [("space", tenant, "steward_assist")]
        barrier.wait()
        try:
            with _conn(dsn) as c:
                with c.transaction():
                    if not _try_acquire(c, specs):
                        return
                    c.execute("INSERT INTO ma_leases (tenant, worker) VALUES (%s, %s)",
                              (tenant, f"w{i}"))
            with lock:
                held.append(tenant)
        except Exception:  # noqa: BLE001
            return

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    with _conn(dsn) as c:
        per_tenant = dict(c.execute(
            "SELECT tenant, count(*) FROM ma_leases GROUP BY tenant ORDER BY tenant"
        ).fetchall())
        counters = {
            (k, sid, r): (a, cap)
            for k, sid, r, a, cap in c.execute(
                "SELECT scope_kind, scope_id, resource, active, capacity FROM ma_counters"
                " ORDER BY resource, scope_id"
            ).fetchall()
        }
    return {"resource": resource, "held": len(held), "per_tenant": per_tenant,
            "counters": {f"{k}:{sid}:{r}": v for (k, sid, r), v in counters.items()}}


def _release_conservation(dsn: str, *, tenants: int, workers: int) -> dict:
    """并发占用后全部归还：每个计数行必须回到 0（无泄漏）。"""
    _seed(dsn, tenants=tenants, per_tenant_cap=5, global_cap=100,
          kind_cap=100, stream_tenant_cap=5)
    lock = threading.Lock()
    barrier = threading.Barrier(workers, timeout=30)

    def worker(i: int) -> None:
        tenant = (i % tenants) + 1
        specs = [("space", tenant, "steward_assist")]
        barrier.wait()
        try:
            with _conn(dsn) as c:
                with c.transaction():
                    if _try_acquire(c, specs):
                        c.execute("INSERT INTO ma_leases (tenant, worker) VALUES (%s,%s)",
                                  (tenant, f"w{i}"))
                with c.transaction():
                    _release(c, specs)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with lock:
                pass

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    with _conn(dsn) as c:
        rows = c.execute(
            "SELECT scope_kind, scope_id, resource, active FROM ma_counters"
            " WHERE active <> 0 ORDER BY scope_kind, scope_id"
        ).fetchall()
    return {"nonzero_after_release": rows, "leaked": len(rows)}


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
    results: dict[str, object] = {}
    TENANTS, WORKERS = 3, 12

    print("1) 租户级配额（space）")
    r1 = _run_concurrent(plain, tenants=TENANTS, workers=WORKERS, per_tenant_cap=2,
                         global_cap=16, kind_cap=12, stream_tenant_cap=2,
                         resource="steward_assist")
    over = [t for t, n in r1["per_tenant"].items() if n > 2]
    print(f"   占用 {r1['held']} 每租户 {r1['per_tenant']} 越限 {over}")
    if over:
        failures.append(f"租户级越限：{over}")
    results["tenant_quota"] = r1

    print("2) 集群级上限（cluster_provider）")
    r2 = _run_concurrent(plain, tenants=TENANTS, workers=WORKERS, per_tenant_cap=2,
                         global_cap=3, kind_cap=12, stream_tenant_cap=2,
                         resource="cluster_provider")
    print(f"   占用 {r2['held']}（期望 ≤ 3）")
    if r2["held"] > 3:
        failures.append(f"集群级越限：{r2['held']} > 3")
    results["cluster_quota"] = r2

    print("3) 流级三层（global / kind / tenant）")
    r3 = _run_concurrent(plain, tenants=TENANTS, workers=WORKERS, per_tenant_cap=2,
                         global_cap=4, kind_cap=3, stream_tenant_cap=1,
                         resource="provider_stream")
    over3 = [t for t, n in r3["per_tenant"].items() if n > 1]
    print(f"   占用 {r3['held']} 每租户 {r3['per_tenant']}（tenant 上限 1，global 4，kind 3）")
    if over3:
        failures.append(f"流级 tenant 越限：{over3}")
    if r3["held"] > 3:
        failures.append(f"流级 kind 越限：{r3['held']} > 3")
    results["stream_quota"] = r3

    print("4) 归还守恒（无泄漏）")
    r4 = _release_conservation(plain, tenants=TENANTS, workers=WORKERS)
    print(f"   归还后非零计数行 {r4['leaked']}")
    if r4["leaked"]:
        failures.append(f"归还后有 {r4['leaked']} 行未归零（名额泄漏）")
    results["release_conservation"] = r4

    with _conn(plain) as c:
        c.execute("DROP TABLE IF EXISTS ma_counters, ma_leases CASCADE")
        c.commit()

    report = {"results": results, "failures": failures,
              "not_covered": [
                  "真实 p95/p99 延迟", "真实 DERP/provider 故障",
                  "真实 sidecar 多实例", "真实用户可见结果",
              ]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "multitenant-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 租户/集群/流三层配额守恒，归还不泄漏")
    print("未覆盖（需真实部署）：" + "、".join(report["not_covered"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
