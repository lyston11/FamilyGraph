"""Provider 连接生命周期基准：每请求新建 client vs 按上游池化复用。

## 这个探针决定什么

它给出「是否值得把 provider 代理改成持久 client」的**唯一依据**：实测成本差。
把成本测出来再决定，而不是照搬「连接池是性能最佳实践」。

## 测的是连接建立成本，不是请求处理时间

用真实 HTTPS 端点、无凭据的轻量路径，只测 TCP + TLS 握手成本。因此不需要
provider 凭据，也不发送任何业务内容。

## 结论（2026-10-10，真实 HTTPS 端点）

```text
per-request client   p50 = 60.9ms   p95 = 133.4ms
pooled client        p50 = 10.4ms   p95 =  12.3ms
中位差               = 50.5ms/请求
```

对一次多轮工具调用（可达十余次出站）来说这是数百毫秒的用户可见延迟，因此
实现池化是值得的。这与 `10-04-provider-reliability-boundaries` 留档的 49.5ms
一致，本探针把它变成可复跑的数字。

## 池化的三个风险（实现里已逐条处理）

1. **池被关闭** → 请求路径里不得有 `client.aclose()`（静态断言锁定）；
2. **跨上游复用** → 按 `base_url` 分池，不是单一全局 client；
3. **凭据入池** → `Authorization` 每请求构造，不做 client 默认头。

## 用法

    python scripts/migration-proof/client_lifecycle_probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))

#: 探测目标。默认用一个稳定的公共 HTTPS 端点；可用 `CLIENT_PROBE_URL` 覆盖。
#: 只做 GET，不发送任何内容。
DEFAULT_URL = "https://api.liu-dada.com/v1"
SAMPLES = int(os.environ.get("CLIENT_PROBE_SAMPLES", "12"))


async def _per_request(url: str) -> list[float]:
    import httpx

    times: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.get(url)
            except Exception:  # noqa: BLE001 - 状态码/可达性不影响握手成本测量
                pass
        times.append((time.perf_counter() - started) * 1000)
    return times


async def _pooled(url: str) -> list[float]:
    import httpx

    times: list[float] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 预热：第一次必然要建连，不计入（池化的意义就在于后续请求不再建连）。
        try:
            await client.get(url)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(SAMPLES):
            started = time.perf_counter()
            try:
                await client.get(url)
            except Exception:  # noqa: BLE001
                pass
            times.append((time.perf_counter() - started) * 1000)
    return times


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "min": ordered[0],
        "max": ordered[-1],
    }


def main() -> int:
    url = os.environ.get("CLIENT_PROBE_URL", DEFAULT_URL)
    try:
        per_request = asyncio.run(_per_request(url))
        pooled = asyncio.run(_pooled(url))
    except Exception as exc:  # noqa: BLE001
        # 网络不可达是**环境阻塞**，不是通过。
        print(f"SKIP: 无法完成基准（{type(exc).__name__}）——需要可访问的 HTTPS 端点")
        return 2

    per_request_stats = _summary(per_request)
    pooled_stats = _summary(pooled)
    saving = per_request_stats["p50"] - pooled_stats["p50"]
    print(f"目标 {url}（{SAMPLES} 次采样）")
    print(
        f"  per-request client   p50={per_request_stats['p50']:.1f}ms"
        f" p95={per_request_stats['p95']:.1f}ms"
    )
    print(
        f"  pooled client        p50={pooled_stats['p50']:.1f}ms"
        f" p95={pooled_stats['p95']:.1f}ms"
    )
    print(f"  中位差               = {saving:.1f}ms/请求")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "client-lifecycle.json").write_text(
        json.dumps(
            {
                "url": url,
                "samples": SAMPLES,
                "per_request": per_request_stats,
                "pooled": pooled_stats,
                "saving_ms_p50": saving,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    failures: list[str] = []
    if saving <= 0:
        failures.append(
            f"池化未带来收益（中位差 {saving:.1f}ms）——池化的实现理由不成立，"
            "应重新评估而不是保留复杂度"
        )
    # 池化后第一次（预热）之外的请求应当不再付出握手成本：p95 必须显著低于
    # per-request 的 p50，否则说明连接实际没有被复用。
    if pooled_stats["p95"] >= per_request_stats["p50"]:
        failures.append(
            f"池化的 p95（{pooled_stats['p95']:.1f}ms）不低于 per-request 的 p50"
            f"（{per_request_stats['p50']:.1f}ms）——连接可能没有被真正复用"
        )
    else:
        print(
            f"  OK  池化 p95（{pooled_stats['p95']:.1f}ms）显著低于 "
            f"per-request p50（{per_request_stats['p50']:.1f}ms），连接确实被复用"
        )

    if failures:
        print("\nFAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS: 池化收益已量化，且连接确实被复用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
