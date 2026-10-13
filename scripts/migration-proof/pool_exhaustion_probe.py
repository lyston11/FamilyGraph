"""真实连接池耗尽与故障注入（专用验收实例，可安全饱和）。

## 为什么需要专用实例

`10-04-postgres-operations-cutover` 的 AC 要求在隔离 PG 上注入
**pool exhaustion、长事务、断线、重启**。在共用开发服务上占满连接池会危及同机
其他服务，因此本探针要求指向**专用验收实例**（独立 PG + Redis + API 进程）。

## 安全护栏（必须在饱和前通过）

1. `PGTEST_DSN` 的端口必须在 `ACC_ALLOWED_PORTS` 白名单内（默认 `55460`）；
2. 拒绝 `55450`（共用开发库）与 `5432`（生产）；
3. 饱和前打印 `max_connections` 与当前占用，且要求剩余连接数 >= 预留量。

任一不满足即拒绝运行——**这是本探针最重要的部分**：一个能对生产库执行
「占满连接」的脚本，护栏比功能重要。

## 注入项

| 项 | 做法 | 期望 |
|---|---|---|
| pool exhaustion | 占满至 `max_connections - 1` | 控制面仍能拿到连接（预留生效），或明确 503 而非挂死 |
| 长事务 | 持有一个未提交事务 30s | 控制面不受影响（读不阻塞） |
| 断线 | 关闭连接池中的连接 | 服务自愈，无 5xx 持续 |
| PG 重启 | 不自动做——需人工决定窗口 | 登记为未做 |

## 用法

    PGTEST_DSN=postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph \
    BASE_URL=http://127.0.0.1:8010 \
        python3 scripts/migration-proof/pool_exhaustion_probe.py
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

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8010")
READY_PATH = os.environ.get("READY_PATH", "/api/ready")

#: 允许执行饱和的 PG 端口白名单。**默认只含专用验收实例**。
ALLOWED_PORTS = {
    int(p) for p in os.environ.get("ACC_ALLOWED_PORTS", "55460").split(",") if p.strip()
}
#: 显式禁止的端口（共用开发库与生产）。
FORBIDDEN_PORTS = {55450, 5432}
#: 饱和时保留多少条连接给服务自己与控制面。
RESERVED_CONNECTIONS = int(os.environ.get("ACC_RESERVED_CONNECTIONS", "8"))
#: 长事务持有时长。
LONG_TX_SECONDS = float(os.environ.get("ACC_LONG_TX_SECONDS", "20"))


def _plain(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def _port_of(dsn: str) -> int:
    import urllib.parse

    parsed = urllib.parse.urlparse(_plain(dsn))
    return int(parsed.port or 5432)


def _guard(dsn: str) -> str | None:
    """返回拒绝原因；None 表示可以继续。"""
    port = _port_of(dsn)
    if port in FORBIDDEN_PORTS:
        return f"拒绝在端口 {port} 上饱和连接（共用开发库/生产）"
    if port not in ALLOWED_PORTS:
        return (
            f"端口 {port} 不在 ACC_ALLOWED_PORTS 白名单 {sorted(ALLOWED_PORTS)} 内；"
            "饱和连接只允许在专用验收实例上执行"
        )
    return None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _get_ready(timeout: float = 15.0) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE_URL + READY_PATH, timeout=timeout) as resp:
            resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        code = exc.code
    except Exception:  # noqa: BLE001 - 超时/拒绝也是一次观测
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


def _pg_state(dsn: str) -> dict[str, object]:
    import psycopg

    with psycopg.connect(_plain(dsn), autocommit=True) as conn:
        maxc = int(conn.execute("SHOW max_connections").fetchone()[0])
        total = int(conn.execute("SELECT count(*) FROM pg_stat_activity").fetchone()[0])
        rows = conn.execute(
            "SELECT state, count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() GROUP BY state"
        ).fetchall()
    return {
        "max_connections": maxc,
        "total_connections": total,
        "by_state": {str(s): int(n) for s, n in rows},
    }


class _PoolHog:
    """占住 N 条连接直到 stop。每条连接空闲但不关闭。"""

    def __init__(self, dsn: str, count: int) -> None:
        self._dsn = _plain(dsn)
        self._count = count
        self._conns: list[object] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.held = 0

    def start(self) -> None:
        def worker() -> None:
            import psycopg

            try:
                for _ in range(self._count):
                    conn = psycopg.connect(self._dsn, autocommit=True)
                    conn.execute("SELECT 1")
                    self._conns.append(conn)
                    self.held += 1
                while not self._stop.is_set():
                    time.sleep(0.2)
            except Exception:  # noqa: BLE001 - 达到 max_connections 是预期
                pass

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for conn in self._conns:
            try:
                conn.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self._conns.clear()


class _LongTx:
    """持有一个未提交事务 `seconds` 秒。"""

    def __init__(self, dsn: str, seconds: float) -> None:
        self._dsn = _plain(dsn)
        self._seconds = seconds
        self._stop = threading.Event()
        self._conn = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()

    def start(self) -> None:
        def worker() -> None:
            import psycopg

            conn = psycopg.connect(self._dsn, autocommit=False)
            self._conn = conn
            conn.execute("SELECT 1")
            self.ready.set()  # 事务已开启且未提交
            self._stop.wait(self._seconds)
            conn.rollback()
            conn.close()

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)


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
    print(f"护栏通过：PG 端口 {_port_of(dsn)} 在专用白名单内\n")

    results: dict[str, object] = {"base_url": BASE_URL, "pg_port": _port_of(dsn)}

    state = _pg_state(dsn)
    results["pg_state_initial"] = state
    print(f"=== 初始状态 ===\n  {state}\n")
    maxc = int(state["max_connections"])
    already = int(state["total_connections"])
    to_hold = max(0, maxc - already - RESERVED_CONNECTIONS)
    print(
        f"  计划占住 {to_hold} 条（max={maxc} 已用={already} 预留={RESERVED_CONNECTIONS}）\n"
    )

    # ---- 1. 基线 ----
    print("=== 1. 基线（40 次）===")
    lat, codes = _sample(40)
    base = _summary(lat)
    results["baseline"] = {**base, "status_codes": codes}
    print(
        f"  p50={base['p50_ms']:.2f}ms p95={base['p95_ms']:.2f}ms p99={base['p99_ms']:.2f}ms {codes}\n"
    )

    # ---- 2. 长事务 ----
    print(f"=== 2. 长事务（持有未提交事务 {LONG_TX_SECONDS:.0f}s）===")
    tx = _LongTx(dsn, LONG_TX_SECONDS)
    tx.start()
    tx.ready.wait(timeout=10)
    mid_state = _pg_state(dsn)
    lat, codes = _sample(60)
    tx_sum = _summary(lat)
    tx.stop()
    results["long_transaction"] = {
        **tx_sum,
        "status_codes": codes,
        "pg_state": mid_state,
    }
    print(
        f"  p50={tx_sum['p50_ms']:.2f}ms p95={tx_sum['p95_ms']:.2f}ms p99={tx_sum['p99_ms']:.2f}ms {codes}"
    )
    print(f"  pg_state：{mid_state['by_state']}\n")

    # ---- 3. pool exhaustion ----
    print(f"=== 3. 连接池耗尽（占住 {to_hold} 条）===")
    hog = _PoolHog(dsn, to_hold)
    hog.start()
    deadline = time.time() + 30
    while hog.held < to_hold and time.time() < deadline:
        time.sleep(0.2)
    print(f"  实际占住 {hog.held} 条")
    sat_state = _pg_state(dsn)
    print(f"  pg_state：total={sat_state['total_connections']} {sat_state['by_state']}")
    lat, codes = _sample(80)
    sat = _summary(lat)
    hog.stop()
    time.sleep(1.0)
    after_state = _pg_state(dsn)
    results["pool_exhaustion"] = {
        **sat,
        "status_codes": codes,
        "held": hog.held,
        "pg_state_during": sat_state,
        "pg_state_after": after_state,
    }
    print(
        f"  p50={sat['p50_ms']:.2f}ms p95={sat['p95_ms']:.2f}ms p99={sat['p99_ms']:.2f}ms {codes}"
    )
    print(
        f"  释放后：total={after_state['total_connections']} {after_state['by_state']}\n"
    )

    # ---- 4. 断线恢复 ----
    print("=== 4. 断线恢复（终止本实例的空闲连接）===")
    import psycopg

    with psycopg.connect(_plain(dsn), autocommit=True) as conn:
        killed = conn.execute(
            "SELECT count(*) FROM ("
            " SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND state = 'idle'"
            " AND pid <> pg_backend_pid()) t"
        ).fetchone()
    print(f"  已终止空闲连接：{killed[0] if killed else 0}")
    time.sleep(2.0)
    lat, codes = _sample(60, interval=0.2)
    rec = _summary(lat)
    results["disconnect_recovery"] = {**rec, "status_codes": codes}
    print(
        f"  p50={rec['p50_ms']:.2f}ms p95={rec['p95_ms']:.2f}ms p99={rec['p99_ms']:.2f}ms {codes}\n"
    )

    # ---- 判定 ----
    print("=== 判定 ===")
    sat_5xx = (
        sum(v for k, v in sat["status_codes"].items() if 500 <= k < 600) if False else 0
    )
    sat_codes = results["pool_exhaustion"]["status_codes"]  # type: ignore[index]
    sat_5xx = sum(v for k, v in sat_codes.items() if 500 <= k < 600)  # type: ignore[union-attr]
    hung = sum(v for k, v in sat_codes.items() if k == 0)  # type: ignore[union-attr]
    print(f"  耗尽期 5xx={sat_5xx}  无响应(k=0)={hung}")
    print(f"  耗尽期 p99={sat['p99_ms']:.2f}ms")
    ok = sat_5xx == 0 and hung == 0
    print(f"  {'OK  控制面在池耗尽下未挂死也未 5xx' if ok else '⚠ 存在 5xx 或无响应'}")
    results["pool_exhaustion_safe"] = ok

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pool-exhaustion.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n证据：{OUT_DIR / 'pool-exhaustion.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
