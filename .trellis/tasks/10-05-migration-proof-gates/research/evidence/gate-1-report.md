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

## 关键阻塞：本 worktree 未包含已修复的方言代码

先前修复（partial-index 双方言 helper、JSON CHECK 方言渲染、运行期 JSON 查询可移植化）位于
**未合并**的 `feat/10-03-postgres-migration` 分支（6 个提交，最新 `79c24479`）。本任务 worktree 基于 `main`，
因此扫描到的是**未修复**状态：`partial_unique_index(` 调用为 0，裸 `json_extract` 为 13 处。

这是任务拓扑问题，不是代码问题。在 Gate 1 视为通过前必须明确：

1. 迁移证明审计的基线是 `main` 还是含修复的迁移分支；
2. 若审计 `main`，则先前修复必须视为 Gate 1 的**待执行工作**，而不是已完成项；
3. 若审计修复分支，则本任务 worktree 必须 rebase/merge 该分支，且需重新扫描。

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
