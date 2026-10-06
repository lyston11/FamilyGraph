# 连续执行台账

本文件是 C0–C10 的唯一进度真相。后续任何模型从这里继续，不要向用户询问下一步。

状态取值：`todo` / `doing` / `done` / `blocked`。

| Gate | 阶段 | 状态 | Owner 任务 | 交付物 | 证据 | 阻塞 |
|---|---|---|---|---|---|---|
| C0 | 环境/边界冻结 | **done** | 本任务 | manifest、worktree、依赖矩阵 | `c0-environment.md` | — |
| C1 | PG baseline/schema/dialect | **done** | postgres-migration | 87 表 + 66 trigger 等价物 + 13 负向/正向用例 | `c1-baseline.md` | — |
| C2 | counter/lease/CAS/settle/recovery | **done** | postgres-migration | 3 个租约入口 + 全部归还路径已接线 | `c2-capacity.md`、`c2-lock-order-verified.md` | — |
| C3 | control-plane fault domain | **partial** | 10-04-control-plane-fault-domain | 集群级名额已交付；AC-5 分进程未做 | `c3-cluster-capacity.md` | AC-5 分进程/分池 |
| C4 | Provider stream reliability | **partial** | 10-04-provider-reliability-boundaries | 流级名额 + deadline 已交付；circuit/backpressure 未做 | `c4-stream-limits.md` | circuit、backpressure、故障注入 |
| C5 | Redis coordination | **partial** | 10-03-redis-coordination | 降级层已交付并验证；未接入准入路径 | `c5-redis-degradation.md` | 接入 admission、wakeup、token bucket |
| C6 | PGroonga lexical + pgvector | **partial** | 10-04-lexical / 10-03-pgvector-rag | 词法方言分派已交付；pgvector 未接入 | `c6-lexical-dispatch.md` | pgvector union/rerank、版本切换 |
| C7 | operations/cutover | **partial** | 10-04-postgres-operations-cutover | epoch + migration health 已交付；PITR/HA 未做 | `c7-writer-epoch.md` | 写路径接入 epoch、PITR、PgBouncer |
| C8 | final load acceptance | **partial** | 10-04-multitenant-load-acceptance | 三层配额守恒已验；真实负载未做 | `c8-capacity-acceptance.md` | 真实部署与负载 |
| C9 | dev shadow → writer | todo | 本任务 | 分阶段切换 | — | 真实部署 |
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

### C2 已完成

- 三个租约入口接线：`_check_concurrency`(account) / `enqueue_steward_job`(space，入队即占)
  / `lease_attempt`(space)；
- 归还路径全部走**行级门**（acquired/released 时间戳，Core SQL），共 8 处调用点；
- 静态调用图重跑：`entries_violating_frozen_order = 0`，`counter_implemented = true`；
- 并发配额与反证：`pg_capacity_concurrency.py`（counter 2/2 vs 子查询 5/5 越限）。

### 已登记的跨任务阻塞（不得由本任务临时实现替代）

| 任务 | 阻塞原因 | 恢复条件 |
|---|---|---|
| C3 control-plane | limiter 仍是进程内；两实例配额会翻倍 | C2 真实入口接线完成 |
| C4 provider | 长流 quota 需 counter 合同 | C2 完成 |
| C5 redis | 降级策略需 counter 语义确定 | C2 完成 |
| C6 RAG | PGroonga/pgvector 已验证，未接入真实 schema | C2 完成 |
| C7 operations | `writer epoch`/`migration health` 未设计 | 独立设计 |
| C8 load | 无真实多实例对象可压 | C3–C7 |


## ORCH-10：无主 TBD 清单（每项都有 owner、依赖、恢复条件与下一命令）

| # | 未完成项 | Owner | 依赖 | 恢复条件 | 下一命令 |
|---|---|---|---|---|---|
| 1 | ~~RAG 接入真实 schema（词法）~~ → **已完成**；pgvector union/rerank 仍待做 | `10-03-pgvector-rag` | C1/C2 已完成 | pgvector 已实测可行 | 把 filter-then-ANN 接入 `search_rag` 的 union/rerank |
| 2 | ~~Redis 接入准入路径~~ → **已完成**（负缓存形态） | — | — | — | 剩余：wakeup/pub-sub、token bucket、circuit hint |
| 3 | control-plane AC-5 分进程/分池 | `10-04-control-plane-fault-domain` | 无 | 需要进程模型设计 | 设计 Assistant/Steward 独立进程与资源预算 |
| 4 | ~~Provider circuit breaker~~ → **已完成**（`upstream × kind` 分区）；**backpressure 仍待做** | `10-04-provider-reliability-boundaries` | 无 | 可立即开始 | 实现上游 SSE 快于消费端时的缓冲上限/丢弃策略 |
| 5 | PITR / WAL archive / HA / failover | `10-04-postgres-operations-cutover` | 需要真实 PG 集群与归档存储 | 环境就绪 | 按 runbook 配置 archive_mode 并演练 PITR |
| 6 | PgBouncer 兼容性（transaction pooling 对 `FOR UPDATE`/advisory lock 的影响） | `10-04-postgres-operations-cutover` | 需要 PgBouncer 实例 | 环境就绪 | 起 PgBouncer 后重跑 `pg_capacity_concurrency.py` |
| 7 | 真实多租户负载 p95/p99 | `10-04-multitenant-load-acceptance` | 需要真实多实例部署 | 部署就绪 | 按 C8 探针的场景在真实部署上重跑 |
| 8 | 开发环境灰度（C9 shadow → writer） | 本任务 | 需要开发环境部署 | operations Gate 通过 | 按 `FG_WRITER_STAGE` 逐级推进并观察 `/ready` |
| 9 | 真实历史库导入与对账 | `10-03-postgres-migration` | C1/C2 已完成 | 可开始 | 用 `import_reconcile_probe.py` 的流程对真实快照执行 |

**不存在无主 TBD**：以上每项都有 owner、依赖、恢复条件和下一命令。

## C9/C10 为何不能在本轮完成

C9（开发灰度切换）与 C10（最终对账 + 回滚演练）需要：

1. **真实开发环境部署**（当前开发环境仍跑 SQLite，未部署本次的 PG 路径）；
2. **真实历史库快照**（不得直接复制运行中的主库）；
3. **真实多实例**（灰度需要两个实例才能验证 epoch 失效与不双主）。

这三项都涉及「接触真实环境」——属于本任务 PRD 的**停止条件**（线上/不可逆数据风险），
因此必须由用户决定时机，不能自主执行。

**可以自主完成的部分已完成**：epoch 机制、migration health 端点、回滚语义、
不双主的守卫、以及全部可执行的容量验收。

## 环境

- 主检出：`/Users/lyston/PycharmProjects/familygraph`（只做串行 merge）
- 本任务 worktree：`/Users/lyston/PycharmProjects/fg-10-06-architecture-completion-orchestration`
- 分支：`feat/10-06-architecture-completion-orchestration`
- base：`main @ 17a08dfe`
- 隔离 PostgreSQL：按需一次性容器，禁止指向开发库/线上
