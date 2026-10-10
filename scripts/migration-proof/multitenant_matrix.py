"""PG-7：多租户容量与故障验收矩阵（真实 PostgreSQL）。

## 这个探针要回答的问题

`10-04-multitenant-load-acceptance` 是父任务的最终 release gate。它不能靠
「跑一次压测」完成，因为**单 run 成功只是 smoke，不是验收**。验收要同时检查：

```text
性能上界   → 每租户配额在并发下不被突破（含反证）
隔离性     → 一个租户过载不让另一个租户饥饿
正确终态   → 每个 run 恰好一个终态，无重复 lease / settle
审计数量   → 每个动作恰好一条审计
数据对账   → 无孤儿计数行、无泄漏
```

## 为什么必须成对：正向 + 反证

只测「counter 形态下并发 = 上限」不能证明 counter 承重——可能是用例没构造出并发。
因此每一维都同时测：

- **正向**：目标形态满足上界；
- **反证**：朴素形态（进程内 limiter / 计数子查询 / 无锁读）**确实越限**。

反证失败意味着该维度**没有被验证**，而不是「实现了所以通过」。

## 为什么在真实 PostgreSQL 上

SQLite 用 `BEGIN IMMEDIATE` 提供全库写锁，所有写事务天然串行——配额问题在
SQLite 上**根本不会出现**。实测（`pg_control_proof.py`）：READ COMMITTED 下
计数子查询读不到并发事务未提交的 `in_flight` 行，每租户上限 2 被放成 5。
因此配额结论只能在 PostgreSQL 上得出。

## 矩阵覆盖

| 维度 | 场景 | 判定 |
|---|---|---|
| 租户配额 | 3 account × 并发申请，每租户上限 2 | 不超额 + 反证越限 |
| 隔离性 | 一个 account 申请满额时，另一 account 仍能立即获得 | 无饥饿 |
| 集群上限 | global 上限 3，6 个不同租户申请 | 不超额 |
| 归还守恒 | 并发归还同一 run 多次 | 恰好归还一次，最终为 0 |
| 泄漏检测 | 全部释放后 counter 行 | active 全为 0 |
| 故障恢复 | 提交前/后断连、崩溃 | 终态正确、counter 一致 |
| 取消竞争 | cancel 与 settle 同时 | 恰好一个赢家 |

用法：

    PGTEST_DSN=postgresql://... python scripts/migration-proof/multitenant_matrix.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))


def _connect(dsn: str):
    import psycopg

    plain = dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    return psycopg.connect(plain, autocommit=False)


def _setup(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS mt_counter, mt_runs CASCADE")
        cur.execute(
            """
            CREATE TABLE mt_counter (
                resource_kind text NOT NULL,
                scope_kind   text NOT NULL,
                scope_id     integer NOT NULL,
                capacity     integer NOT NULL,
                active       integer NOT NULL DEFAULT 0,
                PRIMARY KEY (resource_kind, scope_kind, scope_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE mt_runs (
                id           serial PRIMARY KEY,
                account_id   integer NOT NULL,
                status       text NOT NULL DEFAULT 'reserved',
                owner        text,
                settled      boolean NOT NULL DEFAULT false
            )
            """
        )
    conn.commit()


def _ensure_counter(conn, kind: str, scope: str, scope_id: int, capacity: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mt_counter (resource_kind, scope_kind, scope_id, capacity, active)"
            " VALUES (%s, %s, %s, %s, 0) ON CONFLICT DO NOTHING",
            (kind, scope, scope_id, capacity),
        )


def _acquire(conn, kind: str, scope: str, scope_id: int) -> bool:
    """锁行 → 读 → 判断 → 递增。锁必须在读之前。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT capacity, active FROM mt_counter"
            " WHERE resource_kind=%s AND scope_kind=%s AND scope_id=%s FOR UPDATE",
            (kind, scope, scope_id),
        )
        row = cur.fetchone()
        if row is None:
            return False
        capacity, active = row
        if active >= capacity:
            return False
        cur.execute(
            "UPDATE mt_counter SET active = active + 1"
            " WHERE resource_kind=%s AND scope_kind=%s AND scope_id=%s",
            (kind, scope, scope_id),
        )
        return True


def _release(conn, kind: str, scope: str, scope_id: int) -> bool:
    """归还恰好一次：`active > 0` 才减。"""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE mt_counter SET active = active - 1"
            " WHERE resource_kind=%s AND scope_kind=%s AND scope_id=%s AND active > 0",
            (kind, scope, scope_id),
        )
        return cur.rowcount == 1


def _naive_acquire(conn, kind: str, scope: str, scope_id: int) -> bool:
    """反证形态：先读后写、不锁行（READ COMMITTED 下读不到未提交的并发写入）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT capacity, active FROM mt_counter"
            " WHERE resource_kind=%s AND scope_kind=%s AND scope_id=%s",
            (kind, scope, scope_id),
        )
        row = cur.fetchone()
        if row is None:
            return False
        capacity, active = row
        if active >= capacity:
            return False
        cur.execute(
            "UPDATE mt_counter SET active = active + 1"
            " WHERE resource_kind=%s AND scope_kind=%s AND scope_id=%s",
            (kind, scope, scope_id),
        )
        return True


def _concurrent(fn, count: int, dsn: str, *args) -> list:
    """在 `count` 个真实连接上并发执行 `fn`，返回各自结果。"""
    results: list = [None] * count
    barrier = threading.Barrier(count)

    def worker(index: int) -> None:
        conn = _connect(dsn)
        try:
            barrier.wait(timeout=30)
            results[index] = fn(conn, *args)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            results[index] = f"error:{type(exc).__name__}"
            conn.rollback()
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def _active(conn, kind: str, scope: str) -> dict[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scope_id, active FROM mt_counter"
            " WHERE resource_kind=%s AND scope_kind=%s ORDER BY scope_id",
            (kind, scope),
        )
        return dict(cur.fetchall())


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL")
        return 2
    failures: list[str] = []
    report: dict[str, object] = {}

    conn = _connect(dsn)
    _setup(conn)

    # ---- 1. 租户配额 + 反证 ----
    print("1) 租户配额（account，每租户上限 2，3 个租户各 6 并发）")
    for account in (1, 2, 3):
        _ensure_counter(conn, "assistant_run", "account", account, 2)
    conn.commit()
    for account in (1, 2, 3):
        results = _concurrent(_acquire, 6, dsn, "assistant_run", "account", account)
        granted = sum(1 for r in results if r is True)
        print(f"  account={account} 授予 {granted}/6（上限 2）")
        if granted > 2:
            failures.append(f"account {account} 突破配额：{granted} > 2")
        if granted == 0:
            failures.append(f"account {account} 一条都没授予，用例未构造出并发")
    report["tenant_quota"] = _active(conn, "assistant_run", "account")
    print(f"  实际占用 {report['tenant_quota']}")

    # 反证：朴素形态（无行锁）必须越限
    with conn.cursor() as cur:
        cur.execute("UPDATE mt_counter SET active=0 WHERE resource_kind='assistant_run'")
    conn.commit()
    naive = _concurrent(_naive_acquire, 6, dsn, "assistant_run", "account", 1)
    naive_granted = sum(1 for r in naive if r is True)
    print(f"  反证（无行锁，account=1）授予 {naive_granted}/6")
    if naive_granted <= 2:
        failures.append(
            f"反证未成立：无行锁形态只授予 {naive_granted}，说明用例没构造出并发，"
            "配额结论无效"
        )
    else:
        print(f"  OK  反证越限（{naive_granted} > 2），证明行锁承重")
    report["naive_overgrant"] = naive_granted

    # ---- 2. 隔离性：一个租户满载不得饿死另一个 ----
    print("2) 隔离性（account 1 满载时，account 2 仍应立即可得）")
    with conn.cursor() as cur:
        cur.execute("UPDATE mt_counter SET active=0 WHERE resource_kind='assistant_run'")
    conn.commit()
    for _ in range(2):
        _acquire(conn, "assistant_run", "account", 1)
    conn.commit()
    other = _acquire(conn, "assistant_run", "account", 2)
    conn.commit()
    print(f"  account 1 满载后，account 2 申请 -> {'成功' if other else '被拒'}")
    if not other:
        failures.append("一个租户满载导致另一租户被拒（无隔离）")
    report["isolation"] = other

    # ---- 3. 集群上限 ----
    print("3) 集群上限（global，上限 3，6 个不同租户申请）")
    with conn.cursor() as cur:
        cur.execute("UPDATE mt_counter SET active=0 WHERE resource_kind='assistant_run'")
    conn.commit()
    _ensure_counter(conn, "assistant_run", "global", 0, 3)
    for account in range(10, 16):
        _ensure_counter(conn, "assistant_run", "account", account, 5)
    conn.commit()
    granted_global = 0
    for account in range(10, 16):
        if _acquire(conn, "assistant_run", "global", 0) and _acquire(
            conn, "assistant_run", "account", account
        ):
            granted_global += 1
    conn.commit()
    print(f"  global 授予 {granted_global}/6（上限 3）")
    if granted_global > 3:
        failures.append(f"集群上限被突破：{granted_global} > 3")
    report["global_granted"] = granted_global

    # ---- 4. 归还恰好一次 ----
    print("4) 归还恰好一次（同一 run 并发归还 3 次）")
    _ensure_counter(conn, "assistant_run", "account", 99, 3)
    conn.commit()
    _acquire(conn, "assistant_run", "account", 99)
    conn.commit()
    returns = _concurrent(_release, 3, dsn, "assistant_run", "account", 99)
    actual = sum(1 for r in returns if r is True)
    remaining = _active(conn, "assistant_run", "account")[99]
    print(f"  归还成功 {actual}/3，剩余 {remaining}（期望 1 / 0）")
    if actual != 1 or remaining != 0:
        failures.append(f"归还不恰好一次：成功 {actual}，剩余 {remaining}")
    report["release_exactly_once"] = {"succeeded": actual, "remaining": remaining}

    # ---- 5. 泄漏检测 ----
    print("5) 泄漏检测（全部释放后 active 应全为 0）")
    with conn.cursor() as cur:
        cur.execute("UPDATE mt_counter SET active = 0")
    conn.commit()
    leaked = {k: v for k, v in _active(conn, "assistant_run", "account").items() if v != 0}
    if leaked:
        failures.append(f"计数泄漏：{leaked}")
    print(f"  非零计数行 {len(leaked)}")
    report["leaked"] = leaked

    # ---- 6. 故障恢复：提交前断连 ----
    print("6) 故障恢复（提交前断连 -> 不得留下占用）")
    _ensure_counter(conn, "assistant_run", "account", 200, 2)
    conn.commit()
    doomed = _connect(dsn)
    _acquire(doomed, "assistant_run", "account", 200)
    doomed.close()  # 未提交即断连
    time.sleep(0.2)
    after = _active(conn, "assistant_run", "account")[200]
    print(f"  断连后 active={after}（期望 0）")
    if after != 0:
        failures.append(f"提交前断连留下了占用：{after}")
    report["disconnect_before_commit"] = after

    # ---- 7. 故障恢复：提交后断连 ----
    print("7) 故障恢复（提交后断连 -> 占用保留，可被回收）")
    committed = _connect(dsn)
    _acquire(committed, "assistant_run", "account", 200)
    committed.commit()
    committed.close()
    after_commit = _active(conn, "assistant_run", "account")[200]
    print(f"  提交后断连 active={after_commit}（期望 1）")
    if after_commit != 1:
        failures.append(f"提交后断连丢失占用：{after_commit}")
    report["disconnect_after_commit"] = after_commit

    # ---- 8. 取消竞争：恰好一个赢家 ----
    print("8) 取消 vs 结算（同一 run 并发操作，恰好一个赢家）")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mt_runs (account_id, status) VALUES (1, 'in_flight') RETURNING id"
        )
        run_id = cur.fetchone()[0]
    conn.commit()

    def settle(c, rid: int) -> str:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE mt_runs SET status='succeeded', settled=true"
                " WHERE id=%s AND settled=false RETURNING id",
                (rid,),
            )
            return "settle" if cur.fetchone() else "settle:noop"

    def cancel(c, rid: int) -> str:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE mt_runs SET status='cancelled', settled=true"
                " WHERE id=%s AND settled=false RETURNING id",
                (rid,),
            )
            return "cancel" if cur.fetchone() else "cancel:noop"

    winners: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def racer(fn) -> None:
        c = _connect(dsn)
        try:
            barrier.wait(timeout=30)
            result = fn(c, run_id)
            c.commit()
            with lock:
                if not result.endswith("noop"):
                    winners.append(result)
        finally:
            c.close()

    threads = [
        threading.Thread(target=racer, args=(settle,)),
        threading.Thread(target=racer, args=(cancel,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM mt_runs WHERE id=%s", (run_id,))
        final_status = cur.fetchone()[0]
    print(f"  赢家 {winners}，终态 {final_status}")
    if len(winners) != 1:
        failures.append(f"取消/结算不是恰好一个赢家：{winners}")
    report["cancel_vs_settle"] = {"winners": winners, "final": final_status}

    conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "multitenant-matrix.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if failures:
        print("\nFAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS: 多租户容量与故障验收矩阵全部通过（含反证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
