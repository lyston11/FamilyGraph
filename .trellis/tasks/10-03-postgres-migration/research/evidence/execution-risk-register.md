# 执行风险登记：迁移任务反复引入缺陷（2026-10-05）

## 根因判断

问题不是“测试数量不够”，而是执行顺序错误：在合同、锁序、方言矩阵、跨任务接缝和回滚门冻结前就开始改代码；测试主要运行在 SQLite 或局部探针，导致 PostgreSQL 才暴露的错误被发现得太晚。

## 已暴露缺陷

| ID | 缺陷 | 原因 | 需要的前置证明 |
|---|---|---|---|
| RG-01 | SQLite partial unique index 在 PostgreSQL 丢失谓词 | 只看 SQLAlchemy 模型，没有双方言 DDL 比较 | 每个 index 的 dialect render + 语义 mutation |
| RG-02 | `json_extract` 建表 CHECK 不可移植 | 把 SQLite 函数当作通用 SQL | SQLite/PG 类型、NULL、JSON boundary matrix |
| RG-03 | `json_extract` 运行期查询到 PG 才失败 | 只验证 migration build，不执行查询 | 每条 raw SQL 在两种数据库真实执行 |
| RG-04 | `BEGIN IMMEDIATE` 改行锁后发生死锁 | SQLite 全库写锁掩盖了锁序 | 调用图、全局锁序、双连接 deadlock probe |
| RG-05 | counter 与 status 归还语义容易泄漏 | status 赋值位置多，未按“进入/离开容量集合”建模 | 状态转换表 + enter/leave mutation |
| RG-06 | 单探针被误当成多租户/恢复证明 | 证据等级没有分层 | L0-L4 evidence policy |
| RG-07 | 业务迁移与运维/控制面/RAG 设计混在一起 | 子任务边界过宽 | cross-task contract 和 dependency gate |

## 新执行规则

1. 先规划，再探针，再更新设计，最后实现；探针发现新语义时必须回到设计，不得立即扩大代码改动。
2. 每个切片必须有正向、负向、mutation、故障恢复和回滚证据。
3. SQLite 通过只证明 SQLite；PostgreSQL 多连接证据必须单独取得。
4. 失败不得通过删除断言、放宽阈值、切换环境或手工改数据消除。
5. 代码、证据、任务 worktree 必须同一分支；不允许证据写 main、代码写 task worktree 的分叉状态。
6. 任何未决设计不得进入 `in_progress` 实现；状态恢复为 planning 或建立阻塞子任务。

## 当前阻塞

- `10-05-migration-proof-gates` 未完成前，`postgres-migration` 不得继续 Phase B 业务实现。
- control-plane、provider、operations、lexical-search、load-acceptance 子任务必须先完成各自设计门，不能靠 postgres-migration 临时补齐。
- 迁移 writer 切换、开发部署和线上操作均禁止。
