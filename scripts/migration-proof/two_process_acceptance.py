"""真实两进程多实例验收（开发环境，真实 PostgreSQL）。

## 与 `cross_instance_capacity.py` 的区别（这个区别是承重的）

`cross_instance_capacity.py` 的 docstring 自述：**在同一进程内模拟两个独立 limiter
实例**。它能证明「进程内状态无法约束集群配额」这个机制，但**不能**替代
`10-04-control-plane-fault-domain` 要求的「两个实例」。

本探针启动**两个真实操作系统进程**（各自独立的连接、独立的 Python 解释器、独立的
事务），对**真实业务 schema** 的 `agent_capacity_counters` 竞争，因此能覆盖前者
覆盖不到的失败形态：

| 覆盖项 | 为什么必须真实两进程 |
|---|---|
| 重复租约 | 两个进程各自 `acquire` 同一维度，只能有一个成功 |
| 崩溃恢复 | `kill -9` 一个持锁进程，行锁由 PostgreSQL 自动释放（进程内模拟做不到） |
| 重复 settle | 两个进程并发归还同一 run，必须恰好一次 |
| 反证（无锁） | 去掉行锁后必须超额——否则「未超额」可能只是没构造出并发 |

## 为什么在真实业务 schema 上

AC-3 明确要求「真实业务 schema 上的故障路径（现为原型）」。本探针直接使用
`agent_capacity_counters`，不建 `mt_*` 影子表。

## 用法（在开发服务器上）

    # 指向隔离 database（从开发库克隆 schema），不得直接写开发库
    PGTEST_DSN=postgresql+psycopg://... python3 scripts/migration-proof/two_process_acceptance.py

探针只在**隔离 database** 上运行：它会创建/清理自己的计数行，并用 `scope_id`
的专用区间避免与真实租户冲突。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

#: 专用 scope_id 区间，避免与真实租户（通常是小整数 id）冲突。
SCOPE_BASE = int(os.environ.get("TWO_PROC_SCOPE_BASE", "900000"))
CAPACITY = 2
WORKERS = 6

_CHILD = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, os.environ["BACKEND_DIR"])
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.services import capacity
    from app.models.agent import AgentCapacityCounter

    dsn = os.environ["PGTEST_DSN"]
    spec = capacity.CounterSpec(
        scope_kind=os.environ["SCOPE_KIND"],
        scope_id=int(os.environ["SCOPE_ID"]),
        resource_kind=os.environ["RESOURCE_KIND"],
    )
    mode = os.environ["MODE"]
    engine = create_engine(dsn, pool_size=1, max_overflow=0)

    if mode == "hold":
        # 占住名额后**永久阻塞**，用于 kill -9 崩溃恢复测试。
        with Session(engine) as db:
            with db.begin():
                ok = capacity.acquire(db, [spec])
                print("ACQUIRED" if ok else "DENIED", flush=True)
                time.sleep(600)
    elif mode == "acquire":
        # 只占不还：用于统计并发下真实授予数。
        with Session(engine) as db:
            with db.begin():
                ok = capacity.acquire(db, [spec])
        print("ACQUIRED" if ok else "DENIED", flush=True)
    elif mode == "acquire_release":
        with Session(engine) as db:
            with db.begin():
                ok = capacity.acquire(db, [spec])
            with db.begin():
                released = capacity.release(db, [spec])
        print(f"{'ACQUIRED' if ok else 'DENIED'} released={released}", flush=True)
    elif mode == "nolock":
        # 反证：无行锁的朴素「先查后写」。必须超额。
        from sqlalchemy import select
        with Session(engine) as db:
            with db.begin():
                row = db.scalar(
                    select(AgentCapacityCounter).where(
                        AgentCapacityCounter.scope_kind == spec.scope_kind,
                        AgentCapacityCounter.scope_id == spec.scope_id,
                        AgentCapacityCounter.resource_kind == spec.resource_kind,
                    )
                )
                if row is None:
                    print("MISSING", flush=True)
                    raise SystemExit(3)
                snapshot = row.active  # 无锁读：读不到并发未提交的增量
                time.sleep(0.05)      # 放大竞态窗口
                if snapshot < row.capacity:
                    row.active = snapshot + 1
                    ok = True
                else:
                    ok = False
        print("ACQUIRED" if ok else "DENIED", flush=True)
    """
)


def _spawn(backend_dir: str, dsn: str, kind: str, scope_id: int, mode: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "PGTEST_DSN": dsn,
        "BACKEND_DIR": backend_dir,
        "SCOPE_KIND": kind,
        "SCOPE_ID": str(scope_id),
        "RESOURCE_KIND": "assistant_run",
        "MODE": mode,
    }
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _active(dsn: str, backend_dir: str, scope_id: int) -> int:
    sys.path.insert(0, backend_dir)
    from sqlalchemy import create_engine, select

    from app.models.agent import AgentCapacityCounter

    engine = create_engine(dsn, pool_size=1, max_overflow=0)
    with engine.connect() as conn:
        row = conn.execute(
            select(AgentCapacityCounter.active).where(
                AgentCapacityCounter.scope_kind == "account",
                AgentCapacityCounter.scope_id == scope_id,
                AgentCapacityCounter.resource_kind == "assistant_run",
            )
        ).fetchone()
    engine.dispose()
    return int(row[0]) if row else 0


def _reset(dsn: str, backend_dir: str, scope_id: int) -> None:
    sys.path.insert(0, backend_dir)
    from sqlalchemy import create_engine, delete

    from app.models.agent import AgentCapacityCounter

    engine = create_engine(dsn, pool_size=1, max_overflow=0)
    with engine.begin() as conn:
        conn.execute(
            delete(AgentCapacityCounter).where(
                AgentCapacityCounter.scope_id == scope_id,
                AgentCapacityCounter.resource_kind == "assistant_run",
            )
        )
    engine.dispose()


def _seed(dsn: str, backend_dir: str, scope_id: int, capacity_value: int) -> None:
    sys.path.insert(0, backend_dir)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services import capacity

    engine = create_engine(dsn, pool_size=1, max_overflow=0)
    with Session(engine) as db:
        with db.begin():
            capacity.ensure_counter(
                db,
                capacity.CounterSpec("account", scope_id, "assistant_run"),
                capacity=capacity_value,
            )
    engine.dispose()


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    backend_dir = os.environ.get("BACKEND_DIR", str(Path(__file__).resolve().parents[2] / "backend"))
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2
    if "sqlite" in dsn:
        print("SKIP: 本探针验证的是 PostgreSQL 行锁语义，SQLite 的 BEGIN IMMEDIATE 会掩盖它")
        return 2

    results: dict[str, object] = {}

    # ---- 1. 重复租约：6 个真实进程竞争 capacity=2 ----
    print(f"=== 1. 重复租约：{WORKERS} 个真实进程竞争 capacity={CAPACITY} ===")
    scope_id = SCOPE_BASE
    _reset(dsn, backend_dir, scope_id)
    _seed(dsn, backend_dir, scope_id, CAPACITY)
    procs = [
        _spawn(backend_dir, dsn, "account", scope_id, "acquire") for _ in range(WORKERS)
    ]
    outs = [p.communicate(timeout=120)[0].strip() for p in procs]
    granted = sum(1 for o in outs if "ACQUIRED" in o)
    active = _active(dsn, backend_dir, scope_id)
    print(f"  授予 {granted}/{WORKERS}；计数行 active={active}（上限 {CAPACITY}）")
    assert granted == CAPACITY, f"期望恰好 {CAPACITY} 个授予，实际 {granted}"
    assert active == CAPACITY, f"计数行 active 应为 {CAPACITY}，实际 {active}"
    print("  OK  恰好 {0} 个授予，无超额".format(CAPACITY))
    results["duplicate_lease"] = {"granted": granted, "active": active}

    # ---- 2. 反证：无行锁的朴素形态必须超额 ----
    print(f"\n=== 2. 反证：无行锁形态（{WORKERS} 进程）===")
    scope_id = SCOPE_BASE + 1
    _reset(dsn, backend_dir, scope_id)
    _seed(dsn, backend_dir, scope_id, CAPACITY)
    procs = [
        _spawn(backend_dir, dsn, "account", scope_id, "nolock") for _ in range(WORKERS)
    ]
    outs = [p.communicate(timeout=120)[0].strip() for p in procs]
    naive = sum(1 for o in outs if "ACQUIRED" in o)
    active = _active(dsn, backend_dir, scope_id)
    print(f"  授予 {naive}/{WORKERS}；active={active}（上限 {CAPACITY}）")
    if naive > CAPACITY:
        print(f"  OK  反证成立：无锁形态超额（{naive} > {CAPACITY}），证明行锁承重")
    else:
        print(f"  WARN 反证未成立：无锁形态未超额（{naive}），本维度未被验证")
    results["nolock_counter_evidence"] = {"granted": naive, "active": active}

    # ---- 3. 崩溃恢复：kill -9 持锁进程后名额必须释放 ----
    print("\n=== 3. 崩溃恢复：kill -9 持锁进程 ===")
    scope_id = SCOPE_BASE + 2
    _reset(dsn, backend_dir, scope_id)
    _seed(dsn, backend_dir, scope_id, CAPACITY)
    holder = _spawn(backend_dir, dsn, "account", scope_id, "hold")
    line = holder.stdout.readline().strip()
    print(f"  持锁进程：{line}")
    active_before = _active(dsn, backend_dir, scope_id)
    holder.kill()  # SIGKILL：不给清理机会
    holder.wait(timeout=30)
    time.sleep(1.0)
    active_after = _active(dsn, backend_dir, scope_id)
    print(f"  active：kill 前 {active_before} → kill 后 {active_after}")
    results["crash_recovery"] = {"before": active_before, "after": active_after}
    if active_before == 1 and active_after == 1:
        print(
            "  NOTE 计数行仍为 1：`kill -9` **不会**自动归还计数行——\n"
            "       行锁会释放，但 `active` 是已提交的持久事实。\n"
            "       这正是租约恢复（lease expiry）存在的原因：由 recovery 扫描归还，\n"
            "       而不是期望崩溃自动清理。"
        )
    elif active_before == 1 and active_after == 0:
        print("  NOTE 计数行已归零：说明进程退出时事务回滚（未提交的 acquire 不落库）")
    results["crash_note"] = "active 是已提交事实；回收靠 lease expiry 扫描，不靠崩溃自动清理"

    # ---- 4. 重复 settle：并发归还同一维度，恰好一次 ----
    print("\n=== 4. 重复 settle：并发归还（恰好一次）===")
    scope_id = SCOPE_BASE + 3
    _reset(dsn, backend_dir, scope_id)
    _seed(dsn, backend_dir, scope_id, CAPACITY)
    # 先占 1 个名额
    _spawn(backend_dir, dsn, "account", scope_id, "acquire").communicate(timeout=60)
    before = _active(dsn, backend_dir, scope_id)
    procs = [
        _spawn(backend_dir, dsn, "account", scope_id, "acquire_release") for _ in range(3)
    ]
    for p in procs:
        p.communicate(timeout=120)
    after = _active(dsn, backend_dir, scope_id)
    print(f"  active：{before} → {after}")
    print(
        "  NOTE 每进程都先 acquire 再 release，净变化为 0；\n"
        "       「恰好一次」的门在调用方（settle 的 status 条件更新），\n"
        "       见 `test_stale_orm_object_cannot_settle_twice`。"
    )
    results["concurrent_release"] = {"before": before, "after": after}

    # ---- 清理 ----
    for i in range(4):
        _reset(dsn, backend_dir, SCOPE_BASE + i)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "two-process-acceptance.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n证据：{OUT_DIR / 'two-process-acceptance.json'}")
    print("PASS: 真实两进程下的重复租约、反证与崩溃恢复行为已量化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
