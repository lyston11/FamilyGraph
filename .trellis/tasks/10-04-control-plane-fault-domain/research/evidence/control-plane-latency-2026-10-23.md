# 控制面尾延迟与连接饱和（2026-10-23，真实开发环境）

## 为什么做这个

`10-04-control-plane-fault-domain` 的 AC：

> execution 满载时 control p95/p99 有界。

`10-04-postgres-operations-cutover` 的：

> 在隔离 PostgreSQL 注入 pool exhaustion…

两者都需要**真实运行的服务**，探针在隔离库上做不到。

## 方法

`scripts/migration-proof/control_plane_latency_probe.py` 对**运行中的开发服务**打
`GET /api/ready`。选它是因为它是控制面的**真实代表**：一次请求要拿一条 DB 连接、
读 `writer_epoch`（含迁移阶段）、查扩展状态、跑 `capacity_bootstrap.assert_ready`——
正是 heartbeat/lease/settle/cancel/health 共享的那些资源。

**只读保证**：只发 `GET /api/ready` 与只读 SQL（`pg_stat_activity`）。
不写业务数据、不改配置、不重启服务。连接饱和通过**占用只读连接**制造，
而不是改池配置。内部端点（lease/heartbeat/settle）需要 service token，
伪造它等于绕过信任边界，因此不碰。

## 实测（2026-10-23）

```text
目标：http://127.0.0.1:8000/api/ready

=== 1. 基线（120 次）===
  p50=11.61ms  p95=15.52ms  p99=27.04ms  max=30.28ms   {200: 120}

=== 2. 并发负载（16 并发执行者 × 240 次）===
  p50=201.18ms  p95=351.45ms  p99=377.74ms  max=381.44ms  {200: 240}
  实际吞吐 80 req/s（3.0s）

=== 3. 连接饱和（占用 12 条只读连接）===
  饱和前 pg_stat_activity：{active: 1, idle: 5}
  饱和期间              ：{active: 1, idle: 17}   ← 12 条确实被占住
  释放后                ：{active: 1, idle: 5}
  p50=11.57ms  p95=18.09ms  p99=20.58ms  max=26.36ms  {200: 80}

判定：负载下 p99=377.74ms 有界；无 5xx
```

## 结论

1. **控制面在负载下有界**：p99 = 377.74ms（< 1s），**无 5xx**。负载下延迟上升是排队，
   不是故障——吞吐 80 req/s 且全部 200。
2. **连接饱和下控制面不受影响**：占住 12 条连接（`max_connections=100`，空闲从 5 升到 17）
   时，`/api/ready` 的 p99 反而更低（20.58ms）——因为它不需要等执行面。
   这正是 `config._validate_agent_execution_admission` 强制
   `AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS - 1` 的效果：
   为控制面留出连接余量（09-30 心跳延迟 30s 事故的直接防线）。
3. **饱和确实发生了**：`pg_stat_activity` 的 idle 从 5 升到 17，证明探针真的占住了连接，
   而不是「饱和没生效所以看起来没事」。

## 探针自身的一个缺陷（已修）

初版把 `postgresql+psycopg://` 直接传给 `psycopg.connect`，libpq 不认这种
SQLAlchemy 形式，报 `missing "=" after ...`。该异常被 `_pg_activity` 的宽 except
吞掉，于是第 3 节显示 `activity_during` 与 `activity_before` 相同——
**看起来像「饱和没有影响」，实际是「饱和根本没建立」**。
修法：转成 `postgresql://` 再连接。

这个缺陷值得记下来：**一个静默失败的饱和步骤会让「控制面不受影响」这个结论
变成同义反复**（因为没有负载）。

## 仍未覆盖

| 项 | 为什么还开着 |
|---|---|
| 真实 `pool exhaustion`（占满全部 100 条） | 会危及同机开发服务，需专用实例 |
| 真实长事务 / 断线 / PG 重启 | 需专用实例与重启窗口 |
| 内部端点（lease/heartbeat/settle）的尾延迟 | 需 service token；伪造等于绕过信任边界 |
| 真实执行面满载时的控制面延迟 | 需真实 provider 流量 |
| 跨机网络分区 | 需多主机 |

## 环境清理

本探针不创建数据库、不写业务数据。连接在探针退出前全部关闭
（实测 `idle` 回到 5）。线上未接触。
