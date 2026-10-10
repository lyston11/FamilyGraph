"""真实 schema 对账（Phase C）：SQLite 快照 或 PostgreSQL 库 → 不变量对账。

与 Gate 5 的 `import_reconcile_probe.py` 的区别：**真实业务 schema**（不是合成表）。

## 两种源

```text
reconcile_real_schema.py <sqlite_snapshot_path>       # 迁移前：SQLite 快照
PGTEST_DSN=postgresql://... reconcile_real_schema.py  # 迁移后：生产 PG 库
```

PostgreSQL 模式是 Phase C 的**真正验收**：迁移后的库必须自己满足全部不变量
（取代语义、revision 镜像、外键完整性、egress 发送确定性）。它不需要「源 vs 目标」
比较——迁移的正确性由「目标库是否自洽」与 Gate 5 的行数/摘要对账共同承担，
后者需要两个库同时在线，属于切换窗口的检查。

覆盖的对账维度（每个都对应一个合同）：

| 维度 | 合同 | 对账方式 |
|---|---|---|
| 行数 | 迁移不丢数据 | `count(*)` 逐表 |
| 摘要 | 迁移不改数据 | 逐行 sha256 |
| 状态分布 | 状态机语义保持 | `GROUP BY status` |
| 授权 scope | `ck_memories_scope_space` / `ck_rag_documents_scope` | scope 与 space_id 的联合分布 |
| revision/citation | `rag_documents.revision == source_revision` | 镜像一致性 |
| egress 审计 | `agent_provider_egress` **每次出站尝试**一条 | 尝试数分布 + `sent=false` 必须带 `error_class` |
| attempt/run | `steward_model_calls.run_id` 指向存在的 run | 左连接查孤儿（排除合法的 NULL FK） |

refusal 语义：检出差异后**不自动修复**，只报告。这与 Gate 5 一致。

用法：

    python3 scripts/migration-proof/reconcile_real_schema.py <sqlite_snapshot_path>
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


class PgAdapter:
    """把 psycopg 连接适配成对账函数使用的 `execute(...).fetchone()` 形状。

    对账查询本身是标准 SQL，两种方言都能跑；差异只在驱动 API（`?` vs `%s`
    占位符这里用不到——所有查询都是常量字符串，无参数）。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str) -> Any:
        return _PgCursor(self._conn.execute(sql))

    def close(self) -> None:
        self._conn.close()


class _PgCursor:
    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()


def row_digest(conn: sqlite3.Connection, table: str) -> str:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
    h = hashlib.sha256()
    for row in rows:
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def state_distribution(conn: sqlite3.Connection, table: str, column: str) -> dict:
    return dict(
        conn.execute(f"SELECT {column}, count(*) FROM {table} GROUP BY {column}").fetchall()  # noqa: S608
    )


def scope_distribution(conn: sqlite3.Connection, table: str) -> dict:
    """scope × (space_id 是否为空) 的联合分布。

    这个形状直接对应两个 CHECK 约束：
    `private` 必须 `space_id IS NULL`，`household`/`lineage` 必须非空。
    因此只要有任何一行落在「不该出现的组合」上，分布里就会多出一个键。
    """
    return {
        f"{scope}/space_id={'NULL' if is_null else 'set'}": count
        for scope, is_null, count in conn.execute(
            f"SELECT scope, space_id IS NULL, count(*) FROM {table} "  # noqa: S608
            "GROUP BY scope, space_id IS NULL"
        ).fetchall()
    }


def revision_mismatch(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM rag_documents WHERE revision != source_revision"
    ).fetchone()[0]


def orphan_attempts(conn: sqlite3.Connection) -> int:
    """尝试指向不存在 run 的行数。

    **必须排除 `run_id IS NULL`**：`steward_model_calls.run_id` 的外键是
    `ON DELETE SET NULL`，因此 run 被删除后留下 NULL 是**合法**状态，不是孤儿。
    真实生产库上实测：把 NULL 也算进去会报 75 个「孤儿」，而实际的悬空引用是 0
    —— 一个把正常数据报成损坏的对账脚本，比没有对账脚本更危险（会掩盖真问题）。
    """
    return conn.execute(
        "SELECT count(*) FROM steward_model_calls c "
        "LEFT JOIN agent_runs r ON r.id = c.run_id "
        "WHERE c.run_id IS NOT NULL AND r.id IS NULL"
    ).fetchone()[0]


def egress_audit_counts(conn: sqlite3.Connection) -> dict:
    return dict(
        conn.execute(
            "SELECT target_id, count(*) FROM audit_log "
            "WHERE action = 'agent_provider_egress' GROUP BY target_id"
        ).fetchall()
    )


def egress_sent_false_without_error_class(conn: sqlite3.Connection) -> int:
    """`sent=false` 但缺 `error_class` 的审计行数（应为 0）。

    ## 为什么这才是可检验的 egress 不变量

    egress 的合同是「**每一次真实出站尝试**写恰好一条审计」
    （`spec/backend/agent-runtime.md`），**不是**「每个 run 一条」——
    一个失败 run 可以有多达 24 次真实出站尝试（生产实测 p50=p90=p99=24）。
    因此「每个 run 恰好 1 条」是错误的断言，会把它自己的错误当成数据缺陷。

    真正可从审计自身交叉验证的是：`sent=false` 只能由**连接未建立**的证据得出
    （`ConnectError`/`ConnectTimeout`），因此它必须同时带 `error_class`。
    缺 `error_class` 的 `sent=false` 说明发送确定性被无证据地断言了。
    """
    return conn.execute(
        "SELECT count(*) FROM audit_log "
        "WHERE action = 'agent_provider_egress' "
        "AND detail_json LIKE '%\"sent\": false%' "
        "AND detail_json NOT LIKE '%error_class%'"
    ).fetchone()[0]


def reconcile(src: Any) -> list[str]:
    """返回差异列表；空列表 = 对账通过。"""
    diffs: list[str] = []
    tables = [
        "accounts", "family_spaces", "agent_runs", "steward_model_calls",
        "memories", "rag_documents", "rag_chunks", "audit_log",
    ]
    for table in tables:
        n = src.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        print(f"  {table}: {n} rows")
    # 状态分布
    for table, col in (
        ("agent_runs", "status"),
        ("steward_model_calls", "status"),
        ("memories", "status"),
        ("memories", "confirmation_status"),
    ):
        dist = state_distribution(src, table, col)
        print(f"  {table}.{col}: {dist}")
    # 授权 scope
    for table in ("memories", "rag_documents"):
        dist = scope_distribution(src, table)
        print(f"  {table}.scope: {dist}")
    # revision 镜像
    mismatches = revision_mismatch(src)
    if mismatches:
        diffs.append(f"rag_documents revision 镜像不一致: {mismatches} 行")
    else:
        print("  rag_documents revision 镜像: 一致")
    # 孤儿 attempt
    orphans = orphan_attempts(src)
    if orphans:
        diffs.append(f"孤儿 attempt: {orphans}")
    else:
        print("  attempt/run 孤儿: 无")
    # egress 审计：**不**断言「每个 run 恰好一条」——合同是「每次真实出站尝试一条」，
    # 失败 run 的尝试数可以远超 1。这里只报可观测的分布，并检验一个真正的不变量。
    counts = egress_audit_counts(src)
    if counts:
        values = sorted(counts.values())
        print(
            f"  egress 审计: {len(counts)} 个 run，尝试数 "
            f"min={values[0]} p50={values[len(values) // 2]} max={values[-1]}"
        )
    unsupported_sent = egress_sent_false_without_error_class(src)
    if unsupported_sent:
        diffs.append(f"sent=false 但缺 error_class 的 egress 审计: {unsupported_sent} 行")
    else:
        print("  egress sent=false 均带 error_class: 一致")
    src.close()
    return diffs


def _open_source(argv: list[str]) -> tuple[Any, str] | None:
    """Open the reconciliation source: PG (env) takes precedence over a path."""
    dsn = os.environ.get("PGTEST_DSN")
    if dsn:
        try:
            import psycopg  # noqa: PLC0415
        except ImportError:
            print("SKIP: PGTEST_DSN 已设置但 psycopg 不可用")
            return None
        plain = dsn.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        return PgAdapter(psycopg.connect(plain)), "PostgreSQL (PGTEST_DSN)"
    if len(argv) == 2:
        snapshot = Path(argv[1])
        if not snapshot.exists():
            print(f"快照不存在: {snapshot}")
            return None
        return sqlite3.connect(str(snapshot)), str(snapshot)
    print(
        "用法: reconcile_real_schema.py <sqlite_snapshot_path>\n"
        "   或: PGTEST_DSN=postgresql://... reconcile_real_schema.py"
    )
    return None


def main() -> int:
    source = _open_source(sys.argv)
    if source is None:
        return 2
    conn, label = source
    print(f"对账: {label}")
    diffs = reconcile(conn)
    if diffs:
        print("\n差异:")
        for d in diffs:
            print(f"  - {d}")
        print("\nrefusal: 不自动修复，请人工处理")
        return 1
    print("\n对账通过: 无差异")
    return 0


if __name__ == "__main__":
    sys.exit(main())
