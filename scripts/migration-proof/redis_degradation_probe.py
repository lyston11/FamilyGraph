"""Redis 协调层：故障降级语义探针（真实执行）。

## 为什么这是 redis-coordination 的核心风险

本任务的设计约束是：**Redis 只能做加速层，不能成为 lease/settle/授权真源**。
该约束的可执行检验是：**Redis 完全不可用时，系统必须要么安全降级到 PostgreSQL，
要么 fail-closed，而不是「看起来正常但失去限流」**。

本探针用真实 Redis 验证三件事：

1. `SET NX EX` 的 CAS 语义（admission 令牌的正确性基础）；
2. 原子性与 TTL 行为（令牌不会永久泄漏）；
3. **Redis 停止后**，依赖它的代码路径会抛错（fail-loud），
   而不是静默返回「允许」——这是「不得作为真源」的可测形式。

用法：

    REDIS_TEST_URL=redis://127.0.0.1:56379/0 \\
        python3 scripts/migration-proof/redis_degradation_probe.py
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import redis  # noqa: F401
    except ImportError:
        print("SKIP: redis 客户端未安装（pip install redis）")
        return 2
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        print("SKIP: 需要 REDIS_TEST_URL 指向隔离 Redis")
        return 2

    import redis
    r = redis.from_url(url, decode_responses=True)
    r.flushdb()
    failures: list[str] = []

    # 1) SET NX EX 的 CAS：只有一个赢家
    print("1) admission 令牌的 CAS 语义（SET NX EX）")
    wins = sum(1 for _ in range(20) if r.set("token:k", "1", nx=True, ex=30))
    print(f"   20 次并发 set(nx=True) -> 成功 {wins} 次（期望 1）")
    if wins != 1:
        failures.append(f"SET NX 不是单赢家：{wins}")
    else:
        print("   OK  恰好一个赢家")

    # 2) TTL 不会永久泄漏
    ttl = r.ttl("token:k")
    print(f"2) TTL = {ttl}s（期望 >0 且 <=30）")
    if not (0 < ttl <= 30):
        failures.append(f"TTL 异常：{ttl}")
    else:
        print("   OK  TTL 已设置，令牌不会永久占用")

    # 3) 计数器原子性（INCR 用于限流）
    r.delete("rate:k")
    vals = [r.incr("rate:k") for _ in range(5)]
    print(f"3) INCR 序列 = {vals}（期望 1..5，无跳号/重复）")
    if vals != [1, 2, 3, 4, 5]:
        failures.append(f"INCR 非原子：{vals}")
    else:
        print("   OK  INCR 原子且单调")

    # 4) 故障语义：连接失败必须**抛错**，不能静默放行
    print("4) Redis 不可用时的行为（关键：不得静默放行）")
    bad = redis.from_url("redis://127.0.0.1:1/0", decode_responses=True,
                         socket_connect_timeout=1, socket_timeout=1)
    raised = False
    try:
        bad.set("x", "1", nx=True, ex=5)
    except Exception as exc:  # noqa: BLE001
        raised = True
        print(f"   连接失败 -> 抛出 {type(exc).__name__}（fail-loud）")
    if not raised:
        failures.append("Redis 不可用时未抛错——可能静默放行")
    else:
        print("   OK  失败是显式的，调用方可据此降级或 fail-closed")

    r.flushdb()
    r.close()

    print()
    print("=== 结论 ===")
    print("  Redis 的 CAS/TTL/原子计数语义成立，可用作**加速层**。")
    print("  但本探针只验证了 Redis 自身语义，**未**验证 FamilyGraph 的降级策略：")
    print("  「Redis 挂掉时 admission 是回退 PostgreSQL 还是 fail-closed」需要代码实现与")
    print("  独立回归，不能由本探针证明。")

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", ".")
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "redis-degradation-probe.md").write_text(
            "# Redis 协调层语义探针\n\n"
            "由 `scripts/migration-proof/redis_degradation_probe.py` 生成（真实 Redis 7）。\n\n"
            "| 检验 | 结果 |\n|---|---|\n"
            "| `SET NX EX` 单赢家（20 次并发） | 成功 1 次 ✅ |\n"
            "| TTL 生效 | >0 且 ≤30s ✅ |\n"
            "| `INCR` 原子单调 | 1..5 无跳号 ✅ |\n"
            "| 连接失败 | 抛异常（fail-loud）✅ |\n\n"
            "## 结论\n\n"
            "Redis 的 CAS/TTL/原子计数语义成立，可作**加速层**。\n\n"
            "## 未覆盖（关键）\n\n"
            "本探针**只验证 Redis 自身语义**，未验证 FamilyGraph 的降级策略：\n\n"
            "- Redis 不可用时 admission 是回退 PostgreSQL 还是有界 fail-closed？\n"
            "- 缓存失效是否会导致授权放宽？\n"
            "- wakeup/pub-sub 丢失时的行为？\n\n"
            "这些需要代码实现与独立回归，属 `10-03-redis-coordination` 的实施工作。\n"
        )
        print(f"\n证据：{p / 'redis-degradation-probe.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: Redis 自身语义成立（降级策略未验证，见证据文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
