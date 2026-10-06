# PostgreSQL 迁移执行前证明与安全门

## 目标

在任何 PostgreSQL 迁移业务代码继续扩大前，先建立能够证伪错误设计的证明门，避免“SQLite 测试通过、迁移后才发现语义错误”的循环。

本任务不是实现 PostgreSQL 业务迁移；它负责证明迁移设计是否足够安全，并决定后续实现是否可以开始。

## 已确认的触发事实

本项目已经出现以下真实问题：

- SQLAlchemy partial unique index 只有 `sqlite_where`，PostgreSQL 会丢失谓词，导致历史行与 active 行的唯一性语义改变；
- `json_extract` 同时出现在建表 CHECK 和运行期查询中，前者可能阻塞建表，后者可能只在请求执行时失败；
- SQLite `BEGIN IMMEDIATE` 提供全库写串行化；迁移到 PostgreSQL 行锁后，counter→run 与 run→counter 的反向锁序可产生真实 `DeadlockDetected`；
- capacity counter 的进入/离开不是简单的 status 映射：`reserved→skipped` 可能从未占用名额，而 `in_flight→skipped` 必须归还；
- 单连接探针不能证明多租户配额、多实例租约、崩溃恢复、重复结算或回滚安全。

这些问题说明缺的是执行前合同和证据门，不是单纯缺少更多 pytest。

## 范围

### 必须完成

1. 表、列、FK、CHECK、unique/partial index、trigger、raw SQL、迁移和备份路径的 stable inventory；
2. 全部真实事务入口的语义分类与调用方映射；当前工作假设为 43 个真实调用点，数量变化必须给出差异解释；
3. SQLite/PostgreSQL 建表、运行期 SQL、NULL、JSON、索引谓词、排序、时间和事务语义矩阵；
4. 真实隔离 PostgreSQL 多连接下的 CAS、lease、counter、event idempotency、settle、cancel、recovery 和 deadlock 证明；
5. 故障注入、snapshot/import/reconciliation、backup/restore 和 refusal/runbook 的设计与演练；
6. control-plane、provider、operations、lexical/RAG、load-acceptance 子任务的接缝和阻塞关系。

### 本任务不实现

- PostgreSQL 全量业务 schema 迁移；
- control-plane worker/sidecar 分进程；
- Provider 长流 quota/circuit；
- PGroonga 或 Unicode n-gram 生产检索；
- PostgreSQL 生产 HA/PITR 实施；
- 最终多租户压力验收；
- 任何开发或线上 writer 切换。

## 证据等级

- L0：源码/注释推断，只能产生假设；
- L1：SQLite 单元或静态检查，只能证明 SQLite；
- L2：隔离 PostgreSQL 单连接或局部探针，证明方言/局部行为；
- L3：真实 PostgreSQL 多连接、故障注入、恢复或迁移演练；
- L4：开发环境灰度、真实运行链路和用户可见结果。

低等级证据不得升级成高等级验收。

## 固定执行规则

每个切片必须先有合同卡：

```text
Contract ID
事实来源/调用方
输入/输出
持久不变量
锁参与者与全局锁序
成功/竞争/异常/取消/超时路径
重试与幂等键
审计/计费影响
SQLite/PG 差异
正向/负向/mutation 用例
故障注入与恢复动作
回滚点
证据等级
```

缺一项只能停留在 planning/probe，不能写业务实现。

## 验收标准

| ID | 可观察结果 |
|---|---|
| PG-0 | Gate 0 的任务边界、worktree、环境 manifest 和子任务依赖已固定；不存在未登记的跨任务职责。 |
| PG-1 | inventory 覆盖表/列/FK/CHECK/index/trigger/raw SQL/migration/backup，并为每项记录 owner、状态、证据等级。 |
| PG-2 | 全部真实事务调用点完成 CAS/父行锁/advisory lock/counter/SERIALIZABLE 分类；数量差异可解释。 |
| PG-3 | 全局锁序通过静态调用图和双连接死锁探针；反向锁序 mutation 必须失败。 |
| PG-4 | 隔离 PostgreSQL 完成 schema build、runtime query、constraint、lease/CAS/settle/recovery 矩阵。 |
| PG-5 | 正向、负向、mutation、故障恢复四类证据都存在；不能只靠 happy path。 |
| PG-6 | snapshot/import/reconciliation/backup/restore/refusal/runbook 在隔离环境演练；校验失败不会切 writer。 |
| PG-7 | 所有跨任务接缝已有输入/输出/健康信号/回滚语义；未完成子任务明确阻塞父任务。 |
| PG-8 | 全部风险、未覆盖项、证据等级和回滚点落盘；**PG-0..PG-5 全部通过**才允许 `postgres-migration` 进入业务实现阶段。 |

## 范围修正（2026-10-06）：PG-6/PG-7 不属于本任务

原 PRD 要求「PG-0..PG-7 全部通过才放行」，但其中两项存在**循环依赖**：

| 项 | 问题 |
|---|---|
| PG-6 dev shadow / cutover readiness | shadow read 与 writer 切换**必须基于已存在的实现**；实现之前无法执行 |
| PG-7 cross-task load acceptance | 多租户压测**必须基于已实现的 counter/lease**；且它是父任务的 release gate |

让「实现前的证明门」依赖「实现后的验收」是自相矛盾的。因此修正为：

- **本任务负责 PG-0..PG-5**（实现前门禁）：范围/环境、inventory、锁序/并发证明、
  PostgreSQL prototype、故障恢复、导入对账与备份。
- **PG-6/PG-7 移交**：
  - PG-6 的实现由 `10-03-postgres-migration` 在实现 counter 后自行满足；
  - PG-7 归 `10-04-multitenant-load-acceptance`（父任务的最终 release gate）。

本任务交付的是「迁移是否可以安全开始实现」的证据包，以及可复跑的探针与扫描器，
供 PG-6/PG-7 在其对应阶段重跑。

## 当前状态

PG-0..PG-5 的门禁工作已完成并有可复跑证据（见 `research/evidence/gate-*-*.md` 与
`scripts/migration-proof/`）。PG-6/PG-7 按上述修正移交，不作为本任务的完成条件。
