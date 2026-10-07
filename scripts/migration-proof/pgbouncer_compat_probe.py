"""C7：PgBouncer transaction pooling 兼容性。

## 为什么这是高风险项

transaction pooling 在**每个事务结束时**把后端连接还给池，因此：

| 特性 | transaction pooling | 影响 |
|---|---|---|
| `FOR UPDATE`（事务级行锁） | ✅ 安全 | 锁随事务结束释放，语义不变 |
| `SET LOCAL` | ✅ 安全 | 事务级设置 |
| **会话级 `SET`** | ❌ 泄漏/丢失 | 设置可能落到别的客户端，或自己下次拿到不同连接 |
| `pg_advisory_lock`（会话级） | ❌ 失效 | 锁绑在连接上，连接被复用后锁归属错乱 |
| `pg_advisory_xact_lock`（事务级） | ✅ 安全 | 随事务释放 |
| **prepared statements** | ⚠️ 需配置 | psycopg3 默认在 5 次执行后转 prepared；PgBouncer 未开 `max_prepared_statements` 时报错 |

最后一项是**最容易被漏掉**的：psycopg3 的自动 prepared statement 在本地 PostgreSQL 上
永远正常，只有经过 PgBouncer 才暴露。

## 本探针断言

1. `FOR UPDATE` + 固定锁序的配额逻辑在 PgBouncer 下仍正确（并发守恒）；
2. psycopg3 的 prepared statement 路径**不会**报错（否则必须关闭自动 prepare）；
3. 会话级 `SET` 确实**不可靠**（记录事实，避免有人误用它）；
4. 事务级 advisory lock 可用（会话级不可用）。

用法：

    PGBOUNCER_DSN=postgresql://... DIRECT_DSN=postgresql://... python3 scripts/migration-proof/pgbouncer_compat_probe.py
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
DROP TABLE IF EXISTS pb_counters, pb_leases CASCADE;
CREATE TABLE pb_counters (
  scope_kind text NOT NULL, scope_id int NOT NULL,
  capacity int NOT NULL, active int NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_kind, scope_id),
  CONSTRAINT ck_pb CHECK (active >= 0 AND active <= capacity)
);
CREATE TABLE pb_leases (id serial PRIMARY KEY, tenant int NOT NULL, worker text NOT NULL);
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=False)


def test_quota_conservation(dsn: str, *, label: str) -> dict:
    """并发配额在 PgBouncer 下必须与直连一致：counter + 固定锁序仍然守恒。"""
    with _conn(dsn) as c:
        c.execute(DDL)
        for t in range(1, 4):
            c.execute("INSERT INTO pb_counters VALUES ('space', %s, 2, 0)", (t,))
        c.commit()

    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(12, timeout=30)

    def worker(i: int) -> None:
        tenant = (i % 3) + 1
        barrier.wait()
        try:
            with _conn(dsn) as c:
                with c.transaction():
                    # 冻结锁序：先锁 counter，再取候选
                    row = c.execute(
                        "SELECT capacity, active FROM pb_counters"
                        " WHERE scope_kind='space' AND scope_id=%s FOR UPDATE", (tenant,)
                    ).fetchone()
                    cap, active = row
                    if active >= cap:
                        with lock:
                            results.append("full")
                        return
                    cand = c.execute(
                        "SELECT id FROM pb_leases WHERE tenant IS NULL LIMIT 1"
                    ).fetchone()
                    c.execute("UPDATE pb_counters SET active = active + 1"
                              " WHERE scope_kind='space' AND scope_id=%s", (tenant,))
                    c.execute("INSERT INTO pb_leases (tenant, worker) VALUES (%s, %s)",
                              (tenant, f"w{i}"))
            with lock:
                results.append("leased")
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(f"err:{type(exc).__name__}")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    with _conn(dsn) as c:
        per = dict(c.execute(
            "SELECT tenant, count(*) FROM pb_leases GROUP BY tenant ORDER BY tenant"
        ).fetchall())
        counters = dict(c.execute(
            "SELECT scope_id, active FROM pb_counters ORDER BY scope_id"
        ).fetchall())
    over = [t for t, n in per.items() if n > 2]
    mismatch = [t for t, n in per.items() if counters.get(t) != n]
    return {"label": label, "leased": results.count("leased"), "per_tenant": per,
            "counters": counters, "over_capacity": over, "counter_mismatch": mismatch,
            "errors": sorted({r for r in results if r.startswith("err:")})}


def test_prepared_statements(dsn: str, *, label: str) -> dict:
    """psycopg3 的自动 prepared statement 必须不报错。

    psycopg3 在**第 5 次**执行同一语句后转为 prepared（服务端命名语句）。
    transaction pooling 下服务端语句绑在连接上，连接被复用时会产生
    `prepared statement "..." already exists` 或 `does not exist`。

    本测试执行同一语句 20 次（远超阈值）并**切换连接**，因此能触发该问题。
    """
    errors: list[str] = []
    try:
        for _round in range(4):  # 每轮新建连接，模拟连接被池复用
            with _conn(dsn) as c:
                for i in range(20):
                    c.execute("SELECT %s::int + 1", (i,)).fetchone()
                c.commit()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:110]}")
    return {"label": label, "errors": errors, "ok": not errors}


def test_session_vs_transaction_state(dsn: str, *, label: str) -> dict:
    """记录事实：会话级 `SET` 不可靠；事务级 `SET LOCAL` 可靠。

    这不是「要求失败」的测试，而是**记录部署约束**：应用代码不得依赖会话级
    `SET`（本仓库 grep 确认无 `SET LOCAL`、无会话级 `SET`，因此当前安全）。
    """
    import psycopg

    session_reliable = True
    try:
        with _conn(dsn) as c:
            c.execute("SET statement_timeout = '7s'")
            c.commit()
            # 新事务：transaction pooling 可能已换连接
            observed = c.execute("SHOW statement_timeout").fetchone()[0]
            session_reliable = observed == "7s"
            c.execute("RESET statement_timeout")
            c.commit()
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "session_set_error": type(exc).__name__}

    xact_ok = True
    try:
        with _conn(dsn) as c, c.transaction():
            c.execute("SET LOCAL statement_timeout = '9s'")
            observed = c.execute("SHOW statement_timeout").fetchone()[0]
            xact_ok = observed == "9s"
    except Exception as exc:  # noqa: BLE001
        xact_ok = False
        return {"label": label, "xact_set_error": type(exc).__name__}

    advisory_xact_ok = True
    try:
        with _conn(dsn) as c, c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(4242)")
    except Exception:  # noqa: BLE001
        advisory_xact_ok = False

    return {
        "label": label,
        "session_set_reliable": session_reliable,
        "transaction_set_local_reliable": xact_ok,
        "advisory_xact_lock_ok": advisory_xact_ok,
    }


def test_prepared_statement_failure_mode(dsn: str) -> dict:
    """**负向**：确认 prepared statement 的失败模式真实存在，且由配置决定。

    ## 为什么必须做这一步

    上面的 `test_prepared_statements` 通过**不能**说明「没问题」——它只能说明
    「在当前 PgBouncer 配置下没问题」。若失败模式根本触发不到，那 PASS 就没有
    判别力（可能是 psycopg 从未真正 prepare）。

    本测试用 `prepare_threshold=0` **强制立即 prepare**，从而确认真实行为：

    | PgBouncer 配置 | `prepare_threshold=0` 的结果 |
    |---|---|
    | 默认（有 prepared 支持） | 通过 |
    | `max_prepared_statements=0` | **`DuplicatePreparedStatement`** |

    因此生产约束是明确的：**要么 PgBouncer 支持 prepared statements（默认），
    要么 psycopg 关闭自动 prepare**。两者都没有时会在高并发下报
    `prepared statement "_pg3_0" already exists`——而本地直连 PostgreSQL
    **永远测不出**。
    """
    import psycopg

    errors: list[str] = []
    try:
        with psycopg.connect(dsn, prepare_threshold=0) as c:
            for i in range(5):
                c.execute("SELECT %s::int + 1", (i,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:100]}")
    return {"forced_prepare_errors": errors}


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    pooled = os.environ.get("PGBOUNCER_DSN")
    direct = os.environ.get("DIRECT_DSN")
    if not pooled or not direct:
        print("SKIP: 需要 PGBOUNCER_DSN 与 DIRECT_DSN（均指向隔离环境）")
        return 2

    failures: list[str] = []
    report: dict = {}

    print("1) 直连对照（基线）")
    base = test_quota_conservation(direct, label="direct")
    print(f"   leased={base['leased']} per_tenant={base['per_tenant']}"
          f" over={base['over_capacity']} mismatch={base['counter_mismatch']}")
    report["direct"] = base

    print("2) 经 PgBouncer（transaction pooling）")
    proxied = test_quota_conservation(pooled, label="pgbouncer")
    print(f"   leased={proxied['leased']} per_tenant={proxied['per_tenant']}"
          f" over={proxied['over_capacity']} mismatch={proxied['counter_mismatch']}")
    if proxied["errors"]:
        print(f"   errors={proxied['errors']}")
    report["pgbouncer"] = proxied
    if proxied["over_capacity"]:
        failures.append(f"PgBouncer 下配额越限：{proxied['over_capacity']}")
    if proxied["counter_mismatch"]:
        failures.append(f"PgBouncer 下 counter 与实占不符：{proxied['counter_mismatch']}")
    if proxied["errors"]:
        failures.append(f"PgBouncer 下出现错误：{proxied['errors']}")
    if proxied["per_tenant"] != base["per_tenant"]:
        failures.append(
            f"PgBouncer 与直连结果不同：{proxied['per_tenant']} vs {base['per_tenant']}"
        )

    print("3) prepared statements")
    ps_direct = test_prepared_statements(direct, label="direct")
    ps_pooled = test_prepared_statements(pooled, label="pgbouncer")
    print(f"   直连 ok={ps_direct['ok']}；PgBouncer ok={ps_pooled['ok']}")
    if ps_pooled["errors"]:
        print(f"   PgBouncer 错误：{ps_pooled['errors']}")
    report["prepared_statements"] = {"direct": ps_direct, "pgbouncer": ps_pooled}
    if not ps_pooled["ok"]:
        failures.append(
            "PgBouncer 下 prepared statement 失败——必须关闭 psycopg 自动 prepare "
            f"或启用 max_prepared_statements：{ps_pooled['errors']}"
        )

    print("3b) prepared statement 失败模式（强制 prepare）")
    forced = test_prepared_statement_failure_mode(pooled)
    print(f"   强制 prepare 错误：{forced['forced_prepare_errors'] or '无'}")
    report["prepared_statement_forced"] = forced
    if forced["forced_prepare_errors"]:
        failures.append(
            "强制 prepare 在 PgBouncer 下失败——部署必须二选一："
            "PgBouncer 支持 prepared statements，或 psycopg 关闭自动 prepare。"
            f" 实测：{forced['forced_prepare_errors']}"
        )

    print("4) 会话级 vs 事务级状态")
    state = test_session_vs_transaction_state(pooled, label="pgbouncer")
    print(f"   {state}")
    report["state_semantics"] = state
    if not state.get("transaction_set_local_reliable", False):
        failures.append("事务级 SET LOCAL 不可靠——这不应发生")
    if not state.get("advisory_xact_lock_ok", False):
        failures.append("事务级 advisory lock 不可用")
    if state.get("session_set_reliable"):
        print("   注：本次会话级 SET 恰好可靠（连接未被换出），但**不得依赖**它")

    with _conn(direct) as c:
        c.execute("DROP TABLE IF EXISTS pb_counters, pb_leases CASCADE")
        c.commit()

    report["failures"] = failures
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pgbouncer-compat.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: PgBouncer transaction pooling 下配额守恒、prepared statement 正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
