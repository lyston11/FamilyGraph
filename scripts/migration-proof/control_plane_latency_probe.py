"""控制面尾延迟与 DB 连接饱和（真实开发环境，只读）。

## 要回答的问题

`10-04-control-plane-fault-domain` 的 AC：

> execution 满载时 control p95/p99 有界。

以及 `10-04-postgres-operations-cutover` 的：

> 在隔离 PostgreSQL 注入 pool exhaustion、长事务、断线、重启。

## 为什么用 `/api/ready`

它是控制面的**真实代表**：一次请求要拿一条 DB 连接、读 `writer_epoch`（含迁移阶段）、
查扩展状态、跑 `capacity_bootstrap.assert_ready`。这些正是
heartbeat/lease/settle/cancel/health 共享的资源。内部端点（lease/heartbeat/settle）
需要 service token，伪造它等于绕过信任边界，因此不碰。

## 只读保证

本探针只发 `GET /api/ready` 与只读 SQL（`pg_stat_activity`）。它**不写业务数据**、
不修改配置、不重启服务。连接饱和通过**占用只读连接**制造，而不是改池配置。

## 用法（在开发服务器上）

    BASE_URL=http://127.0.0.1:8000 python3 scripts/migration-proof/control_plane_latency_probe.py

可选 `PGTEST_DSN` 用于读取 `pg_stat_activity` 的连接分布（缺省时跳过该项）。
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
READY_PATH = os.environ.get("READY_PATH", "/api/ready")

#: 基线采样次数（无负载）。
BASELINE_SAMPLES = int(os.environ.get("LATENCY_BASELINE", "120"))
#: 负载下的采样次数与并发执行者数。
LOAD_SAMPLES = int(os.environ.get("LATENCY_LOAD", "240"))
LOAD_WORKERS = int(os.environ.get("LATENCY_WORKERS", "16"))
#: 饱和项占用的只读连接数。
SATURATE_CONNECTIONS = int(os.environ.get("LATENCY_SATURATE", "12"))


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _get_ready(timeout: float = 10.0) -> tuple[int, float]:
    """返回 (status_code, elapsed_ms)。4xx/5xx 也计入延迟——它们同样是控制面响应。"""
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE_URL + READY_PATH, timeout=timeout) as resp:
            resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        code = exc.code
    except Exception:  # noqa: BLE001 - 连接失败也算一次观测，如实计入
        code = 0
    return code, (time.perf_counter() - started) * 1000


def _sample(n: int) -> tuple[list[float], dict[int, int]]:
    lat: list[float] = []
    codes: dict[int, int] = {}
    for _ in range(n):
        code, ms = _get_ready()
        lat.append(ms)
        codes[code] = codes.get(code, 0) + 1
    return lat, codes


def _summary(lat: list[float]) -> dict[str, float]:
    return {
        "n": len(lat),
        "p50_ms": round(_percentile(lat, 0.50), 2),
        "p95_ms": round(_percentile(lat, 0.95), 2),
        "p99_ms": round(_percentile(lat, 0.99), 2),
        "max_ms": round(max(lat), 2) if lat else 0.0,
        "mean_ms": round(statistics.fmean(lat), 2) if lat else 0.0,
    }


def _saturate_connections(
    dsn: str, count: int, stop: threading.Event
) -> threading.Thread:
    """在后台占住 `count` 条**只读**连接，模拟连接池紧张。"""

    def worker() -> None:
        import psycopg

        # SQLAlchemy 风格的 `postgresql+psycopg://` 不是 libpq 连接串，
        # `psycopg.connect` 会报 `missing "=" after ...`。必须转成普通形式。
        plain = (
            dsn.replace("postgresql+psycopg://", "postgresql://")
            .replace("postgresql+psycopg2://", "postgresql://")
        )
        conns = []
        try:
            for _ in range(count):
                c = psycopg.connect(plain, autocommit=True)
                c.execute("SELECT 1")  # 只读
                conns.append(c)
            while not stop.is_set():
                time.sleep(0.2)
        finally:
            for c in conns:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def _pg_activity(dsn: str) -> dict[str, int]:
    try:
        import psycopg

        plain = dsn.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(plain, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT state, count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() GROUP BY state"
            ).fetchall()
        return {str(state): int(n) for state, n in rows}
    except Exception:  # noqa: BLE001 - 该项是可选观测
        return {}


def main() -> int:
    print(f"目标：{BASE_URL}{READY_PATH}")
    code, ms = _get_ready()
    if code == 0:
        print("SKIP: 目标不可达——先确认开发服务在跑")
        return 2
    print(f"首探：HTTP {code}，{ms:.1f}ms\n")

    results: dict[str, object] = {"base_url": BASE_URL, "ready_path": READY_PATH}

    # ---- 1. 基线 ----
    print(f"=== 1. 基线（无额外负载，{BASELINE_SAMPLES} 次）===")
    lat, codes = _sample(BASELINE_SAMPLES)
    base = _summary(lat)
    results["baseline"] = {**base, "status_codes": codes}
    print(
        f"  p50={base['p50_ms']:.2f}ms  p95={base['p95_ms']:.2f}ms  "
        f"p99={base['p99_ms']:.2f}ms  max={base['max_ms']:.2f}ms  {codes}"
    )

    # ---- 2. 并发负载 ----
    print(f"\n=== 2. 并发负载（{LOAD_WORKERS} 并发执行者 × {LOAD_SAMPLES} 次）===")
    collected: list[float] = []
    load_codes: dict[int, int] = {}
    lock = threading.Lock()

    def load_worker() -> None:
        per = max(1, LOAD_SAMPLES // LOAD_WORKERS)
        local: list[float] = []
        local_codes: dict[int, int] = {}
        for _ in range(per):
            c, m = _get_ready()
            local.append(m)
            local_codes[c] = local_codes.get(c, 0) + 1
        with lock:
            collected.extend(local)
            for k, v in local_codes.items():
                load_codes[k] = load_codes.get(k, 0) + v

    threads = [threading.Thread(target=load_worker) for _ in range(LOAD_WORKERS)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started
    loaded = _summary(collected)
    results["under_load"] = {
        **loaded,
        "status_codes": load_codes,
        "wall_seconds": round(wall, 2),
    }
    print(
        f"  p50={loaded['p50_ms']:.2f}ms  p95={loaded['p95_ms']:.2f}ms  "
        f"p99={loaded['p99_ms']:.2f}ms  max={loaded['max_ms']:.2f}ms  {load_codes}"
    )
    print(f"  实际吞吐 {loaded['n'] / wall:.0f} req/s（{wall:.1f}s）")

    # ---- 3. 连接饱和 ----
    dsn = os.environ.get("PGTEST_DSN")
    if dsn and SATURATE_CONNECTIONS > 0:
        print(f"\n=== 3. 连接饱和（占用 {SATURATE_CONNECTIONS} 条只读连接）===")
        before = _pg_activity(dsn)
        print(f"  饱和前 pg_stat_activity：{before}")
        stop = threading.Event()
        _saturate_connections(dsn, SATURATE_CONNECTIONS, stop)
        time.sleep(1.0)
        during = _pg_activity(dsn)
        sat_lat, sat_codes = _sample(max(40, LOAD_SAMPLES // 3))
        sat = _summary(sat_lat)
        stop.set()
        time.sleep(0.5)
        after = _pg_activity(dsn)
        results["under_connection_pressure"] = {
            **sat,
            "status_codes": sat_codes,
            "activity_before": before,
            "activity_during": during,
            "activity_after": after,
        }
        print(
            f"  p50={sat['p50_ms']:.2f}ms  p95={sat['p95_ms']:.2f}ms  "
            f"p99={sat['p99_ms']:.2f}ms  max={sat['max_ms']:.2f}ms  {sat_codes}"
        )
        print(f"  饱和期间 pg_stat_activity：{during}")
        print(f"  释放后：{after}")
    else:
        print("\n=== 3. 连接饱和：跳过（未提供 PGTEST_DSN）===")

    # ---- 判定 ----
    print("\n=== 判定 ===")
    bounded = loaded["p99_ms"] < 1000.0
    no_5xx = all(code < 500 for code in load_codes if code != 0)
    print(
        f"  负载下 p99={loaded['p99_ms']:.2f}ms  {'有界' if bounded else '⚠ 超过 1s'}"
    )
    print(f"  负载下无 5xx：{'是' if no_5xx else '否'}  {load_codes}")
    results["bounded_p99_under_load"] = bounded
    results["no_5xx_under_load"] = no_5xx

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "control-plane-latency.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n证据：{OUT_DIR / 'control-plane-latency.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
