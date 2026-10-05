# 技术设计：迁移执行前证明与安全门

## 1. 核心原则

先证明合同，再实现迁移；先在隔离 PostgreSQL 证明，再触及业务路径；失败时停留在当前 Gate，不通过放宽断言或切换环境消除失败。

本任务输出的是“迁移是否可以安全开始”的证据包，不是 PostgreSQL 生产实现。

## 2. 证据与问题分类

每个结论同时记录：

```text
事实 / 假设 / 未决设计
证据等级 L0-L4
证据路径
反证方式
当前结论
下一步
```

失败必须归类为：

- 实现 bug；
- 测试 oracle 错误；
- 环境阻塞；
- 设计未决；
- 范围越界。

不得通过删除用例、放宽阈值、手工改数据或替换环境来“修复”失败。

## 3. Schema 与 SQL 合同

建立 `SCHEMA-*` stable inventory，至少包含：

- table/column/type/nullability/default；
- FK、`ON DELETE`、CHECK；
- unique/partial index 的双方言 render；
- trigger、virtual table、FTS、generated expression；
- raw SQL、JSON 运算、时间函数、排序和 NULL 语义；
- migration upgrade/downgrade/refusal guard；
- backup/restore/export/import 路径。

每一项要有 SQLite 与 PostgreSQL 的：

```text
DDL render
runtime execution
boundary values
failure shape
rollback behavior
```

## 4. 事务与锁合同

第一版全局锁序冻结为：

```text
global capacity
→ kind capacity
→ tenant capacity
→ parent/resource row
→ run row
→ attempt/event row
```

分类规则：

- 单行状态：条件 UPDATE + affected-row CAS；
- 已有父资源的资格与多表写入：父行 `FOR UPDATE`；
- 自然键不存在：事务 advisory lock + DB unique constraint；
- 租约/配额：持久 counter row + candidate `FOR UPDATE SKIP LOCKED`；
- 无法分解的极少数跨行不变量：`SERIALIZABLE` + bounded retry。

每个入口都要说明是否持有两类以上锁。若现有 fence/settle 调用顺序违反锁序，必须在边界重构为 admission-before-fence、独立幂等 release transaction 或 CAS，而不能用注释掩盖反向锁序。

## 5. Agent control prototype

只建立最小隔离 prototype，不接真实业务 writer：

```text
agent_capacity_counter
agent_run_or_grant
agent_attempt
agent_event_idempotency
agent_audit
```

验证：

- 两实例/多连接不会重复 lease；
- tenant/global capacity 不超额；
- lease renewal/cancel/settle/recovery 终态唯一；
- `(run_id, seq)` 重放幂等；
- counter enter/leave 恰好一次；
- process crash、连接断开、deadlock/serialization failure 后可恢复；
- 不把网络或模型 I/O 放在事务内。

## 6. 故障矩阵

至少注入：

```text
lock timeout / deadlock
serialization failure
connection loss before commit
connection loss after upstream may have sent
process crash after lease
process crash after phase-1 settle
duplicate event/settle/recovery
cancel racing with settle
membership revocation during execution
```

每种故障必须记录：终态、幂等键、是否重试、是否计费、审计结果、恢复动作和回滚动作。

## 7. 数据迁移证明

迁移只允许使用静态隔离 SQLite snapshot：

```text
snapshot provenance
→ staging import
→ preserve IDs/timestamps/revisions/FKs
→ sequence repair
→ count/hash/scope/status/audit reconciliation
→ backup/restore rehearsal
→ refusal on mismatch
```

不允许在 live SQLite 上直接复制数据库文件；不允许 SQLite 与 PostgreSQL 同时裁决 lease/settle；对账失败不自动修复、不切 writer。

## 8. 跨任务接缝

| 接缝 | 提供方 | 本任务要求 |
|---|---|---|
| control reserve / worker recovery | `10-04-control-plane-fault-domain` | counter、health、writer epoch 接口稳定 |
| stream quota / circuit | `10-04-provider-reliability-boundaries` | 不绕过 run budget 和 egress audit |
| pool / backup / HA / cutover | `10-04-postgres-operations-cutover` | migration health/readiness/rollback 信号 |
| lexical/RAG index | `10-04-lexical-search-migration` + `10-03-pgvector-rag` | source/revision/scope/citation 不变 |
| final matrix | `10-04-multitenant-load-acceptance` | 作为父任务 release gate |

未完成的接缝不得以本任务的简化实现替代。

## 9. Gate 通过条件

```text
Gate 0 scope/worktree/env
→ Gate 1 inventory/dialect
→ Gate 2 lock/CAS/counter/idempotency
→ Gate 3 real PostgreSQL prototype
→ Gate 4 fault/recovery
→ Gate 5 import/reconciliation/backup
→ Gate 6 dev shadow/cutover readiness
→ Gate 7 cross-task load acceptance
```

任何前置 Gate 失败，后续 Gate 自动阻塞。
