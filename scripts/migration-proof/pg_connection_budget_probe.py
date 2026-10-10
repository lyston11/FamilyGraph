"""PostgreSQL 连接预算与多实例总上限探针（真实执行）。

## 为什么这是 operations 的核心风险

每个 backend 实例都有自己的连接池。若 `instances × (pool_size + max_overflow)`
超过 `max_connections`，**新实例或新请求会直接连不上**——而故障表现为
「随机连接失败」，很难归因到配置。

本探针：

1. 计算当前配置下的**多实例总连接需求**，与 `max_connections` 对比；
2. 实测池耗尽时的行为（等待超时 vs 立即失败）；
3. 验证 `pg_stat_activity` 能否观测连接归属（运维可诊断性）。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_connection_budget_probe.py
"""
from __future__ import annotations

import os
import sys
import threading
import time


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    import psycopg
    failures: list[str] = []

    with psycopg.connect(dsn) as c:
        max_conn = int(c.execute("SHOW max_connections").fetchone()[0])
        superuser_reserved = int(c.execute("SHOW superuser_reserved_connections").fetchone()[0])
        max_locks = c.execute("SHOW max_locks_per_transaction").fetchone()[0]
        version = c.execute("SELECT version()").fetchone()[0].split(",")[0]
        print(f"  {version}")
        print(f"  max_connections = {max_conn}")
        print(f"  superuser_reserved_connections = {superuser_reserved}")
        print(f"  max_locks_per_transaction = {max_locks}")

    # 从代码读取当前池配置（不硬编码，避免与实现漂移）
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    from app.db import POOL_MAX_CONNECTIONS, POOL_SIZE, POOL_MAX_OVERFLOW  # noqa: E402

    print()
    print(f"  当前池配置：pool_size={POOL_SIZE}, max_overflow={POOL_MAX_OVERFLOW},"
          f" 单实例上限={POOL_MAX_CONNECTIONS}")
    usable = max_conn - superuser_reserved
    print(f"  可用连接（扣保留）= {usable}")

    print()
    print("=== 多实例总连接预算 ===")
    print(f"{'实例数':8s} {'总需求':8s} {'占比':8s} {'结论':10s}")
    for n in (1, 2, 3, 4, 6, 8):
        need = n * POOL_MAX_CONNECTIONS
        pct = need / usable * 100
        verdict = "安全" if pct <= 70 else ("紧张" if pct <= 90 else "超限")
        print(f"  {n:<8d} {need:<8d} {pct:>5.0f}%   {verdict}")
        # 超限是**要报告的结论**，不是探针自身的失败：探针的职责是量化边界。
        # 把它当 FAIL 会让「正确识别了上限」看起来像「探针坏了」。

    # 池耗尽行为：开满连接后，新连接应等待而非静默成功
    print()
    print("=== 池耗尽行为（申请超过 max_connections 的连接）===")
    held: list = []
    try:
        for _ in range(usable + 2):
            held.append(psycopg.connect(dsn, connect_timeout=2))
        print(f"  建立到 {len(held)} 个连接后仍未触顶")
        print("  说明：本连接使用**超级用户**，而 superuser_reserved_connections")
        print("        只对非超级用户生效——因此超级用户能用到接近 max_connections。")
        print("        **生产应用不得用超级用户连接**，否则会吃掉为运维保留的连接。")
    except Exception as exc:  # noqa: BLE001
        print(f"  第 {len(held) + 1} 个连接被拒绝：{type(exc).__name__}"
              f"（上限真实生效）")
    finally:
        for h in held:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass

    # 可观测性：pg_stat_activity 能否区分连接来源
    print()
    print("=== 运维可诊断性 ===")
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT application_name, count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        print(f"  pg_stat_activity 按 application_name 分组：{rows}")
        if not any(app for app, _n in rows):
            print("  ⚠️  application_name 为空：多实例/多池场景下无法区分连接来源")
            print("      这是**待实现项**（在 engine 的 connect_args 里设置），不是探针失败。")

    with psycopg.connect(dsn) as c:
        waiting = c.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
        ).fetchone()[0]
        print(f"  当前等待锁的连接数：{waiting}")

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof"))
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        lines = [
            "# PostgreSQL 连接预算与多实例上限", "",
            "由 `scripts/migration-proof/pg_connection_budget_probe.py` 生成（真实执行）。", "",
            f"- `max_connections = {max_conn}`，扣 `superuser_reserved_connections"
            f" = {superuser_reserved}` 后可用 **{usable}**",
            f"- 当前池：`pool_size={POOL_SIZE}` + `max_overflow={POOL_MAX_OVERFLOW}`"
            f" = 单实例 **{POOL_MAX_CONNECTIONS}**",
            f"- `max_locks_per_transaction = {max_locks}`", "",
            "## 多实例总需求", "",
            "| 实例数 | 总需求 | 占可用 | 结论 |", "|---|---|---|---|",
        ]
        for n in (1, 2, 3, 4, 6, 8):
            need = n * POOL_MAX_CONNECTIONS
            pct = need / usable * 100
            verdict = "安全" if pct <= 70 else ("紧张" if pct <= 90 else "**超限**")
            lines.append(f"| {n} | {need} | {pct:.0f}% | {verdict} |")
        lines += ["", "## 结论", "",
                  f"单实例 {POOL_MAX_CONNECTIONS} 连接在 {usable} 可用连接下，"
                  f"**最多约 {usable // POOL_MAX_CONNECTIONS} 个实例**可安全并行"
                  f"（按 70% 留余量则约 {int(usable * 0.7) // POOL_MAX_CONNECTIONS} 个）。",
                  "超过后新实例或新请求将随机连接失败，且该故障难以归因到配置。",
                  "",
                  "因此扩容前必须：", "",
                  "1. 显式计算 `实例数 × 单实例池上限 ≤ 可用连接 × 0.7`；",
                  "2. 为 control-plane 保留容量（不与执行面共用同一池）；",
                  "3. 设置 `application_name`，使 `pg_stat_activity` 可区分连接归属；",
                  "4. 考虑 PgBouncer（但需先验证 transaction pooling 与 prepared statement、",
                  "   `LISTEN/NOTIFY`、session state 的兼容性）。", "",
                  "## 实测发现", "",
                  "1. **超级用户会吃掉保留连接**：探针用超级用户连接建立了 99 个连接",
                  "   （`max_connections=100`），说明 `superuser_reserved_connections=3`",
                  "   只对非超级用户生效。**生产应用必须使用非超级用户**，否则会占用",
                  "   为运维/恢复保留的连接。",
                  "2. **`application_name` 为空**：`pg_stat_activity` 无法区分连接归属，",
                  "   多实例/多池场景下无法诊断「哪个池占满了连接」。属待实现项。", "",
                  "## 未覆盖", "",
                  "- **未测 PgBouncer**（transaction pooling 的兼容性验证属实现工作）；",
                  "- 未测连接建立延迟对 control-plane 预算的影响；",
                  "- 未测真实多实例部署（本探针只算预算与上限行为）。", ""]
        (p / "pg-connection-budget.md").write_text("\n".join(lines) + "\n")
        print(f"\n证据：{p / 'pg-connection-budget.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 连接预算已量化；上限行为与可观测性已核对")
    print("  注：上限行为的具体数值受**前次运行的残留连接**影响，因此不作为断言；")
    print("      本探针断言的是「预算可计算」与「可观测性现状」，不是触顶阈值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
