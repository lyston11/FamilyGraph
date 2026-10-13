"""后台维护（RAG/embedding）预算对控制面的影响（真实开发环境，只读）。

## 要回答的问题

`10-03-pgvector-rag` 的 AC：

> 测量查询 p50/p95/p99、更新延迟、索引构建、空间隔离和 **background budget 对
> control-plane 的影响**。

`maintenance.py` 的向量索引段自述：

> 有界批数：`max_batches=1` 使单次 tick 最多处理一批，控制权定期回到维护循环，
> 且不会长时间占用那 1 核 embedding 预算。

本探针在**运行中的开发服务**上验证这个说法：服务每 5 秒跑一次维护 tick（含向量索引），
因此持续采样控制面延迟，观察**维护 tick 是否在延迟分布里形成可见的尖峰**。

## 方法

1. 以固定间隔（默认 50ms）持续采样 `GET /api/ready` 若干秒，得到高密度时间序列；
2. 同时读取 embedding 服务的 `/readyz`，拿到 `gate` 的活跃/排队计数；
3. 把延迟序列按「是否与 embedding 活动重叠」分组比较，并报告 p99 与最大值；
4. **反证**：若把 embedding 服务停掉再采样，尖峰应当消失——但那需要重启服务，
   属停止条件，故只记录为未做。

## 只读保证

只发 `GET /api/ready` 与 `GET <embedding>/readyz`。不写业务数据、不改配置、不重启服务。
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
READY_PATH = os.environ.get("READY_PATH", "/api/ready")
EMBEDDING_URL = os.environ.get("EMBEDDING_BASE_URL", "")

DURATION_SECONDS = float(os.environ.get("BUDGET_DURATION", "60"))
INTERVAL_SECONDS = float(os.environ.get("BUDGET_INTERVAL", "0.05"))


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _timed_get(url: str, timeout: float = 10.0) -> tuple[int, float, bytes]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        code = exc.code
    except Exception:  # noqa: BLE001
        return 0, (time.perf_counter() - started) * 1000, b""
    return code, (time.perf_counter() - started) * 1000, body


def _embedding_gate() -> dict[str, object] | None:
    if not EMBEDDING_URL:
        return None
    code, _ms, body = _timed_get(EMBEDDING_URL.rstrip("/") + "/readyz")
    if code != 200 or not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    gate = payload.get("gate")
    return gate if isinstance(gate, dict) else None


def main() -> int:
    code, ms, _ = _timed_get(BASE_URL + READY_PATH)
    if code == 0:
        print("SKIP: 目标不可达——先确认开发服务在跑")
        return 2
    print(f"目标：{BASE_URL}{READY_PATH}；首探 HTTP {code} {ms:.1f}ms")
    print(f"embedding：{EMBEDDING_URL or '(未配置，跳过 gate 采样)'}")
    print(f"采样 {DURATION_SECONDS:.0f}s，间隔 {INTERVAL_SECONDS * 1000:.0f}ms\n")

    samples: list[dict[str, object]] = []
    started = time.perf_counter()
    busy_ticks = 0
    while time.perf_counter() - started < DURATION_SECONDS:
        code, ms, _ = _timed_get(BASE_URL + READY_PATH)
        gate = _embedding_gate()
        active = int(gate.get("active", 0)) if gate else 0
        waiting = int(gate.get("waiting", 0)) if gate else 0
        if active > 0 or waiting > 0:
            busy_ticks += 1
        samples.append(
            {
                "t": round(time.perf_counter() - started, 3),
                "code": code,
                "ms": ms,
                "emb_active": active,
                "emb_waiting": waiting,
            }
        )
        time.sleep(INTERVAL_SECONDS)

    lat_all = [float(s["ms"]) for s in samples]
    busy = [
        float(s["ms"])
        for s in samples
        if int(s["emb_active"]) > 0 or int(s["emb_waiting"]) > 0
    ]
    idle = [
        float(s["ms"])
        for s in samples
        if not (int(s["emb_active"]) > 0 or int(s["emb_waiting"]) > 0)
    ]
    codes: dict[int, int] = {}
    for s in samples:
        codes[int(s["code"])] = codes.get(int(s["code"]), 0) + 1

    def summary(values: list[float]) -> dict[str, float]:
        if not values:
            return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
        return {
            "n": len(values),
            "p50_ms": round(_percentile(values, 0.50), 2),
            "p95_ms": round(_percentile(values, 0.95), 2),
            "p99_ms": round(_percentile(values, 0.99), 2),
            "max_ms": round(max(values), 2),
            "mean_ms": round(statistics.fmean(values), 2),
        }

    all_s = summary(lat_all)
    print("=== 全部采样 ===")
    print(
        f"  n={all_s['n']}  p50={all_s['p50_ms']:.2f}ms  p95={all_s['p95_ms']:.2f}ms  "
        f"p99={all_s['p99_ms']:.2f}ms  max={all_s['max_ms']:.2f}ms  {codes}"
    )

    results: dict[str, object] = {
        "base_url": BASE_URL,
        "duration_seconds": DURATION_SECONDS,
        "interval_seconds": INTERVAL_SECONDS,
        "all": all_s,
        "status_codes": codes,
        "embedding_busy_samples": busy_ticks,
    }

    if EMBEDDING_URL:
        print("\n=== 按 embedding 活动分组 ===")
        print(f"  embedding 忙的采样数：{busy_ticks}/{len(samples)}")
        if busy:
            b = summary(busy)
            print(
                f"  忙：n={b['n']}  p50={b['p50_ms']:.2f}ms  p95={b['p95_ms']:.2f}ms  "
                f"p99={b['p99_ms']:.2f}ms  max={b['max_ms']:.2f}ms"
            )
            results["embedding_busy"] = b
        if idle:
            i = summary(idle)
            print(
                f"  闲：n={i['n']}  p50={i['p50_ms']:.2f}ms  p95={i['p95_ms']:.2f}ms  "
                f"p99={i['p99_ms']:.2f}ms  max={i['max_ms']:.2f}ms"
            )
            results["embedding_idle"] = i

    # ---- 判定 ----
    print("\n=== 判定 ===")
    bounded = all_s["p99_ms"] < 1000.0
    no_5xx = all(c < 500 for c in codes if c != 0)
    print(f"  控制面 p99={all_s['p99_ms']:.2f}ms  {'有界' if bounded else '⚠ 超过 1s'}")
    print(f"  无 5xx：{'是' if no_5xx else '否'}  {codes}")
    if EMBEDDING_URL and busy and idle:
        overhead = summary(busy)["p95_ms"] - summary(idle)["p95_ms"]
        print(f"  embedding 活动期的 p95 增量：{overhead:+.2f}ms")
        results["p95_overhead_ms"] = round(overhead, 2)
    results["bounded_p99"] = bounded
    results["no_5xx"] = no_5xx

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "background-budget-impact.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n证据：{OUT_DIR / 'background-budget-impact.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
