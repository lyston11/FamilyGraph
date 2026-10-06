# 连续执行台账

本文件是 C0–C10 的唯一进度真相。后续任何模型从这里继续，不要向用户询问下一步。

状态取值：`todo` / `doing` / `done` / `blocked`。

| Gate | 阶段 | 状态 | Owner 任务 | 交付物 | 证据 | 阻塞 |
|---|---|---|---|---|---|---|
| C0 | 环境/边界冻结 | **done** | 本任务 | manifest、worktree、依赖矩阵 | `c0-environment.md` | — |
| C1 | PG baseline/schema/dialect | **done** | postgres-migration | 87 表 + 66 trigger 等价物 + 13 负向/正向用例 | `c1-baseline.md` | — |
| C2 | counter/lease/CAS/settle/recovery | **doing（机制已完成，入口未接入）** | postgres-migration | counter schema + 真实入口接入 | `c2-capacity.md` | 真实入口接线 |
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

## 环境

- 主检出：`/Users/lyston/PycharmProjects/familygraph`（只做串行 merge）
- 本任务 worktree：`/Users/lyston/PycharmProjects/fg-10-06-architecture-completion-orchestration`
- 分支：`feat/10-06-architecture-completion-orchestration`
- base：`main @ 17a08dfe`
- 隔离 PostgreSQL：按需一次性容器，禁止指向开发库/线上
