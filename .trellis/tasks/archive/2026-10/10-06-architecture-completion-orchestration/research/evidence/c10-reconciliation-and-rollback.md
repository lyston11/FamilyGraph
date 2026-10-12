# C10：最终对账与验收矩阵（2026-10-23 补完）

## 本次执行的是什么

C9 之后 C10 长期停在 `todo`。本次在**开发环境真实 PostgreSQL**上补完对账与验收矩阵，
并逐项核对十个任务的实际状态。

**证据等级：L3（隔离多连接/故障）+ L4（开发真实链路对账）**，未接触线上。

## 1. 最终对账（L4，只读）

`scripts/migration-proof/reconcile_real_schema.py` 直连开发库（`pg_all` 阶段，
与 `main @ 44d7cb59` 同 commit）。该脚本**只读**（仅 stdlib + 惰性 psycopg，
无 INSERT/UPDATE/DELETE/DDL）。

```text
对账: PostgreSQL (PGTEST_DSN)
  accounts: 51 rows          family_spaces: 20 rows
  agent_runs: 1510 rows      steward_model_calls: 2580 rows
  memories: 1 rows           rag_documents: 4 rows        rag_chunks: 6 rows
  audit_log: 30491 rows
  agent_runs.status: {succeeded:1037, failed:380, running:1, expired:92}
  steward_model_calls.status: {in_flight:1, skipped:339, succeeded:1642,
                              degraded:25, failed:396, unknown:177}
  memories.scope: {household/space_id=set: 1}
  rag_documents.scope: {household/space_id=set: 1, public/space_id=NULL: 3}
  rag_documents revision 镜像: 一致
  attempt/run 孤儿: 无
  egress 审计: 1441 个 run，尝试数 min=1 p50=4 max=28
  egress sent=false 均带 error_class: 一致

对账通过: 无差异
```

七个维度各自对应一个合同：行数（不丢数据）、状态分布（状态机语义）、
scope/space 联合分布（`ck_*_scope` 约束）、revision 镜像、attempt/run 外键完整性、
egress **每次出站尝试恰好一条**且 `sent=false` 必须带 `error_class`。

**refusal 语义**：检出差异只报告、不自动修复。本次无差异。

## 2. 多租户容量与故障矩阵（L3，隔离库 `fg_probe_c10`）

`scripts/migration-proof/multitenant_matrix.py` 在独立 database 中自建 `mt_*` 表
（不碰业务 schema）：

| # | 维度 | 结果 |
|---|---|---|
| 1 | 租户配额（3 account × 6 并发，上限 2） | 各授予 2/6 ✓；**反证**：无行锁形态授予 6/6 越限，证明行锁承重 |
| 2 | 隔离性（account 1 满载，account 2 申请） | 立即可得，无饥饿 ✓ |
| 3 | 集群上限（global 上限 3，6 租户） | 授予 3/6 ✓ |
| 4 | 归还恰好一次（同一 run 并发归还 3 次） | 成功 1/3，剩余 0 ✓ |
| 5 | 泄漏检测（全部释放后） | 非零计数行 0 ✓ |
| 6 | 故障恢复（提交**前**断连） | active=0，未留占用 ✓ |
| 7 | 故障恢复（提交**后**断连） | active=1，占用保留可回收 ✓ |
| 8 | 取消 vs 结算并发 | 恰好一个赢家（cancel），终态 cancelled ✓ |

`PASS: 多租户容量与故障验收矩阵全部通过（含反证）`

## 3. 跨实例配额（L3，C3/AC-4 关键缺口）

`scripts/migration-proof/cross_instance_capacity.py`：

```text
1) 进程内 limiter（旧形态）
   每实例配置=2 实例=2 观测并发=4 期望集群上限=2
   OK  确实越限（4 > 2），证明进程内状态无法约束集群配额
2) 持久化 counter（现形态）
   集群配置=2 实例=2 观测并发=2 lease 行=2
   OK  未越限（2 <= 2）
PASS
```

**这条是承重的**：它证明 `ResourceLimiter` 的进程内 `active` 在多实例下会把配额
翻倍（2 实例 → 4），而持久化 counter 形态正确约束。没有反证，「counter 形态并发=2」
可能只是用例没构造出并发。

## 4. 三层配额守恒（L3）

`scripts/migration-proof/multitenant_acceptance.py`：

```text
1) 租户级配额（space）      占用 6 每租户 {1:2, 2:2, 3:2} 越限 []
2) 集群级上限（cluster_provider）  占用 3（期望 ≤ 3）
3) 流级三层（global/kind/tenant）  占用 3 每租户 {1:1, 2:1, 3:1}
4) 归还守恒（无泄漏）       归还后非零计数行 0
PASS
```

## 5. 回滚语义（已由单测覆盖，未在 dev 演练）

**核实结论：`writer_state.stage` 不控制写路由。** 选库只由 `DATABASE_URL` 决定
（`db.py:31 IS_SQLITE = config.DATABASE_URL.startswith("sqlite")`）；`stage` 只影响
`/ready` 报告与 `capacity.assert_ready` 的 `pg_all` 门禁（`capacity._writer_is_pg_all`）。

因此「回滚」= 改 `DATABASE_URL` + `advance()` 递增 epoch（使旧实例立即失去写权），
**不是** stage 变化触发的写重路由。这一区分很重要：把 stage 当写路由会让
「回退到 pg_control」意外停写。

`writer_epoch` 的回滚机制已实现并有 **22 条**测试覆盖，含：

- `test_rollback_is_adjacent_and_invalidates_old_epoch`（回退是相邻的，且使旧 epoch 失效）
- `test_epoch_mismatch_is_a_safety_error_not_a_retry`（epoch 过期是安全异常，不重试）
- `test_advance_rejects_stage_skipping`（禁止跳级，保证「哪些面切过」可知）
- `test_migration_health_contains_no_secrets`

**未做 dev 实机回滚演练**：它需要改开发环境的 `DATABASE_URL` 并重启 systemd 服务，
属于「接触真实环境」——PRD 停止条件，须由用户决定时机。机制与语义已由单测与
本文件第 5 节的代码核实共同承担；实机演练登记为待用户批准项。

## 6. 环境清理

```text
探针数据库 fg_probe_c10：已 DROP（pg_database 中无 %probe% 残留）
开发库 familygraph：未受影响（agent_runs 1510 行不变）
线上：未接触
本机 docker daemon：DOWN（探针经 ssh lyston 在开发服务器执行，与 C0 manifest 记录一致）
```

## 7. C10 结论

| C10 要求 | 结论 |
|---|---|
| 最终对账 | ✅ 通过（7 维度，无差异） |
| 回滚演练 | ⚠️ 机制与语义已证（22 条单测 + 代码核实）；**实机演练待用户批准** |
| 验收矩阵 | ✅ 通过（容量/隔离/集群/归还/泄漏/故障恢复/取消竞争 + 反证） |
| 跨实例配额 | ✅ 通过（含 limiter 越限反证） |
| spec 更新 | 见本轮 spec 变更 |

**仍然不能归档本任务**：C9 的线上发布由用户手动执行；C10 的实机回滚演练同属
停止条件。两项都已登记 owner、依赖与恢复条件。
