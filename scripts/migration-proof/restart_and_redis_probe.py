"""PostgreSQL 重启与 Redis 故障注入（专用验收实例）。

## 安全护栏

与 `pool_exhaustion_probe.py` 同源：PG 端口必须在 `ACC_ALLOWED_PORTS` 白名单内，
显式拒绝共用开发库（55450）与生产（5432）。重启只对白名单实例执行。

## 注入项

| 项 | 做法 | 期望 |
|---|---|---|
| PG 重启 | `docker restart <container>` | 服务自愈，控制面恢复 200，无持续 5xx |
| Redis 中断 | 停 Redis 容器 | 服务降级而非崩溃（`pg_fallback` 策略） |
| Redis 恢复 | 起 Redis 容器 | 自动恢复使用，无需人工干预 |

## 用法

    PGTEST_DSN=postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph \
    BASE_URL=http://127.0.0.1:8010 \
    ACC_PG_CONTAINER=fg-acc-pg ACC_REDIS_CONTAINER=fg-acc-redis \
        python3 scripts/migration-proof/restart_and_redis_probe.py
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8010")
READY_PATH = os.environ.get("READY_PATH", "/api/ready")

ALLOWED_PORTS = {
    int(p) for p in os.environ.get("ACC_ALLOWED_PORTS", "55460").split(",") if p.strip()
}
FORBIDDEN_PORTS = {55450, 5432}

PG_CONTAINER = os.environ.get("ACC_PG_CONTAINER", "")
REDIS_CONTAINER = os.environ.get("ACC_REDIS_CONTAINER", "")

#: 重启后等待服务自愈的最长时间。
RECOVER_TIMEOUT = float(os.environ.get("ACC_RECOVER_TIMEOUT", "120"))


def _plain(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def _port_of(dsn: str) -> int:
    import urllib.parse

    return int(urllib.parse.urlparse(_plain(dsn)).port or 5432)


def _guard(dsn: str) -> str | None:
    port = _port_of(dsn)
    if port in FORBIDDEN_PORTS:
        return f"拒绝在端口 {port} 上执行重启（共用开发库/生产）"
    if port not in ALLOWED_PORTS:
        return f"端口 {port} 不在白名单 {sorted(ALLOWED_PORTS)} 内"
    if not PG_CONTAINER or not REDIS_CONTAINER:
        return "需要 ACC_PG_CONTAINER 与 ACC_REDIS_CONTAINER"
    return None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _get_ready(timeout: float = 20.0) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE_URL + READY_PATH, timeout=timeout) as resp:
            resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        code = exc.code
    except Exception:  # noqa: BLE001
        code = 0
    return code, (time.perf_counter() - started) * 1000


def _sample(n: int, interval: float = 0.0) -> tuple[list[float], dict[int, int]]:
    lat: list[float] = []
    codes: dict[int, int] = {}
    for _ in range(n):
        code, ms = _get_ready()
        lat.append(ms)
        codes[code] = codes.get(code, 0) + 1
        if interval:
            time.sleep(interval)
    return lat, codes


def _summary(lat: list[float]) -> dict[str, float]:
    if not lat:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    return {
        "n": len(lat),
        "p50_ms": round(_percentile(lat, 0.50), 2),
        "p95_ms": round(_percentile(lat, 0.95), 2),
        "p99_ms": round(_percentile(lat, 0.99), 2),
        "max_ms": round(max(lat), 2),
        "mean_ms": round(statistics.fmean(lat), 2),
    }


def _docker(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=180, check=False
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _wait_ready(deadline: float) -> tuple[bool, float]:
    """等到 `/api/ready` 返回 200；返回 (是否成功, 恢复耗时秒)。"""
    started = time.perf_counter()
    while time.perf_counter() < deadline:
        code, _ms = _get_ready()
        if code == 200:
            return True, time.perf_counter() - started
        time.sleep(1.0)
    return False, time.perf_counter() - started


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向专用验收实例")
        return 2
    refusal = _guard(dsn)
    if refusal:
        print(f"REFUSED: {refusal}")
        return 2

    code, ms = _get_ready()
    if code == 0:
        print(f"SKIP: {BASE_URL}{READY_PATH} 不可达")
        return 2
    print(f"目标：{BASE_URL}{READY_PATH}；首探 HTTP {code} {ms:.1f}ms")
    print(
        f"护栏通过：PG 端口 {_port_of(dsn)}；容器 {PG_CONTAINER} / {REDIS_CONTAINER}\n"
    )

    results: dict[str, object] = {"base_url": BASE_URL, "pg_port": _port_of(dsn)}

    # ---- 1. 基线 ----
    print("=== 1. 基线（40 次）===")
    lat, codes = _sample(40)
    base = _summary(lat)
    results["baseline"] = {**base, "status_codes": codes}
    print(
        f"  p50={base['p50_ms']:.2f}ms p95={base['p95_ms']:.2f}ms p99={base['p99_ms']:.2f}ms {codes}\n"
    )

    # ---- 2. Redis 中断 ----
    print(f"=== 2. Redis 中断（停 {REDIS_CONTAINER}）===")
    rc, out = _docker("stop", REDIS_CONTAINER)
    print(f"  docker stop rc={rc} {out[:120]}")
    time.sleep(2.0)
    lat, codes = _sample(60, interval=0.1)
    down = _summary(lat)
    print(
        f"  Redis 停时：p50={down['p50_ms']:.2f}ms p99={down['p99_ms']:.2f}ms {codes}"
    )
    crashed = sum(v for k, v in codes.items() if k == 0)
    print(
        f"  无响应(k=0)={crashed}；5xx={sum(v for k, v in codes.items() if 500 <= k < 600)}"
    )
    results["redis_down"] = {**down, "status_codes": codes}

    rc, out = _docker("start", REDIS_CONTAINER)
    print(f"  docker start rc={rc} {out[:120]}")
    time.sleep(5.0)
    lat, codes = _sample(40, interval=0.1)
    up = _summary(lat)
    print(f"  Redis 恢复后：p50={up['p50_ms']:.2f}ms p99={up['p99_ms']:.2f}ms {codes}")
    results["redis_restored"] = {**up, "status_codes": codes}
    print()

    # ---- 3. PG 重启 ----
    print(f"=== 3. PostgreSQL 重启（重启 {PG_CONTAINER}）===")
    rc, out = _docker("restart", PG_CONTAINER)
    print(f"  docker restart rc={rc} {out[:120]}")
    lat, codes = _sample(30, interval=0.5)
    during = _summary(lat)
    print(
        f"  重启期间：p50={during['p50_ms']:.2f}ms p99={during['p99_ms']:.2f}ms {codes}"
    )
    recovered, elapsed = _wait_ready(time.time() + RECOVER_TIMEOUT)
    print(f"  自愈：{'成功' if recovered else '⚠ 超时'}，耗时 {elapsed:.1f}s")
    lat, codes = _sample(60, interval=0.1)
    after = _summary(lat)
    print(f"  恢复后：p50={after['p50_ms']:.2f}ms p99={after['p99_ms']:.2f}ms {codes}")
    results["pg_restart"] = {
        "during": {**during, "status_codes": codes},
        "recovered": recovered,
        "recovery_seconds": round(elapsed, 1),
        "after": {**after, "status_codes": codes},
    }
    print()

    # ---- 判定 ----
    print("=== 判定 ===")
    redis_ok = (
        sum(v for k, v in results["redis_down"]["status_codes"].items() if k == 0) == 0
    )  # type: ignore[index,union-attr]
    print(f"  Redis 中断：{'无挂死' if redis_ok else '⚠ 存在无响应'}")
    print(f"  PG 重启自愈：{'是' if recovered else '否'}（{elapsed:.1f}s）")
    print(
        f"  恢复后 200 占比："
        f"{results['pg_restart']['after']['status_codes'].get(200, 0)}/{results['pg_restart']['after']['n']}"  # type: ignore[index]
    )
    results["redis_down_no_hang"] = redis_ok
    results["pg_restart_recovered"] = recovered

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "restart-and-redis.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n证据：{OUT_DIR / 'restart-and-redis.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
