# Gate 5：静态快照 → 导入 → 拒绝式对账

## 实测结果

`research/tools/import_reconcile_probe.py`，隔离 PostgreSQL 16 + 合成 SQLite 库：

```
快照 integrity_check = ok
[OK ] 导入后对账       -> 无差异
[OK ] 重复导入对账     -> 无差异（幂等）
[OK ] 制造差异后被检出 -> ['agent_runs: 摘要不一致',
                          "agent_runs.status 分布不一致: {'succeeded':5} != {'failed':1,'succeeded':4}"]
OK   对账未自动修复差异（refusal 语义保持）
PASS
```

## 验证的合同

| 合同 | 实现 | 实测 |
|---|---|---|
| 只用静态快照 | `sqlite3` 的 `Connection.backup()`（与 `app.backup` 同一机制） | integrity_check = ok |
| 不复制运行中主库 | 合成库 + online backup API | 未触碰任何真实库 |
| 保留 ID/FK | 逐表 `INSERT` 原始行 | 对账摘要一致 |
| 行数 + 摘要对账 | `count(*)` + 逐行 sha256 | 检出人为差异 |
| 交叉不变量 | attempt→run 左连接查孤儿 | 无孤儿 |
| 状态分布对账 | 按 status 分组计数 | 检出分布偏移 |
| **refusal** | 检出差异后**不自动修复** | 差异保留，未改写 |
| 幂等 | 重复导入收敛同一结果 | 无差异 |

**refusal 是重点**：对账发现差异后，脚本**没有**自动把 `failed` 改回 `succeeded`，
而是保留差异。这正是 `design.md` 要求的「失败即拒绝，不自动修数据、不切 writer」。

## 覆盖边界（诚实声明）

1. **合成数据，不是真实开发库快照**。真实迁移需要：真实 schema（88 表）、真实
   sequence 修复、真实 RAG revision/citation 对账、真实 egress 一次一审计核对。
2. **sequence 未真正验证**：合成表的 PK 是 plain `int`，不是 `serial`/`IDENTITY`，
   因此 `setval` 路径没有被执行。真实迁移必须验证导入后新插入不撞已有 ID。
3. **未覆盖**：备份/恢复演练（`pg_dump`/`pg_restore`）、RPO/RTO、跨表 FK 顺序导入、
   大表分批与断点重试。
4. **未覆盖**：`PRAGMA integrity_check` 只证明 SQLite 快照自洽，不证明与主库的业务一致。

## 证据等级

**L2/L3（原型）**：真实 PostgreSQL + 真实 online backup 机制 + 真实对账逻辑，
但数据是合成的、schema 是最小子集。**不是**真实历史库的迁移演练。
