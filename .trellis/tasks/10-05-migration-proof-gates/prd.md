# PostgreSQL 迁移执行前证明与安全门

## Goal

在任何迁移业务代码前建立逐表合同映射、并发证明、方言矩阵、故障注入、对账和回滚门，防止执行阶段反复引入静默缺陷。

## Why this is a separate gate

当前执行已经暴露出多类「本地测试通过、迁移后才失败」的问题：

- SQLite partial index 未声明 `postgresql_where`，在 PostgreSQL 上唯一性范围错误；
- `json_extract` 在建表约束和运行期查询中分别产生不同类型的故障；
- SQLite `BEGIN IMMEDIATE` 隐含全库串行化，换成 PostgreSQL 行锁后出现反向锁序死锁；
- 仅验证单个探针无法证明租约配额、恢复、双写和多实例语义。

这些不是实现阶段偶然 bug，而是缺少执行前证明门导致的流程缺陷。

## Requirements

- 在任何业务代码实现前，建立完整 schema/SQL/事务/约束/索引/触发器 inventory，并为每项分配稳定 ID。
- 逐项映射所有真实事务入口：CAS、父行锁、自然键 advisory lock、持久化 counter 或 bounded `SERIALIZABLE`；不能以 `BEGIN IMMEDIATE` 数量代替语义分析。
- SQLite 与 PostgreSQL 必须做建表、运行期 SQL、NULL、JSON 类型、索引谓词、排序和时间语义矩阵验证。
- 所有关键并发合同必须在真实 PostgreSQL 多连接环境中正向、负向和 mutation 验证。
- 所有故障路径必须定义终态、幂等键、重试边界、恢复动作和回滚动作。
- 证据必须分为源码推断、隔离探针、真实 PostgreSQL、开发灰度四级；低级别证据不得冒充高等级验收。
- 验证失败时 fail-closed：不切 writer、不自动修数据、不创建 SQLite/PG 双主。
- 任务工件必须明确跨任务边界，不能将 control-plane、provider 长流、运维 HA、RAG 质量和最终压测隐含在 schema 迁移中。

## Constraints

- 不操作线上数据库、线上 compose 或线上环境变量。
- 不在 main 或非任务 worktree 修改业务代码。
- 不依赖 Redis 作为持久事实或 lease/settle 真源。
- 不使用 `create_all` 替代正式迁移，不以全量 pytest 通过替代 PostgreSQL 语义证明。

## Acceptance Criteria

- [ ] inventory 覆盖表、列、FK、CHECK、unique/partial index、trigger、raw SQL、迁移和备份路径，且每项有 owner/status/evidence。
- [ ] 43 个事务调用点完成语义分类、锁参与者和全局锁序检查；反向锁序 mutation/死锁探针可捕获。
- [ ] PostgreSQL prototype 通过 schema build、runtime query、constraint、lease/CAS/settle/recovery 并发矩阵。
- [ ] 正向、负向、mutation 三类测试均能证明关键保护确实被使用。
- [ ] snapshot/import/reconciliation/backup/restore/refusal/runbook 在隔离环境演练通过。
- [ ] 未完成项、风险、证据等级和回滚点全部落盘；只有全部 gate 通过才允许父任务进入业务实现。
