# Gate 1：Schema / SQL / dialect inventory（真实扫描结果）

基线：`feat/10-05-migration-proof-gates` @ `168f3ef3`（任务 worktree，基于 main）

## 自动扫描统计（L0，由脚本现场生成）

| 项 | 值 |
|---|---|
| app Python 文件 | 211 |
| model Python 文件 | 32 |
| migration 文件 | 59 |
| 模型表 | 88 |
| 列/关系声明 | 979 |
| Index 声明 | 128 |
| CHECK/FK/Unique/PK 声明 | 107 |
| `sqlite_where` | 16 |
| `postgresql_where` | 0 |
| 裸 `json_extract` | 13 |
| 事务候选调用点 | 46 |

## 与先前假设的差异（必须解释，不得沿用旧数字）

- 事务调用点：先前记录为 18，随后改为 43，本次真实扫描为 **46**。差异来自统计口径（是否包含 docstring、是否包含 helper 定义本身）。**以扫描 ID 清单为准**，数量只是索引。
- `sqlite_where`：**16** 处，`postgresql_where`：**0** 处。
- 裸 `json_extract`：**13** 处，分布在 `models/memory.py`（CHECK 表达式）与 3 个运行期查询文件。

## 已解决的拓扑阻塞：审计基线

先前修复（partial-index 双方言 helper、JSON CHECK 方言渲染、运行期 JSON 查询可移植化）位于
**未合并**的 `feat/10-03-postgres-migration`。本任务 worktree 原先基于 `main`，因此扫描到的是
**未修复**代码（16 个 `sqlite_where` / 0 个 `postgresql_where`）。

**已通过 merge 解决**（提交 `3bf4a069`）：证明门必须审计「实际会发布的代码」，而不是修复前的基线。
merge 后重新扫描结果：

| 项 | merge 前（main 基线） | merge 后（候选代码） |
|---|---|---|
| `sqlite_where` | 16 | **4** |
| `postgresql_where` | 0 | **4** |
| 裸 `json_extract`（未分类） | 13 | 见 `dialect-blockers.md` |

`4/4` 说明 helper 生效：模型层不再有单方言谓词。`sqlite_where` 剩余的 4 处全部在
`app/models/indexes.py` 的 helper 内部，是刻意保留的双方言渲染。

## SQLite 专属构造的真实命中

初版扫描把注释与文档字符串中的 `json_extract` 计入，得到 16 处虚假命中。
`dialect-blockers.md` 用 AST 分类后，真实会影响执行的位置是：

- **模型层**：`app/models/memory.py` 的 CHECK 已改为经 `DialectCheck` 双方言渲染（已修复）；
- **历史迁移**：`0042_memory_source_contract.py`（裸 `json_extract` CHECK）、
  `0022_system_admin_space_manager.py`（`last_insert_rowid()`）、
  `0014_memory_rag.py`（`CREATE VIRTUAL TABLE ... fts5`）——**这三条会阻止在 PostgreSQL 上重放历史 Alembic**。

这与 `solution-decision.md` 的结论一致（不重放历史 Alembic，改用审查后的 PG baseline），
但 Gate 1 必须显式记录：**ORM metadata 可建表 ≠ 历史迁移可重放**。

## 证据等级

本文件全部为 **L0**（源码结构扫描）。没有任何条目升级为 L2/L3：

- DDL render 未逐条比较双方言；
- raw SQL 未在真实 PostgreSQL 执行；
- 约束/索引语义未做 boundary value 验证。

## 下一步（Gate 1 未通过）

- 逐条比较每个 Index/CheckConstraint 的 SQLite 与 PostgreSQL render；
- 在隔离 PostgreSQL 真实执行每条 raw SQL；
- 记录 NULL/JSON/时间/排序/错误码差异并给出保留或适配结论；
- 先解决上述基线归属问题。


## 闭合状态（后续更新）

Gate 1 的门禁要求已闭合，详见 `gate-1-closure.md`：

- inventory 完整（88 表 / 979 列 / 112 索引 / 105 约束 / 14 位点→69 实际触发器）
- 方言语义矩阵 13 项：11 一致、2 差异均判「保留」
- PRAGMA 7 种 31 处、SQLite DDL 23 处：**23/23 被 PostgreSQL 拒绝**，已穷举
- 剩余 4 项风险属实现期工作（逐表触发器语义、列级触发器等价写法等）
