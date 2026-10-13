# 连接池耗尽与故障注入（2026-10-23，专用验收实例）

## 为什么需要专用实例

`10-04-postgres-operations-cutover` 的 AC 要求在隔离 PG 上注入
**pool exhaustion、长事务、断线、重启**。在**共用开发服务**上占满连接池会危及
同机其他服务（`fast-note-sync-service`、`learngraph-*`、生产容器都在同一台机器上），
因此这类注入此前一直挂着。

本轮建立**专用验收实例**：独立 PG（`fg-acc-pg`，127.0.0.1:55460，pgvector +
pgroonga）+ 独立 Redis（`fg-acc-redis`，56380）+ 第二个真实 API 进程（8010/8011/8012）。
与开发服务、生产容器完全隔离。

## 安全护栏（本探针最重要的部分）

一个能对数据库执行「占满连接」的脚本，护栏比功能重要。`pool_exhaustion_probe.py`
在饱和前强制检查：

1. PG 端口必须在 `ACC_ALLOWED_PORTS` 白名单内（默认只含 `55460`）；
2. **显式拒绝** `55450`（共用开发库）与 `5432`（生产）；
3. 打印 `max_connections` 与当前占用，饱和量 = `max - 已用 - 预留(8)`。

任一不满足即 `REFUSED` 退出（返回码 2），不做任何注入。

## 实测

```text
护栏通过：PG 端口 55460 在专用白名单内
初始：max_connections=100，已用 11，计划占住 81（预留 8）

=== 1. 基线（40 次）===
  p50=12.51ms  p95=13.71ms  p99=14.03ms   {200: 40}

=== 2. 长事务（持有未提交事务 20s）===
  p50=12.31ms  p95=14.11ms  p99=17.62ms   {200: 60}
  pg_state：{active: 1, idle: 5, idle in transaction: 1}

=== 3. 连接池耗尽（占住 81 条）===
  实际占住 81 条
  pg_state：total=92 {active: 1, idle: 86}
  p50=12.56ms  p95=17.13ms  p99=18.23ms   {200: 80}
  释放后：total=11 {active: 1, idle: 5}

=== 4. 断线恢复（终止本实例空闲连接）===
  已终止空闲连接：5
  p50=13.76ms  p95=19.45ms  p99=33.45ms   {200: 60}

判定：耗尽期 5xx=0，无响应(k=0)=0，p99=18.23ms
     OK  控制面在池耗尽下未挂死也未 5xx
```

## 结论

1. **池耗尽不影响控制面**：占住 81/100 条连接（`total=92`）时，`/api/ready` 仍然
   p99=18.23ms、全部 200、**零 5xx、零无响应**。这正是
   `AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS - 1` 预留的验证——
   执行面容量上限为控制面留下连接，所以执行面打满不会让控制面拿不到连接。
2. **长事务不影响控制面**：`idle in transaction` 存在时 p99=17.62ms。
   `/api/ready` 的读不阻塞在长事务上（PostgreSQL MVCC 的正常行为）。
3. **断线后自愈**：终止 5 条空闲连接后，后续 60 次采样全部 200，
   p99 从 18ms 升到 33.45ms（重连成本）后稳定。
4. **饱和确实发生**：`total_connections` 从 11 升到 92，证明探针真的占住了连接。
   释放后回到 11——无泄漏。

## 仍未做

| 项 | 为什么 |
|---|---|
| PostgreSQL 重启 | 需人工决定窗口；专用实例上可做，但重启会中断其他注入项，应单独一轮 |
| HA failover / 主备切换 | 专用实例是单节点，无主备 |
| 跨机网络分区 | 需多主机 |
| 真实上游（DERP/DNS/TLS）故障 | 需真实上游 |

## 环境

```text
fg-acc-pg   127.0.0.1:55460  (pgvector + pgroonga, baseline 91 表)
fg-acc-redis 127.0.0.1:56380
API 8010/8011/8012（第二实例，独立 DATA_DIR=/tmp/fg-acc-data）
```
