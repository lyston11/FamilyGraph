# 运行时多租户资源隔离与重试预算

## Goal

在不等待数据库迁移完成的前提下，先为 Assistant(account) 与 Steward(space) 建立可证明的运行时资源隔离、控制面保留容量、公平调度和单一 run-level retry/time budget。

## Scope

- Assistant 以 `account_id`、Steward 以 `space_id` 为 tenant key；同时支持 global、kind、control-plane 预算。
- 分离 control/model/tool/background limiter 或 worker pool；控制请求不等待模型流、工具突发和索引维护。
- 消除 provider retry 与 session retry 的乘法放大，定义错误类别、总尝试次数、总墙钟预算、退避和 circuit scope。
- 增加不含 prompt、正文、凭据和 SQL 参数的 queue/saturation/retry 诊断。
- 在当前 SQLite 环境先做运行时并发实验，但不改变 SQLite 作为过渡存储的定位。

## Dependencies / non-dependencies

- 依赖父任务定义的 resource key、控制面优先、kind/token/fence/settle 合同。
- 不依赖 PostgreSQL、Redis 或 pgvector 实现即可完成第一阶段的运行时测试；后续必须在 PostgreSQL 迁移后的连接/事务模型上复验。
- 不得把 Redis 或 sidecar 内存状态当作持久 run/lease 真源。

## Acceptance Criteria

- 单 account/space 的突发不能占满另一 tenant、另一 kind 或 control-plane 的保留容量。
- heartbeat/lease/cancel/settle 在 model/tool/retry 过载下注入时仍在有界时间内完成。
- 单个 provider/DERP 故障只消耗该 upstream/kind/tenant 的 retry budget；不形成 6×4 无界乘法。
- 取消、撤权、租约恢复、egress 一次一审计、fence 和 settle 合同回归通过。
- 真实并发矩阵与故障注入证据写入任务 research，不以单 run 成功代替验收。

## Resolution (2026-10-04): delivered scope and remaining gaps

### 已交付并有证据

| 项 | 证据 |
|---|---|
| run 级重试预算（两层不再相乘） | `agent/test/retry-budget.test.ts`；生产基线：失败 run p50=p90=p99=**24** 次出站，成功 run p50=3 |
| 执行面按租户准入（account/space） | `tests/test_agent_execution_admission.py` |
| 两个执行平面独立（tool / provider） | 同上 |
| 按竞争保留（无竞争时可用满全局，避免误伤 26 个工具的单回合扇出） | `test_a_lone_tenant_may_use_the_whole_global_capacity` |
| aging 式公平队列（先到者优先，后来者不插队） | `test_earliest_waiter_wins_within_a_tenant_group`（变异：LIFO 失败） |
| 有界等待与队满立即拒绝 | `test_wait_is_bounded_and_rejects_explicitly`、`test_a_full_queue_rejects_immediately_instead_of_growing` |
| 取消不泄漏名额；超时竞态不丢名额 | 两个专项用例 |
| 多租户同时突发时控制面仍在 1s 预算内 | `test_control_plane_keeps_its_budget_under_multi_tenant_burst` |
| 名额达连接池上限时**进程拒绝启动** | 变异验证：`AGENT_EXECUTION_GLOBAL_CAPACITY=15` → `RuntimeError` |

### 未交付（明确记录，不当作完成）

- **控制面独立 worker 池**：当前靠「全局名额 < 连接池上限」提供余量，不是独立池。
- **跨实例全局配额**：limiter 是进程内状态，只保护当前实例；需要持久化协调。
- **上游并发流数**：不受本层限制（需流级配额）。
- **upstream/profile circuit key**：一个 DERP/provider 故障仍会消耗所有租户的 run 预算
  （虽然被 run 级上限约束住，没有全局熔断，但也没有按 upstream 隔离的熔断）。
- **Phase A 完整基线**、**Phase D sidecar 分进程**、**Phase E 完整矩阵**
  （同用户跨空间 / RAG background / 撤权 / 重启）。

父任务 AC-2/AC-3 的**完整形态**因此尚未达成：隔离已建立且可证明，但控制面独立池与
跨实例配额仍缺。
