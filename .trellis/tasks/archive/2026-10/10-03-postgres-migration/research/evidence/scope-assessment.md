# PostgreSQL 迁移范围评估：为什么不能一次性完成（2026-10-04）

## 结论

`10-03-postgres-migration` 的完整范围（schema + 事务 + 租约 + 导入 + 灰度 + 回滚）
**不可能在一次会话内安全完成**，且其中至少一项（RAG 检索）需要独立的产品决策。
本文件说明依据，并把范围切成可独立验收的部分。

## 依据

### 1. 18 处 check-then-act 需要逐处重新论证

`BEGIN IMMEDIATE` 在 SQLite 上提供**免费串行化**（单写者）。PostgreSQL READ COMMITTED
下没有这个性质，因此这 18 处（12 个文件）每一处都必须回答：唯一索引兜底，还是需要
显式行锁/advisory lock？

`database-guidelines.md` 自己记录过这个判断：「不能因为 CAS 单独看是安全的就省掉写锁；
写锁守护的是读阶段窗口，第三方并发者（如并发移除成员资格）只能靠它挡住。」
逐一重新论证是**必须的**，且每处都需要一个「去掉锁即失败」的并发回归。

### 2. 租约语义不是机械替换（已实测证明）

朴素 `SKIP LOCKED` 移植会**静默**违反每租户并发上限（实测 5 个 worker 全领同一租户）。
正确形态需要 advisory lock，见 `compatibility-inventory.md`。这还只是**领取**；
续期、回收、`recover_stuck_attempts` 的三种崩溃点都需各自验证。

### 3. RAG 检索需要独立决策（阻塞项）

FTS5 `tokenize='trigram'` 在 PostgreSQL 无对等物，且选它的原因是为 CJK 服务。
替换方案（pg_trgm / pgroonga / 分词器）会改变检索质量，需要**对照基准**，
不能夹在 schema 迁移里悄悄替换。

### 4. 导入与对账是独立工作量

- 领域表：users / accounts / spaces / members / relations / memories / RAG 文档与片段
- Agent 表：runs / events / attempts / plans / model_calls / audits
- 需要行数、摘要、状态、授权 scope、revision/citation 的对账
- 需要 refusal guard（`database-guidelines.md`：refusal guards 必须在任何 DDL 之前）
- 需要备份/恢复演练与回滚点

### 5. 连接池与事务边界要重做

现有 `POOL_SIZE=5 / MAX_OVERFLOW=10 / POOL_TIMEOUT=30` 是为「SQLite 单写者 + 15 条上限」
调的。PostgreSQL 下这个数字没有意义（并发能力不同），需要按 control/execution/background
重新设计，并重新验证控制面余量（10-03 的准入上界 `POOL_MAX_CONNECTIONS - 1` 也依赖它）。

## 建议的拆分

| 子任务 | 内容 | 依赖 |
|---|---|---|
| **P1 schema + 事务语义** | Alembic 迁移到 PostgreSQL 方言；18 处 check-then-act 逐处重新论证 + 并发回归；约束/索引/FK 对等 | 无 |
| **P2 租约与恢复** | advisory lock 租约、续期、回收、三个崩溃点；对照 E1 合同验证 | P1 |
| **P3 导入与对账** | 隔离快照导出、导入、对账、refusal guard、备份恢复演练 | P1 |
| **P4 RAG 检索方案** | pg_trgm vs pgroonga vs 分词器；CJK 检索质量对照基准 | 独立 |
| **P5 连接池与灰度** | control/execution/background 池；shadow read；受控 dual-write；writer 切换 | P1–P3 |

本会话完成 **P1 的盘点与原型**（已交付证据），并把上述拆分写入任务工件；
不在没有逐处并发回归的情况下声称「已迁移」。
