# 多租户架构全量完成与连续执行编排

## Goal

为 `10-03-multi-tenant-agent-concurrency-storage` 及其全部子任务建立唯一的连续执行协议，使后续任何模型都能按固定顺序自主推进 PostgreSQL、control-plane、Provider、Redis、RAG、运维切换和最终多租户验收，不因阶段切换、方案选择或普通测试失败反复停下询问用户。

本任务负责**编排、依赖、停止条件和最终整合**，不替代各子任务的实现责任。

## User outcome

最终交付必须是一个可运行、可恢复、可验证的多用户系统：

- Assistant 按 `account_id` 隔离；
- Steward 按 `space_id` 隔离；
- PostgreSQL 是持久事实、lease、counter、settle、audit 和 recovery 真源；
- control-plane 不会被 Provider 长流、工具突发、RAG maintenance 或连接池耗尽拖垮；
- Redis 失效不会破坏持久语义；
- PGroonga/pgvector 只提供派生检索候选，授权、scope、revision 和 citation 仍由 PostgreSQL 裁决；
- writer 切换、备份恢复、回滚和多租户故障矩阵有可执行证据。

## Continuous execution contract

后续模型在获得本任务最终规划批准并启动后，必须遵守以下协议：

1. **不得因普通方案选择再次询问用户**。使用本文件、子任务 PRD/design、现有 spec 和代码证据中的既定决策。
2. 每个阶段先读取：当前任务 `implement.jsonl`、`check.jsonl`、`prd.md`、`design.md`、`implement.md`，再读取对应子任务材料。
3. 每个阶段必须先检查前置 Gate；前置未通过时，执行可行的诊断、修复、证据补齐和任务记录，不跳到后续阶段。
4. 每个独立切片都必须：在任务 worktree 修改 → 运行正向/负向/mutation/故障验证 → 更新证据 → 小步 commit → 继续下一个切片。
5. 测试失败必须分类为：实现 bug、oracle 错误、环境阻塞、设计未决、范围越界。只有真实环境破坏风险、密钥/线上安全风险、需求矛盾或无法自主选择的产品决策才允许暂停。
6. 普通技术选择采用本任务的默认决策，不向用户回问：
   - PostgreSQL 持久真源；
   - Redis 仅加速且可失效；
   - pgvector-first；
   - PGroonga-first lexical，Unicode n-gram portable fallback；
   - control-plane 保留容量；
   - writer epoch 路由回滚；
   - refusal over auto-repair；
   - 不双主、不把低等级证据升级为高等级验收。
7. 环境阻塞不得伪造通过：记录 blocker、运行不依赖该环境的其余工作、提供恢复命令，然后继续；不得把 exit 2 当 pass。
8. 每个子任务完成后先运行 `trellis-check`，再更新 spec，最后归档；父任务只有最终矩阵、备份恢复、对账和开发灰度全部闭合才可归档。
9. 不能因为“已经做了很多”提前停止；必须继续直到本文件的所有 required deliverables 完成，或把无法完成的项明确变成有 owner、依赖、恢复条件和下一执行命令的 blocker。
10. 不得自动操作线上环境。开发环境操作也必须在 operations/cutover Gate 通过后按 runbook 执行。

## In scope

- PostgreSQL baseline、schema、counter、CAS、lease、settle、recovery、导入对账和 writer epoch 接缝；
- control-plane/执行面/后台资源隔离、DB reserve、多实例恢复；
- Provider stream quota、backpressure、deadline、circuit 和 retry/egress 合同；
- Redis admission、token bucket、cache/wakeup 和故障降级；
- PGroonga lexical、pgvector semantic、索引生命周期和授权过滤；
- PostgreSQL 连接预算、PgBouncer、WAL/PITR、HA、backup/restore、cutover/rollback；
- account×space×kind 的负载、故障、恢复、可见结果和 release gate；
- 子任务依赖、证据等级、提交顺序、验收和归档。

## Out of scope

- 未经本任务 Gate 批准的线上 writer 切换；
- 把 Redis 或向量索引提升为持久业务事实；
- 用文档矩阵冒充真实多连接/故障/开发验收；
- 静默转换或删除历史数据；
- 为了让测试通过而放宽授权、唯一性、审计、回滚或 scope 约束。

## Fixed execution order

```text
C0 任务/环境/边界冻结
C1 PostgreSQL baseline + schema/trigger/index/SQL contract
C2 PostgreSQL transaction/counter/lease/CAS/settle/recovery
C3 control-plane fault domain and DB reserve
C4 Provider stream reliability boundary
C5 Redis coordination and degradation
C6 PGroonga lexical + pgvector RAG lifecycle
C7 operations: pool/PgBouncer/WAL/PITR/HA/backup/cutover
C8 integrated account×space×kind load/fault acceptance
C9 development shadow → control writer → agent/domain writer
C10 final reconciliation, rollback rehearsal, spec update, archive
```

### Dependency graph

```text
10-05 migration-proof-gates
        ↓
10-03-postgres-migration
        ├── 10-04-control-plane-fault-domain
        ├── 10-04-provider-reliability-boundaries
        ├── 10-03-redis-coordination
        ├── 10-04-lexical-search-migration ─┐
        ├── 10-03-pgvector-rag ─────────────┤
        └── 10-04-postgres-operations-cutover
                                             ↓
                         10-04-multitenant-load-acceptance
                                             ↓
                                      parent release gate
```

## Acceptance criteria

| ID | Observable result |
|---|---|
| ORCH-0 | 所有活跃子任务都有 owner、依赖、输入/输出、健康信号、rollback 和明确阻塞条件。 |
| ORCH-1 | 后续模型可从任务工件独立读取完整执行顺序，不需要用户提供下一步指令。 |
| ORCH-2 | PostgreSQL 成为唯一持久协调真源；counter/lease/CAS/settle/recovery 在真实多连接下守恒。 |
| ORCH-3 | control-plane 有独立保留容量，多租户执行不会拖垮 heartbeat/lease/cancel/health。 |
| ORCH-4 | Provider 长流、retry、deadline、circuit 和 egress exactly-once 在故障矩阵下收敛。 |
| ORCH-5 | Redis 失效、重启或延迟不会造成超额 lease、双主或丢失 settle。 |
| ORCH-6 | lexical/vector 索引撤权后不再命中；source/revision/scope/visibility/citation 由数据库最终裁决。 |
| ORCH-7 | backup/restore/PITR/sequence/FK/trigger/constraint 和 mismatch refusal 有实操证据。 |
| ORCH-8 | account×space×kind×provider×control-plane×RAG 矩阵完成正向、负向、mutation 和故障验收。 |
| ORCH-9 | writer 切换仅按 `shadow → control → agent/domain` 顺序进行，失败可按 epoch/路由回滚，不双主。 |
| ORCH-10 | 所有未完成项要么关闭，要么形成有 owner、依赖、恢复条件和可执行下一命令的 blocker；不存在无主 TBD。 |

## Stop conditions

允许暂停的条件只有：

- 即将接触线上环境或不可逆真实数据，但 runbook/授权不完整；
- 密钥、凭据、真实个人数据或安全边界可能泄露；
- 用户产品意图与现有合同冲突，且无法从既有决策推导；
- 环境破坏风险无法通过隔离实例规避；
- 关键测试 oracle 无法定义，继续执行会制造伪通过。

普通失败、依赖未完成、测试红、容器缺失、工具缺失、性能不达标都不是询问用户的理由：记录并修复、替代执行、创建明确 blocker，随后继续其他独立工作。

## Current planning status

本任务创建于 2026-10-05，下一步是完成 design/implement/manifests，获得本版最终规划批准后启动。启动后不得回到“先问下一步”的工作方式。
