# 连续执行台账

本文件是 C0–C10 的唯一进度真相。后续任何模型从这里继续，不要向用户询问下一步。

状态取值：`todo` / `doing` / `done` / `blocked`。

| Gate | 阶段 | 状态 | Owner 任务 | 交付物 | 证据 | 阻塞 |
|---|---|---|---|---|---|---|
| C0 | 环境/边界冻结 | **done** | 本任务 | manifest、worktree、依赖矩阵 | `c0-environment.md` | — |
| C1 | PG baseline/schema/dialect | **done** | postgres-migration | 87 表 + 66 trigger 等价物 + 13 负向/正向用例 | `c1-baseline.md` | — |
| C2 | counter/lease/CAS/settle/recovery | **doing（机制完成；入口未接线）** | postgres-migration | 机制已验证；真实入口接线待做 | `c2-capacity.md` | 需接线 3 个 lease 入口 + 4 条归还路径 |
| C3 | control-plane fault domain | todo | 10-04-control-plane-fault-domain | pool/reserve/recovery | — | C2 |
| C4 | Provider stream reliability | todo | 10-04-provider-reliability-boundaries | quota/deadline/circuit | — | C2 |
| C5 | Redis coordination | todo | 10-03-redis-coordination | admission/degradation | — | C2 |
| C6 | PGroonga lexical + pgvector | todo | 10-04-lexical / 10-03-pgvector-rag | 索引 + 授权过滤 | — | C2 |
| C7 | operations/cutover | todo | 10-04-postgres-operations-cutover | epoch/PITR/backup | — | C2 |
| C8 | final load acceptance | todo | 10-04-multitenant-load-acceptance | account×space×kind 矩阵 | — | C3–C7 |
| C9 | dev shadow → writer | todo | 本任务 | 分阶段切换 | — | C8 |
| C10 | reconciliation/archive | todo | 本任务 | 对账 + 回滚演练 | — | C9 |

## 执行规则（不得回问用户）

1. 普通技术选择使用 `prd.md` / `design.md` 的固定决策。
2. 每个切片：正向 + 负向 + mutation + 故障恢复 + 证据 + commit，然后继续。
3. 环境阻塞：记录命令与恢复条件，执行无依赖切片，继续。
4. 只有线上风险、不可逆数据、凭据泄露、产品语义矛盾、无法定义 oracle 才暂停。
5. 不双主、不手工修对账差异、不把低等级证据升级为验收。

## 当前进度（2026-10-06）

```text
C0  done    环境/边界冻结；扫描器基线（87 表/66 触发器/65 入口/0 锁序违反）
C1  done    PG baseline：87 表 + 66 触发器等价物 + 13 负向/正向用例
C2  doing   counter 机制已交付并证明；尚未接入真实入口
C3-C10 todo 见下表
```

### C2 剩余（下一步）

1. 接线 `agent_queue.lease_next`、`steward.lease_next_steward_job`、
   `steward_assist.lease_attempt` 三个租约入口；
2. 接线四条归还路径：settle / cancel / 租约过期恢复 / 栅栏退休；
3. 每条路径验证「恰好一次」；
4. 重跑 `pg_capacity_concurrency.py` 与 `build_lock_order_graph.py`
   （`entries_violating_frozen_order` 必须仍为 0）。

### 已登记的跨任务阻塞（不得由本任务临时实现替代）

| 任务 | 阻塞原因 | 恢复条件 |
|---|---|---|
| C3 control-plane | limiter 仍是进程内；两实例配额会翻倍 | C2 真实入口接线完成 |
| C4 provider | 长流 quota 需 counter 合同 | C2 完成 |
| C5 redis | 降级策略需 counter 语义确定 | C2 完成 |
| C6 RAG | PGroonga/pgvector 已验证，未接入真实 schema | C2 完成 |
| C7 operations | `writer epoch`/`migration health` 未设计 | 独立设计 |
| C8 load | 无真实多实例对象可压 | C3–C7 |

## 环境

- 主检出：`/Users/lyston/PycharmProjects/familygraph`（只做串行 merge）
- 本任务 worktree：`/Users/lyston/PycharmProjects/fg-10-06-architecture-completion-orchestration`
- 分支：`feat/10-06-architecture-completion-orchestration`
- base：`main @ 17a08dfe`
- 隔离 PostgreSQL：按需一次性容器，禁止指向开发库/线上
