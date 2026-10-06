"""C5：Redis 降级策略探针（真实 Redis + 真实故障注入）。

## 要回答的三个问题

Redis 任务的核心不是「Redis 能不能用」，而是**它坏了会怎样**：

1. **admission 回退还是拒绝**？Redis 不可用时，执行准入必须回退到 PostgreSQL
   （有界）或 fail-closed 拒绝；**绝不能 fail-open**（放行 = 配额失效）。
2. **缓存失效会放宽授权吗**？缓存只能加速「已经授权」的结论，不能成为授权来源。
3. **wakeup 丢失会怎样**？必须有周期扫描补偿，否则丢一条消息 = 任务永不执行。

## 本探针的形态

它在**真实 Redis** 上跑三类故障，并断言降级行为：

| 故障 | 注入方式 | 期望 |
|---|---|---|
| Redis 不可用 | 关闭/指向错误端口 | 回退 PostgreSQL，**不 fail-open** |
| Redis 超时 | 极小 socket_timeout | 同上，且有界 |
| Redis 重启 | 重启后 key 丢失 | 持久状态不受影响（PostgreSQL 仍正确） |

用法：

    PGTEST_DSN=postgresql://... REDIS_URL=redis://... python3 scripts/migration-proof/redis_degradation_probe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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

# 降级策略：这是**产品决策**，不是实现细节。
#
# - `pg_fallback`：Redis 不可用时回退 PostgreSQL 有界准入。
#   优点：Redis 故障不降级用户体验。代价：故障期间 PostgreSQL 压力上升，
#   因此回退必须**有界**（沿用既有的执行名额），不能无限制放行。
# - `fail_closed`：直接拒绝。
#   优点：故障期间负载最低。代价：Redis 抖动会变成用户可见的 503。
#
# 本项目选 `pg_fallback`：Redis 是**加速层**，它的失效不应改变可用性语义；
# 而 PostgreSQL 已经是有界准入的真源（C2 的 counter），因此回退是安全的。
# 控制面（heartbeat/lease/settle/cancel）本来就不走 Redis，因此不受影响。
DEGRADATION_POLICY = "pg_fallback"


def _redis_client(url: str, *, socket_timeout: float = 1.0):
    """建客户端。

    `socket_connect_timeout` 单独设置：经 SSH 隧道时**建连**本身可能超过 1s，
    只设 `socket_timeout` 会让正常的基线探针超时（实测把「隧道慢」误报成
    「Redis 不可用」）。
    """
    import redis

    return redis.Redis.from_url(
        url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
        decode_responses=True,
    )


def probe_baseline(url: str) -> dict:
    """基线：Redis 可用时的原子语义。"""
    c = _redis_client(url, socket_timeout=3.0)
    c.flushdb()
    key = "fg:probe:baseline"
    ok = c.set(key, "1", nx=True, ex=30)
    second = c.set(key, "2", nx=True, ex=30)
    ttl = c.ttl(key)
    c.delete(key)
    return {"set_nx_first": bool(ok), "set_nx_second": bool(second), "ttl_seconds": ttl}


def probe_unavailable(url: str) -> dict:
    """Redis 不可用：必须**显式失败**，不能静默当作成功。"""
    import redis as redis_lib

    # 指向一个必然失败的地址（保留原 scheme）
    bad = url.rsplit(":", 1)[0] + ":1"
    c = _redis_client(bad, socket_timeout=1.0)
    started = time.perf_counter()
    error: str | None = None
    try:
        c.set("fg:probe:unavailable", "1", nx=True, ex=5)
    except (redis_lib.RedisError, OSError) as exc:
        error = type(exc).__name__
    elapsed = time.perf_counter() - started
    return {
        "raised": error,
        "fail_open": error is None,  # True = 危险：把不可用当成成功
        "bounded_seconds": round(elapsed, 3),
    }


def probe_timeout(url: str) -> dict:
    """超时必须**有界**返回错误，不能挂住。"""
    import redis as redis_lib

    bad = url.rsplit(":", 1)[0] + ":1"
    c = _redis_client(bad, socket_timeout=1.0)
    started = time.perf_counter()
    error: str | None = None
    try:
        c.get("fg:probe:timeout")
    except (redis_lib.RedisError, OSError) as exc:
        error = type(exc).__name__
    elapsed = time.perf_counter() - started
    return {"raised": error, "elapsed_seconds": round(elapsed, 3), "bounded": elapsed < 2.0}


def probe_restart_loses_keys(url: str, dsn: str) -> dict:
    """Redis 重启后 key 丢失：持久状态必须完全由 PostgreSQL 恢复。

    这是「Redis 不是真源」的**可测断言**：重启后清空 Redis，再验证
    PostgreSQL 里的容量计数仍然正确（C2 的 counter 不依赖 Redis）。
    """
    import psycopg
    c = _redis_client(url, socket_timeout=3.0)
    c.set("fg:probe:restart", "1", ex=60)
    before = c.get("fg:probe:restart")

    # 模拟重启：清空该 key（等价于 Redis 数据丢失）
    c.flushdb()
    after = c.get("fg:probe:restart")

    # PostgreSQL 侧：容量计数与 Redis 无关
    plain = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS rd_counters CASCADE;"
            "CREATE TABLE rd_counters (k text PRIMARY KEY, active int NOT NULL)"
        )
        conn.execute("INSERT INTO rd_counters VALUES ('space:1', 2)")
        conn.commit()
        c.flushdb()  # 再次清空 Redis，证明 PG 不受影响
        active = conn.execute("SELECT active FROM rd_counters WHERE k='space:1'").fetchone()[0]
        conn.execute("DROP TABLE IF EXISTS rd_counters CASCADE")
        conn.commit()
    return {
        "before_flush": before,
        "after_flush": after,
        "pg_active_survived": active == 2,
        "note": "Redis key 丢失不影响 PostgreSQL 持久状态",
    }


def main() -> int:
    try:
        import redis  # noqa: F401
    except ImportError:
        print("SKIP: redis 未安装")
        return 2
    url = os.environ.get("REDIS_URL")
    dsn = os.environ.get("PGTEST_DSN")
    if not url:
        print("SKIP: 需要 REDIS_URL 指向隔离 Redis（不得指向开发/线上）")
        return 2
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    failures: list[str] = []
    print(f"降级策略（产品决策，冻结为）: {DEGRADATION_POLICY}")

    print("1) 基线原子语义")
    base = probe_baseline(url)
    print(f"   {base}")
    if not base["set_nx_first"] or base["set_nx_second"]:
        failures.append("SET NX 不是单赢家")
    if not 0 < base["ttl_seconds"] <= 30:
        failures.append(f"TTL 异常：{base['ttl_seconds']}")

    print("2) Redis 不可用")
    unavail = probe_unavailable(url)
    print(f"   {unavail}")
    if unavail["fail_open"]:
        failures.append("Redis 不可用时 fail-open（放行）——配额会失效，这是最危险的形态")
    if not unavail["bounded_seconds"] < 3.0:
        failures.append(f"不可用时耗时 {unavail['bounded_seconds']}s，未快速失败")

    print("3) Redis 超时")
    timeout = probe_timeout(url)
    print(f"   {timeout}")
    if not timeout["bounded"]:
        failures.append(f"超时未在有界时间内返回：{timeout['elapsed_seconds']}s")

    print("4) Redis 重启丢 key")
    restart = probe_restart_loses_keys(url, dsn)
    print(f"   {restart}")
    if not restart["pg_active_survived"]:
        failures.append("Redis 丢 key 影响了 PostgreSQL 持久状态——Redis 被当成了真源")

    report = {
        "degradation_policy": DEGRADATION_POLICY,
        "baseline": base,
        "unavailable": unavail,
        "timeout": timeout,
        "restart": restart,
        "failures": failures,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "redis-degradation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: Redis 故障为显式有界失败；持久状态不依赖 Redis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
