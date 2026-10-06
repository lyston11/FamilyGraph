# Gate 6/7：跨任务接缝与验收矩阵（契约，**未执行**）

> **重要**：本文件是**接口契约与验收计划**，不是验收结果。它记录「哪些格子必须由谁
> 证明」，以及本任务当前**没有**证明什么。任何把它读作「多租户已验收」的行为都是误读。

## Gate 6：跨任务接缝

`postgres-migration` 提供、其他子任务消费的接口：

| 接缝 | 本任务提供 | 消费方 | 阻塞条件 |
|---|---|---|---|
| capacity counter | `(scope_kind, scope_id, resource)` 行 + CAS 增减 | `control-plane-fault-domain`、`redis-coordination` | counter 表未落地 |
| lease/grant | `lease_owner`/`lease_until`/`generation` + `SKIP LOCKED` 选行 | `provider-reliability-boundaries` | 真实入口未在 PG 上验证 |
| writer epoch | 尚未设计 | `postgres-operations-cutover` | **未设计** |
| migration health/readiness | 尚未设计 | `postgres-operations-cutover` | **未设计** |
| reconciliation report | 行数/摘要/scope/status 对账（原型已验） | `multitenant-load-acceptance` | 仅原型，非真实库 |
| transaction 分类 | 65 入口 + 锁序 + 类别 | 全部子任务 | 静态调用图未完成 |

**明确未设计的两项**（`writer epoch`、`migration health`）属于
`10-04-postgres-operations-cutover` 的职责，本任务不代为实现。

## Gate 7：验收矩阵（计划，未执行）

行 = 隔离主体，列 = 维度。每格标注**谁负责**与**当前状态**。

| | 并发 lease | 配额上限 | 取消/撤权 | 故障恢复 | RAG 授权 |
|---|---|---|---|---|---|
| Assistant（account） | 原型 L3 | 原型 L3 | **未测** | 原型 L3 | 未测 |
| Steward（space） | 原型 L3 | 原型 L3 | **未测** | 原型 L3 | 未测 |
| 同用户跨空间 | **未测** | **未测** | 未测 | 未测 | 未测 |
| 多空间并发 | 原型 L3 | 原型 L3 | 未测 | 未测 | 未测 |
| control-plane 保留 | 未测 | n/a | 未测 | 未测 | n/a |
| Provider 长流 | 未测 | 未测 | 未测 | 未测 | n/a |

「原型 L3」= 在隔离 PostgreSQL 上用 `fi_*`/`proof_*` 最小模型验证过语义，
**不是**真实业务入口或真实多租户负载。

## 明确不能宣布的事

1. **不能**宣布多租户并发已达标：矩阵中「同用户跨空间」「control-plane 保留」
   「Provider 长流」全部未测。
2. **不能**宣布 SQLite 可以退出：writer 切换、导入真实历史库、备份恢复演练均未做。
3. **不能**宣布迁移设计已完整：`writer epoch` 与 `migration health` 未设计。
4. **不能**宣布触发器已全部等价：只验证了四类语义，60 个 `sri_*` 的逐表
   `scope_id` 解析未验证。

## 由谁闭合

- 真实多租户矩阵 → `10-04-multitenant-load-acceptance`
- control-plane 容量与故障域 → `10-04-control-plane-fault-domain`
- Provider 长流与 circuit → `10-04-provider-reliability-boundaries`
- 连接预算/备份/PITR/writer epoch → `10-04-postgres-operations-cutover`
- 中文词法检索 → `10-04-lexical-search-migration`
- 语义检索 → `10-03-pgvector-rag`

本任务的职责是：**为这些子任务冻结接缝与验收格子，并证明迁移机制本身安全**，
而不是替它们完成验收。
