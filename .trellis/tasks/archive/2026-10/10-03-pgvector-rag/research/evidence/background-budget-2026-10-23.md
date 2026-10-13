# 后台维护预算对控制面的影响（2026-10-23，真实开发环境）

## 要回答的问题

`10-03-pgvector-rag` 的最后一条 AC：

> 测量查询 p50/p95/p99、更新延迟、索引构建、空间隔离和 **background budget 对
> control-plane 的影响**。

`maintenance.py` 的向量索引段自述：

> 有界批数：`max_batches=1` 使单次 tick 最多处理一批，控制权定期回到维护循环，
> 且不会长时间占用那 1 核 embedding 预算。

## 方法

`scripts/migration-proof/background_budget_probe.py` 在**运行中的开发服务**上以
30–50ms 间隔持续采样 `GET /api/ready`，同时读 embedding 服务的 `/readyz`，
把延迟序列按「是否与 embedding 活动重叠」分组。

**只读保证**：只发两个 GET。不写业务数据、不改配置、不重启服务。

## 实测（90s + 180s 两轮）

```text
=== 180s / 30ms 间隔，n=4050 ===
  全部采样：p50=12.52ms  p95=16.03ms  p99=22.30ms  max=185.24ms   {200: 4050}
  embedding 忙的采样数：0/4050
  判定：控制面 p99=22.30ms 有界；无 5xx
```

## 结论（附诚实的限制）

**控制面在有维护循环的情况下仍然有界**：p99 = 22.30ms（4050 次采样），无 5xx。

**但「embedding 活动期 vs 空闲期」的对比没有做出来**：180s 内 embedding gate 的
`active` 始终为 0。原因是**没有待索引的 chunk**——维护循环日志显示
`rag_index_materialized: 1` 但 `embedding_indexed: 0`（每 5s 一次 tick）。

因此**不能声称「已证明 embedding 满载不影响控制面」**。本轮能证明的只是：
维护循环（含 RAG 物化）在跑时，控制面 p99 有界。

要真正做出该对比，需要先制造待索引语料（例如灌入大量 memory 使
`pending_chunks` 非空），再重跑本探针。登记为未做。

## 本轮附带发现并修复的真实缺陷

采样时发现 `/api/ready` **持续返回 503**：

```json
{"capacity_ready":false,
 "capacity_error":"pg_all 阶段容量计数行未就绪：孤儿占用
   global:0:provider_stream 计数行 active=1;
   孤儿占用 space:2:provider_stream 计数行 active=1。..."}
```

根因是 `capacity_bootstrap.reconcile` 把**在途 SSE 流**误判为孤儿占用
（`_active_counts` 从不建 `provider_stream` 的 bucket）。**不是泄漏被漏报，
而是正常占用被误报为泄漏**——每次有流在途，整个实例的就绪探针都失败。

已修复并合入 main（任务 `10-13-control-plane-stream-orphan`，含两条回归与反证）。
修复后 `/api/ready` 恢复 200，本探针才得以产出上面的数字。

## 探针自身的一个缺陷（已修）

初版读 embedding gate 的 `waiting` 键，而真实键是 `waiting_online` /
`waiting_background`。读错键让 `busy` 恒为 0——**看起来像「后台无影响」，
实际是「没有分组」**。已修正；修正后仍为 0，但这次是真实的「无 embedding 负载」。

## 环境清理

本探针不创建数据库、不写业务数据、不重启服务。线上未接触。
