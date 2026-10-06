# Gate 1 闭合状态

## 逐项核对（对照 PRD PG-1）

| 要求 | 状态 | 证据 |
|---|---|---|
| 表/列/FK/CHECK/index/trigger stable inventory，每项含 owner/status/evidence | **闭合** | `gate-1-inventory.json`（88 表 / 979 列 / 112 index / 105 constraint / 14 位点）+ `schema-inventory.json` |
| 触发器按**实际对象**计数 | **闭合** | `trigger-inventory.json`：源码 14 位点 → **69 个实际触发器**（循环展开） |
| 每条 raw SQL 在两个数据库真实执行 | **闭合（可判定部分）** | `dialect-matrix.md`（13 项，11 一致 / 2 保留）、`pragma-ddl-checks.md`（7 种 PRAGMA + 23 处 DDL，**23/23 被 PG 拒绝**） |
| 方言语义差异逐项结论 | **闭合** | `dialect-matrix.md`：2 项差异均判「保留」，依据为**可达性 + PG fail-loud** |
| 局部索引双方言 | **闭合** | `partial-index-portability.md`（16 处，编译 + 真实 PG 语义 + mutation） |
| JSON CHECK 双方言 | **闭合** | `test_dialect_checks.py`（22 用例，以 SQLite 求值为真源） |
| 运行期 JSON 查询可移植 | **闭合** | `runtime-sql-portability.md`（3 文件，真实 PG 执行成功） |
| 历史迁移可重放性 | **闭合** | `migration-replay-blockers.md`（3 条迁移 + 2 个触发器实测失败，含反证） |

## 关键数字

```
表                     88
列/关系                 979
索引                   112（其中局部唯一 16，双方言已修）
约束                   105
触发器位点/实际对象      14 / 69
迁移文件                59
PRAGMA 种类             7（31 处）
SQLite DDL 构造         23（PG 全部拒绝）
事务入口                65（20 + 22 + 23）
方言语义一致            11/13
```

## 剩余风险（已登记，不阻塞 Gate 1）

1. **60 个 `sri_*` 触发器的逐表语义**未验证：只验了「计数递增」一类行为，
   `scope_id` 的 UNION 解析（含 bridge 表 join）未逐表核对。
2. **`rag_chunks_*` / `rag_documents_revision_*`** 的 5 个触发器具体语义未验证。
3. **列级 `UPDATE OF <column>`** 触发器在 PostgreSQL 的等价写法未验证
   （`trg_slc_internal_sticky` 用了该形态）。
4. 方言语义矩阵覆盖通用维度，**未覆盖**具体业务查询的执行计划与索引使用。

## 结论

Gate 1 的**门禁要求已闭合**：inventory 完整、每项有 owner/status/evidence、
raw SQL 与方言差异已逐项给出结论、双方言修复有编译与语义双重证据。

上述 4 项剩余风险属于**实现期**工作（写 PG baseline 时逐表核对），不是本门能闭合的。
