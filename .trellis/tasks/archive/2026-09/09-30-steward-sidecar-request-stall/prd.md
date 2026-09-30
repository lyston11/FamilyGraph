# Sidecar 侧请求停滞：心跳与模型调用连续 100s+ 发不出

## 目标

消除 steward child run 期间 sidecar 的请求停滞：停滞发生时心跳与轮询请求连续
100s+ 无法发出，心跳重试耗尽后 sidecar 判定失租并 abort run，导致已成功完成的
模型调用被丢弃。限定开发环境；线上由用户手动发布。

## 触发

用户质疑上一任务（`09-30-steward-event-loop-block`）验收记录中「上游
`stream_interrupted`」的归因：`buddy2api` 可用，且当时 Pi 会话本身就在用它。
复核后确认该归因**错误**，并发现一个**独立于连接池饥饿**的缺陷。

## 事实与证据（run 232，2026-09-30 13:00–13:08）

### 服务端全程健康（已确证）

| 观测 | 值 |
|---|---|
| provider egress | `upstream_status=200`、`header_ms=1405`、`status=succeeded` |
| 心跳请求响应 | **全部 200**（13:05:19 / 13:05:37 / 13:05:57 / 13:06:17） |
| 同期 API 请求数 | 窗口 13:03:30–13:06:30 服务 **992** 个请求 |
| 13:00:27–13:03:36 窗口 | 服务 **1146** 个 heartbeat/lease |
| API 进程 CPU / 日志 | 停滞期间仅 10 行日志，进程空闲 |

**结论：模型调用成功，心跳端点正常，服务端没有饥饿。** 上一任务记录的
「上游 `stream_interrupted`」是把**结果**当成了原因——`stream_interrupted` 在
`provider_proxy.passthrough_with_audit` 里是兜底分支，同时覆盖
`httpx.HTTPError`、`GeneratorExit`、`asyncio.CancelledError` 与 `Exception`，
sidecar abort 也会落到它。

### 停滞发生在 sidecar 侧（已确证）

```
13:03:09  心跳正常
13:03:37  心跳正常（此后 API 侧 101 秒 0 请求）
13:03:38 ─┬─ API 未收到任何请求 101 秒
13:05:19 ─┘ （连每 2s 一次的 lease 轮询也中断）
13:04:39  sidecar: poll loop iteration failed (steward)
            "internal request failed after 4 attempts: aborted due to timeout"
            （= 15s 请求超时 × 4 次重试，约 60s 全部超时）
13:05:19  积压请求一次性到达：1 秒内 6 条 heartbeat + 多条 lease
13:06:17  heartbeat 再次停滞约 60s 后 sidecar: lease lost, aborting run 232
13:08:01  run 232 → expired
```

**判据**：API 侧该窗口每秒完成请求数为 0（uvicorn access 在请求完成时写日志，
所以「0 完成」也可由在途请求造成——但 API 进程同期 CPU 空闲、日志仅 10 行、
且 `tool_admission_wait` 计数为 **0**，排除了工具突发与池饥饿）。请求**没有发出**。

### sidecar 侧已排除

- 进程未重启（`NRestarts=0`）、CPU 0%、无 GC 高峰；
- fd 充裕（24 个，`ulimit -n` 1048576）、无 TIME_WAIT 堆积、无端口耗尽；
- 到 8001 仅 2 条 ESTAB 连接，无连接泄漏；
- 代码内无 `execSync`/`spawnSync`/自定义 dispatcher。

## 需求

### R0 先定位 sidecar 停滞的确切原因（前置门）

必须取得直接证据说明停滞期间 sidecar 在做什么，而不是从「服务端健康」反推。
候选机制（**均未验证**，实施前必须区分）：

1. **SDK 层 provider 重试**：`session.ts` 给 pi-ai 传
   `maxRetries=AGENT_PROVIDER_STREAM_MAX_RETRIES`（默认 5）+
   `maxRetryDelayMs=AGENT_PROVIDER_STREAM_MAX_RETRY_DELAY_MS`（默认 20000），
   最坏 **5 × 20s = 100s**，与观测的 101s/158s 量级吻合。但 run 232 在网关侧只有
   **2 次** provider 请求（13:02:02、13:05:21），需解释重试为何没打到网关。
2. **HTTP 客户端连接被挂起的请求占满**：心跳与轮询共用同一 client；若某请求挂住
   并占用连接，后续请求会排队，直到它释放才一起发出（观测到 13:05:19 积压 6 条
   同时到达，符合此形态）。
3. **Node 事件循环被同步操作阻塞**：`setInterval` 心跳未能按 20s 触发。

### R1 停滞不得导致失租与产物丢弃

- 心跳在长 run 期间必须持续有效；已成功完成的模型调用不得因 sidecar 侧停滞被丢弃。
- **不得**通过放宽 `AGENT_REQUEST_TIMEOUT_MS`、`defaultLeaseMs` 或
  `STEWARD_ASSIST_CALL_LEASE_SECONDS` 掩盖。
- 停滞原因在 sidecar 时，修复应落在 sidecar；**不得**为掩盖它在服务端加特权。

### R2 可诊断

停滞发生时必须能直接定位（例如记录事件循环延迟、在途请求数、最近一次成功请求
的时刻），而不是事后靠 journalctl 拼接。诊断字段不得含正文、token、凭据。

### R3 合同不变

- 不改变 wire 协议、不新增端点、不改 fence/幂等/审计语义、不改 schema。
- 保留「取消/失租是服务端权威裁决」的合同。

## 验收

| ID | 可观察结果 |
|---|---|
| AC-0 | 停滞期间 sidecar 侧的直接证据（事件循环延迟、在途请求、阻塞栈之一），指名具体机制与量级 |
| AC-1 | 修复后同场景下 sidecar 无 100s+ 请求停滞；心跳间隔稳定 |
| AC-2 | 开发环境实测至少一次完整长 run，无因 sidecar 停滞产生的 `expired` |
| AC-3 | 既有 Provider/fence/结算合同回归通过；backend 与 agent 全量检查通过 |
| AC-4 | 停滞可从事后日志直接定位，无需人工拼接 |

## 不在范围

- 放宽任何超时或租约；改服务端特权；重构 provider 转发架构。
- 回填历史 `expired` run。
- 线上操作。
