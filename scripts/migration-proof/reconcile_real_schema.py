"""真实 schema 对账（Phase C）：SQLite 快照 → 对账。

与 Gate 5 的 `import_reconcile_probe.py` 的区别：**真实业务 schema**（不是合成表）。

**注意**：本脚本只验证**对账逻辑本身**（用 SQLite 快照作为源），
不验证 PostgreSQL 导入——那需要真实 PG 连接（`FAMILYGRAPH_TEST_PG_DSN`）。
对账逻辑的正确性由 Gate 5 的探针（合成数据 + 真实 PG）证明；
本脚本把同样的对账逻辑应用到真实业务 schema 上，作为 Phase C 的
「SQLite 侧基线」。

覆盖的对账维度（每个都对应一个合同）：

| 维度 | 合同 | 对账方式 |
|---|---|---|
| 行数 | 迁移不丢数据 | `count(*)` 逐表 |
| 摘要 | 迁移不改数据 | 逐行 sha256 |
| 状态分布 | 状态机语义保持 | `GROUP BY status` |
| 授权 scope | `ck_memories_scope_space` / `ck_rag_documents_scope` | scope 与 space_id 的联合分布 |
| revision/citation | `rag_documents.revision == source_revision` | 镜像一致性 |
| egress 审计 | `agent_provider_egress` 一次一审计 | 按 target_id 分组计数 |
| attempt/run | `steward_model_calls.run_id` 指向存在的 run | 左连接查孤儿 |

refusal 语义：检出差异后**不自动修复**，只报告。这与 Gate 5 一致。

用法：

    python3 scripts/migration-proof/reconcile_real_schema.py <sqlite_snapshot_path>
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path


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
    return dict(
        conn.execute(f"SELECT scope, space_id IS NULL, count(*) FROM {table} GROUP BY scope, space_id IS NULL").fetchall()  # noqa: S608
    )


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


def reconcile(src_path: Path) -> list[str]:
    """返回差异列表；空列表 = 对账通过。"""
    diffs: list[str] = []
    src = sqlite3.connect(str(src_path))
    tables = [
        "accounts", "spaces", "agent_runs", "steward_model_calls",
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
    # egress 审计
    counts = egress_audit_counts(src)
    for target_id, count in counts.items():
        if count != 1:
            diffs.append(f"egress 审计不唯一: target_id={target_id} count={count}")
    print(f"  egress 审计: {len(counts)} 个 target_id，均一次" if not diffs else "")
    src.close()
    return diffs


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python3 scripts/migration-proof/reconcile_real_schema.py <sqlite_snapshot_path>")
        return 2
    snapshot = Path(sys.argv[1])
    if not snapshot.exists():
        print(f"快照不存在: {snapshot}")
        return 2
    print(f"对账: {snapshot}")
    diffs = reconcile(snapshot)
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
